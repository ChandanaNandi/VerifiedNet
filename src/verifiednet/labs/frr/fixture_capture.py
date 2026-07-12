"""Live FRR fixture capture with complete provenance (Gate 4 Step 2).

Captures raw read-only command output from a LIVE healthy lab into a fixture
directory, exactly as emitted (no hand-editing, no normalization at capture
time — normalization happens in code, in the collectors). A ``manifest.json``
binds the capture set to the image digests, host platform, exact logical and
transport commands, per-file SHA-256 hashes, topology hash and source commit.

The provisional source-derived fixtures under ``tests/fixtures/frr/`` are NOT
touched: live capture sets live in their own subdirectory (e.g.
``tests/fixtures/frr/live/frr-8.4.1-linux-arm64/``).

Mutation safety: capture refuses to proceed (raises) if the backend transcript
contains ANY mutation-mode entry — a live fixture set must provably come from
an untouched healthy lab.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from verifiednet.common.canonical import canonical_json_str
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.common.runctx import RunContext
from verifiednet.runtime.results import ExecStatus
from verifiednet.schemas.topology import TopologySpec

if TYPE_CHECKING:
    from verifiednet.labs.frr.backend import FrrComposeBackend

FIXTURE_SCHEMA_VERSION = 1

#: (capture key, logical argv, filename template) — raw output saved verbatim.
CAPTURE_SPECS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "bgp_summary",
        ("vtysh", "-c", "show ip bgp summary json"),
        "{node}_bgp_summary_established.json",
    ),
    ("interfaces", ("vtysh", "-c", "show interface json"), "{node}_interfaces.json"),
    ("routes", ("vtysh", "-c", "show ip route json"), "{node}_routes.json"),
    ("running_config", ("vtysh", "-c", "show running-config"), "{node}_running_config.txt"),
)

_VERSION_ARGV = ("vtysh", "-c", "show version")
_VERSION_RE = re.compile(r"FRRouting (\S+)")


class FixtureCaptureError(VerifiedNetError):
    """Live fixture capture failed (command failure, mutation taint, IO error)."""


def _require_no_mutation(backend: FrrComposeBackend) -> None:
    entries = getattr(backend.transcript, "entries", None)
    if entries is None:
        raise FixtureCaptureError(
            "backend transcript does not expose entries; cannot prove the "
            "capture lab is mutation-free"
        )
    modes = {entry.mode for entry in entries}
    if "mutation" in modes:
        raise FixtureCaptureError(
            "backend transcript contains mutation entries; a live fixture set "
            "must come from an untouched healthy lab"
        )


def _live_frr_version(backend: FrrComposeBackend, node: str, timeout_s: float) -> str:
    result = backend.execute_readonly(node, _VERSION_ARGV, timeout_s)
    if result.status is not ExecStatus.OK:
        raise FixtureCaptureError(
            f"show version on {node!r} failed: {result.status.value}"
        )
    match = _VERSION_RE.search(result.stdout)
    if match is None:
        raise FixtureCaptureError(
            f"cannot extract FRR version from live output: {result.stdout[:120]!r}"
        )
    return match.group(1)


def capture_live_fixture_set(
    backend: FrrComposeBackend,
    topology: TopologySpec,
    run_ctx: RunContext,
    out_dir: str | Path,
    *,
    platform_digest: str,
    extra_environment: dict[str, str],
    source_commit: str,
    command_timeout_s: float = 10.0,
) -> dict[str, Any]:
    """Capture raw outputs from every node into *out_dir*; return the manifest.

    Raw stdout is written byte-exactly. The manifest is written as canonical
    JSON to ``out_dir/manifest.json`` and also returned. Raises
    :class:`FixtureCaptureError` on any command failure or mutation taint;
    ``OSError`` from fixture writes propagates (a failed write must be loud).
    """
    _require_no_mutation(backend)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    environment = dict(backend.capture_environment_metadata())
    environment.update(extra_environment)
    frr_version = _live_frr_version(backend, topology.nodes[0].name, command_timeout_s)

    files: dict[str, dict[str, Any]] = {}
    for node in topology.nodes:
        for _key, argv, template in CAPTURE_SPECS:
            result = backend.execute_readonly(node.name, argv, command_timeout_s)
            if result.status is not ExecStatus.OK:
                raise FixtureCaptureError(
                    f"capture {argv!r} on {node.name!r} failed: {result.status.value}"
                )
            filename = template.format(node=node.name)
            (out_path / filename).write_text(result.stdout, encoding="utf-8")
            transport = (
                list(result.invocation.transport_argv)
                if result.invocation is not None
                else list(result.argv)
            )
            files[filename] = {
                "target": node.name,
                "logical_argv": list(argv),
                "transport_argv": transport,
                "sha256": sha256_bytes(result.stdout.encode("utf-8")),
            }

    _require_no_mutation(backend)  # nothing mutated during capture either
    image_ref = topology.images.frr
    manifest: dict[str, Any] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "kind": "frr-live-fixture-capture",
        "frr_version": frr_version,
        "image_reference": image_ref,
        "manifest_list_digest": (
            image_ref.split("@", 1)[1] if "@" in image_ref else ""
        ),
        "platform_digest": platform_digest,
        "environment": {key: environment[key] for key in sorted(environment)},
        "captured_at": run_ctx.now().isoformat(),
        "topology_sha256": sha256_canonical(topology),
        "source_commit": source_commit,
        "statements": {
            "produced_from_live_two_router_healthy_lab": True,
            "no_mutation_command_executed": True,
        },
        "files": {name: files[name] for name in sorted(files)},
    }
    (out_path / "manifest.json").write_text(
        canonical_json_str(manifest) + "\n", encoding="utf-8"
    )
    return manifest


def verify_fixture_manifest(fixture_dir: str | Path) -> list[str]:
    """Check a captured fixture set against its manifest; return problems.

    Verifies: manifest parses, schema version matches, both provenance
    statements are present and true, every listed file exists with a matching
    SHA-256, and no unlisted stray file sits in the directory. An empty list
    means the set is intact.
    """
    problems: list[str] = []
    dir_path = Path(fixture_dir)
    manifest_path = dir_path / "manifest.json"
    if not manifest_path.is_file():
        return [f"missing manifest: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"manifest is not valid JSON: {exc}"]
    if manifest.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        problems.append(
            f"schema_version {manifest.get('schema_version')!r} != {FIXTURE_SCHEMA_VERSION}"
        )
    statements = manifest.get("statements", {})
    for key in (
        "produced_from_live_two_router_healthy_lab",
        "no_mutation_command_executed",
    ):
        if statements.get(key) is not True:
            problems.append(f"statement {key!r} is not true")
    listed = manifest.get("files", {})
    if not isinstance(listed, dict) or not listed:
        problems.append("manifest lists no files")
        listed = {}
    for name, meta in listed.items():
        path = dir_path / name
        if not path.is_file():
            problems.append(f"listed file missing: {name}")
            continue
        actual = sha256_bytes(path.read_bytes())
        if actual != meta.get("sha256"):
            problems.append(f"sha256 mismatch for {name}")
    strays = {
        p.name
        for p in dir_path.iterdir()
        if p.is_file() and p.name != "manifest.json" and p.name not in listed
    }
    for stray in sorted(strays):
        problems.append(f"stray unlisted file: {stray}")
    return problems
