"""Expose a running message queue to aiogram handlers."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aiogram import Dispatcher

from aiogram_extensions._validation import ConfigurationError, nonempty_string

from .queue import MessageQueue


@asynccontextmanager
async def message_queue_context(
    dispatcher: Dispatcher,
    queue: MessageQueue,
    *,
    key: str = "message_queue",
) -> AsyncGenerator[MessageQueue]:
    """Run a queue and inject it into handlers under the given parameter name."""
    nonempty_string("key", key)
    if key in dispatcher.workflow_data:
        raise ConfigurationError("key", "is already registered on the dispatcher")

    async with queue:
        dispatcher[key] = queue
        try:
            yield queue
        finally:
            if dispatcher.get(key) is queue:
                del dispatcher[key]
