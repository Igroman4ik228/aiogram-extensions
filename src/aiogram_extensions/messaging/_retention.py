"""Expiry for local queue records, ordered by their fixed retention period."""

from collections import OrderedDict
from typing import Generic, TypeVar

_Key = TypeVar("_Key")
_Value = TypeVar("_Value")


class Retained(Generic[_Key, _Value]):
    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._items: OrderedDict[_Key, tuple[float, _Value]] = OrderedDict()

    def put(self, key: _Key, value: _Value, now: float) -> None:
        self._items[key] = now + self._ttl, value
        self._items.move_to_end(key)

    def get(self, key: _Key) -> _Value | None:
        item = self._items.get(key)
        return item[1] if item is not None else None

    def discard(self, key: _Key) -> None:
        self._items.pop(key, None)

    def values(self) -> tuple[_Value, ...]:
        return tuple(value for _, value in self._items.values())

    def expire(self, now: float) -> None:
        while self._items:
            key = next(iter(self._items))
            if self._items[key][0] > now:
                return
            self._items.popitem(last=False)
