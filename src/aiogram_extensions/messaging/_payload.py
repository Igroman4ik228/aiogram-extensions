"""Supported Telegram requests and snapshots for local admission."""

import json
from copy import deepcopy
from hashlib import sha256
from typing import Any, TypeAlias, cast

from aiogram.client.default import Default
from aiogram.methods import (
    SendAudio,
    SendContact,
    SendDocument,
    SendLocation,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    SendVideo,
)
from aiogram.types import BufferedInputFile, FSInputFile, URLInputFile

from .errors import SerializationError

SendMethod: TypeAlias = (
    SendMessage
    | SendPhoto
    | SendDocument
    | SendVideo
    | SendAudio
    | SendMediaGroup
    | SendLocation
    | SendContact
)
_METHODS = (
    SendMessage,
    SendPhoto,
    SendDocument,
    SendVideo,
    SendAudio,
    SendMediaGroup,
    SendLocation,
    SendContact,
)


def _escape_user_keys(value: Any) -> Any:
    # A single leading $ identifies a marker emitted by encode_special.
    if isinstance(value, dict):
        return {
            f"${key}" if isinstance(key, str) and key.startswith("$") else key: _escape_user_keys(
                item
            )
            for key, item in cast("dict[object, object]", value).items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _escape_user_keys(item) for item in cast("list[object] | tuple[object, ...]", value)
        ]
    return value


def snapshot(method: SendMethod, max_bytes: int) -> tuple[SendMethod, bytes]:
    """Copy a request and fingerprint its fields without reading external files."""
    if type(method) not in _METHODS:
        raise SerializationError("Unsupported Telegram method")

    buffered_bytes = 0

    def encode_special(value: object) -> object:
        nonlocal buffered_bytes

        if isinstance(value, Default):
            return {"$default": value.name}
        if isinstance(value, BufferedInputFile) and type(value) is BufferedInputFile:
            buffered_bytes += len(value.data)
            if buffered_bytes > max_bytes:
                raise SerializationError("Message payload exceeds max_payload_bytes")
            source = {"$buffer_sha256": sha256(value.data).hexdigest()}
        elif isinstance(value, FSInputFile) and type(value) is FSInputFile:
            source = {"$local_file": str(value.path)}
        elif isinstance(value, URLInputFile) and type(value) is URLInputFile and value.bot is None:
            source = {"$url_file": value.url, "headers": value.headers, "timeout": value.timeout}
        else:
            raise TypeError

        return {**source, "filename": value.filename, "chunk_size": value.chunk_size}

    try:
        params = method.model_dump(warnings="error")
        encoded = json.dumps(
            {"method": method.__api_method__, "params": _escape_user_keys(params)},
            default=encode_special,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) + buffered_bytes > max_bytes:
            raise SerializationError("Message payload exceeds max_payload_bytes")

        return type(method).model_validate(deepcopy(params)), sha256(encoded).digest()
    except (ValueError, TypeError, RecursionError):
        # Serializer errors may include the original input.
        raise SerializationError("Invalid or unsupported message payload") from None
