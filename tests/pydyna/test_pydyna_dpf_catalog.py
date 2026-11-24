"""T1.3a: PyDPF API catalog (dpf_catalog.py), DPF operators, the script check for PyDPF and the
lookup_dpf_api / find_dpf_operators / check_dpf_script tools.

The tests build a small fake ansys-dpf-post and ansys-dpf-core in tmp_path (their modules stop
at import, so a test fails if the catalog or the check ever imports one): DataSources, Operator,
Input / Output, a dynamic Results class, two generated-like operators (one for LS-DYNA, one
with repeated pins), an examples module with ``import *``, a post Simulation with a base in
core and annotated return types. The last tests use the real installed packages (skipped
where they are not installed).
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import json
import textwrap
from pathlib import Path

import pytest
from agents import RunContextWrapper

from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.policy.tool_policy import rule_for
from champion.specialists.pydyna import api_catalog as ac
from champion.specialists.pydyna import api_check as ck
from champion.specialists.pydyna import dpf_catalog as dc
from champion.specialists.pydyna import tools as pt
from champion.storage.audit import AuditLog
from champion.tools.guard import ToolError, run_guarded

STOP = 'raise SystemExit("package code ran")\n'

OPERATOR = '''
from ansys.dpf.core.dpf_operator import Operator
from ansys.dpf.core.inputs import Input, _Inputs
from ansys.dpf.core.outputs import Output, _Outputs
from ansys.dpf.core.operators.specification import PinSpecification, Specification


class part_internal_energy(Operator):
    """Read Part Internal Energy (LSDyna) by calling the readers defined by the datasources."""

    def __init__(self, data_sources=None, entity_scoping=None, config=None, server=None):
        super().__init__(name="M_IE", config=config, server=server,
                         inputs_type=InputsPartInternalEnergy, outputs_type=OutputsPartInternalEnergy)

    @staticmethod
    def _spec() -> Specification:
        description = r"""Read Part Internal Energy (LSDyna) by calling the readers defined by the
