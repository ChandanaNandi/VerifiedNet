"""Deliberately-violating fixture: a lab module that bypasses the runtime.

A real lab backend must drive processes through ``verifiednet.runtime.process``
(the single ``subprocess`` boundary). This fixture imports ``subprocess``
directly and shells out to ``docker`` — exactly the bypass the AST guard must
catch for the ``labs`` package. It is never imported by production code.
"""

import subprocess


def start_lab() -> None:
    subprocess.run(["docker", "compose", "up", "-d"], check=False)
