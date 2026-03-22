import json
from pathlib import Path

from nanobot.admin.service import AdminService
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.softnix_app import SoftnixAppChannel


def test_relay_mobile_message_persists_thread_and_attachments(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"workspace": str(workspace)}},
                "channels": {"softnix_app": {"enabled": True, "allow_from": ["mob-1"]}},
            }
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "instances.json"
    registry_path.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "id": "prod",
                        "name": "Production",
                        "config": str(config_path),
                        "workspace": str(workspace),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    service = AdminService(registry_path=registry_path)
    result = service.relay_mobile_message(
        "prod",
        "mob-1",
        "hello with file",
        session_id="mobile-mob-1#thread:root-1",
        message_id="msg-1",
        reply_to="root-1",
        thread_root_id="root-1",
        attachments=[
            {
                "name": "note.txt",
                "type": "text/plain",
                "data_base64": "aGVsbG8gd29ybGQ=",
            }
        ],
    )

    assert result["message_id"] == "msg-1"
    assert result["attachments"][0]["url"].startswith("/admin/mobile/media?instance_id=prod")
    inbound_path = workspace / "mobile_relay" / "inbound.jsonl"
    payload = json.loads(inbound_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["session_id"] == "mobile-mob-1#thread:root-1"
    assert payload["reply_to"] == "root-1"
    assert payload["thread_root_id"] == "root-1"
    assert len(payload["media"]) == 1
    saved_path = Path(payload["media"][0])
    assert saved_path.exists()
    assert saved_path.read_text(encoding="utf-8") == "hello world"


async def test_softnix_app_channel_relays_attachment_metadata(tmp_path: Path) -> None:
    class _Config:
        allow_from = ["*"]

    workspace = tmp_path / "workspace"
    media_file = tmp_path / "image.png"
    media_file.write_bytes(b"png-bytes")
    channel = SoftnixAppChannel(_Config(), MessageBus(), workspace)

    await channel.send(
        OutboundMessage(
            channel="softnix_app",
            chat_id="mobile-mob-9#thread:root-9",
            content="reply",
            metadata={
                "sender_id": "mob-9",
                "message_id": "msg-9",
                "reply_to": "msg-9",
                "thread_root_id": "root-9",
            },
            media=[str(media_file)],
        )
    )

    outbound_path = workspace / "mobile_relay" / "outbound.jsonl"
    payload = json.loads(outbound_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["sender_id"] == "mob-9"
    assert payload["reply_to"] == "msg-9"
    assert payload["thread_root_id"] == "root-9"
    assert payload["attachments"][0]["name"] == "image.png"
    copied_name = payload["attachments"][0]["file_name"]
    assert (workspace / "mobile_relay" / "outbound_media" / "mob-9" / copied_name).exists()


async def test_softnix_app_channel_extracts_inline_audio_path_from_content(tmp_path: Path) -> None:
    class _Config:
        allow_from = ["*"]

    workspace = tmp_path / "workspace"
    audio_source = workspace / "skills" / "read-aloud"
    audio_source.mkdir(parents=True, exist_ok=True)
    audio_file = audio_source / "out.mp3"
    audio_file.write_bytes(b"mp3-bytes")
    channel = SoftnixAppChannel(_Config(), MessageBus(), workspace)

    await channel.send(
        OutboundMessage(
            channel="softnix_app",
            chat_id="mobile-mob-10#thread:root-10",
            content="ไฟล์เสียงสร้างเสร็จแล้วที่ workspace/skills/read-aloud/out.mp3 — กดเล่นได้เลย",
            metadata={
                "sender_id": "mob-10",
                "message_id": "msg-10",
                "reply_to": "msg-10",
                "thread_root_id": "root-10",
            },
        )
    )

    outbound_path = workspace / "mobile_relay" / "outbound.jsonl"
    payload = json.loads(outbound_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["attachments"][0]["kind"] == "audio"
    assert payload["attachments"][0]["mime_type"] == "audio/mpeg"
    copied_name = payload["attachments"][0]["file_name"]
    assert (workspace / "mobile_relay" / "outbound_media" / "mob-10" / copied_name).exists()


async def test_softnix_app_channel_extracts_inline_image_path_from_content(tmp_path: Path) -> None:
    class _Config:
        allow_from = ["*"]

    workspace = tmp_path / "workspace"
    image_source = workspace / "artifacts"
    image_source.mkdir(parents=True, exist_ok=True)
    image_file = image_source / "poster.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    channel = SoftnixAppChannel(_Config(), MessageBus(), workspace)

    await channel.send(
        OutboundMessage(
            channel="softnix_app",
            chat_id="mobile-mob-11",
            content="สร้างรูปเสร็จแล้วที่ workspace/artifacts/poster.png เปิดดูได้เลย",
            metadata={"sender_id": "mob-11"},
        )
    )

    outbound_path = workspace / "mobile_relay" / "outbound.jsonl"
    payload = json.loads(outbound_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["attachments"][0]["kind"] == "image"
    assert payload["attachments"][0]["mime_type"] == "image/png"
    copied_name = payload["attachments"][0]["file_name"]
    assert (workspace / "mobile_relay" / "outbound_media" / "mob-11" / copied_name).exists()


async def test_softnix_app_channel_resolves_workspace_relative_skill_media_path(tmp_path: Path) -> None:
    class _Config:
        allow_from = ["*"]

    workspace = tmp_path / "workspace"
    image_source = workspace / "skills" / "image-create-banana"
    image_source.mkdir(parents=True, exist_ok=True)
    image_file = image_source / "tmp_fal_image.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    channel = SoftnixAppChannel(_Config(), MessageBus(), workspace)

    await channel.send(
        OutboundMessage(
            channel="softnix_app",
            chat_id="mobile-mob-13",
            content="สร้างรูปเสร็จแล้ว",
            metadata={"sender_id": "mob-13"},
            media=["skills/image-create-banana/tmp_fal_image.png"],
        )
    )

    outbound_path = workspace / "mobile_relay" / "outbound.jsonl"
    payload = json.loads(outbound_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["attachments"][0]["kind"] == "image"
    assert payload["attachments"][0]["mime_type"] == "image/png"
    copied_name = payload["attachments"][0]["file_name"]
    assert (workspace / "mobile_relay" / "outbound_media" / "mob-13" / copied_name).exists()


async def test_softnix_app_channel_uses_parent_instance_id_for_workspace_media_urls(tmp_path: Path) -> None:
    class _Config:
        allow_from = ["*"]

    instance_root = tmp_path / "bigbike01-prod"
    workspace = instance_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (instance_root / "config.json").write_text("{}", encoding="utf-8")
    image_source = workspace / "skills" / "image-create-banana"
    image_source.mkdir(parents=True, exist_ok=True)
    image_file = image_source / "tmp_fal_image.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    channel = SoftnixAppChannel(_Config(), MessageBus(), workspace)

    await channel.send(
        OutboundMessage(
            channel="softnix_app",
            chat_id="mobile-mob-14",
            content="สร้างรูปเสร็จแล้ว",
            metadata={"sender_id": "mob-14"},
            media=["skills/image-create-banana/tmp_fal_image.png"],
        )
    )

    outbound_path = workspace / "mobile_relay" / "outbound.jsonl"
    payload = json.loads(outbound_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["attachments"][0]["url"].startswith("/admin/mobile/media?instance_id=bigbike01-prod")


async def test_softnix_app_channel_supports_remote_image_urls(tmp_path: Path) -> None:
    class _Config:
        allow_from = ["*"]

    workspace = tmp_path / "workspace"
    channel = SoftnixAppChannel(_Config(), MessageBus(), workspace)

    await channel.send(
        OutboundMessage(
            channel="softnix_app",
            chat_id="mobile-mob-12",
            content="นี่คือรูปจากอินเทอร์เน็ต https://example.com/images/chart.png",
            metadata={"sender_id": "mob-12"},
        )
    )

    outbound_path = workspace / "mobile_relay" / "outbound.jsonl"
    payload = json.loads(outbound_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["attachments"][0]["kind"] == "image"
    assert payload["attachments"][0]["mime_type"] == "image/png"
    assert payload["attachments"][0]["url"] == "https://example.com/images/chart.png"
    assert not (workspace / "mobile_relay" / "outbound_media" / "mob-12").exists()


async def test_softnix_app_channel_extracts_sender_id_from_mobile_session_id(tmp_path: Path) -> None:
    class _Config:
        allow_from = ["*"]

    workspace = tmp_path / "workspace"
    channel = SoftnixAppChannel(_Config(), MessageBus(), workspace)

    await channel.send(
        OutboundMessage(
            channel="softnix_app",
            chat_id="mobile-mob-a7d516b7-e5f0-4bb6-b5dc-136d876c0c3f-9d284171-c296-4f23-9b97-c82a0d85f156",
            content="scheduled reply",
            metadata={},
        )
    )

    outbound_path = workspace / "mobile_relay" / "outbound.jsonl"
    payload = json.loads(outbound_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["sender_id"] == "mob-a7d516b7-e5f0-4bb6-b5dc-136d876c0c3f"
    assert payload["session_id"] == "mobile-mob-a7d516b7-e5f0-4bb6-b5dc-136d876c0c3f-9d284171-c296-4f23-9b97-c82a0d85f156"


async def test_softnix_app_channel_processes_threaded_inbound_message(tmp_path: Path) -> None:
    class _Config:
        allow_from = ["mob-2"]

    workspace = tmp_path / "workspace"
    bus = MessageBus()
    channel = SoftnixAppChannel(_Config(), bus, workspace)
    channel.inbound_file.write_text(
        json.dumps(
            {
                "sender_id": "mob-2",
                "session_id": "mobile-mob-2#thread:root-2",
                "message_id": "msg-2",
                "reply_to": "root-2",
                "thread_root_id": "root-2",
                "text": "threaded hello",
                "media": [],
            }
        ) + "\n",
        encoding="utf-8",
    )

    await channel._process_inbound()

    inbound = await bus.consume_inbound()
    assert inbound.sender_id == "mob-2"
    assert inbound.chat_id == "mobile-mob-2#thread:root-2"
    assert inbound.session_key == "mobile-mob-2#thread:root-2"
    assert inbound.metadata["message_id"] == "msg-2"


def test_detect_audio_mime_identifies_ogg_and_mp3(tmp_path: Path) -> None:
    ogg_file = tmp_path / "audio.mp3"
    ogg_file.write_bytes(b"OggS" + b"\x00" * 32)
    assert AdminService._detect_audio_mime(ogg_file) == "audio/ogg"

    mp3_file = tmp_path / "real.mp3"
    mp3_file.write_bytes(b"ID3" + b"\x00" * 33)
    assert AdminService._detect_audio_mime(mp3_file) == "audio/mpeg"

    wav_file = tmp_path / "audio.wav"
    wav_file.write_bytes(b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 24)
    assert AdminService._detect_audio_mime(wav_file) == "audio/wav"
