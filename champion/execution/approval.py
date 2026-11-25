"""Approval of deletes (step 4a, PLAN 4a.6).

Writes at a write level run without a prompt (they go to the change list).
Deletes (``rm``) - and process control in a later step - ask the user every
time. Step 4d: a higher-risk ``file_edit`` (many lines deleted, a large part
of a file changed), a ``file_write`` and a large change set ask once too
(class ``edit``, the change in ``note``); a ``run_check`` asks every time
(class ``check``). The request shows exactly what will run: the resolved program, the
argument list, the class, the folder it runs in, the affected paths and the
level. Default answer is no.

The *fingerprint* is a SHA-256 of all of that. It is taken when the user is
asked and taken again from a fresh policy check just before the command
starts; if anything differs (another program found, other paths after
wildcard expansion, the level changed), the command does not run.

Without an approver (``champion chat -q``, tests) every approval request is
refused: nothing that needs a yes runs unattended.

M6: with memory on, the request is stored as an open approval (runtime state
and working memory) while the prompt is shown, and the answer after it. The
memory never answers: only the approver (the user at the prompt) does.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass

from champion.core.context import ChampionContext
from champion.execution.command_policy import CommandCheck
from champion.policy.scope import PolicyDenied

WARNINGS = {
    "delete": "Deleted files cannot be restored by Champion (/undo does not cover deletes).",
    "process_control": "This signals or stops processes.",
    "check": "This runs project code (for example tests) with your rights; time, CPU and memory are limited.",
    "remote": "This acts on another system (for example a job queue); Champion cannot undo it.",  # M2
}


@dataclass(frozen=True)
class ApprovalRequest:
    tool: str
    executable: str
    argv: tuple[str, ...]
    command_class: str
    cwd: str
    level: str
    paths: tuple[str, ...]
    note: str = ""  # what changes, for edits (step 4d)

    @classmethod
    def for_command(cls, tool: str, check: CommandCheck, executable: str, level: str) -> "ApprovalRequest":
        return cls(tool, executable, tuple(check.argv), check.command_class, str(check.cwd), level,
                   tuple(check.affected))

    @property
    def fingerprint(self) -> str:
        data = json.dumps([self.tool, self.executable, list(self.argv), self.command_class, self.cwd, self.level,
                           list(self.paths), self.note], separators=(",", ":"))
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def describe(self) -> str:
        lines = [f"Approval needed ({self.command_class}) for {self.tool}:",
                 f"  command : {shlex.join(self.argv)}",
                 f"  program : {self.executable}",
                 f"  folder  : {self.cwd}",
                 f"  level   : {self.level}"]
        shown = list(self.paths[:20])
        lines += [f"  affects : {p}" for p in shown]
        if len(self.paths) > len(shown):
            lines.append(f"  ... and {len(self.paths) - len(shown)} more path(s)")
        if self.note:
            lines.append(f"  change  : {self.note}")
        if self.command_class in WARNINGS:
            lines.append(f"  warning : {WARNINGS[self.command_class]}")
        return "\n".join(lines)


def ask(context: ChampionContext, request: ApprovalRequest) -> None:
    """Return if the user said yes; raise ``PolicyDenied`` otherwise (nothing has run)."""
    if not context.config.get("execution.require_approval"):
        raise PolicyDenied(f"{request.command_class} commands need approval and execution.require_approval is "
                           "false, so they are refused; nothing was run")
    if context.approver is None:
        raise PolicyDenied(f"this {request.command_class} command needs the user's approval, which is not possible "
                           "here (non-interactive mode); nothing was run")
    declined = getattr(context, "declined", None)
    if declined is not None and request.fingerprint in declined:
        # Local 4d check: after a no, the orchestrator called the specialist again and the user was
        # asked a second time. The same request is not asked again in the same turn.
        raise PolicyDenied("the user already declined this exact request in this turn; it is not asked again "
                           "and nothing was run or changed. Do not retry it. Tell the user it was not done "
                           "because they declined")
    memory = getattr(context, "memory", None)
    label = f"{request.tool} ({request.command_class}): {shlex.join(request.argv)[:300]}"
    approval_id = None
    if memory is not None:
        approval_id = memory.approval_asked(tool=request.tool, label=label, summary=request.describe(),
                                            fingerprint=request.fingerprint)
    approved = False
    try:
        approved = bool(context.approver(request))
    except (EOFError, KeyboardInterrupt):
        approved = False
    finally:
        if memory is not None:
            memory.approval_answered(approval_id, approved, label)
    if not approved:
        if declined is not None:
            declined.add(request.fingerprint)
        # Local 4d check: "ask the user what to do" led to "Would you like to approve?" after a no.
        raise PolicyDenied("the user did not approve this command (answered no at the approval prompt); "
                           "nothing was run or changed. Do not retry it. Tell the user it was not done because "
                           "they declined; do not ask them to approve it in your answer: approval is only given "
                           "at the prompt, which appears again if they ask for it again")
