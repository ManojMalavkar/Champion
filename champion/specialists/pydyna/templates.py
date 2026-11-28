"""Checked PyDyna starter scripts for the Code sub-agent (T1.2).

The templates are kept as text, never imported or run by Champion: the Code sub-agent reads one
with ``pydyna_template``, changes it for the user's request, checks it with ``check_pydyna_script``
and writes it with the core file tools. The user runs the script (T1.4 adds runs with approval).

Rules for every template (user, 28 Nov 2026):
* offline ``ansys.dyna.core`` ``Deck`` and ``keywords`` API only; no ``DynaMech`` or other
  ``pre`` API that needs a server (``ansys.dyna.core.pre`` is not in ansys-dyna-core 0.12.1);
* ``check_pydyna_script`` gives no error and no warning (tested, ``tests/pydyna/test_pydyna_templates.py``);
* run by hand, the script writes a ``.k`` deck next to itself and nothing else.

Contact, boundary condition, initial velocity, load curve and prescribed motion templates come
with the T1.6 skills.

Immutable (user, 28 Nov 2026): ``Template`` is frozen, ``TEMPLATES`` is a read-only mapping, and
each template records the SHA-256 of its code; ``get`` refuses a template whose code no longer
matches (a change to a template must update its SHA-256, its provenance and be checked again).
The Code sub-agent cannot write into Champion's own package (guard.py), so not into this file.

Provenance and compatibility: ``provenance`` says who wrote the template, from what, and who
decided; ``checked_with`` lists the ansys-dyna-core versions it was checked with (tests:
``check_pydyna_script`` clean, every name in ``api_names`` in the catalog, run by hand writes the
.k deck); ``pydyna_template`` compares them with the installed version.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import MappingProxyType

PROVENANCE = ("Written for Champion T1.2 (28 Nov 2026) from the lookup_api records and keyword classes of the "
              "installed ansys-dyna-core (offline Deck and keywords API); not copied from DynaMech or other "
              "pre / server examples. Template set, API rule and checks decided by the user (28 Nov 2026).")
CHECKED_WITH = ("0.12.1",)  # ansys-dyna-core versions: API check clean and the .k deck written


class TemplateChanged(RuntimeError):
    """A template's code does not match its recorded SHA-256."""


@dataclass(frozen=True)
class Template:
    name: str
    title: str
    description: str
    keywords: tuple[str, ...]
    code: str
    sha256: str  # of code (UTF-8); recorded, checked by get()
    api_names: tuple[str, ...]  # every PyDyna name the code uses (lookup_api names)
    checked_with: tuple[str, ...] = CHECKED_WITH
    provenance: str = PROVENANCE


DECK_SKELETON = Template(
    name="deck_skeleton",
    title="Deck skeleton",
    description=("Empty explicit deck with *TITLE, *CONTROL_TERMINATION, *CONTROL_TIMESTEP, "
                 "*DATABASE_GLSTAT, *DATABASE_MATSUM and *DATABASE_BINARY_D3PLOT; writes a .k file."),
    keywords=("*CONTROL_TERMINATION", "*CONTROL_TIMESTEP", "*DATABASE_GLSTAT", "*DATABASE_MATSUM",
              "*DATABASE_BINARY_D3PLOT"),
    code='''"""PyDyna template: deck skeleton (offline ansys-dyna-core keywords/Deck API, no server).

Builds a deck with the control and output keywords every explicit run needs and writes it
next to this script as a .k file when the user runs the script. Units: mm, t, s, N, MPa.
Change END_TIME, the output intervals and OUTPUT before use.
"""
from pathlib import Path

from ansys.dyna.core import Deck
from ansys.dyna.core import keywords as kwd

OUTPUT = Path(__file__).with_suffix(".k")
END_TIME = 0.01  # s
D3PLOT_INTERVAL = 5.0e-4  # s
ASCII_INTERVAL = 1.0e-5  # s

deck = Deck()
deck.title = "Deck skeleton"
deck.extend([
    kwd.ControlTermination(endtim=END_TIME),
    kwd.ControlTimestep(tssfac=0.9),
    kwd.DatabaseGlstat(dt=ASCII_INTERVAL),
    kwd.DatabaseMatsum(dt=ASCII_INTERVAL),
    kwd.DatabaseBinaryD3Plot(dt=D3PLOT_INTERVAL),
])

OUTPUT.write_text(deck.write(), encoding="utf-8")
print(f"wrote {OUTPUT}")
''',
    sha256="3850ebf65a6b0773cce5bbff22c176a48a2bfad5eed13969a9c18f4706621c71",
    api_names=("Deck", "Deck.title", "Deck.extend", "Deck.write", "ControlTermination", "ControlTimestep",
               "DatabaseGlstat", "DatabaseMatsum", "DatabaseBinaryD3Plot"),
)

