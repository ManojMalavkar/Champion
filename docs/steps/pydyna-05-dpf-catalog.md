# T1.3a - PyDPF API catalog, DPF operators and PyDPF script check

Track T1 (PyDyna), branch `track/pydyna`, 24 Nov 2026. Roadmap PD-09 with PD-04 / PD-05. No M1 and no core change.

## What it does
The DPF-Post sub-agent gets the same exact API help the Code sub-agent got in T1.2a / T1.2b, for the installed ansys-dpf-post (0.12.0) and ansys-dpf-core (0.16.3):

| Tool | Sub-agent | Gives |
| --- | --- | --- |
| `lookup_dpf_api` | DPF-Post | Record of a DPF operator (input and output pins with types, optional or not, documents; internal name; LS-DYNA or not; a usage line) or of a class, method, property or function (parameters, defaults, return type, import line), with package version and source file. Unknown names come back with close names. |
| `find_dpf_operators` | DPF-Post | Operators whose name or description has the query words, best first; `lsdyna=true` for d3plot / binout results, optional `category`. |
| `check_dpf_script` | DPF-Post | Static check of a script (a `.py` file in the working folder or code text), nothing run, no DPF server: unknown imports, names, operators, methods, attributes, arguments, operator pins (`op.inputs.<pin>`, `op.outputs.<pin>`), read-only properties; each issue with line and close names. |

All three have right `none` (like the T1.2 tools); a path goes through `check_path` with right `read`.

## Files
* `champion/specialists/pydyna/dpf_catalog.py` (new): `DpfCatalog` = a post `Catalog` and a core `_CoreCatalog` that are peers. `_CoreCatalog` also reads the generated operators (`operators/<category>/<name>.py`: `_spec()` pins and description, internal name from `super().__init__(name=...)`, `InputsX` / `OutputsX` classes). `find_operators` is an exact word match (3 points for a name word, 1 for a description or pin word, LS-DYNA operators first when asked). `dpf_catalog_for()` builds it once (4.9 s on serverless).
* `champion/specialists/pydyna/api_catalog.py`: `Catalog` takes `package`, `distribution`, `search_tool` and `peers` (defaults keep the PyDyna behaviour). New, for both packages: re-exports followed through every module (`from x import name`, also in `if` / `try` blocks and relative); classes that add names at run time (`__getattr__`, `__setattr__`, `setattr(self, ...)`, `__dict__.update`) and modules with `import *` or `__getattr__` are marked incomplete, so no false error; class attributes inside `try:`; `import a.b.c` loads the file `a/b/c.py` even when `a.b` also imports a name `c`; return types of properties, methods and functions (`member_type`, `function_type`).
* `champion/specialists/pydyna/api_check.py`: package-neutral (messages from `catalog.label()`, note from `catalog.package`); follows the class a property, method or function returns (`sim.displacement().max().plot()`); names bound inside a function no longer change the module's names.
* `champion/specialists/pydyna/tools.py`, `__init__.py`: the 3 tools, policy rules, DPF-Post sub-agent instructions (find the operator, look it up, check every script before giving it).
* `tests/pydyna/test_pydyna_dpf_catalog.py` (new, 17 tests), `tests/pydyna/test_pydyna_tools.py` (tool lists).

## Why it is safe to trust
* Nothing is imported: the fake packages in the tests stop at import (`SystemExit`), so a test fails if the catalog or the check runs package code.
* Official code, no false errors: the 148 example and test files of pydpf-post v0.12.0 and pydpf-core v0.16.3 (40,059 lines; 449 imports, 7718 calls, 2992 attributes checked) give 0 issues. The first runs gave 56 errors and 15 warnings; each was a gap in the catalog and is fixed (see the track plan findings).
* Wrong scripts fail as the check says: real-package tests run each script without a server; `part_internal_enrgy` and `load_simulaton` raise AttributeError, `data_source=` TypeError, `from ansys.dpf.post import Simulaton` ImportError, and the correct lines run.
* Complete means complete: for every class the catalog calls complete (exported classes and every 10th operator with its inputs / outputs), every public runtime name is in the catalog. On serverless: 1602 complete classes, 0 missing names.
* PyDyna unchanged: PyDyna stats are the same (3178 / 3171 / 71 / 388 / 64, 0 skipped) and all PyDyna tests pass.

