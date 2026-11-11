"""Try a per-user/chat rate limit without a bot or an external service."""

import asyncio

from aiogram_extensions.rate_limit import RateLimitConfig, RateLimitContext, RateLimiter, RateRule


async def main() -> None:
    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="activity", limit=1, window=60),)),
        direction="incoming",
    )

    first_user = RateLimitContext(bot_id=1, chat_id=2, user_id=3)
    second_user = RateLimitContext(bot_id=1, chat_id=2, user_id=4)

    print(f"First update: {(await limiter.check(first_user)).allowed}")
    denied = await limiter.check(first_user)
    print(f"Blocked by: {', '.join(denied.triggered_rules)}")
    print(f"Another user: {(await limiter.check(second_user)).allowed}")


if __name__ == "__main__":
    asyncio.run(main())
