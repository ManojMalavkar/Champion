# mChampion PyDyna Development Plan

## 1. Purpose

This plan defines the implementation of the **PyDyna specialist branch of Champion**. The objective is to build a safe, reusable, evidence-driven CAE assistant that can:

1. Answer LS-DYNA keyword and PyDyna API questions.
2. Generate and edit PyDyna and PyDPF Python code on the local machine.
3. Validate generated code against the installed API and organizational modeling rules.
4. Reuse existing user scripts and functions where appropriate.
5. Convert successful scripts into reusable, versioned capabilities.
6. Improve and rank capabilities from real usage evidence.
7. Safely test generated code in a controlled workspace and later in an isolated sandbox.
8. Inspect and modify LS-DYNA models without giving generated code unrestricted access to original files.
9. Preserve original authorship and all contributor history.
10. Integrate later with Champion's Linux, HPC, memory, model-indexing, and execution services.

---

## 2. Core Architectural Decisions

### 2.1 Champion owns safety and execution

The PyDyna specialist understands CAE intent, LS-DYNA, PyDyna, PyDPF, modeling methods, and domain-specific validation. It must not implement its own unrestricted file or command execution.

```text
Champion Core
├── access control
├── path policy
├── approval policy
├── code validation
├── capability registry
├── sandbox and execution
├── change tracking
├── audit logging
└── telemetry
        │
        ▼
PyDyna Specialist
├── Keyword Lookup
├── PyDyna Code
└── PyDPF Post
```

### 2.2 Specialists use shared Champion services

The following services must be generic and reusable by PyDyna, Linux, HPC, ANSA/META, and future agents:

- Coder and patching tools
- CodeArtifact model
- Capability registry
- Usage ranking and maturity engine
- Author and contributor provenance
- SandboxSession
- Diff, commit, discard, and rollback
- Approval and access policy
- Audit and telemetry

### 2.3 Generated code never receives unrestricted access

Generated PyDyna code must not directly edit the original CAE model tree. Model changes must use:

```text
Original project       read-only
Selected target        staged writable copy
Generated outputs      isolated work directory
Commit to original     only after validation and approval
```

### 2.4 Deterministic validation before LLM review

The LLM must not be the only API or safety checker.

```text
Syntax/AST checks
    +
Installed API and signature checks
    +
Static security and path checks
    +
Modeling-rule engine
    +
LLM semantic review
```

### 2.5 Evidence-driven capability sharing

Routine capabilities do not require formal team approval. They mature automatically from successful use, while execution safety remains risk-based.

```text
Draft
  → User Validated
  → Proven Personal
  → Team Emerging
  → Team Proven
  → Standardized
```

Every stage remains open to improvement, versioning, regression testing, and rollback.

---

## 3. Current Baseline

The branch already contains the initial PyDyna foundation:

- PyDyna knowledge records and JSONL normalization
- PyDyna knowledge base data
- Dedicated PyDyna specialist area
- Keyword Lookup, PyDyna Code, and PyDPF-Post agent structure
- Initial read-only knowledge tools
- Champion tool-policy integration
- Track-local Chroma retrieval adapter
- PyDyna records, data, and tool tests

### Immediate baseline tasks

- Run pending tests against the real Linux knowledge data.
- Verify retrieval against the actual local Chroma collection.
- Record installed PyDyna/PyDPF package versions.
- Confirm the current branch test baseline before starting code generation.
- Replace the track-local search implementation with Champion's common knowledge service when M1 is available.

### Baseline exit criteria

- Existing Champion tests pass.
- PyDyna knowledge retrieval works against real local data.
- Missing or skipped data-dependent tests are documented.
- Installed API versions are recorded in configuration.

---

## 4. Target PyDyna Flow

```text
User request
    ↓
Champion Orchestrator
    ↓
Determine task, project, program, release, variant, loadcase, and paths
    ↓
PyDyna Specialist
    ├── retrieve exact API records
    ├── retrieve approved or proven capabilities
    ├── retrieve existing scripts and examples
    ├── retrieve applicable modeling rules
    └── inspect authorized local code/model context
    ↓
Reuse capability / adapt capability / generate new code
    ↓
CodeArtifact or PatchArtifact
    ↓
Deterministic validation
    ↓
LLM semantic reviewer
    ↓
Controlled local workspace
    ↓
Sandbox test execution
    ↓
Bounded repair loop
    ↓
Diff and validation report
    ↓
Approval where required
    ↓
Commit / save / register capability
    ↓
Usage and reliability evidence
```

