"""Gate 10D property tests: identity stability, digest sensitivity, safe paths."""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.training import (
    fake_payload_bytes,
    validate_checkpoint_relative_path,
)

pytestmark = pytest.mark.property

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


@given(steps=st.integers(1, 10_000))
@settings(max_examples=100)
def test_fake_payload_generation_is_stable_and_step_sensitive(steps: int) -> None:
    kw = dict(execution_id="trainexec-x", training_plan_id="trainplan-y",
              training_spec_id="trainspec-z", model_spec_id="model-m",
              tokenizer_spec_id="tok-t", format_spec_id="ckptfmt-f")
    a = fake_payload_bytes(completed_steps=steps, **kw)
    assert a == fake_payload_bytes(completed_steps=steps, **kw)
    assert a != fake_payload_bytes(completed_steps=steps + 1, **kw)


@given(name=st.text(
    alphabet=st.characters(codec="ascii", exclude_characters="\x00"),
    min_size=1, max_size=40))
@settings(max_examples=300)
def test_safe_path_validation_never_admits_escapes(name: str) -> None:
    path = f"payload/{name}"
    try:
        validate_checkpoint_relative_path(path)
    except ValueError:
        return  # rejection is always acceptable
    # anything accepted must be canonical, relative, forward-slash, in payload/
    assert not path.startswith("/")
    assert "\\" not in path
    assert ".." not in path.split("/")
    assert "." not in [p for p in path.split("/")]
    assert "//" not in path
    import posixpath

    assert posixpath.normpath(path) == path


def test_digest_is_sensitive_to_every_payload_byte(
    tmp_path_factory, checkpoint_pipeline,
) -> None:
    # Flip EVERY byte of the fake payload (with two different masks) and prove
    # the recomputed digest always changes: the digest binds file hashes, so a
    # one-byte flip anywhere in any payload changes the content identity.
    from verifiednet.training import (
        compute_checkpoint_digest,
        read_checkpoint_manifest,
        write_checkpoint,
    )

    tmp = tmp_path_factory.mktemp("ckpt-digest")
    ctx = checkpoint_pipeline(tmp, accepted=_ACC, rejected=["run-rej"])
    cand = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                format_spec=ctx.format_spec,
                                policy=ctx.production_policy)
    written = write_checkpoint(cand, tmp / "checkpoints")
    manifest = read_checkpoint_manifest(written.root)
    entry = next(f for f in manifest.files
                 if f.relative_path == "payload/model.fakebin")
    blob = next(f.content for f in cand.files
                if f.relative_path == "payload/model.fakebin")

    def recompute(files):
        return compute_checkpoint_digest(
            schema_version=manifest.schema_version,
            checkpoint_format_version=manifest.checkpoint_format_version,
            checkpoint_id=manifest.checkpoint_id,
            format_spec=manifest.format_spec,
            production_policy=manifest.production_policy,
            lineage=manifest.lineage, compatibility=manifest.compatibility,
            simulated=manifest.simulated, generated_by=manifest.generated_by,
            files=files)

    for pos in range(len(blob)):
        for mask in (0x01, 0xFF):
            mutated = bytes([*blob[:pos], blob[pos] ^ mask, *blob[pos + 1:]])
            mutated_entry = entry.model_copy(update={
                "sha256": hashlib.sha256(mutated).hexdigest()})
            files = tuple(
                mutated_entry if f.relative_path == entry.relative_path else f
                for f in manifest.files)
            assert recompute(files) != manifest.checkpoint_digest, pos
    # lineage sensitivity: any lineage change ripples into the digest
    mutated_lineage = manifest.lineage.model_copy(
        update={"training_corpus_digest": "traindig-" + "0" * 24})
    assert compute_checkpoint_digest(
        schema_version=manifest.schema_version,
        checkpoint_format_version=manifest.checkpoint_format_version,
        checkpoint_id=manifest.checkpoint_id, format_spec=manifest.format_spec,
        production_policy=manifest.production_policy, lineage=mutated_lineage,
        compatibility=manifest.compatibility, simulated=manifest.simulated,
        generated_by=manifest.generated_by, files=manifest.files,
    ) != manifest.checkpoint_digest


def test_identity_and_digest_stability_build_twice(
    tmp_path_factory, checkpoint_pipeline,
) -> None:
    import hashlib as _h

    from verifiednet.training import write_checkpoint

    tmp = tmp_path_factory.mktemp("ckpt-twice")
    ctx = checkpoint_pipeline(tmp, accepted=_ACC, rejected=["run-rej"])

    def build():
        return ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                    format_spec=ctx.format_spec,
                                    policy=ctx.production_policy)

    c1, c2 = build(), build()
    assert c1.format_spec.format_spec_id == c2.format_spec.format_spec_id
    assert (c1.production_policy.production_policy_id
            == c2.production_policy.production_policy_id)
    assert c1.lineage.lineage_id == c2.lineage.lineage_id
    assert c1.compatibility.compatibility_id == c2.compatibility.compatibility_id
    assert c1.intended_checkpoint_id == c2.intended_checkpoint_id
    assert [f.content for f in c1.files] == [f.content for f in c2.files]
    w1 = write_checkpoint(c1, tmp / "r1")
    w2 = write_checkpoint(c2, tmp / "r2")
    assert w1.checkpoint_digest == w2.checkpoint_digest

    def fingerprint(root):
        return {str(p.relative_to(root)): _h.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}

    assert fingerprint(w1.root) == fingerprint(w2.root)  # byte-identical dirs


def test_file_declaration_order_independence_and_size_arithmetic(
    tmp_path_factory, checkpoint_pipeline,
) -> None:
    from verifiednet.training import CheckpointCandidate, write_checkpoint

    tmp = tmp_path_factory.mktemp("ckpt-order")
    ctx = checkpoint_pipeline(tmp, accepted=_ACC, rejected=["run-rej"])
    cand = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                format_spec=ctx.format_spec,
                                policy=ctx.production_policy)
    dump = cand.model_dump()
    # reversed declaration order is not accepted as-is (canonical sort is the
    # contract) — a candidate must present files path-sorted.
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        CheckpointCandidate.model_validate(
            dump | {"files": tuple(reversed(dump["files"]))})
    # re-sorting yields the identical candidate → identical artifact
    resorted = CheckpointCandidate.model_validate(
        dump | {"files": tuple(sorted(reversed(dump["files"]),
                               key=lambda f: f["relative_path"]))})
    assert resorted == cand
    written = write_checkpoint(resorted, tmp / "checkpoints")
    assert written.total_bytes == sum(len(f.content) for f in cand.files)
