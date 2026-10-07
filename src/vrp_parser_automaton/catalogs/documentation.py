"""Named documentation placeholders preserve offsets and accept one raw token."""

from __future__ import annotations

import re

from vrp_parser_automaton.errors import PatternDocumentError
from vrp_parser_automaton.parameters import (
    ParameterDeclaration,
    ParameterFamily,
    ParameterResult,
    ParameterType,
)
from vrp_parser_automaton.parameters.models import DeclarationRecognition
from vrp_parser_automaton.parameters.readers import SingleTokenReader


class NamedDeclaration:
    def recognize(self, pattern: str, position: int) -> DeclarationRecognition | None:
        if pattern[position : position + 1] != "<":
            return None
        match = re.match(r"<([A-Za-z0-9_][A-Za-z0-9_.:/-]*)>", pattern[position:])
        if match is None:
            raise PatternDocumentError(
                f"invalid documentation placeholder at {position}"
            )
        return DeclarationRecognition(position + match.end())


class DocumentValue:
    def probe(self, raw: str, declaration: ParameterDeclaration) -> ParameterResult:
        return ParameterResult.success(raw)


def documentation_parameter() -> ParameterType:
    # Documentation annotations constrain offline matching, not runtime values.
    return ParameterType(
        "document-parameter",
        ParameterFamily.GENERIC,
        NamedDeclaration(),
        SingleTokenReader(),
        DocumentValue(),
    )