---

# 5. Implementation Phases

## Phase PD-00: Knowledge Records

**Status:** Implemented; verify locally.

### Scope

- Load PyDyna JSONL records.
- Normalize ID, text, metadata, source, version, and record type.
- Report malformed lines instead of silently skipping them.
- Preserve source provenance.

### Required tests

- Valid records
- Malformed JSONL
- Missing identifiers
- Duplicate identifiers
- Metadata normalization
- Version-specific records

### Exit criteria

- Records load deterministically.
- Invalid input produces actionable diagnostics.
- Source and version metadata are preserved.

---

## Phase PD-01: Knowledge Retrieval Tools

**Status:** Initial implementation present; complete integration.

### Scope

- API search
- Keyword search
- Example retrieval
- Exact symbol lookup
- Related API lookup
- Source citation and provenance

### Required improvements

- Add version-aware filtering.
- Add result type filters: API, keyword, example, rule, script, capability.
- Add exact symbol preference before semantic search.
- Add confidence and source-authority metadata.
- Migrate from local `search.py` to the common Champion knowledge service.

### Exit criteria

- Exact API names are found reliably.
- Retrieval can be restricted to the installed version.
- Responses expose the authoritative source used.

---

## Phase PD-02: PyDyna Specialist Group

**Status:** Initial agent structure present.

### Agents

1. **Keyword Lookup**
   - LS-DYNA keyword explanation
   - Card and field guidance
   - Relationships between parts, sections, materials, contacts, sets, and curves

2. **PyDyna Code**
   - Generate or edit Python automation
   - Reuse existing scripts and capabilities
   - Produce CodeArtifact/PatchArtifact outputs

3. **PyDPF Post**
   - Generate result-processing scripts
   - Extract curves, forces, acceleration, energy, and other available outputs

### Routing rules

- A specialist is invoked as a Champion tool, not through unrestricted handoff.
- Read-only questions must not invoke execution.
- Code generation and code execution are separate operations.
- The narrowest sufficient specialist/capability must be selected.

---

## Phase PD-03: CodeArtifact and Local Coder Workflow

**Priority:** Next major implementation.

### 3.1 CodeArtifact schema

```python
@dataclass
class CodeArtifact:
    artifact_id: str
    language: str
    purpose: str
    code: str
    entrypoint: str | None
    dependencies: list[str]
    required_inputs: list[dict]
    expected_outputs: list[dict]
    requested_read_paths: list[str]
    requested_write_paths: list[str]
    api_symbols: list[str]
    knowledge_sources: list[str]
    applicable_rules: list[str]
    risk_level: str
```

### 3.2 PatchArtifact schema

Use a separate artifact when editing an existing local script:

```python
@dataclass
class PatchArtifact:
    target_file: str
    purpose: str
    base_hash: str
    patch: str
    changed_symbols: list[str]
    changed_line_count: int
    changed_percent: float
    required_checks: list[str]
```

### 3.3 Local coder tools

Implement controlled tools:

- `read_code`
- `search_code`
- `find_symbol`
- `create_file`
- `apply_patch`
- `replace_symbol`
- `insert_function`
- `format_file`
- `validate_code`
- `show_diff`
- `undo_change`

### Rules

- Prefer small patches over complete-file rewrites.
- Verify the file hash immediately before patching.
- Reject writes outside authorized paths.
- Show or record a diff for every change.
- Large changes follow Champion's approval thresholds.
- Generated files go first to a session workspace, not directly to the source tree.

### Exit criteria

- Agent can create a new `.py` file in an authorized workspace.
- Agent can patch an existing script safely.
- All modifications are reversible and audited.
- Code is not executed automatically.

---

## Phase PD-04: Version-Aware PyDyna API Catalog

### Sources in authority order

