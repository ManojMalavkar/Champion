"""L2 - conversation summaries (track T8.3).

When a conversation gets long, its completed parts become structured
records instead of raw messages: decisions (with the reason), constraints,
paths set by the user, naming and command rules, architecture choices, open
items and rejected alternatives, plus the sessions they came from.

One JSON file per summary (``SUM-YYYYMMDD-NNN.json``) in the folder the
caller chooses: the selected project's memory folder, else the user's
``~/.champion/memory/summaries/``. Every item has a status (``active``,
``superseded``, ``rejected``, ``done``); only active items are given to the
model. A superseded decision points to the summary item that replaced it.

Statements with an engineering value (a result, a requirement, a limit; see
``separation.py``) are never stored as decisions: they are kept apart in
``deferred`` with their kind until the engineering layers exist.

``summarize`` asks the configured model for the JSON (any async callable in
tests: a fake model), checks it and stores nothing that looks like a secret.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from champion.memory.separation import engineering_layer
from champion.memory.storage import (MemoryRefused, claim_id, clean_text, cut, now_iso, read_json, redact,
                                     refuse_secrets, user_memory_root, write_json)

# kind -> key in the JSON (the user's format: "decisions", "open_items", ...)
KINDS = {
    "decision": "decisions",
    "constraint": "constraints",
    "path": "paths",
    "rule": "rules",
    "architecture": "architecture",
    "open_item": "open_items",
    "rejected_alternative": "rejected_alternatives",
}
STATUSES = ("active", "superseded", "rejected", "done")
SUMMARY_ID = re.compile(r"^SUM-\d{8}-\d{3,}$")
MAX_ITEMS_PER_SUMMARY = 100


def summaries_folder(project_memory: str | os.PathLike | None = None) -> Path:
    """Project memory folder if one is selected, else the user's summaries folder."""
    if project_memory:
        return Path(project_memory) / "summaries"
    return user_memory_root() / "summaries"


@dataclass
class SummaryItem:
    kind: str
    text: str
    reason: str = ""
    status: str = "active"
    superseded_by: str = ""  # "<summary_id>#<index>"

    def check(self) -> "SummaryItem":
        if self.kind not in KINDS:
            raise MemoryRefused(f"unknown summary item kind {self.kind!r}; kinds: {', '.join(KINDS)}")
        if self.status not in STATUSES:
            raise MemoryRefused(f"unknown status {self.status!r}; statuses: {', '.join(STATUSES)}")
        self.text = clean_text(self.text, f"the {self.kind}", 1000, required=True)
        self.reason = clean_text(self.reason, "the reason", 1000)
        return self


@dataclass
class ConversationSummary:
    topic: str
    project_id: str = ""
    items: list[SummaryItem] = field(default_factory=list)
    deferred: list[dict] = field(default_factory=list)  # {text, layer}: engineering statements kept apart
    source_sessions: list[str] = field(default_factory=list)
    created_by: str = "agent"
    created_at: str = field(default_factory=now_iso)
    summary_id: str = ""

    def check(self) -> "ConversationSummary":
        self.topic = clean_text(self.topic, "the topic", 200, required=True)
        self.project_id = clean_text(self.project_id, "the project ID", 100)
        if self.created_by not in ("agent", "user"):
            raise MemoryRefused("created_by is 'agent' or 'user'")
        if len(self.items) > MAX_ITEMS_PER_SUMMARY:
            raise MemoryRefused(f"a summary holds at most {MAX_ITEMS_PER_SUMMARY} items")
        kept = []
        for item in self.items:
            item.check()
            layer = engineering_layer(item.text)
            if layer:
                self.deferred.append({"text": item.text, "layer": layer, "from_kind": item.kind})
            else:
                kept.append(item)
        self.items = kept
        refuse_secrets(self.deferred, "a deferred statement")
        self.source_sessions = [clean_text(s, "a session ID", 200, required=True) for s in self.source_sessions]
        return self

    def to_dict(self) -> dict:
        data: dict[str, Any] = {"summary_id": self.summary_id, "project_id": self.project_id, "topic": self.topic}
        for kind, key in KINDS.items():
            data[key] = [{name: value for name, value in asdict(item).items() if name != "kind"}
                         for item in self.items if item.kind == kind]
        data.update(deferred=self.deferred, source_sessions=self.source_sessions, created_by=self.created_by,
                    created_at=self.created_at)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConversationSummary":
        items = []
        for kind, key in KINDS.items():
            for entry in data.get(key) or []:
                if isinstance(entry, str):
                    entry = {"text": entry}
                if not isinstance(entry, Mapping):
                    raise MemoryRefused(f"{key}: each entry is text or an object with 'text'")
                text = entry.get("text", entry.get(kind, ""))  # {"decision": "..."} is accepted too
                items.append(SummaryItem(kind=kind, text=text, reason=entry.get("reason", ""),
                                         status=entry.get("status", "active"),
                                         superseded_by=entry.get("superseded_by", "")))
        return cls(topic=data.get("topic", ""), project_id=data.get("project_id", "") or "", items=items,
                   deferred=list(data.get("deferred") or []), source_sessions=list(data.get("source_sessions") or []),
                   created_by=data.get("created_by", "agent"), created_at=data.get("created_at") or now_iso(),
                   summary_id=data.get("summary_id", "") or "")


