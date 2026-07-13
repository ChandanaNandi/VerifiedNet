"""Deliberately-violating fixture: a lower package importing the composition root.

The orchestrator is the Gate 4 composition root — the dependency arrow points
DOWN into it from the top, never out of a lower package like ``labs``. A lab
module importing ``verifiednet.orchestrator`` inverts that arrow and is exactly
the coupling the AST guard must catch. Never imported by production code.
"""

from verifiednet.orchestrator.live_run import run_accepted_incident


def compose() -> object:
    return run_accepted_incident
