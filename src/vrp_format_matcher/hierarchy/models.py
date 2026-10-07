"""Command evidence and documentation transitions, before device-view resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from vrp_format_matcher.models import CommandLocation, PreparedPair


@dataclass(frozen=True)
class DocumentTransition:
    """The documented effect; target_view always belongs to the documentation."""

    kind: Literal["switch", "stay", "unknown"]
    target_view: str | None = None

    @classmethod
    def from_command(cls, command: Mapping[str, Any]) -> DocumentTransition:
        # Catalog validation has already checked all switch_to_view references.
        if "switch_to_view" not in command:
            return cls("unknown")
        target = command["switch_to_view"]
        return cls("stay") if target is None else cls("switch", target)


@dataclass(frozen=True)
class CommandLink:
    """Keep the original pair: its slots and scope also constrain the transition.

    An intersection applies only on accepted full paths of pair.automaton.
    A structural pair applies to its whole matched structure. Neither establishes
    equivalence of the source views or identifies a target device view.
    """

    pair: PreparedPair
    device: CommandLocation
    documentation: CommandLocation
    transition: DocumentTransition


@dataclass(frozen=True)
class ViewLink:
    """Group positive command evidence without ranking or resolving views."""

    device_view: str
    documentation_view: str
    commands: tuple[CommandLink, ...]


@dataclass(frozen=True)
class HierarchyEvidence:
    """Only full matches contribute; missing links do not prove incompatibility."""

    command_links: tuple[CommandLink, ...]
    view_links: tuple[ViewLink, ...]


@dataclass(frozen=True)
class ViewTarget:
    """Positive candidates are not exhaustive; a singleton is still unresolved."""

    status: Literal["resolved", "unresolved"]
    candidates: tuple[ViewLink, ...] = ()
    device_view: str | None = None


@dataclass(frozen=True)
class PreparedHierarchy:
    """Scoped documented effects and target options, not a runtime routing table.

    Resolving a target does not establish the source-view correspondence of
    every referencing command. Its original CommandLink remains the condition.
    """

    device_entry_view: str
    documentation_entry_view: str
    evidence: HierarchyEvidence
    targets: dict[str, ViewTarget]
    # Explicit input switches already use the target catalog's own view keys.
    # A missing entry is unknown; None means an explicitly declared stay.
    declared_transitions: dict[str, str | None] = field(default_factory=dict)