1. Installed PyDyna/PyDPF package and stubs
2. Version-matched official API documentation
3. Approved or standardized capability implementations
4. User-tested scripts
5. General examples
6. LLM prior knowledge

### Catalog record

```yaml
qualified_name: Model.add_mat_piecewise_linear_plasticity
package: ansys.dyna.core
package_version: x.y
record_type: method
parameters:
  - name: mid
    required: true
    type: int
returns: MatPiecewiseLinearPlasticity
source_type: installed_stub
source_path: ...
authority: authoritative
```

### Catalog builders

- Python package introspection
- Stub parser
- Source parser where permitted
- Existing documentation ingestion
- Example linker
- Version-difference detector

### Exit criteria

- The checker knows whether a class/function exists in the installed version.
- Required, optional, and unknown arguments are identified.
- API records retain authoritative provenance.
- Version conflicts are reported instead of guessed.

---

## Phase PD-05: Deterministic Code and API Checker

### Validation pipeline

```text
CodeArtifact/PatchArtifact
    ↓
Python AST parse
    ↓
Import and symbol checks
    ↓
API call extraction
    ↓
Installed signature validation
    ↓
Security and dangerous-operation analysis
    ↓
Path and permission analysis
    ↓
Modeling-rule validation
    ↓
Structured validation report
```

### Static tools

- `ast.parse()`
- `compile()` without execution
- Ruff
- Pyright or mypy where practical
- Restricted import policy
- API signature checker
- Champion path-policy checker

### Initially blocked or reviewed patterns

- `eval`
- `exec`
- `os.system`
- unapproved `subprocess`
- `shutil.rmtree`
- `os.remove` / `os.unlink`
- socket/network libraries
- dynamic downloads
- credential access
- writes outside declared output paths

### Structured result

```json
{
  "decision": "pass|revise|block",
  "syntax_valid": true,
  "api_valid": true,
  "security_valid": true,
  "path_policy_valid": true,
  "rule_compliance": "pass",
  "issues": []
}
```

### Exit criteria

- Invalid API names and parameters are caught deterministically.
- Dangerous operations are blocked or escalated.
- Every issue identifies file, line, category, and evidence.

---

## Phase PD-06: LLM Semantic Reviewer

### Purpose

The reviewer checks intent and engineering meaning, not basic syntax alone.

### Reviewer input

- User request
- Generated code or patch
- Extracted API calls
- Exact API catalog records
- Relevant existing script/capability
- Applicable modeling methods and rules
- Project/program/loadcase context

### Reviewer checks

- Does the code satisfy the requested operation?
- Is the selected PyDyna API appropriate?
- Is a required load/save/export operation missing?
- Are units and project assumptions explicit?
- Does the implementation contradict a modeling rule?
- Is an approved/proven capability being reimplemented unnecessarily?

### Boundaries

- The reviewer cannot override a deterministic security block.
- The reviewer cannot expand path permissions.
- The reviewer cannot mark a rule as approved.
- All findings must reference supplied evidence.

### Exit criteria

- Reviewer returns structured PASS/REVISE/BLOCK output.
- Suggested corrections identify supporting API/rule evidence.
- Unsupported assumptions are reported clearly.

---

## Phase PD-07: Existing Script Ingestion and Reuse

### Goal

Allow PyDyna to use scripts already created and tested by engineers.

### Script categories

```text
scripts/
├── personal/
├── team/
├── standardized/
├── examples/
├── legacy/
└── generated/
```

### Ingestion metadata

- Original author
- Contributors
- Source file and hash
- Purpose and intent phrases
- Language and entrypoint
- PyDyna/PyDPF version
- Inputs and outputs
- API calls
- Read/write access requirements
- Program/loadcase applicability
- Validation status
- Known limitations
- Usage statistics

### Reuse decision

```text
Exact proven capability exists? → reuse
Close capability exists?        → adapt and validate
No capability exists?           → generate from API + rules + examples
```

### Rules

- Never blindly trust a script solely because it exists.
- Preserve the source script unchanged during initial ingestion.
- Prefer reusable functions over copying complete scripts.
- Revalidate against the installed package version.
- Detect hard-coded user paths, credentials, and environment assumptions.

### Exit criteria

