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


#: Parameter fragments permitted only in explicitly-named positions of a shape.
_IPV4 = r"(?:\d{1,3}\.){3}\d{1,3}"
_ASN = r"\d{1,10}"


@dataclass(frozen=True)
class MutationCommandShape:
    """A named, complete vtysh ``-c`` command sequence.

    ``commands`` is the *exact* ordered set of ``-c`` command patterns for this
    shape. A candidate matches only when it has the identical command count and
    every command fully matches (``re.fullmatch``) the pattern in the same
    position. Patterns are fully-literal except for explicitly-permitted
    parameter positions (an ASN or an IPv4 address). There are no partial
    prefixes: a lone ``configure terminal`` cannot match a three-command shape,
    and ``router bgp`` without an ASN cannot match at all.
    """

    name: str
    commands: tuple[re.Pattern[str], ...]

    def matches(self, normalized_commands: Sequence[str]) -> bool:
        if len(normalized_commands) != len(self.commands):
            return False
        return all(
            pattern.fullmatch(command) is not None
            for pattern, command in zip(self.commands, normalized_commands, strict=True)
        )


def bgp_remote_as_mutation_shapes() -> tuple[MutationCommandShape, ...]:
    """The only two mutation shapes the BGP remote-AS scenario is permitted.

    Provenance: the vtysh grammar mirrors sonic-troubleshooting-agent
    ``faults/bgp_asn_mismatch.py`` (MIT, commit eb4c818), re-targeted at plain
    FRR and tightened to exact, complete shapes (Gate 3 freeze-check correction 5).
    """
    return (
        MutationCommandShape(
            name="set_remote_as",
            commands=(
                re.compile(r"configure terminal"),
                re.compile(rf"router bgp {_ASN}"),
                re.compile(rf"neighbor {_IPV4} remote-as {_ASN}"),
            ),
        ),
        MutationCommandShape(
            name="clear_bgp",
            commands=(re.compile(rf"clear bgp {_IPV4}"),),
        ),
    )


def bgp_neighbor_removal_mutation_shapes() -> tuple[MutationCommandShape, ...]:
    """The only three mutation shapes the neighbor-removal scenario is permitted.

    Gate 5.2. ``remove_neighbor`` deletes the peer object; ``restore_neighbor``
    recreates it exactly as the rendered baseline does — including the
    load-bearing ``neighbor <ip> activate`` under ``address-family ipv4
    unicast`` (the lab renders ``no bgp default ipv4-unicast``). ``clear_bgp``
    is the same forced-reset shape the remote-AS family uses.
    """
    return (
        MutationCommandShape(
            name="remove_neighbor",
            commands=(
                re.compile(r"configure terminal"),
                re.compile(rf"router bgp {_ASN}"),
                re.compile(rf"no neighbor {_IPV4}"),
            ),
        ),
        MutationCommandShape(
            name="restore_neighbor",
            commands=(
                re.compile(r"configure terminal"),
                re.compile(rf"router bgp {_ASN}"),
                re.compile(rf"neighbor {_IPV4} remote-as {_ASN}"),
                re.compile(r"address-family ipv4 unicast"),
                re.compile(rf"neighbor {_IPV4} activate"),
            ),
        ),
        MutationCommandShape(
            name="clear_bgp",
            commands=(re.compile(rf"clear bgp {_IPV4}"),),
        ),
    )


@dataclass(frozen=True)
class MutationCommandPolicy:
    """Mutation-path command policy: binary allow-list plus exact vtysh shapes.

    A vtysh argv is permitted only when its full ``-c`` command sequence matches
    exactly one :class:`MutationCommandShape` — identical command count, identical
    ordering, each command fully matching its position's pattern. Parameters may
    vary only in the shape's explicitly-permitted positions (ASN, peer address).
    Partial prefixes and truncated sequences are rejected.
    """

    allowed_binaries: frozenset[str]
    allowed_shapes: tuple[MutationCommandShape, ...] = ()

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
            normalized = tuple(_normalize(command) for command in commands)
            for shape in self.allowed_shapes:
                if shape.matches(normalized):
                    return
            raise PolicyViolationError(
                "vtysh command sequence matches no allowed mutation shape "
                f"(exact count and ordering required): {commands!r}"
            )


@dataclass(frozen=True)
class TargetPolicy:
    """Target allow-list: only named lab nodes may be addressed."""

    allowed_targets: frozenset[str]

    def check(self, target: str) -> None:
        """Raise ``PolicyViolationError`` unless *target* is allowed."""
        if target not in self.allowed_targets:
            raise PolicyViolationError(f"target not allowed: {target!r}")
