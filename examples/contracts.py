"""Use the implemented contracts and callbacks without services or credentials."""

import asyncio

from aiogram_extensions.integrations import Event, EventDispatcher, TaskEvent
from aiogram_extensions.messaging import QueueConfig
from aiogram_extensions.rate_limit import RateLimitConfig, RateRule


async def observe(event: Event) -> None:
    if isinstance(event, TaskEvent):
        print(f"Event: {event.kind}")


async def main() -> None:
    limiter = RateLimitConfig(rules=(RateRule(name="incoming", limit=5, window=1),))
    queue = QueueConfig()
    rule = limiter.rules[0]
    print(f"Incoming rule: {rule.scope}/{rule.algorithm}")
    print(f"Queue capacity: {queue.maxsize}")
    events = EventDispatcher((observe,))
    await events.emit(TaskEvent(kind="task_enqueued", task_id="example", generation="1"))


if __name__ == "__main__":
    asyncio.run(main())
