"""Validation shared by the public dataclass configurations."""

import math
import re


class ConfigurationError(ValueError):
    """A named setting violates its public contract."""

    def __init__(self, name: str, requirement: str) -> None:
        super().__init__(f"{name} {requirement}")


def require_instance(name: str, value: object, expected: type[object]) -> None:
    if not isinstance(value, expected):
        raise ConfigurationError(name, f"must be {expected.__name__}")


def nonempty_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(name, "must be a non-empty string")


def positive_integer(name: str, value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ConfigurationError(name, "must be a positive integer")


def seconds(name: str, value: float, *, allow_zero: bool = False) -> None:
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < 0 or (value == 0 and not allow_zero):
        raise ConfigurationError(
            name, f"must be finite and {'non-negative' if allow_zero else 'positive'}"
        )


def identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ConfigurationError(name, "must contain only ASCII letters, digits, _, . or -")
