"""Command and target allow-list policies for the runtime executors.

Provenance: adapted (copy with modifications) from neuronoc-network-ops-assistant
``backend/app/lab/collector.py::_assert_show_command`` / ``_FORBIDDEN_VTYSH_TOKENS``
(MIT, commit 5f24447): parameterized into policy objects, dead multi-word tokens
dropped.

All policies raise ``PolicyViolationError`` with a precise reason and never
execute anything themselves. Shell metacharacters are rejected outright anywhere
in argv — commands are argv lists, never shell strings.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from verifiednet.common.errors import PolicyViolationError

_SHELL_METACHARACTERS = frozenset("|;&$><`")

#: NN-derived forbidden vtysh tokens (whole-word, case-insensitive).
DEFAULT_FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {
        "configure",
        "copy",
        "write",
        "delete",
        "reload",
        "clear",
        "shutdown",
        "no",
        "enable",
        "import",
        "terminal",
    }
)

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9_]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _require_nonempty(argv: Sequence[str]) -> None:
    if not argv:
        raise PolicyViolationError("empty argv")


def _reject_shell_metacharacters(argv: Sequence[str]) -> None:
    for token in argv:
        hits = _SHELL_METACHARACTERS.intersection(token)
        if hits:
            raise PolicyViolationError(
                f"shell metacharacter(s) {sorted(hits)!r} in argument {token!r}"
            )


def _vtysh_commands(argv: Sequence[str]) -> tuple[str, ...]:
    """Collect the value following every ``-c`` flag; reject a dangling ``-c``."""
    commands: list[str] = []
    index = 1
    while index < len(argv):
        if argv[index] == "-c":
            if index + 1 >= len(argv):
                raise PolicyViolationError("dangling -c flag with no command value")
            commands.append(argv[index + 1])
            index += 2
        else:
            index += 1
    return tuple(commands)


def _normalize(command: str) -> str:
    return _WHITESPACE_RE.sub(" ", command.strip().lower())


def _assert_show_command(command: str, forbidden_tokens: frozenset[str]) -> None:
    normalized = _normalize(command)
    words = tuple(word for word in _WORD_SPLIT_RE.split(normalized) if word)
    if not words or words[0] != "show":
        raise PolicyViolationError(f"vtysh command is not a show command: {command!r}")
    for word in words:
        if word in forbidden_tokens:
            raise PolicyViolationError(
                f"forbidden token {word!r} in vtysh command: {command!r}"
            )


@dataclass(frozen=True)
class CommandPolicy:
    """Read-path command policy: binary allow-list plus vtysh show-only checks."""

    allowed_binaries: frozenset[str]
    vtysh_show_only: bool = True
    forbidden_tokens: frozenset[str] = DEFAULT_FORBIDDEN_TOKENS

    def check(self, argv: Sequence[str]) -> None:
        """Raise ``PolicyViolationError`` unless *argv* is allowed. Executes nothing."""
        _require_nonempty(argv)
        _reject_shell_metacharacters(argv)
        binary = argv[0]
        if binary not in self.allowed_binaries:
            raise PolicyViolationError(f"binary not allowed: {binary!r}")
        if binary == "vtysh" and self.vtysh_show_only:
            commands = _vtysh_commands(argv)
            if not commands:
                raise PolicyViolationError("vtysh without -c commands (interactive shell) denied")
            for command in commands:
                _assert_show_command(command, self.forbidden_tokens)


@dataclass(frozen=True)
class MutationCommandPolicy:
    """Mutation-path command policy: binary allow-list plus vtysh prefix templates.

    Each entry in ``allowed_vtysh_prefixes`` is an ordered template of allowed
    ``-c`` command prefixes. The argv's sequence of ``-c`` values matches a
    template when it has the same count or fewer commands and each command
    ``startswith`` the corresponding template prefix, in order.
    """

    allowed_binaries: frozenset[str]
    allowed_vtysh_prefixes: tuple[tuple[str, ...], ...] = ()

    def check(self, argv: Sequence[str]) -> None:
        """Raise ``PolicyViolationError`` unless *argv* is allowed. Executes nothing."""
        _require_nonempty(argv)
        _reject_shell_metacharacters(argv)
        binary = argv[0]
        if binary not in self.allowed_binaries:
            raise PolicyViolationError(f"binary not allowed: {binary!r}")
        if binary == "vtysh":
            commands = _vtysh_commands(argv)
            if not commands:
                raise PolicyViolationError("vtysh without -c commands (interactive shell) denied")
            for template in self.allowed_vtysh_prefixes:
                if len(commands) <= len(template) and all(
                    command.startswith(prefix)
                    for command, prefix in zip(commands, template, strict=False)
                ):
                    return
            raise PolicyViolationError(
                f"vtysh command sequence matches no allowed mutation template: {commands!r}"
            )


@dataclass(frozen=True)
class TargetPolicy:
    """Target allow-list: only named lab nodes may be addressed."""

    allowed_targets: frozenset[str]

    def check(self, target: str) -> None:
        """Raise ``PolicyViolationError`` unless *target* is allowed."""
        if target not in self.allowed_targets:
            raise PolicyViolationError(f"target not allowed: {target!r}")