- Agent can find a relevant existing script/function.
- Agent explains why it selected it.
- Agent adapts it through a patch rather than unnecessary regeneration.

---

## Phase CAP-01: Shared Capability Registry

This is a Champion Core component used by PyDyna and later by Linux, HPC, and ANSA/META specialists.

### Capability record

```yaml
capability_id: lsdyna.extract_include_tree
name: Extract LS-DYNA include tree
version: 1.0.0
original_author: Manoj Malavkar
scope: personal
status: user_validated
implementation:
  language: python
  file: implementation.py
  entrypoint: extract_include_tree
intents:
  - extract files called by master
  - list LS-DYNA includes
  - find local files referenced by master
risk:
  level: read_only
inputs: []
outputs: []
supported_cases:
  - "*INCLUDE"
  - "*INCLUDE_PATH"
  - "*INCLUDE_STAMPED_PART"
validation: {}
limitations: []
```

### Storage

Use SQLite for metadata and telemetry, with implementation files in a version-controlled capability directory.

Suggested tables:

- `capabilities`
- `capability_versions`
- `capability_intents`
- `capability_dependencies`
- `capability_validation`
- `capability_executions`
- `capability_failures`
- `capability_contributors`
- `capability_promotions`

---

## Phase CAP-02: Script-to-Capability Enrichment

### Trigger

When the user says something equivalent to:

- “This works; use it for future.”
- “Remember this script for this operation.”
- “Add this to the reusable library.”

### Automated flow

```text
User-tested script
    ↓
Identify reusable functions
    ↓
Generate capability metadata
    ↓
Extract API calls, inputs, outputs, paths, and risk
    ↓
Create or complete tests
    ↓
Run validation
    ↓
Register personal capability
    ↓
Index intents and parameter mappings
```

### Important rule

One successful execution may produce `user_validated`, but not `standardized`.

### Exit criteria

- A tested local script can be registered for future use.
- Original authorship is immutable.
- The agent can retrieve the capability using different natural-language wording.

---

## Phase CAP-03: Evidence-Driven Maturity and Team Sharing

### Maturity levels

| Level | Initial promotion trigger | Visibility |
|---|---|---|
| Draft | Created but unvalidated | Author only |
| User Validated | Author confirms successful real test | Author only |
| Proven Personal | 5 successful author uses and no unresolved critical failure | Team discoverable |
| Team Emerging | Successful use by another user | Team |
| Team Proven | 3 users, 15 successful runs, acceptable success rate | Preferred team option |
| Standardized | 5 users, 30 runs, ≥95% success, regression checks pass | Default option |
| Deprecated | Replaced or high unresolved risk | Hidden from normal selection |

Thresholds must be configuration, not hard-coded constants.

### Team discoverability

First-time users receive an explanation:

```text
Capability: Extract LS-DYNA Include Tree
Original author: Manoj Malavkar
Current version: 1.2.0
Used by: 4 engineers
Successful runs: 22
Reads: master and include files
Writes: none
Supports: INCLUDE, INCLUDE_PATH, nested includes, stamped parts
Known limitations: ...
```

### No mandatory routine team approval

- Low-risk, read-only capabilities can become team discoverable automatically.
- File-modifying capabilities can be shared automatically but still execute through staging, diff, and commit approval.
- Destructive or operational capabilities never become unguarded through popularity.

---

## Phase CAP-04: Capability Matching and Ranking

### Hard filters before ranking

Reject a candidate if:

- incompatible with installed PyDyna/PyDPF version
- required paths are not authorized
- capability is deprecated or disabled
- task scope does not match program/loadcase constraints
- last critical validation failed
- required input is unavailable
- risk exceeds execution policy

### Ranking factors

```text
score =
    intent match
  + context match
  + validation coverage
  + success rate
  + distinct-user confidence
  + usage confidence
  + recency
  + maturity
  - permission scope penalty
  - failure penalty
  - incompatibility penalty
```

### Selection principles

- Prefer the narrowest capability that fully satisfies the request.
- Cross-user success is stronger evidence than repeated use by one user.
- One serious wrong-result event should outweigh several successful runs.
- A high-usage destructive capability still requires safeguards.

### Exit criteria