datasources.
"""
        spec = Specification(
            description=description,
            map_input_pin_spec={
                4: PinSpecification(name="data_sources", type_names=["data_sources"], optional=False,
                                    document=r"""result file path container"""),
                6: PinSpecification(name="entity_scoping", type_names=["scoping"], optional=True,
                                    document=r"""entity (part for matsum)"""),
            },
            map_output_pin_spec={
                0: PinSpecification(name="fields_container", type_names=["fields_container"], optional=False,
                                    document=r""""""),
            },
        )
        return spec

    @property
    def inputs(self) -> InputsPartInternalEnergy:
        return self._inputs

    @property
    def outputs(self) -> OutputsPartInternalEnergy:
        return self._outputs


class InputsPartInternalEnergy(_Inputs):
    def __init__(self, op):
        super().__init__(part_internal_energy._spec().inputs, op)

    @property
    def data_sources(self) -> Input[DataSources]:
        return self._data_sources

    @property
    def entity_scoping(self) -> Input:
        return self._entity_scoping


class OutputsPartInternalEnergy(_Outputs):
    @property
    def fields_container(self) -> Output:
        return self._fields_container
'''

MERGE = '''
from ansys.dpf.core.dpf_operator import Operator
from ansys.dpf.core.inputs import Input, _Inputs
from ansys.dpf.core.outputs import Output, _Outputs


class merge_fields(Operator):
    """Assembles a set of fields into a unique one."""

    def __init__(self, fields1=None, fields2=None, config=None, server=None):
        super().__init__(name="merge::field", config=config, server=server,
                         inputs_type=InputsMergeFields, outputs_type=OutputsMergeFields)

    @staticmethod
    def _spec():
        description = r"""Assembles a set of fields into a unique one."""
        spec = Specification(
            description=description,
            map_input_pin_spec={
                0: PinSpecification(name="fields", type_names=["field"], optional=False, document=r"""a field"""),
                1: PinSpecification(name="fields", type_names=["field"], optional=False, document=r"""a field"""),
            },
            map_output_pin_spec={0: PinSpecification(name="merged_field", type_names=["field"], optional=False,
                                                      document=r"""""")},
        )
        return spec

    @property
    def inputs(self) -> InputsMergeFields:
        return self._inputs


class InputsMergeFields(_Inputs):
    @property
    def fields1(self) -> Input:
        return self._fields1

    @property
    def fields2(self) -> Input:
        return self._fields2


class OutputsMergeFields(_Outputs):
    @property
    def merged_field(self) -> Output:
        return self._merged_field
'''

CORE = {
    "__init__.py": '''
from ansys.dpf.core.data_sources import DataSources
from ansys.dpf.core.dpf_operator import Operator
from ansys.dpf.core.model import Model
from ansys.dpf.core.unit_system import unit_systems
from ansys.dpf.core import operators
''',
    "common.py": "locations = None\n",
    "data_sources.py": '''
class DataSources:
    """Result files."""

    def __init__(self, result_path=None, server=None):
        self._path = result_path

    def set_result_file_path(self, filepath, key: str = "") -> None:
        """Set the main result file."""
''',
    "dpf_operator.py": '''
class Operator:
    """A DPF operator."""

    def __init__(self, name, config=None, server=None, inputs_type=None, outputs_type=None):
        self.name = name

    def eval(self, pin=None):
        """Evaluate."""
''',
    "inputs.py": '''
class Input:
    def connect(self, inpt):
        """Connect a value to the pin."""


class _Inputs:
    def __init__(self, spec, op):
        for name in spec:
            setattr(self, name, Input())

    def connect(self, inpt):
        """Connect the outputs of another operator."""
''',
    "outputs.py": '''
class Output:
    def __call__(self):
        """The output data."""

    def get_data(self):
        """The output data."""


class _Outputs:
    def __init__(self, spec, op):
        for name in spec:
            setattr(self, name, Output())
''',
    "support.py": '''
class Support:
    """A support."""

    def units(self) -> str:
        """Units."""
''',
    "model.py": '''
class Results:
    """Results of the result file, added at run time."""

    def __init__(self, names):
        for name in names:
            setattr(self.__class__, name, property(lambda self: None))


class Model:
    """A result file."""

    def __init__(self, data_sources=None, server=None):
        self._ds = data_sources

    @property
    def results(self) -> Results:
        """The results."""
''',
    "unit_system.py": '''
class UnitSystem:
    def __init__(self, name, ID=None):
        self.name = name


class unit_systems:
    """Unit systems."""

    try:
        solver_mks = UnitSystem("solver_mks", ID=11)
    except ImportError:
        pass
''',
    "examples/__init__.py": "from .downloads import *\n",
    "examples/downloads.py": "def download_d3plot_beam():\n    return 'd3plot'\n",
    "operators/__init__.py": "from . import result\nfrom . import utility\n",
    "operators/specification.py": "class PinSpecification:\n    pass\n\n\nclass Specification:\n    pass\n",
    "operators/result/__init__.py": "from .part_internal_energy import part_internal_energy\n",
    "operators/result/part_internal_energy.py": OPERATOR,
    "operators/utility/__init__.py": "from .merge_fields import merge_fields\n",
    "operators/utility/merge_fields.py": MERGE,
}

POST = {
    "__init__.py": '''
from ansys.dpf.core.common import locations
from ansys.dpf.post.post_utility import load_simulation
from ansys.dpf.post.dataframe import DataFrame
from ansys.dpf.post.simulation import TransientMechanicalSimulation
''',
    "dataframe.py": '''
class DataFrame:
    """A table of results."""

    def plot(self, **kwargs) -> None:
        """Plot."""

    def max(self, axis=None) -> DataFrame:
        """Maximum."""
''',
    "simulation.py": '''
from ansys.dpf.core.support import Support
from ansys.dpf.post.dataframe import DataFrame


class Simulation(Support):
    """A simulation."""

    def __init__(self, result_file):
        self._file = result_file

    def displacement(self, set_ids=None, norm: bool = False) -> DataFrame:
        """Displacement."""


class TransientMechanicalSimulation(Simulation):
    """A transient simulation."""
''',
    "post_utility.py": '''
from ansys.dpf.post.simulation import Simulation


def load_simulation(data_sources, simulation_type=None) -> Simulation:
    """Load a simulation."""
''',
}


def write(base: Path, files: dict) -> Path:
    for name, text in files.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text) + STOP)
    return base


@pytest.fixture
def catalog(tmp_path):
    return dc.DpfCatalog(write(tmp_path / "post", POST), "9.1", write(tmp_path / "core", CORE), "8.2")


HEAD = ("from ansys.dpf import core as dpf\nfrom ansys.dpf import post\n"
        "from ansys.dpf.core import operators as ops\n")


def check(catalog, body: str, head: str = HEAD) -> dict:
    return ck.check_source(catalog, head + textwrap.dedent(body).lstrip("\n"))


def codes(result: dict) -> list[tuple[int, str, str]]:
    return [(i["line"], i["severity"], i["code"]) for i in result["issues"]]


# ---------------------------------------------------------------- catalog and lookups

def test_operator_records_and_stats(catalog):
    stats = catalog.stats()
    assert stats["operators"] == 2 and stats["lsdyna_operators"] == 1 and stats["skipped_files"] == 0
    assert (stats["post_version"], stats["core_version"]) == ("9.1", "8.2")
    record = catalog.lookup("part_internal_energy")
    assert record["kind"] == "operator" and record["internal_name"] == "M_IE" and record["lsdyna"] == "specific"
    assert record["category"] == "result" and record["package"] == "ansys-dpf-core" and record["version"] == "8.2"
    assert [(p["pin"], p["name"], p["optional"]) for p in record["inputs"]] == [
        (4, "data_sources", False), (6, "entity_scoping", True)]
    assert record["outputs"][0]["name"] == "fields_container"
    assert record["usage"] == "op = ops.result.part_internal_energy(data_sources=...); op.outputs.fields_container()"
    assert record["source_path"] == "ansys/dpf/core/operators/result/part_internal_energy.py"
    for name in ("ops.result.part_internal_energy", "dpf.operators.result.part_internal_energy", "M_IE",
                 "result.part_internal_energy", "ansys.dpf.core.operators.result.part_internal_energy"):
        assert catalog.lookup(name)["name"] == "part_internal_energy"
    assert catalog.lookup("merge_fields")["lsdyna"] is None


def test_class_and_function_lookups_through_both_packages(catalog):
    assert catalog.lookup("post.load_simulation")["qualified_name"] == "ansys.dpf.post.post_utility.load_simulation"
    assert catalog.lookup("dpf.DataSources.set_result_file_path")["kind"] == "method"
    assert catalog.lookup("DataFrame")["import"] == "from ansys.dpf.post import DataFrame"
    assert catalog.lookup("Model")["package"] == "ansys-dpf-core"
    inherited = catalog.lookup("TransientMechanicalSimulation.displacement")
    assert inherited["kind"] == "method" and inherited["defined_in"] == "ansys.dpf.post.simulation.Simulation"
    with pytest.raises(ac.CatalogError, match="Close names: .*load_simulation"):
        catalog.lookup("post.load_simulaton")
    with pytest.raises(ac.CatalogError, match="part_internal_energy"):
        catalog.lookup("part_internal_enrgy")


def test_find_operators_by_words_lsdyna_and_category(catalog):
    hits = catalog.find_operators("internal energy part")
    assert hits[0]["operator"] == "ops.result.part_internal_energy" and hits[0]["lsdyna"] == "specific"
    assert [h["operator"] for h in catalog.find_operators("field", lsdyna=True)] == []
    assert [h["operator"] for h in catalog.find_operators("", lsdyna=True)] == ["ops.result.part_internal_energy"]
    assert [h["operator"] for h in catalog.find_operators("fields", category="utility")] == ["ops.utility.merge_fields"]
    with pytest.raises(ac.CatalogError, match="unknown category"):
        catalog.find_operators("energy", category="nothing")
    with pytest.raises(ac.CatalogError, match="empty"):
        catalog.find_operators("  ")


# ---------------------------------------------------------------- the script check

def test_correct_script_has_no_issues_and_nothing_is_imported(catalog):
    result = check(catalog, """
        ds = dpf.DataSources()
        ds.set_result_file_path("binout", "binout")
        op = ops.result.part_internal_energy(data_sources=ds)
        op.inputs.entity_scoping.connect([1, 2])
        op.inputs.data_sources.connect(ds)
        fc = op.outputs.fields_container()
        fc2 = op.outputs.fields_container.get_data()
        op.eval()
        merge = dpf.operators.utility.merge_fields(fields1=fc, fields2=fc)
        merge.inputs.fields3.connect(fc)
        model = dpf.Model(ds)
        energy = model.results.global_internal_energy()
        sim = post.load_simulation("d3plot")
        sim.displacement(set_ids=[1]).max(axis=0).plot()
        sim.units()
        sim2 = post.TransientMechanicalSimulation("d3plot")
        sim2.displacement(norm=True)
        mks = dpf.unit_systems.solver_mks
        from ansys.dpf.core import examples
        path = examples.download_d3plot_beam()
        import ansys.dpf.core.operators.result.part_internal_energy
        from ansys.dpf.core.operators.result.part_internal_energy import InputsPartInternalEnergy
    """)
    assert result["issues"] == [] and result["ok"]
    assert result["package"] == "ansys-dpf-post + ansys-dpf-core" and result["version"] == "9.1 + 8.2"
    assert "ansys.dpf" in result["note"]


def test_operators_their_arguments_and_pins_are_checked(catalog):
    result = check(catalog, """
        ds = dpf.DataSources()
        bad = ops.result.part_internal_enrgy(data_sources=ds)
        op = ops.result.part_internal_energy(data_source=ds)
        op.inputs.entity_scope.connect(ds)
        op.outputs.fields_containers()
        op.inputs.data_sources.conect(ds)
    """)
    assert codes(result) == [(5, "error", "unknown-name"), (6, "error", "unknown-argument"),
                             (7, "error", "unknown-member"), (8, "error", "unknown-member"),
                             (9, "error", "unknown-member")]
    close = [i.get("close_names", [])[:1] for i in result["issues"]]
    assert close == [["part_internal_energy"], ["data_sources"], ["entity_scoping"], ["fields_container"],
                     ["connect"]]
    assert "ansys-dpf-core 8.2" in result["issues"][0]["message"]


def test_post_names_and_returned_classes_are_followed(catalog):
    result = check(catalog, """
        sim = post.load_simulaton("d3plot")
        sim = post.load_simulation("d3plot")
        df = sim.displacment()
        df = sim.displacement(set_id=1)
        df.max().plt()
        dpf.DataSources().set_result_file_pth("x")
        from ansys.dpf.post import Simulaton
    """)
    assert codes(result) == [(4, "error", "unknown-name"), (6, "error", "unknown-member"),
                             (7, "error", "unknown-argument"), (8, "error", "unknown-member"),
                             (9, "error", "unknown-member"), (10, "error", "unknown-import")]
    assert "ansys-dpf-post 9.1" in result["issues"][0]["message"]


def test_imports_in_a_function_stay_in_the_function(catalog):
    result = check(catalog, """
        def helper():
            from ansys.dpf.core import Model as dpf
            return dpf
        dpf.DataSources().set_result_file_pth("x")
    """, head="from ansys.dpf import core as dpf\n")
    assert codes(result) == [(5, "error", "unknown-member")]


def test_other_ansys_dpf_packages_and_other_libraries_are_not_checked(catalog):
    result = check(catalog, """
        from ansys.dpf.gate import anything
        import numpy
        numpy.zeros(3)
    """)
    assert result["issues"] == []
    assert "does not import ansys.dpf" in ck.check_source(catalog, "import numpy\n")["note"]


# ---------------------------------------------------------------- the tools

@pytest.fixture
def setup(tmp_path):
    (tmp_path / "config").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    config = load_config(tmp_path / "config" / "config.yaml", env={}, cwd=tmp_path)
    return ChampionContext.from_config(config, level="1.1", audit=AuditLog()), work


def run(context, name, work):
    return asyncio.run(run_guarded(RunContextWrapper(context=context), name, {}, work))


def test_tools_check_a_file_or_code_look_up_and_find(setup, catalog, tmp_path):
    context, work = setup
    (work / "post_energy.py").write_text(HEAD + "ops.result.part_internal_enrgy()\n")
    by_path = json.loads(run(context, "check_dpf_script", lambda: pt.script_check(context, catalog, "post_energy.py")))
    assert by_path["script"] == str(work / "post_energy.py") and codes(by_path) == [(4, "error", "unknown-name")]
    outside = tmp_path / "outside.py"
    outside.write_text(HEAD)
    assert run(context, "check_dpf_script", lambda: pt.script_check(context, catalog, str(outside))).startswith("BLOCKED:")
    record = json.loads(run(context, "lookup_dpf_api", lambda: pt.api_record(catalog, "M_IE")))
    assert record["name"] == "part_internal_energy"
    assert "Close names" in run(context, "lookup_dpf_api", lambda: pt.api_record(catalog, "Modell"))
    found = json.loads(run(context, "find_dpf_operators", lambda: pt.dpf_operators(catalog, "energy", True)))
    assert found["operators"][0]["operator"] == "ops.result.part_internal_energy" and found["version"] == "8.2"
    with pytest.raises(ToolError, match="n_results"):
        pt.dpf_operators(catalog, "energy", n_results=31)
    for name in ("lookup_dpf_api", "find_dpf_operators", "check_dpf_script"):
        assert rule_for(name).right == "none"
    assert [r["tool"] for r in context.audit.recent()] == ["check_dpf_script", "check_dpf_script", "lookup_dpf_api",
                                                           "lookup_dpf_api", "find_dpf_operators"]


# ---------------------------------------------------------------- real installed packages

REAL = (importlib.util.find_spec("ansys") is not None and importlib.util.find_spec("ansys.dpf") is not None
        and importlib.util.find_spec("ansys.dpf.post") is not None)
real_only = pytest.mark.skipif(not REAL, reason="ansys-dpf-post is not installed")


@pytest.fixture(scope="module")
def real():
    return dc.dpf_catalog_for()


@real_only
def test_real_packages_catalog_and_lsdyna_operators(real):
    stats = real.stats()
    assert stats["skipped_files"] == 0 and stats["operators"] > 500 and stats["lsdyna_operators"] >= 20
    for name in ("part_internal_energy", "global_kinetic_energy", "interface_resultant_contact_force"):
        record = real.lookup(name)
        assert record["lsdyna"] == "specific" and any(p["name"] == "data_sources" for p in record["inputs"])
    names = [h["operator"] for h in real.find_operators("internal energy part", lsdyna=True)]
    assert names[0] == "ops.result.part_internal_energy"
    assert real.lookup("load_simulation")["kind"] == "function"


# (script body, issue codes of the check, what running it does without a DPF server)
REAL_CASES = [
    ("ops.result.part_internal_enrgy\n", ["unknown-name"], "AttributeError"),
    ("ops.result.part_internal_energy(data_source=1)\n", ["unknown-argument"], "TypeError"),
    ("post.load_simulaton\n", ["unknown-name"], "AttributeError"),
    ("dpf.DataSources.set_result_file_pth\n", ["unknown-member"], "AttributeError"),
    ("from ansys.dpf.post import Simulaton\n", ["unknown-import"], "ImportError"),
    ("x = ops.result.part_internal_energy\ny = post.load_simulation\nz = dpf.DataSources.set_result_file_path\n",
     [], "ran"),
]


@real_only
@pytest.mark.parametrize("body, expected, outcome", REAL_CASES)
def test_real_packages_check_agrees_with_running_the_script(real, body, expected, outcome):
    script = HEAD + body
    assert [i["code"] for i in ck.check_source(real, script)["issues"]] == expected
    try:
        exec(compile(script, "<script>", "exec"), {})
        ran = "ran"
    except Exception as exc:  # noqa: BLE001 - the exception name is the outcome
        ran = type(exc).__name__
    assert ran == outcome


@real_only
def test_real_packages_every_runtime_name_of_complete_classes_is_in_the_catalog(real):
    """Classes the catalog calls complete (exported ones, operators and their inputs / outputs):
    every public name of the class at run time is in the catalog."""
    import ansys.dpf.core as core
    import ansys.dpf.post as post
    missing, checked = {}, 0
    targets = [(obj, f"{obj.__module__}.{obj.__qualname__}") for mod in (core, post) for obj in vars(mod).values()
               if inspect.isclass(obj) and obj.__module__.startswith("ansys.dpf.")]
    for op in list(real.core.operators.values())[::10]:
        module = importlib.import_module(op.qualified_name.rsplit(".", 1)[0])
        targets += [(getattr(module, q.rsplit(".", 1)[1]), q) for q in (op.qualified_name, op.inputs_class,
                                                                          op.outputs_class) if q]
    for obj, qualified in targets:
        members, complete = real.class_members(qualified)
        if complete and (qualified in real.core._classes or qualified in real.post._classes):
            checked += 1
            gone = sorted(n for n in dir(obj) if not n.startswith("_") and n not in members)
            if gone:
                missing[qualified] = gone
    assert checked > 50 and missing == {}
