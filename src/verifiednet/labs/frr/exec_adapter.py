"""Docker Compose execution adapters (Gate 4 Step 7).

The adapter preserves the Gate 3 security boundary while adding the transport
split: the *logical* network command is validated by the runtime command policy,
the *transport* command (``docker compose … exec -T <service> <logical…>``) is
what the single runtime process runner executes, and both are retained under one
deterministic ``command_id`` (a :class:`CommandInvocation`).

Two adapters exist as SEPARATE capabilities:

- :class:`FrrReadOnlyTransportAdapter` wraps a ``ReadOnlyExecutor``; it exposes a
  ``run(target, logical_argv, timeout_s) -> ExecResult`` surface compatible with
  the read-only executor protocol collectors already depend on.
- :class:`FrrMutationTransportAdapter` wraps a ``MutationExecutor`` and is
  constructed independently; it is NOT handed to collectors and the lab backend
  never exposes it (the ``LabBackend`` protocol has no mutation method).

``command_id`` is deterministic — a content hash over the run id, target, logical
argv, and a per-adapter operation index — never a random UUID.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.compose_project import ComposeProject
from verifiednet.runtime.invocation import CommandInvocation
from verifiednet.runtime.mutation import MutationExecutor
from verifiednet.runtime.readonly import ReadOnlyExecutor
from verifiednet.runtime.results import ExecResult


def _build_invocation(
    run_ctx: RunContext,
    target: str,
    logical_argv: tuple[str, ...],
    op_index: int,
    transport: tuple[str, ...],
) -> CommandInvocation:
    command_id = run_ctx.content_id(
        "cmd",
        {
            "run_id": run_ctx.run_id,
            "target": target,
            "logical_argv": list(logical_argv),
            "op": op_index,
        },
    )
    return CommandInvocation(
        command_id=command_id,
        target=target,
        logical_argv=logical_argv,
        transport_argv=transport,
    )


class FrrReadOnlyTransportAdapter:
    """Read-only transport adapter: validate logical, execute compose-exec transport."""

    def __init__(
        self, project: ComposeProject, executor: ReadOnlyExecutor, run_ctx: RunContext
    ) -> None:
        self._project = project
        self._executor = executor
        self._run_ctx = run_ctx
        self._ops = itertools.count(1)

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        """Run a logical read command on service *target* via ``compose exec -T``."""
        logical = tuple(argv)
        op_index = next(self._ops)
        transport = tuple(self._project.exec_argv(target, logical))
        invocation = _build_invocation(self._run_ctx, target, logical, op_index, transport)
        return self._executor.run(
            target, logical, timeout_s, transport_argv=transport, invocation=invocation
        )


class FrrMutationTransportAdapter:
    """Mutation transport adapter (separately constructed; never given to collectors)."""

    def __init__(
        self, project: ComposeProject, executor: MutationExecutor, run_ctx: RunContext
    ) -> None:
        self._project = project
        self._executor = executor
        self._run_ctx = run_ctx
        self._ops = itertools.count(1)

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        """Run a logical mutation command on service *target* via ``compose exec -T``."""
        logical = tuple(argv)
        op_index = next(self._ops)
        transport = tuple(self._project.exec_argv(target, logical))
        invocation = _build_invocation(self._run_ctx, target, logical, op_index, transport)
        return self._executor.run(
            target, logical, timeout_s, transport_argv=transport, invocation=invocation
        )