- Matching requests select proven capabilities before generating new code.
- Selection is explainable with score components and evidence.
- Risk and excessive permissions reduce ranking.

---

## Phase CAP-05: Continuous Improvement, Versioning, and Regression

### Improvement flow

```text
Failure or limitation found
    ↓
Record structured issue
    ↓
Create failing regression test
    ↓
Patch capability
    ↓
Run old and new tests
    ↓
Create candidate version
    ↓
Canary use
    ↓
Promote stable version
    ↓
Retain previous version for rollback
```

### Semantic versioning

- Patch: bug fix without normal behavior change
- Minor: compatible new behavior or supported case
- Major: breaking interface/output behavior

### Example

```text
v1.0.0: INCLUDE + INCLUDE_PATH
v1.1.0: add INCLUDE_STAMPED_PART
v1.1.1: fix comments or quoted-path parsing
v2.0.0: return structured include graph instead of path list
```

### Provenance

Always preserve:

- Original author
- Contributors and contribution descriptions
- Source version
- Validation evidence
- Usage and failure history
- Superseded and rollback versions

---

## Phase PD-08: Modeling Methods and Rule Service

### Knowledge layers

```text
1. PyDyna API knowledge
2. Shared modeling methods
3. Program-specific standards
4. Release/variant rules
5. Loadcase-specific rules
6. Project decisions
7. User-specific learnings
8. Existing model and script evidence
```

### Structured rule example

```yaml
rule_id: MAT_024_001
name: MAT_024 density must be positive
scope:
  programs: [all]
  loadcases: [all]
keywords:
  - "*MAT_PIECEWISE_LINEAR_PLASTICITY"
severity: error
check:
  field: RO
  operator: greater_than
  value: 0
source:
  document: Approved material modeling method
  section: "3.2"
status: approved
version: 1.0.0
```

### Rule precedence

```text
Mandatory shared standard
    ↓
Program standard
    ↓
Release/variant rule
    ↓
Loadcase-specific rule
    ↓
Project decision
    ↓
User preference
```

### Rules

- The LLM retrieves and explains rules but does not create organizational approval.
- Personal improvements remain personal until evidence-driven sharing criteria are met.
- Conflicting mandatory rules must be reported, not silently resolved.
- Every generated or edited script records which rules were applied.

### Exit criteria

- Agent retrieves only rules applicable to current context.
- Rule violations appear in the validation report.
- Source, scope, version, and severity are visible.

---

## Phase PD-09: PyDPF Code Generation

### Scope

Generate post-processing CodeArtifacts for available result operations, such as:

- section forces
- energy curves
- node acceleration
- selected histories
- model/result summaries
- CSV/plot export

### Rules

- Use installed-version API records.
- Reuse proven post-processing capabilities first.
- Keep generation separate from execution.
- Declare required result files and expected outputs.

### Exit criteria

- Generated PyDPF code passes syntax and API validation.
- Required input/result files are declared.
- Outputs are written only to the configured workspace.

---

## Phase M3-A: Generic SandboxSession

This must be implemented in Champion Core.

### Interface

```python
sandbox = SandboxSession()
sandbox.add_readonly(project_path)
sandbox.stage_writable(target_path)
sandbox.prepare()
result = sandbox.run(code_artifact)
diff = sandbox.diff()
sandbox.commit()   # after approval
# or
sandbox.discard()
```

### Backends

```text
SandboxSession
├── WorkingCopyBackend
└── BubblewrapBackend
```

### Exit criteria

- Specialists do not know backend details.
- Every run records mounted/readable/writable paths.
- Original model files remain untouched until commit.

---

## Phase M3-B: Working-Copy Staging

### Layout

```text
session/
├── source-view/       read-only project view
├── work/              staged writable files
├── generated/         generated code
├── outputs/           new output files
├── logs/
├── validation/
└── changes/
```

### Rules

- Stage only determined target files as writable.
- Record hashes before and after execution.
- Detect unexpected created, deleted, or modified files.
- Refuse commit if the source changed after staging.

---

## Phase M3-C: Diff, Commit, Discard, and Rollback

### Required behavior

- Show affected files.
- Show text or structured keyword differences.
- Run validation before commit.
- Require commit approval for model/source modifications.
- Use atomic replacement where practical.
- Retain rollback information.

