"""
The `Version` value object.

Replaces the free functions `is_valid_version` and `_get_next_version`. A
version is now *validated at construction time*, so once you hold a Version
instance you know it's well-formed. No more scattered `if not
is_valid_version(...)` checks.
"""
from dataclasses import dataclass
from typing import Tuple
from velto_cli.domain.errors import InvalidVersionError


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "Version":
        if not isinstance(value, str):
            raise InvalidVersionError(str(value))

        parts = value.strip().split(".")

        if len(parts) != 3:
            raise InvalidVersionError(value)

        try:
            numbers = tuple(int(p) for p in parts)
        except ValueError:
            raise InvalidVersionError(value) from None

        if any(n < 0 for n in numbers):
            raise InvalidVersionError(value)

        return cls(*numbers)

    @classmethod
    def is_valid(cls, value: str) -> bool:
        try:
            cls.parse(value)
        except InvalidVersionError:
            return False
        return True

    def next_patch(self) -> "Version":
        return Version(
            self.major,
            self.minor,
            self.patch + 1,
        )

    def next_minor(self) -> "Version":
        return Version(
            self.major,
            self.minor + 1,
            0,
        )

    def next_major(self) -> "Version":
        return Version(
            self.major + 1,
            0,
            0,
        )

    def matches_caret(self, minimum: "Version") -> bool:
        return self >= minimum and self.major == minimum.major

    def as_tuple(self) -> Tuple[int, int, int]:
        return self.major, self.minor, self.patch

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"