MATERIAL_SECTION_PART = Template(
    name="material_section_part",
    title="Material, section and part",
    description=("One steel shell part: *MAT_024 (bilinear), *SECTION_SHELL and *PART with matching ids; "
                 "no nodes or elements; writes a .k file."),
    keywords=("*MAT_024", "*SECTION_SHELL", "*PART"),
    code='''"""PyDyna template: material, section and part (offline ansys-dyna-core keywords/Deck API, no server).

One steel shell part: *MAT_024 (piecewise linear plasticity, bilinear here), *SECTION_SHELL and
*PART, written next to this script as a .k file when the user runs the script.
Units: mm, t, s, N, MPa. Change the values, ids and OUTPUT before use; nodes and elements are
not part of this template.
"""
from pathlib import Path

import pandas as pd
from ansys.dyna.core import Deck
from ansys.dyna.core import keywords as kwd

OUTPUT = Path(__file__).with_suffix(".k")
MID, SECID, PID = 1, 1, 1
THICKNESS = 1.5  # mm

deck = Deck()
deck.title = "Material, section and part"
deck.extend([
    kwd.Mat024(mid=MID, ro=7.85e-9, e=210000.0, pr=0.3, sigy=355.0, etan=1000.0),
    kwd.SectionShell(secid=SECID, elform=2, nip=5, t1=THICKNESS, t2=THICKNESS, t3=THICKNESS, t4=THICKNESS),
    kwd.Part(parts=pd.DataFrame({"heading": ["shell part"], "pid": [PID], "secid": [SECID], "mid": [MID]})),
])

OUTPUT.write_text(deck.write(), encoding="utf-8")
print(f"wrote {OUTPUT}")
''',
    sha256="bd773b80249a441305d3b5640204dbe14c21c2a9f0e439508b67236353af4621",
    api_names=("Deck", "Deck.title", "Deck.extend", "Deck.write", "Mat024", "SectionShell", "Part"),
)

TEMPLATES = MappingProxyType({t.name: t for t in (DECK_SKELETON, MATERIAL_SECTION_PART)})
LATER = ("contact", "boundary condition", "initial velocity", "load curve", "prescribed motion")  # T1.6 skills


def names() -> list[str]:
    return list(TEMPLATES)


def get(name: str) -> Template:
    """The template with this name; KeyError (with the known names) if there is none."""
    key = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key not in TEMPLATES:
        raise KeyError(f"no PyDyna template {name!r}; templates: {', '.join(TEMPLATES)} "
                       f"(not yet: {', '.join(LATER)}; they come with the T1.6 skills)")
    found = TEMPLATES[key]
    if code_sha256(found.code) != found.sha256:
        raise TemplateChanged(f"template {found.name} does not match its recorded SHA-256; it was changed "
                              "without being checked again")
    return found


def code_sha256(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def compatibility(installed: str | None) -> str:
    """How the installed ansys-dyna-core version relates to the versions the templates were checked with."""
    if installed is None:
        return "ansys-dyna-core is not installed: the script cannot be checked"
    if installed in CHECKED_WITH:
        return f"checked with the installed ansys-dyna-core {installed}"
    return (f"checked with ansys-dyna-core {', '.join(CHECKED_WITH)}, installed {installed}: not checked with this "
            "version; check_pydyna_script on the text decides")


def listing() -> list[dict]:
    return [{"name": t.name, "title": t.title, "description": t.description, "keywords": list(t.keywords),
             "checked_with": list(t.checked_with)} for t in TEMPLATES.values()]