### Two approvals for model-changing execution

1. Permission to execute generated code in the sandbox.
2. Permission to commit validated changes to original files.

---

## Phase M3-D: Bubblewrap Isolation

### Target Linux runtime

```text
/usr and runtime       read-only
PyDyna libraries       read-only
project/model          read-only
staged work            read-write
/tmp                   isolated
network                disabled by default
user secrets           not mounted
process/time/memory    limited
```

### Important

Bubblewrap adds isolation but does not replace:

- Champion path checks
- tool policies
- approvals
- auditing
- diff/commit controls
- runtime limits

### Portability

Keep the interface backend-neutral so a Windows sandbox backend can be added later.

---

## Phase PD-10: Controlled Test Execution and Repair Loop

### Flow

```text
Validated CodeArtifact
    ↓
Prepare controlled workspace
    ↓
Execution approval where required
    ↓
Run in sandbox
    ↓
Capture stdout, stderr, traceback, return code, runtime, and file effects
    ↓
If failed: retrieve exact API/rule evidence
    ↓
Generate minimal patch
    ↓
Revalidate
    ↓
Retry within configured limit
```

### Initial limits

```yaml
max_repair_attempts: 3
network_access: false
original_model_write: false
execution_timeout_seconds: 60
```

Resource limits must be configurable by capability type because large CAE operations may legitimately need longer execution.

### Exit criteria

- Execution cannot bypass SandboxSession.
- Repair loop stops at the configured limit.
- Every attempt and patch is logged.
- Remaining errors are presented with evidence.

---

## Phase PD-11: Model-Aware Read Tools

### Tools

- `model_outline`
- `resolve_include_tree`
- `find_keyword`
- `find_part`
- `find_material`
- `find_section`
- `find_contact`
- `find_set`
- `read_keyword`
- `keyword_location`
- `entity_usage`

### Large-model strategy

Do not place entire LS-DYNA models in LLM context.

```text
Model tree
    ↓
include graph and keyword index
    ↓
keyword offsets and entity metadata
    ↓
targeted reads
    ↓
small grounded agent context
```

### First pilot capability

`lsdyna.extract_include_tree`

It must support:

- `*INCLUDE`
- `*INCLUDE_PATH`
- nested includes
- `*INCLUDE_STAMPED_PART`
- local/external/missing classification
- filtering by selected location
- read-only operation
- original author and version history
- regression fixtures for previously missed cases

---

## Phase PD-12: Model Modification

### Example flow

```text
Request: Change a material property
    ↓
Resolve project/loadcase context
    ↓
Find material and owning include
    ↓
Stage owning include as writable copy
    ↓
Generate or reuse modification capability
    ↓
Validate API, rules, and access
    ↓
Execute in sandbox
    ↓
Validate LS-DYNA deck
    ↓
Show structured diff
    ↓
Commit approval
    ↓
Atomic commit and audit
```

### Rules

- Original model tree is read-only during execution.
- Only selected target files are staged writable.
- Unexpected modifications block commit.
- All model changes require engineering validation and diff review.

---

## Phase PD-13: Engineering Model Validation

### Initial validations

- keyword syntax
- include path resolution
- missing include detection
- duplicate IDs
- missing referenced IDs
- unexpected keyword deletion
- unexpected file creation/deletion
- modified-file scope

### Later validations

- part → section → material integrity
- contact references
- set references
- curve references
- output-card consistency
- program and loadcase rule compliance
- unit-system consistency

### Result format

```text
Syntax                 PASS
Include tree            PASS
ID references           PASS
Modeling rules          PASS/WARN
Unexpected modifications NONE
Files modified          1
Commit eligible         YES
```

---

## Phase PD-14: Reusable PyDyna and CAE Skills

Build higher-level skills by composing proven capabilities:

- `find_parts_using_material`
- `clone_material`
- `replace_material`
- `change_material_property`
- `change_shell_thickness`
- `inspect_contact_definition`
- `create_output_request`
- `build_postprocess_script`
- `validate_include_tree`

Skills must use Champion capability selection and safety services. They must not directly bypass access policy or sandbox execution.

---

