"""Deliberately-violating fixture: an artifacts module importing live execution.

The artifacts package is low-level persistence and must not depend on labs
(live execution). This importing of ``verifiednet.labs`` is exactly the coupling
the AST guard must catch. Never imported by production code.
"""

from verifiednet.labs.frr.backend import FrrComposeBackend


def persist() -> type:
    return FrrComposeBackend
