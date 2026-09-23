"""Small declarative predicate vocabulary; no eval or executable expressions."""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Any

from .models import CaptureTag, MetadataError, ParameterBinding

_COMPARISONS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
}


def validate_predicate(value: Any, names: frozenset[str]) -> None:
    if isinstance(value, bool) or value == "always":
        return
    if not isinstance(value, dict):
        raise MetadataError("when must be 'always', a boolean, or a predicate object")
    op = value.get("op")
    if not isinstance(op, str):
        raise MetadataError("predicate op must be a string")
    if op in {"all", "any"}:
        args = value.get("args")
        if not isinstance(args, list):
            raise MetadataError(f"{op} requires an args array")
        for arg in args:
            validate_predicate(arg, names)
    elif op == "not":
        if "arg" not in value:
            raise MetadataError("not requires arg")
        validate_predicate(value["arg"], names)
    elif op == "exists" or op in _COMPARISONS:
        if (
            not isinstance(value.get("parameter"), str)
            or value["parameter"] not in names
        ):
            raise MetadataError(
                f"unknown predicate parameter: {value.get('parameter')!r}"
            )
        if op != "exists" and "value" not in value:
            raise MetadataError(f"{op} requires value")
    else:
        raise MetadataError(f"unsupported predicate op: {op!r}")


def evaluate_predicate(
    predicate: Any,
    bindings: tuple[ParameterBinding, ...],
    scope: CaptureTag | None,
) -> bool:
    if predicate == "always":
        return True
    if isinstance(predicate, bool):
        return predicate
    op = predicate["op"]
    if op in {"all", "any"}:
        outcomes = (
            evaluate_predicate(arg, bindings, scope) for arg in predicate["args"]
        )
        return all(outcomes) if op == "all" else any(outcomes)
    if op == "not":
        return not evaluate_predicate(predicate["arg"], bindings, scope)
    iterations = dict(scope.iterations) if scope else {}
    values = [
        binding.value.normalized
        for binding in bindings
        if binding.document.name == predicate["parameter"]
        and all(
            key not in iterations or iterations[key] == index
            for key, index in binding.document.iterations
        )
    ]
    if op == "exists":
        return bool(values)
    try:
        return any(_COMPARISONS[op](value, predicate["value"]) for value in values)
    except TypeError as error:
        raise MetadataError(
            f"incompatible values in predicate {predicate!r}"
        ) from error
