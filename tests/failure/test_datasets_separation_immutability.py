"""Gate 6.2 Part 4 guarantees: no source mutation, no execution.

The separation pipeline writes ONLY into its own ``prepared/`` directory. Neither
the verified run library nor the Part 3 exported dataset may change, and the
whole pipeline must run with subprocess/process-runner sabotaged (it touches no
lab, Docker, or shell).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from verifiednet.datasets import (
    build_prepared,
    load_features,
    load_prepared,
    separate_dataset,
    verify_prepared,
    write_prepared,
)
from verifiednet.datasets.features import FeaturePolicy, LabelPolicy

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("pf-ref", "run-c")]


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _run_part4(ctx, prepared_dir: Path) -> None:
    sep = separate_dataset(ctx.loaded.examples, feature_policy=FeaturePolicy(),
                           label_policy=LabelPolicy(), dataset_version="v1",
                           source_index_digest=ctx.source_index_digest)
    prep = build_prepared(sep, feature_policy=FeaturePolicy(), label_policy=LabelPolicy(),
                          dataset_version="v1", source_index_digest=ctx.source_index_digest,
                          source_dataset_digest=ctx.dataset.manifest.dataset_digest)
    write_prepared(prep, prepared_dir)
    assert verify_prepared(prepared_dir).verified is True
    load_prepared(prepared_dir)
    load_features(prepared_dir)


def test_part4_does_not_mutate_sources(tmp_path: Path, separated_pipeline) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    runs_before = _fingerprint(ctx.run_root)
    export_before = _fingerprint(Path(ctx.dataset_dir))

    _run_part4(ctx, tmp_path / "prepared")

    assert _fingerprint(ctx.run_root) == runs_before  # verified runs untouched
    assert _fingerprint(Path(ctx.dataset_dir)) == export_before  # Part 3 export untouched


def test_part4_executes_no_process(
    tmp_path: Path, separated_pipeline, monkeypatch,
) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("separation must not spawn a process")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr("verifiednet.runtime.process.default_runner", _boom)

    _run_part4(ctx, tmp_path / "prepared")  # completes -> no execution attempted
