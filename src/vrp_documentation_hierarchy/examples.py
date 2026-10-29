"""Local example blocks; prompt labels stay opaque, not interface type rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PROMPT = re.compile(r"^\s*(<[^<>]+>|\[[^\[\]]+\])\s*(.*?)\s*$")
NAVIGATION = {"system-view", "quit", "return"}


@dataclass(frozen=True)
class Command:
    line: int
    text: str
    ancestors: tuple[Command, ...]
    rooted: bool


@dataclass
class Frame:
    prompt: str
    ancestors: tuple[Command, ...] = ()
    rooted: bool = False


def parse_example(lines: list[str]) -> tuple[list[Command], list[dict[str, Any]]]:
    if any(PROMPT.match(line) for line in lines):
        return _session(lines)
    return _indented(lines), []


def _session(lines: list[str]) -> tuple[list[Command], list[dict[str, Any]]]:
    frames: list[Frame] = []
    commands: list[Command] = []
    issues: list[dict[str, Any]] = []
    previous: Command | None = None
    hostname = "HUAWEI"
    for number, raw in enumerate(lines, 1):
        match = PROMPT.match(raw)
        if match is None:
            continue  # Console output and captions do not open blocks.
        prompt, text = match.groups()
        label = prompt[1:-1].lstrip("~*")
        action = previous.text if previous else ""
        if action == "quit":
            frames = frames[:-1]
        elif action == "return":
            frames = []
        if prompt.startswith("<"):
            hostname = label
            frames = [Frame(label)]
        elif action == "system-view":
            hostname = label
            frames = [Frame(label, rooted=True)]
        elif action in {"quit", "return"}:
            if not frames or frames[-1].prompt != label:
                issues.append({
                    "line": number, "reason": "unexpected_prompt_after_exit",
                    "prompt": prompt,
                })
                frames = [Frame(label)]
        elif action.startswith("sysname ") and frames:
            old = hostname
            hostname = action.split(maxsplit=1)[1]
            for frame in frames:
                if frame.prompt == old or frame.prompt.startswith(old + "-"):
                    frame.prompt = hostname + frame.prompt[len(old):]
            if frames[-1].prompt != label:
                issues.append({
                    "line": number, "reason": "unexpected_prompt_after_sysname",
                    "prompt": prompt,
                })
                frames = [Frame(label)]
        elif frames and frames[-1].prompt == label:
            pass
        elif label == hostname:
            frames = [Frame(label, rooted=True)]
        elif any(frame.prompt == label for frame in frames):
            while frames[-1].prompt != label:
                frames.pop()
        elif previous is not None and frames:
            frame = frames[-1]
            frames.append(Frame(label, (*frame.ancestors, previous), frame.rooted))
        else:
            issues.append({
                "line": number, "reason": "unknown_initial_context", "prompt": prompt,
            })
            frames = [Frame(label)]
        previous = None
        if text:
            frame = frames[-1]
            previous = Command(number, text, frame.ancestors, frame.rooted)
            commands.append(previous)
    return commands, issues


def _indented(lines: list[str]) -> list[Command]:
    result: list[Command] = []
    frames: list[tuple[int, Command]] = []
    previous: tuple[int, Command] | None = None
    for number, raw in enumerate(lines, 1):
        expanded = raw.expandtabs(8)
        text = expanded.strip()
        if not text:
            continue
        depth = len(expanded) - len(expanded.lstrip())
        if text == "#":
            if depth == 0:
                frames.clear()
            previous = None
            continue
        while frames and depth <= frames[-1][0]:
            frames.pop()
        if previous is not None and depth > previous[0]:
            frames.append(previous)
        ancestors = tuple(node for _, node in frames)
        rooted = depth == 0 or bool(ancestors and ancestors[0].rooted)
        node = Command(number, text, ancestors, rooted)
        result.append(node)
        previous = depth, node
    return result
