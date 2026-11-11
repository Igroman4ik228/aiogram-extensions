"""The outcome of a rate limit check."""

from dataclasses import dataclass

from aiogram_extensions._validation import ConfigurationError, identifier, require_instance, seconds


@dataclass(frozen=True, kw_only=True)
class RateLimitResult:
    allowed: bool
    retry_after: float = 0.0
    triggered_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        seconds("retry_after", self.retry_after, allow_zero=True)
        if type(self.allowed) is not bool:
            raise ConfigurationError("allowed", "must be a boolean")

        require_instance("triggered_rules", self.triggered_rules, tuple)
        for name in self.triggered_rules:
            identifier("triggered_rules", name)

        if self.allowed and (self.retry_after or self.triggered_rules):
            raise ConfigurationError("allowed", "results cannot contain blocking rules or a wait")
        if not self.allowed and (not self.triggered_rules or self.retry_after == 0):
            raise ConfigurationError("denied", "results require blocking rules and a positive wait")
