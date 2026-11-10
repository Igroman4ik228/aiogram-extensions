"""Public limiter contracts, independent of a backend or Telegram."""

from typing import Any

import pytest

from aiogram_extensions.rate_limit import RateRule


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


@pytest.mark.parametrize("settings", [{"scope": "unknown"}, {"algorithm": "bucket"}])
def test_rule_rejects_unknown_options(settings: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=next(iter(settings))):
        RateRule(name="messages", limit=1, window=1, **settings)


def test_config_rejects_duplicate_rule_names() -> None:
    from aiogram_extensions.rate_limit import RateLimitConfig  # noqa: PLC0415

    rule = RateRule(name="messages", limit=1, window=1)
    with pytest.raises(ValueError, match="unique"):
        RateLimitConfig(rules=(rule, rule))


def test_result_reports_all_blocking_rules_and_wait() -> None:
    from aiogram_extensions.rate_limit import RateLimitResult  # noqa: PLC0415

    result = RateLimitResult(allowed=False, retry_after=2.5, triggered_rules=("chat", "bot"))
    assert result.retry_after == 2.5
    assert result.triggered_rules == ("chat", "bot")
    with pytest.raises(ValueError, match="allowed"):
        RateLimitResult(allowed=True, retry_after=2.5, triggered_rules=("chat",))


@pytest.mark.parametrize(
    "settings",
    [{"rules": ()}, {"rules": []}, {"rules": (None,)}, {"on_limit": "ignore"}],
)
def test_config_rejects_invalid_rules_or_mode(settings: dict[str, Any]) -> None:
    from aiogram_extensions.rate_limit import RateLimitConfig  # noqa: PLC0415

    options: dict[str, Any] = {"rules": (RateRule(name="bot", limit=1, window=1),)}
    options.update(settings)
    with pytest.raises(ValueError, match=next(iter(settings))):
        RateLimitConfig(**options)


@pytest.mark.parametrize(
    "settings",
    [
        {"allowed": 1},
        {"allowed": True, "retry_after": -1},
        {"allowed": False},
        {"allowed": False, "retry_after": 1},
        {"allowed": False, "retry_after": 1, "triggered_rules": ["bot"]},
        {"allowed": False, "retry_after": 1, "triggered_rules": ("",)},
    ],
)
def test_result_rejects_inconsistent_decisions(settings: dict[str, Any]) -> None:
    from aiogram_extensions.rate_limit import RateLimitResult  # noqa: PLC0415

    with pytest.raises(ValueError, match=r"allowed|retry_after|denied|triggered_rules"):
        RateLimitResult(**settings)
