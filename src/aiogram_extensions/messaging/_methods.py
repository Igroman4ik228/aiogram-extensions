"""Convenience methods for submitting aiogram requests."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

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
from aiogram.types import InputFile, MediaUnion
from pydantic import ValidationError

from ._payload import SendMethod
from .errors import SerializationError
from .models import EnqueueResult


class SendMethods(ABC):
    @abstractmethod
    async def send(
        self, method: SendMethod, *, idempotency_key: str | None = None
    ) -> EnqueueResult: ...

    async def send_message(
        self, chat_id: int | str, text: str, *, idempotency_key: str | None = None, **kwargs: Any
    ) -> EnqueueResult:
        return await self._submit(
            SendMessage, chat_id, text=text, idempotency_key=idempotency_key, **kwargs
        )

    async def send_photo(
        self,
        chat_id: int | str,
        photo: str | InputFile,
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> EnqueueResult:
        return await self._submit(
            SendPhoto, chat_id, photo=photo, idempotency_key=idempotency_key, **kwargs
        )

    async def send_document(
        self,
        chat_id: int | str,
        document: str | InputFile,
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> EnqueueResult:
        return await self._submit(
            SendDocument, chat_id, document=document, idempotency_key=idempotency_key, **kwargs
        )

    async def send_video(
        self,
        chat_id: int | str,
        video: str | InputFile,
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> EnqueueResult:
        return await self._submit(
            SendVideo, chat_id, video=video, idempotency_key=idempotency_key, **kwargs
        )

    async def send_audio(
        self,
        chat_id: int | str,
        audio: str | InputFile,
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> EnqueueResult:
        return await self._submit(
            SendAudio, chat_id, audio=audio, idempotency_key=idempotency_key, **kwargs
        )

    async def send_media_group(
        self,
        chat_id: int | str,
        media: Sequence[MediaUnion],
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> EnqueueResult:
        return await self._submit(
            SendMediaGroup, chat_id, media=media, idempotency_key=idempotency_key, **kwargs
        )

    async def send_location(
        self,
        chat_id: int | str,
        latitude: float,
        longitude: float,
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> EnqueueResult:
        return await self._submit(
            SendLocation,
            chat_id,
            latitude=latitude,
            longitude=longitude,
            idempotency_key=idempotency_key,
            **kwargs,
        )

    async def send_contact(
        self,
        chat_id: int | str,
        phone_number: str,
        first_name: str,
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> EnqueueResult:
        return await self._submit(
            SendContact,
            chat_id,
            phone_number=phone_number,
            first_name=first_name,
            idempotency_key=idempotency_key,
            **kwargs,
        )

    async def _submit(
        self,
        method_type: type[SendMethod],
        chat_id: int | str,
        *,
        idempotency_key: str | None = None,
        **params: Any,
    ) -> EnqueueResult:
        try:
            method = method_type(chat_id=chat_id, **params)
        except ValidationError:
            raise SerializationError(
                f"Invalid parameters for {method_type.__api_method__}"
            ) from None
        return await self.send(method, idempotency_key=idempotency_key)
