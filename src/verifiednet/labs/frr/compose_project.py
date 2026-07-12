"""Deterministic Docker Compose project abstraction (Gate 4 Step 5).

Container identity comes from Compose project + service labels — never guessed
generated container names, never ``container_name``. All command builders return
argv lists (no shell strings, no ``shell=True``).

The project name is derived deterministically from the ``run_id``:
lowercased, normalized to the Compose-safe charset, prefixed, and bounded; if
truncation is needed a deterministic content hash of the full ``run_id`` is
appended for collision resistance. No time-based or random component.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical

_PROJECT_PREFIX = "vnet"
_MAX_PROJECT_LEN = 63
_INVALID_CHARS = re.compile(r"[^a-z0-9_-]")
_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
_COMPOSE_SERVICE_LABEL = "com.docker.compose.service"


class ServiceResolutionError(VerifiedNetError):
    """A declared service resolved to zero (missing) or more than one (ambiguous)
    container within the project."""


def project_name_for_run(run_id: str) -> str:
    """Return a deterministic, Compose-safe project name derived from *run_id*."""
    sanitized = _INVALID_CHARS.sub("-", run_id.lower())
    base = f"{_PROJECT_PREFIX}-{sanitized}"
    if len(base) <= _MAX_PROJECT_LEN:
        name = base
    else:
        digest = sha256_canonical(run_id)[:10]
        # keep = room for "vnet-" + kept-run-id + "-" + digest
        keep = _MAX_PROJECT_LEN - len(_PROJECT_PREFIX) - 2 - len(digest)
        name = f"{_PROJECT_PREFIX}-{sanitized[:keep]}-{digest}"
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):  # pragma: no cover - defensive
        raise VerifiedNetError(f"derived project name is not Compose-safe: {name!r}")
    return name


@dataclass(frozen=True)
class ComposeProject:
    """Argv builders and label queries for one deterministic Compose project."""

    project: str
    compose_file: Path
    services: tuple[str, ...]

    @classmethod
    def for_run(
        cls, run_id: str, compose_file: str | Path, services: tuple[str, ...]
    ) -> ComposeProject:
        return cls(
            project=project_name_for_run(run_id),
            compose_file=Path(compose_file),
            services=tuple(services),
        )

    def _base(self) -> list[str]:
        return ["docker", "compose", "-p", self.project, "-f", str(self.compose_file)]

    def up_argv(self) -> list[str]:
        return [*self._base(), "up", "-d", "--remove-orphans"]

    def down_argv(self) -> list[str]:
        return [*self._base(), "down", "--volumes", "--remove-orphans"]

    def exec_argv(self, service: str, logical_argv: tuple[str, ...]) -> list[str]:
        """``docker compose -p <project> -f <file> exec -T <service> <logical…>``.

        Does not raise for an unknown service — target validation is enforced by
        the executor's ``TargetPolicy`` so an unknown service is *denied* (result)
        rather than executed. Service membership for resolution/health lives in
        :meth:`resolve_service_container`.
        """
        return [*self._base(), "exec", "-T", service, *logical_argv]

    def ps_labels_argv(self) -> list[str]:
        """List this project's containers as ``<id>\\t<service>\\t<state>`` lines."""
        return [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={_COMPOSE_PROJECT_LABEL}={self.project}",
            "--format",
            '{{.ID}}\t{{.Label "' + _COMPOSE_SERVICE_LABEL + '"}}\t{{.State}}',
        ]

    def network_ls_argv(self) -> list[str]:
        """List this project's networks by Compose project label."""
        return [
            "docker",
            "network",
            "ls",
            "--filter",
            f"label={_COMPOSE_PROJECT_LABEL}={self.project}",
            "--format",
            "{{.Name}}",
        ]

    @staticmethod
    def parse_ps_labels(stdout: str) -> tuple[tuple[str, str, str], ...]:
        """Parse ``ps_labels_argv`` output into ``(id, service, state)`` tuples."""
        rows: list[tuple[str, str, str]] = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                raise ServiceResolutionError(f"unparseable ps row: {line!r}")
            rows.append((parts[0], parts[1], parts[2]))
        return tuple(rows)

    def resolve_service_container(
        self, rows: tuple[tuple[str, str, str], ...], service: str
    ) -> str:
        """Return the single container id for *service*; raise on missing/ambiguous."""
        matches = [cid for cid, svc, _state in rows if svc == service]
        if len(matches) == 0:
            raise ServiceResolutionError(f"service {service!r} has no container in project")
        if len(matches) > 1:
            raise ServiceResolutionError(
                f"service {service!r} is ambiguous: {len(matches)} containers"
            )
        return matches[0]