class SummaryStore:
    def __init__(self, folder: str | os.PathLike) -> None:
        self.folder = Path(folder)

    def _path(self, summary_id: str) -> Path:
        if not SUMMARY_ID.match(summary_id or ""):
            raise MemoryRefused(f"{summary_id!r} is not a summary ID (SUM-YYYYMMDD-NNN)")
        return self.folder / f"{summary_id}.json"

    def add(self, summary: ConversationSummary) -> ConversationSummary:
        summary.check()
        if not summary.items and not summary.deferred:
            raise MemoryRefused("the summary has no items")
        summary.summary_id, path = claim_id(self.folder, "SUM", 3)
        try:
            write_json(path, summary.to_dict())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return summary

    def load(self, summary_id: str) -> ConversationSummary:
        path = self._path(summary_id)
        if not path.is_file():
            raise MemoryRefused(f"no summary {summary_id}")
        return ConversationSummary.from_dict(read_json(path))

    def all(self, project_id: str | None = None) -> list[ConversationSummary]:
        result = []
        for file in sorted(self.folder.glob("SUM-*.json")) if self.folder.is_dir() else ():
            if file.stat().st_size == 0:
                continue  # claimed by an add that crashed before writing
            summary = ConversationSummary.from_dict(read_json(file))
            if project_id is None or summary.project_id == project_id:
                result.append(summary)
        return result

    def active_items(self, kind: str | None = None, project_id: str | None = None) -> list[tuple[str, int, SummaryItem]]:
        """(summary ID, index, item) of every active item, oldest summary first."""
        return [(summary.summary_id, index, item)
                for summary in self.all(project_id)
                for index, item in enumerate(summary.items)
                if item.status == "active" and (kind is None or item.kind == kind)]

    def set_status(self, summary_id: str, index: int, status: str, *, superseded_by: str = "") -> SummaryItem:
        if status not in STATUSES:
            raise MemoryRefused(f"unknown status {status!r}; statuses: {', '.join(STATUSES)}")
        summary = self.load(summary_id)
        if not 0 <= index < len(summary.items):
            raise MemoryRefused(f"{summary_id} has no item {index}")
        item = summary.items[index]
        item.status = status
        item.superseded_by = superseded_by if status == "superseded" else ""
        write_json(self._path(summary_id), summary.to_dict())
        return item

    def supersede(self, summary_id: str, index: int, new: ConversationSummary) -> ConversationSummary:
        """Store ``new`` (its first item replaces the old one) and mark the old item superseded."""
        old = self.load(summary_id).items
        if not 0 <= index < len(old):
            raise MemoryRefused(f"{summary_id} has no item {index}")
        stored = self.add(new)
        if not stored.items:
            raise MemoryRefused("the new summary has no item to replace the old one")
        self.set_status(summary_id, index, "superseded", superseded_by=f"{stored.summary_id}#0")
        return stored

    def render_active(self, project_id: str | None = None, max_chars: int = 4000) -> str:
        """Text block with the active items, for the agent instructions (M6)."""
        lines = []
        for summary_id, index, item in self.active_items(project_id=project_id):
            reason = f" (reason: {item.reason})" if item.reason else ""
            lines.append(f"- [{item.kind}] {item.text}{reason} ({summary_id}#{index})")
        if not lines:
            return ""
        text = "[Earlier decisions and open items from summarised conversations]\n" + "\n".join(lines)
        return text if len(text) <= max_chars else text[:max_chars - 40].rstrip() + "\n- [more items not shown]"


