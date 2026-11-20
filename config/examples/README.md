# Config sections of agents (step M0)

An agent package that declares its own config section (`SpecialistSpec.config_section`) puts the example of that section here, in `<section>.yaml`, with exactly the section's defaults, for example `hpc.yaml`:

```yaml
hpc:
  mjob_path: null
  timeout_seconds: 60
```

To use it, copy the section into your `config/config.yaml` and change the values. `tests/test_parallel_enablers.py` checks that every declared section has its file here and that no file is left without a section.

Built-in sections of Champion (`llm`, `access`, `edit`, `coder`, ...) stay in `config/config.example.yaml`.
