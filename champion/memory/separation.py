"""Keeps memory kinds apart (track T8.4).

A preference, an engineering fact, a requirement and a simulation result have
different authorities and lifecycles, so they are never stored together:

=========================================  ==================
"Show force in kN"                         preference (L3)
"Section force must remain below 320 kN"   engineering fact
"Program target is 300 kN"                 requirement
"Job 784231 produced 318 kN"               simulation result
=========================================  ==================

``engineering_layer`` finds the last three, and engineering changes ("Increase
the gauge from 1.6 mm to 1.8 mm"): a statement with a value and a unit plus a
result, requirement, limit or change word. It is a guard, deliberately
broad: L3 refuses such text, L2 keeps it apart as a deferred engineering
statement (never lost, never used as a decision). The engineering layers that
will hold them with evidence come later (T8.5 onwards).
"""

from __future__ import annotations

import re

PREFERENCE = "preference"
ENGINEERING_FACT = "engineering_fact"
REQUIREMENT = "requirement"
RESULT = "result"
ENGINEERING_CHANGE = "engineering_change"

LAYER_TEXT = {
    ENGINEERING_FACT: "an engineering fact (it needs evidence; engineering knowledge comes in a later step)",
    REQUIREMENT: "a requirement (it needs a revision and approval; requirements come in a later step)",
    RESULT: "a simulation result (it needs a JobID and a source; results come in a later step)",
    ENGINEERING_CHANGE: ("an engineering change (it needs the model, the changed entities and the outcome; "
                         "engineering decisions come in a later step)"),
}

UNITS = (r"kN|N|MN|mm|cm|m|ms|s|g|kg|t|tonne|MPa|GPa|kPa|Pa|J|kJ|Nm|kNm|%|deg|°|km/h|kph|mph|m/s|m/s2|"
         r"m/s²|G|Hz|W|kW")
VALUE_UNIT = re.compile(rf"(?<![\w.])[-+]?\d+(?:[.,]\d+)?\s*(?:{UNITS})(?![\w/])")
JOB = re.compile(r"(?i)\b(?:job|run|simulation)\s*(?:id\s*)?[#:\-]?\s*\d{3,}")
RESULT_WORDS = re.compile(r"(?i)\b(produced|resulted|results? (?:is|was|of)|gave|reached|measured|peaked|"
                          r"showed|shows|obtained|computed|predicted|was \d)")
REQUIREMENT_WORDS = re.compile(r"(?i)\b(target|requirement|required|regulation|regulatory|rating|euro ?ncap|"
                               r"iihs|fmvss|ece|un r\d+|homologation|specification|spec)\b")
LIMIT_WORDS = re.compile(r"(?i)(\bmust\b|\bshall\b|\bshould (?:stay|remain|be)\b|\bremain|\bbelow\b|\babove\b|"
                         r"\bless than\b|\bmore than\b|\bgreater than\b|\bnot exceed|\bat most\b|\bat least\b|"
                         r"\bmax(?:imum)?\b|\bmin(?:imum)?\b|\blimit\b|<=|>=|≤|≥|<|>)")
CHANGE_WORDS = re.compile(r"(?i)\b(increase|decrease|reduce|raise|lower|change|set|modify|thicken|thin|"
                          r"gauge|thickness)\w*\b.*\b(to|by)\s+[-+]?\d")


def engineering_layer(text: str) -> str | None:
    """``result``, ``requirement``, ``engineering_fact`` or ``engineering_change`` for a CAE value, else None."""
    text = text or ""
    if not VALUE_UNIT.search(text):
        return None
    if JOB.search(text) or RESULT_WORDS.search(text):
        return RESULT
    if REQUIREMENT_WORDS.search(text):
        return REQUIREMENT
    if LIMIT_WORDS.search(text):
        return ENGINEERING_FACT
    if CHANGE_WORDS.search(text):
        return ENGINEERING_CHANGE
    return None


def classify(text: str) -> str:
    """The memory kind of a statement (preference when it states no CAE value)."""
    return engineering_layer(text) or PREFERENCE
