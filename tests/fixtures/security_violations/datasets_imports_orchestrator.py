"""Deliberately-violating fixture: the dataset engine importing the live root.

The dataset engine is a READ-ONLY consumer of verified run artifacts (ADR-0018);
it must never import the live composition root (which runs the lab). This
importing of ``verifiednet.orchestrator`` is exactly the coupling the AST guard
must catch. Never imported by production code.
"""

from verifiednet.orchestrator import run_accepted_incident


def build() -> object:
    return run_accepted_incident
