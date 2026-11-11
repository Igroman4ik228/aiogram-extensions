"""Public limiter contracts, independent of a backend or Telegram."""

from typing import Any

import pytest

from aiogram_extensions.rate_limit import RateLimitConfig, RateLimitResult, RateRule


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_rule_rejects_non_positive_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        RateRule(name="messages", limit=limit, window=1.0)


@pytest.mark.parametrize(
    "window",
    [0, -1, float("nan"), float("inf"), True, pytest.param(10**1000, id="unrepresentable-seconds")],
)
def test_rule_rejects_invalid_window(window: float) -> None:
    with pytest.raises(ValueError, match="window"):
        RateRule(name="messages", limit=1, window=window)


@pytest.mark.parametrize("name", ["", " ", "message:count", "☃"])
def test_rule_rejects_unstable_names(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        RateRule(name=name, limit=1, window=1)


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ({"scope": "unknown"}, "scope"),
        ({"algorithm": "bucket"}, "algorithm"),
    ],
)
def test_rule_rejects_unknown_options(settings: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        RateRule(name="messages", limit=1, window=1, **settings)


def test_config_rejects_duplicate_rule_names() -> None:
    rule = RateRule(name="messages", limit=1, window=1)

    with pytest.raises(ValueError, match="unique"):
        RateLimitConfig(rules=(rule, rule))


def test_result_reports_all_blocking_rules_and_wait() -> None:
    result = RateLimitResult(allowed=False, retry_after=2.5, triggered_rules=("chat", "bot"))

    assert result.retry_after == 2.5
    assert result.triggered_rules == ("chat", "bot")


def test_allowed_result_cannot_have_a_wait_or_blocking_rules() -> None:
    with pytest.raises(ValueError, match="allowed"):
        RateLimitResult(allowed=True, retry_after=2.5, triggered_rules=("chat",))


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ({"rules": ()}, "non-empty"),
        ({"rules": []}, "must be tuple"),
        ({"rules": (None,)}, "must be RateRule"),
        ({"on_limit": "ignore"}, "on_limit"),
    ],
)
def test_config_rejects_invalid_rules_or_mode(settings: dict[str, Any], error: str) -> None:
    options: dict[str, Any] = {"rules": (RateRule(name="bot", limit=1, window=1),)}
    options.update(settings)

    with pytest.raises(ValueError, match=error):
        RateLimitConfig(**options)


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ({"allowed": 1}, "allowed must be a boolean"),
        ({"allowed": True, "retry_after": -1}, "retry_after"),
        ({"allowed": False}, "positive wait"),
        ({"allowed": False, "retry_after": 1}, "blocking rules"),
        ({"allowed": False, "retry_after": 1, "triggered_rules": ["bot"]}, "must be tuple"),
        ({"allowed": False, "retry_after": 1, "triggered_rules": ("",)}, "triggered_rules"),
    ],
)
def test_result_rejects_inconsistent_decisions(settings: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        RateLimitResult(**settings)
