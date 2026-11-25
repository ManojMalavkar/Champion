"""Program tools (M2): trusted external programs and scripts as agent tools.

* ``spec``     - how a program is declared (``ProgramSpec``, ``parse_entry``, ``register_program``)
* ``manifest`` - the user's ``programs.yaml``, file rules, trust store, status of each program
* ``runner``   - checks and runs one call (arguments, class, approval, hash, limits, changes)
* ``tools``    - ``program_<name>`` tools and ``attach_program_tools`` (chat start)

The user trusts each program (``champion programs trust NAME``); a program whose
file, declared interpreter or entry changed is not offered until trusted again.
"""

from champion.programs.spec import ProgramSpec, register_program, unregister_program  # noqa: F401