## Limits
* Unknown names on an operator object (`op.anything`) are not reported: `Operator` adds sub-results at run time (`X()`, `Y()`, `Z()`). Its pins are checked.
* Operators with repeated pins (`any_input1`, `any_input2` ...) take more at run time; their pins are not reported.
* `post.load_simulation(...)` returns `SimulationType` (the class depends on the file), so calls on its result are not checked; with a concrete class (`post.TransientMechanicalSimulation(...)`) they are.
* Static check only: values, units, result availability in the file and the DPF server are not checked.

## Tests
Serverless (ansys-dyna-core 0.12.1, ansys-dpf-post 0.12.0, ansys-dpf-core 0.16.3): `tests/pydyna` 127 passed, 6 skipped (knowledge data and chromadb, as before); full suite 476 passed, 6 skipped (459 + 17).

On Linux in the Champion venv (expected): `tests/pydyna` 133 passed, 0 skipped; full suite 482 passed.

## Verify on Linux
```
source ~/.ansa_meta_copilot/.venv/bin/activate
cd /nobackup/people/t0018k6/script/Github_repo/LLM/Champion-pydyna
git pull
python -m pytest -q tests/pydyna
python -m pytest -q
python - <<'EOF'
from champion.specialists.pydyna import dpf_catalog as dc, api_check as ck
c = dc.dpf_catalog_for()
print(c.stats())
r = c.lookup("part_internal_energy")
print(r["internal_name"], r["lsdyna"], [p["name"] for p in r["inputs"]])
print(r["usage"])
print([h["operator"] for h in c.find_operators("internal energy part", lsdyna=True, n_results=3)])
print([h["operator"] for h in c.find_operators("contact force", lsdyna=True, n_results=3)])
m = c.lookup("TransientMechanicalSimulation.displacement")
print(m["kind"], m.get("class"), m["package"], m["version"], m.get("signature"))
script = """from ansys.dpf import core as dpf
from ansys.dpf import post
from ansys.dpf.core import operators as ops
ds = dpf.DataSources()
ds.set_result_file_path("binout", "binout")
op = ops.result.part_internal_energy(data_source=ds)
op.inputs.entity_scope.connect([1])
fc = op.outputs.fields_container()
sim = post.TransientMechanicalSimulation("d3plot")
sim.displacment()
"""
for i in ck.check_source(c, script)["issues"]:
    print(i["line"], i["code"], i.get("close_names", [])[:2])
EOF
```

Expected output (serverless, 24 Nov 2026):
```
{'post_version': '0.12.0', 'core_version': '0.16.3', 'post_classes': 91, 'post_functions': 6, 'core_classes': 2693, 'core_functions': 201, 'operators': 848, 'lsdyna_operators': 40, 'skipped_files': 0}
M_IE specific ['streams_container', 'data_sources', 'entity_scoping', 'unit_system']
op = ops.result.part_internal_energy(data_sources=...); op.outputs.fields_container()
['ops.result.part_internal_energy', 'ops.result.part_eroded_internal_energy', 'ops.result.global_internal_energy']
['ops.result.interface_contact_force', 'ops.result.interface_resultant_contact_force', 'ops.result.interface_contact_area']
method ansys.dpf.post.transient_mechanical_simulation.TransientMechanicalSimulation ansys-dpf-post 0.12.0 displacement(node_ids: ... , skin: Union[bool, List[int]] = False) -> DataFrame
6 unknown-argument ['data_sources']
7 unknown-member ['entity_scoping']
10 unknown-member ['displacement']
```
(The `displacement(...)` signature line is shortened here; it lists 12 parameters.)
