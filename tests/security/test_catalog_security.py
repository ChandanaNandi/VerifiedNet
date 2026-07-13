"""Gate 5.6 security/policy regression for the scenario catalog.

Proves the catalog cannot become an injection or privilege-escalation surface:
case data is plain scalars (no callables), scenario parameters cannot inject
arbitrary vtysh commands (mutations flow only through the exact shape
allow-list), no new mutation binary was introduced, and the composition-root
import boundary still holds.
"""

from __future__ import annotations

import pytest

from verifiednet.orchestrator import SCENARIO_CATALOG
from verifiednet.orchestrator.families import APPROVED_FAMILY_BINDINGS

pytestmark = pytest.mark.security


def test_catalog_case_data_is_plain_scalars() -> None:
    # ScenarioDefinition.parameters is dict[str, str|int]; no case may carry a
    # callable or other object (schema-enforced, re-asserted here).
    for case in SCENARIO_CATALOG:
        for key, value in case.scenario.parameters.items():
            assert isinstance(key, str)
            assert isinstance(value, (str, int)) and not callable(value)


def test_scenario_definition_rejects_callable_parameters() -> None:
    from verifiednet.schemas import ScenarioDefinition, ScenarioTimeouts

    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        ScenarioDefinition(
            scenario_id="s", family="bgp", template_id="bgp_remote_as_mismatch", version=1,
            parameters={"target_node": (lambda: "x")},  # type: ignore[dict-item]
            timeouts=ScenarioTimeouts(precondition_s=1, onset_s=1, recovery_s=1,
                                      command_s=1, poll_interval_s=1))


def test_parameters_cannot_inject_vtysh_commands() -> None:
    # A target_node carrying an embedded vtysh command is not routable to any
    # topology node and is rejected by validation before any lab action; it can
    # never reach command construction (which reads only topology-derived values).
    from verifiednet.labs.frr.topologies import two_router_frr_topology
    from verifiednet.orchestrator import (
        ScenarioCase,
        ScenarioValidationError,
        validate_scenario_case,
    )
    from verifiednet.schemas import ScenarioDefinition, ScenarioTimeouts

    evil = ScenarioCase(
        case_id="x", expected_target="router_a; clear bgp 172.30.0.2",
        scenario=ScenarioDefinition(
            scenario_id="s", family="bgp", template_id="bgp_neighbor_removal", version=1,
            parameters={"target_node": "router_a; clear bgp 172.30.0.2",
                        "target_session": "a-b"},
            timeouts=ScenarioTimeouts(precondition_s=1, onset_s=1, recovery_s=1,
                                      command_s=1, poll_interval_s=1)))
    with pytest.raises(ScenarioValidationError, match="unknown target_node"):
        validate_scenario_case(evil, two_router_frr_topology())


def test_no_mutation_shape_uses_a_non_vtysh_binary() -> None:
    # Every approved family's mutation shapes are vtysh -c sequences; the runtime
    # allow-list is {"vtysh"} only. No new binary (e.g. ip) was introduced.
    from verifiednet.faults.frr_commands import (
        iface_shutdown_argv,
        remove_neighbor_argv,
        set_remote_as_argv,
        withdraw_network_argv,
    )

    for argv in (
        set_remote_as_argv(65001, "172.30.0.2", 65999),
        remove_neighbor_argv(65001, "172.30.0.2"),
        iface_shutdown_argv("eth1"),
        withdraw_network_argv(65001, "10.255.0.1/32"),
    ):
        assert argv[0] == "vtysh"
    # every binding's shapes are named vtysh command shapes (not raw binaries)
    for binding in APPROVED_FAMILY_BINDINGS:
        shapes = binding.mutation_shapes()
        assert shapes and all(hasattr(s, "commands") for s in shapes)


def test_orchestrator_import_boundary_still_holds() -> None:
    # The composition-root boundary must still hold with catalog.py added: no
    # src package below the orchestrator may import it. Self-contained AST scan.
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "verifiednet"
    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src)
        package = rel.parts[0] if len(rel.parts) > 1 else None
        if package == "orchestrator":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules = [node.module]
            for module in modules:
                if module == "verifiednet.orchestrator" or module.startswith(
                    "verifiednet.orchestrator."
                ):
                    offenders.append(f"{path}:{node.lineno} {module}")
    assert offenders == [], offenders
