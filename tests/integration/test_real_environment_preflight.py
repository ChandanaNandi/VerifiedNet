"""OPTIONAL integration: preflight against the REAL local environment.

Deselected by default (`-m "not integration"`). When explicitly enabled it
performs PREFLIGHT ONLY against the real machine via the CPU-only
``SystemEnvironmentProbe``: no downloads, no model loading, no gradients, no
checkpoints. The assertion is structural — the preflight either authorizes or
refuses with complete findings; on a machine without the ML packages (or with
the v1 probe's undeclared total memory) refusal is the EXPECTED honest
outcome.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.training import (
    FindingSeverity,
    PreflightStage,
    SystemEnvironmentProbe,
    read_training_authorization,
    write_training_authorization,
)

pytestmark = pytest.mark.integration

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def test_real_environment_preflight_is_structural(
    tmp_path: Path, preflight_pipeline,
) -> None:
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    backend = ctx.make_backend(SystemEnvironmentProbe())
    snapshot = backend.inspect_environment()
    assert snapshot.environment_snapshot_id.startswith("envsnap-")
    assert snapshot.device.device_type == "cpu"  # v1 probe is CPU-only

    auth, snap = backend.preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver)
    # structural completeness regardless of outcome
    assert {f.stage for f in auth.findings} == set(PreflightStage)
    has_error = any(f.severity is FindingSeverity.ERROR for f in auth.findings)
    assert auth.authorized == (not has_error)
    written = write_training_authorization(
        auth, snap, tmp_path / "training-authorizations")
    assert read_training_authorization(written.root).authorization == auth
