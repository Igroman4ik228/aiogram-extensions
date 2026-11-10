"""Check the experimental wire format at its producer boundary."""

import json
from pathlib import Path

import pytest
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import BufferedInputFile, FSInputFile
from payload import decode, encode


def test_message_payload_is_json_without_live_bot_or_defaults() -> None:
    wire = encode(SendMessage(chat_id=42, text="hello"))

    assert json.loads(json.dumps(wire)) == {
        "version": 1,
        "method": "sendMessage",
        "params": {"chat_id": 42, "text": "hello"},
    }


def test_local_file_is_a_descriptor_not_file_bytes(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"photo contents")

    wire = encode(SendPhoto(chat_id=42, photo=FSInputFile(path)))

    assert json.loads(json.dumps(wire))["params"]["photo"] == {
        "$local_file": str(path),
        "filename": "photo.jpg",
    }
    assert "photo contents" not in json.dumps(wire)


def test_worker_reconstructs_method_and_shared_file(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    restored = decode(
        {
            "version": 1,
            "method": "sendPhoto",
            "params": {"chat_id": 42, "photo": {"$local_file": str(path), "filename": "x.jpg"}},
        }
    )

    assert isinstance(restored, SendPhoto)
    assert restored.chat_id == 42
    assert isinstance(restored.photo, FSInputFile)
    assert str(restored.photo.path) == str(path)
    assert restored.photo.filename == "x.jpg"


def test_buffered_file_is_rejected_before_enqueue() -> None:
    with pytest.raises(ValueError, match="BufferedInputFile"):
        encode(SendPhoto(chat_id=42, photo=BufferedInputFile(b"secret", filename="x.jpg")))


def test_unknown_schema_is_rejected_by_worker() -> None:
    with pytest.raises(ValueError, match="99"):
        decode({"version": 99, "method": "sendMessage", "params": {"chat_id": 42, "text": "x"}})