## Phase PD-15: HPC Integration

### End-to-end flow

```text
Inspect model
    ↓
Modify in sandbox
    ↓
Validate and approve
    ↓
Commit model version
    ↓
HPC Agent prepares run
    ↓
Submit LS-DYNA job
    ↓
Record Job ID and model provenance
    ↓
Monitor job
    ↓
Collect results
    ↓
PyDPF post-processing
    ↓
Store reusable outcome and traceability
```

HPC submission, cancellation, and destructive operations remain governed regardless of capability ranking.

---

# 6. Risk Model

| Risk | Examples | Sharing | Execution |
|---|---|---|---|
| R0 Read-only | API lookup, include listing, log parsing | Automatic after evidence threshold | Path checks; normally no commit approval |
| R1 Create outputs | Reports, manifests, new scripts | Automatic after evidence threshold | Authorized output directory |
| R2 Modify files | Patch script, change model include | Team discoverable automatically | Staging, diff, validation, commit approval |
| R3 Operational/destructive | Delete, kill job, overwrite directory | May be discoverable | Always explicit approval and strict policy |

Popularity or standardization does not remove execution safeguards.

---

# 7. Suggested Repository Structure

```text
champion/
├── capabilities/
│   ├── models.py
│   ├── registry.py
│   ├── extractor.py
│   ├── indexer.py
│   ├── matcher.py
│   ├── ranking.py
│   ├── maturity.py
│   ├── telemetry.py
│   ├── versioning.py
│   ├── provenance.py
│   └── promotion.py
│
├── coding/
│   ├── artifacts.py
│   ├── reader.py
│   ├── symbols.py
│   ├── patcher.py
│   ├── diff.py
│   └── formatter.py
│
├── execution/
│   ├── approval.py
│   ├── code_check.py
│   ├── api_check.py
│   ├── rule_check.py
│   ├── commands.py
│   └── sandbox/
│       ├── session.py
│       ├── result.py
│       ├── staging.py
│       ├── working_copy.py
│       └── bubblewrap.py
│
└── specialists/
    └── pydyna/
        ├── agent.py
        ├── routing.py
        ├── records.py
        ├── search.py
        ├── tools.py
        ├── api_catalog/
        ├── reviewer/
        ├── rules/
        ├── capabilities/
        │   └── include_tree/
        │       ├── implementation.py
        │       ├── capability.yaml
        │       └── tests/
        └── pydpf/
```

---

# 8. Testing Strategy

## Unit tests

- record normalization
- API catalog parsing
- API signature checking
- CodeArtifact and PatchArtifact validation
- symbol-aware patching
- capability matching and ranking
- maturity transitions
- rule selection and precedence
- telemetry aggregation
- semantic version transitions

## Security tests

- blocked imports
- path traversal
- symlink escape
- time-of-check/time-of-use protections where applicable
- unexpected writes
- credential/environment leakage
- subprocess and network attempts
- source modification after staging

## Integration tests

- request → retrieval → code generation
- existing script → capability registration
- future request → capability reuse
- test failure → repair patch → revalidation
- personal success → automatic team discoverability
- multi-user evidence → standardized maturity
- sandbox execution → diff → commit/discard

## CAE regression fixtures

- simple include
- nested include
- include path
- quoted paths and spaces
- missing include
- cyclic include
- stamped-part include
- comments and continuation lines
- local versus external path filtering

---

# 9. Observability and Audit

Record for every generation, edit, reuse, and execution:

- user and session ID
- specialist and model used
- selected capability and version
- selection/ranking evidence
- source API records and rules
- files read, created, edited, or staged
- approvals and access decisions
- validation outcomes
- execution runtime, stdout, stderr, and return code
- repair attempts
- final diff and commit result
- user correction or success confirmation

Capability telemetry must store aggregate reliability without exposing another user's private project content.

---

# 10. Practical Delivery Order