# -------------------------------------------------------------- compaction

SUMMARY_INSTRUCTIONS = """You summarise a finished part of a conversation between an engineer and Champion,
a CAE assistant. Return only one JSON object, no other text:
{"topic": "...",
 "decisions": [{"text": "...", "reason": "..."}],
 "constraints": [{"text": "..."}], "paths": [{"text": "..."}], "rules": [{"text": "..."}],
 "architecture": [{"text": "...", "reason": "..."}], "open_items": [{"text": "..."}],
 "rejected_alternatives": [{"text": "...", "reason": "..."}]}
Rules: use only what the conversation says; a decision counts only if the user made or confirmed it;
keep paths exactly as written; leave a list empty when nothing fits; never copy keys, tokens or passwords;
do not record simulation results, requirements or engineering limits as decisions."""


def turns_to_text(turns: list[list], max_chars: int = 60000, output_chars: int = 500) -> str:
    """Plain text of SDK items for the summary request (tool outputs shortened, secrets removed)."""
    lines = []
    for turn in turns:
        for item in turn:
            if not isinstance(item, Mapping):
                continue
            kind = item.get("type", "message")
            if kind == "message":
                content = item.get("content")
                if isinstance(content, list):
                    content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, Mapping))
                lines.append(f"{item.get('role', '?')}: {content}")
            elif kind == "function_call":
                lines.append(f"tool call {item.get('name')}({cut(str(item.get('arguments', '')), 300)})")
            elif kind == "function_call_output":
                lines.append(f"tool result: {cut(str(item.get('output', '')), output_chars)}")
    text = "\n".join(redact(line) for line in lines)
    return text if len(text) <= max_chars else "[start shortened]\n" + text[-max_chars:]


def parse_summary(text: str, *, project_id: str = "", source_sessions: list[str] | None = None,
                  topic: str = "") -> ConversationSummary:
    """The model's answer as a checked summary (a ```json code fence is accepted)."""
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise MemoryRefused("the summary from the model is not valid JSON; nothing was saved") from None
    if not isinstance(data, dict):
        raise MemoryRefused("the summary from the model is not a JSON object; nothing was saved")
    refuse_secrets(data, "the summary from the model")
    data = dict(data, project_id=project_id, source_sessions=list(source_sessions or []), created_by="agent")
    data.setdefault("topic", topic)
    if not data.get("topic"):
        data["topic"] = topic or "conversation"
    for key in ("summary_id", "deferred", "created_at"):
        data.pop(key, None)  # set by Champion, never by the model
    for key in KINDS.values():
        for entry in data.get(key) or []:
            if isinstance(entry, Mapping) and entry.get("status", "active") != "active":
                raise MemoryRefused("the model may not set an item status; nothing was saved")
    return ConversationSummary.from_dict(data).check()


async def summarize(turns: list[list], call_model: Callable[[str, str], Awaitable[str]], *,
                    project_id: str = "", source_session: str = "", topic: str = "") -> ConversationSummary:
    """Ask the model (``call_model(instructions, text)``) for a summary of ``turns``; not stored yet."""
    if not turns:
        raise MemoryRefused("nothing to summarise")
    answer = await call_model(SUMMARY_INSTRUCTIONS, turns_to_text(turns))
    return parse_summary(answer, project_id=project_id, topic=topic,
                         source_sessions=[source_session] if source_session else [])
