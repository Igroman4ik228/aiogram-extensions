"""Experimental wire format, deliberately outside the library's public API."""

from typing import Any, cast

from aiogram.methods import (
    SendAudio,
    SendContact,
    SendDocument,
    SendLocation,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    SendVideo,
    TelegramMethod,
)
from aiogram.types import FSInputFile

METHODS: dict[str, type[TelegramMethod[Any]]] = {
    cls.__api_method__: cls
    for cls in (
        SendMessage,
        SendPhoto,
        SendDocument,
        SendVideo,
        SendAudio,
        SendMediaGroup,
        SendLocation,
        SendContact,
    )
}


def _file_descriptor(value: object) -> dict[str, str | None]:
    if isinstance(value, FSInputFile):
        return {"$local_file": str(value.path), "filename": value.filename}
    raise TypeError(type(value).__name__)


def encode(method: TelegramMethod[Any]) -> dict[str, Any]:
    try:
        params = method.model_dump(mode="json", exclude_defaults=True, fallback=_file_descriptor)
    except TypeError as exc:
        raise ValueError(str(exc)) from exc
    return {
        "version": 1,
        "method": method.__api_method__,
        "params": params,
    }


def decode(wire: dict[str, Any]) -> TelegramMethod[Any]:
    if wire["version"] != 1:
        raise ValueError(wire["version"])
    return METHODS[wire["method"]].model_validate(_restore(wire["params"]))


def _restore(value: Any) -> Any:
    if isinstance(value, dict):
        mapping = cast("dict[str, Any]", value)
        if set(mapping) == {"$local_file", "filename"}:
            return FSInputFile(mapping["$local_file"], filename=mapping["filename"])
        return {key: _restore(item) for key, item in mapping.items()}
    if isinstance(value, list):
        return [_restore(item) for item in cast("list[Any]", value)]
    return value