| Order | ID | Deliverable | State |
|---:|---|---|---|
| 1 | PD-00 | Knowledge records | Done; verify locally |
| 2 | PD-01 | Retrieval tools | Initial implementation |
| 3 | PD-02 | Specialist group | Initial implementation |
| 4 | BASE-01 | Linux real-data and full regression validation | Next |
| 5 | PD-03 | CodeArtifact, PatchArtifact, and controlled local coder | Next |
| 6 | PD-04 | Version-aware API catalog | Planned |
| 7 | PD-05 | Deterministic code/API checker | Planned |
| 8 | PD-06 | LLM semantic reviewer | Planned |
| 9 | PD-07 | Existing-script ingestion and reuse | Planned |
| 10 | CAP-01 | Shared capability registry | Planned |
| 11 | CAP-02 | Script-to-capability enrichment | Planned |
| 12 | CAP-03 | Automatic maturity and team sharing | Planned |
| 13 | CAP-04 | Matching and evidence-based ranking | Planned |
| 14 | CAP-05 | Versioning, regression, canary, rollback | Planned |
| 15 | PD-08 | Modeling methods and rule service | Planned |
| 16 | PD-09 | PyDPF code generation | Planned |
| 17 | M3-A | Generic SandboxSession | Planned |
| 18 | M3-B | Working-copy staging | Planned |
| 19 | M3-C | Diff, commit, discard, rollback | Planned |
| 20 | M3-D | Bubblewrap backend | Planned |
| 21 | PD-10 | Controlled execution and repair loop | Planned |
| 22 | PD-11 | Model-aware read tools | Planned |
| 23 | PD-12 | Model modification | Planned |
| 24 | PD-13 | Engineering validation | Planned |
| 25 | PD-14 | Reusable CAE skills | Planned |
| 26 | PD-15 | HPC integration | Future |

---

# 11. Immediate Next Sprint

## Sprint objective

Deliver safe local PyDyna code generation and editing without enabling unrestricted execution.

## Work items

### A. Baseline verification

- Run current tests on Linux with real knowledge data.
- Record pass/skip/fail baseline.
- Record installed PyDyna and PyDPF versions.

### B. Artifact models

- Implement `CodeArtifact`.
- Implement `PatchArtifact`.
- Add serialization and schema validation.

### C. Controlled coder

- Implement authorized file read.
- Implement create-in-workspace.
- Implement exact patch with base hash.
- Implement diff and undo.
- Integrate existing Champion access and audit controls.

### D. Basic validation

- Add AST parse and compile check.
- Add Ruff integration.
- Extract imports and calls.
- Block obviously unsafe APIs.
- Do not execute generated code yet.

### E. Existing script pilot

Use one proven read-only script, preferably include-tree extraction:

- ingest original script
- preserve author
- extract reusable function
- describe inputs/outputs
- add stamped-part regression fixture
- register as a personal capability
- retrieve it from alternate user wording

## Sprint exit criteria

- PyDyna agent generates a valid CodeArtifact.
- Champion creates a `.py` file only in the authorized workspace.
- Champion safely patches an existing script and shows a diff.
- Syntax/static validation runs without executing code.
- One existing script is registered and retrieved as a reusable capability.
- Original author and version are visible.
- Full Champion regression suite remains green.

---

# 12. Non-Goals for the Immediate Sprint

Do not implement yet:

- direct model modification
- unrestricted Python execution
- automatic writes to original model trees
- Bubblewrap integration
- HPC submission
- automatic destructive Linux operations
- organization-wide standard status based on one successful run

---

# 13. Definition of Done for the PyDyna Track

The PyDyna development track is complete when Champion can:

1. Ground answers in version-aware PyDyna/PyDPF knowledge.
2. Reuse an existing proven capability when it matches the request.
3. Generate or safely edit local code when no suitable capability exists.
4. Validate syntax, API usage, paths, security, and modeling rules.
5. Test code in an isolated workspace with bounded repair attempts.
6. Preserve original model files until an approved commit.
7. Inspect large LS-DYNA models through targeted indexes and reads.
8. Perform controlled model changes with engineering validation and diff.
9. Register successful user scripts for future use.
10. Automatically share proven low-risk capabilities with the team.
11. Rank capabilities using intent, context, reliability, distinct users, maturity, and least privilege.
12. Preserve original author and contributor history.
13. Continuously improve standardized capabilities through versioning and regression tests.
14. Integrate safely with Linux and HPC specialists.
15. Maintain a complete audit trail from request to final result.
