"""The supported device-to-documentation type mapping, without value bounds."""

DEVICE_PARAMETER_TYPES = {
    "integer": "integer",  # INTEGER<min-max>
    "string": "string",  # STRING<min-max>
    "text": "string",  # TEXT<min-max>
    "ipv4-address": "ipv4-address",  # X.X.X.X
    "ipv6-address": "ipv6-address",  # X:X::X:X
    "ipv6-prefix": "ipv6-prefix",  # X:X::X:X/M
    "hex": "hex",
    "mac": "mac",
    "passwordex": "passwordex",
    "date-slash": "date-slash",
    "date-iso": "date-iso",
    "month-day": "month-day",
    "date-us": "date-us",
    "datetime-slash": "datetime-slash",
    "time-seconds": "time-seconds",
    "time": "time",
}

KNOWN_PARAMETER_TYPES = frozenset(DEVICE_PARAMETER_TYPES.values())
# Unknown is explicit missing knowledge, never an alias for string or a proof.
DOCUMENT_PARAMETER_TYPES = frozenset(DEVICE_PARAMETER_TYPES) | {"unknown"}


def normalized_type(type_id: str | None) -> str | None:
    return DEVICE_PARAMETER_TYPES.get(type_id) if type_id is not None else None


def compatible_types(left: str | None, right: str | None) -> bool:
    """Only a known disagreement excludes a parameter correspondence."""
    left, right = normalized_type(left), normalized_type(right)
    return left is None or right is None or left == right
