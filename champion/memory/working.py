"""L1 - current conversation (working memory) (track T8.2).

Two parts:

* ``WorkingMemory``: what the next model call must know about the active
  task, kept outside the raw messages so it survives when old turns are
  dropped: the task, pinned user instructions and corrections, selected
  entities (paths, model, part, material, keyword and job IDs), retrieved
  evidence, assumptions, open questions and pending approvals.
  ``render()`` gives a short text block for the agent instructions (M6).
* ``select_history``: which earlier messages are sent. Not the whole
  conversation: the last ``max_turns`` turns, tool outputs of older turns cut
  to an excerpt, and the oldest turns dropped until the estimated size fits
  ``max_tokens``. A turn starts at a user message, so a tool call is never
  separated from its result. The dropped turns are reported, so L2 can
  summarise them (M6). The input list is never changed.

Tokens are estimated as characters / 4 (no tokenizer dependency); the
budget is a guard, not an exact count.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping

from champion.memory.storage import MemoryRefused, clean_text, cut, redact

CHARS_PER_TOKEN = 4
MAX_ITEMS = 50  # per list in the working memory
ENTITY_KINDS = ("paths", "model_ids", "part_ids", "material_ids", "keyword_ids", "job_ids")


@dataclass(frozen=True)
class WorkingSettings:
    max_turns: int = 8  # earlier turns sent with a request (6-12 recommended)
    max_tokens: int = 16000  # estimated budget for earlier turns + the new input
    tool_output_chars: int = 2000  # tool outputs of older turns are cut to this
    full_tool_turns: int = 2  # the last N turns keep their tool outputs whole
    render_chars: int = 4000  # size of the working-memory block

    def __post_init__(self) -> None:
        checks = (("max_turns", 1, 50), ("max_tokens", 1000, 400000), ("tool_output_chars", 200, 100000),
                  ("full_tool_turns", 0, 50), ("render_chars", 500, 50000))
        for name, low, high in checks:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise MemoryRefused(f"working memory: {name} must be a whole number from {low} to {high}")


# ------------------------------------------------------------------ entities

_NUMBERS = r"(\d{1,12}(?:\s*(?:,|and|&)\s*\d{1,12})*)"
ENTITY_PATTERNS = {
    "paths": re.compile(r"(?<![\w.~])((?:~|/)[\w.+\-]*(?:/[\w.+\-]+)+/?)"),
    "keyword_ids": re.compile(r"(\*[A-Z][A-Z0-9_]+:\d+)"),
    "part_ids": re.compile(r"(?i)\bparts?\s*(?:ids?\s*)?[#:]?\s*" + _NUMBERS),
    "material_ids": re.compile(r"(?i)\b(?:mat|material)s?\s*(?:ids?\s*)?[#:]?\s*" + _NUMBERS),
    "job_ids": re.compile(r"(?i)\bjobs?\s*(?:ids?\s*)?[#:\-]?\s*(\d{3,12})\b"),
    "model_ids": re.compile(r"(?i)\bmodel\s*(?:id|version)?\s*[#:]?\s*([A-Z]{0,3}\d{2,8})\b"),
}


def find_entities(text: str) -> dict[str, list[str]]:
    """Paths and IDs named in ``text``, by kind, in order of appearance."""
    found: dict[str, list[str]] = {}
    for kind, pattern in ENTITY_PATTERNS.items():
        values: list[str] = []
        for match in pattern.finditer(text or ""):
            group = match.group(1)
            parts = re.split(r"\s*(?:,|and|&)\s*", group) if kind in ("part_ids", "material_ids") else [group]
            for part in parts:
                part = part.strip().rstrip(".,;:)")
                if part and part not in values:
                    values.append(part)
        if values:
            found[kind] = values
    return found


# ------------------------------------------------------------ working memory

def _add(items: list, value: Any) -> None:
    if value in items:
        items.remove(value)
    items.append(value)
    del items[:-MAX_ITEMS]


@dataclass
class WorkingMemory:
    session_id: str
    active_task: str = ""
    relevant_messages: list[str] = field(default_factory=list)  # pinned user instructions
    user_corrections: list[str] = field(default_factory=list)
    selected_entities: dict[str, list[str]] = field(default_factory=dict)
    retrieved_evidence: list[dict] = field(default_factory=list)  # {ref, excerpt}
    assumptions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    pending_approvals: list[str] = field(default_factory=list)

    def set_task(self, task: str) -> None:
        self.active_task = clean_text(task, "the task", 1000)

    def pin_instruction(self, text: str) -> None:
        """Keep a user instruction for the whole task, even after its turn is dropped."""
        _add(self.relevant_messages, clean_text(text, "the instruction", 1000, required=True))
        self.note_entities(text)

    def add_correction(self, text: str) -> None:
        _add(self.user_corrections, clean_text(text, "the correction", 1000, required=True))
        self.note_entities(text)

    def add_entity(self, kind: str, value: str) -> None:
        if kind not in ENTITY_KINDS:
            raise MemoryRefused(f"unknown entity kind {kind!r}; kinds: {', '.join(ENTITY_KINDS)}")
        _add(self.selected_entities.setdefault(kind, []), clean_text(value, kind, 500, required=True))

    def note_entities(self, text: str) -> dict[str, list[str]]:
        """Add the paths and IDs named in ``text`` (secret-looking text is ignored)."""
        found = find_entities(redact(text or ""))
        for kind, values in found.items():
            for value in values:
                _add(self.selected_entities.setdefault(kind, []), value)
        return found

    def add_evidence(self, ref: str, excerpt: str = "") -> None:
        entry = {"ref": clean_text(ref, "the evidence reference", 500, required=True),
                 "excerpt": cut(clean_text(excerpt, "the evidence excerpt", 20000), 1000)}
        self.retrieved_evidence = [item for item in self.retrieved_evidence if item["ref"] != entry["ref"]]
        _add(self.retrieved_evidence, entry)

    def add_assumption(self, text: str) -> None:
        _add(self.assumptions, clean_text(text, "the assumption", 1000, required=True))

    def add_question(self, text: str) -> None:
        _add(self.open_questions, clean_text(text, "the question", 1000, required=True))

    def resolve_question(self, text: str) -> None:
        self.open_questions = [item for item in self.open_questions if item != text]

    def add_pending_approval(self, text: str) -> None:
        _add(self.pending_approvals, clean_text(text, "the approval", 1000, required=True))

    def resolve_pending_approval(self, text: str) -> None:
        self.pending_approvals = [item for item in self.pending_approvals if item != text]

    def clear_task(self) -> None:
        """The task is finished: forget everything that belonged to it (L2 keeps the summary)."""
        session = self.session_id
        self.__init__(session_id=session)  # type: ignore[misc]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkingMemory":
        known = set(cls.__dataclass_fields__)
        return cls(**{key: copy.deepcopy(value) for key, value in data.items() if key in known})

    def render(self, max_chars: int = 4000) -> str:
        """Text block for the agent instructions; empty when nothing is known yet."""
        lines: list[str] = []
        if self.active_task:
            lines.append(f"Active task: {self.active_task}")
        sections = (("User corrections (follow these)", self.user_corrections),
                    ("User instructions for this task", self.relevant_messages),
                    ("Pending approvals", self.pending_approvals),
                    ("Open questions", self.open_questions),
                    ("Assumptions (not confirmed)", self.assumptions))
        for title, items in sections:
            if items:
                lines.append(f"{title}:")
                lines.extend(f"- {item}" for item in reversed(items))
        entities = [f"{kind}: {', '.join(reversed(values[-10:]))}"
                    for kind, values in self.selected_entities.items() if values]
        if entities:
            lines.append("Selected entities:")
            lines.extend(f"- {item}" for item in entities)
        if self.retrieved_evidence:
            lines.append("Evidence already retrieved:")
            lines.extend(f"- {item['ref']}" for item in reversed(self.retrieved_evidence[-10:]))
        if not lines:
            return ""
        text = "[Working memory of this task]\n" + "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars - 40].rstrip() + "\n- [more items not shown]"
        return text


# ---------------------------------------------------------- message selection

def _is_user_message(item: Any) -> bool:
    return isinstance(item, Mapping) and item.get("role") == "user" and item.get("type", "message") == "message"


def estimate_tokens(items: list) -> int:
    return len(json.dumps(items, ensure_ascii=False, default=str)) // CHARS_PER_TOKEN


def split_turns(history: list) -> list[list]:
    """Turns: each starts at a user message (items before the first one form their own group)."""
    turns: list[list] = []
    for item in history:
        if _is_user_message(item) or not turns:
            turns.append([item])
        else:
            turns[-1].append(item)
    return turns


def _cut_outputs(turn: list, max_chars: int) -> tuple[list, int]:
    cut_count = 0
    result = []
    for item in turn:
        if (isinstance(item, Mapping) and item.get("type") == "function_call_output"
                and isinstance(item.get("output"), str) and len(item["output"]) > max_chars):
            item = dict(item)
            item["output"] = (cut(item["output"], max_chars)
                              + " (older tool output shortened by Champion; call the tool again for the full text)")
            cut_count += 1
        result.append(item)
    return result, cut_count


@dataclass
class Selection:
    items: list
    kept_turns: int
    dropped_turns: list[list]  # oldest first; what L2 can summarise
    cut_outputs: int
    estimated_tokens: int


def select_history(history: list, new_input: list, settings: WorkingSettings | None = None) -> Selection:
    """The earlier items to send with ``new_input`` (see module text). ``history`` is not changed."""
    settings = settings or WorkingSettings()
    turns = split_turns(list(history))
    dropped = turns[:max(0, len(turns) - settings.max_turns)]
    kept = turns[len(dropped):]
    cut_total = 0
    shaped = []
    for index, turn in enumerate(kept):
        if index < len(kept) - settings.full_tool_turns:
            turn, count = _cut_outputs(turn, settings.tool_output_chars)
            cut_total += count
        shaped.append(turn)
    budget = settings.max_tokens - estimate_tokens(list(new_input))
    while shaped and estimate_tokens([item for turn in shaped for item in turn]) > budget:
        if len(shaped) > 1:
            dropped.append(kept[len(kept) - len(shaped)])
            shaped.pop(0)
            continue
        turn, count = _cut_outputs(shaped[0], settings.tool_output_chars)  # last turn still too big
        cut_total += count
        shaped[0] = turn
        if estimate_tokens(turn) > budget:
            dropped.append(kept[-1])
            shaped = []
        break
    items = [item for turn in shaped for item in turn] + list(new_input)
    return Selection(items=items, kept_turns=len(shaped), dropped_turns=dropped, cut_outputs=cut_total,
                     estimated_tokens=estimate_tokens(items))


def make_session_callback(settings: WorkingSettings | None = None,
                          on_dropped: Callable[[list[list]], None] | None = None) -> Callable[[list, list], list]:
    """``RunConfig.session_input_callback`` for M6 (replaces ``runtime.keep_last_turns``)."""
    def callback(history: list, new_input: list) -> list:
        selection = select_history(history, new_input, settings)
        if on_dropped is not None and selection.dropped_turns:
            on_dropped(selection.dropped_turns)
        return selection.items
    return callback
