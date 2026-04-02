import json
from pathlib import Path

from nanobot.admin.service import AdminService
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.softnix_app import SoftnixAppChannel
from nanobot.session.manager import SessionManager


def _make_mobile_service(tmp_path: Path, *, allow_from: list[str] | None = None) -> tuple[AdminService, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"workspace": str(workspace)}},
                "channels": {"softnix_app": {"enabled": True, "allow_from": allow_from or ["mob-1"]}},
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
    return AdminService(registry_path=registry_path), workspace


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


def test_relay_mobile_message_rejects_oversized_attachment(tmp_path: Path) -> None:
    service, _workspace = _make_mobile_service(tmp_path)
    service.max_mobile_attachment_bytes = 4

    try:
        service.relay_mobile_message(
            "prod",
            "mob-1",
            "hello",
            attachments=[
                {
                    "name": "oversized.txt",
                    "type": "text/plain",
                    "data_base64": "aGVsbG8=",
                }
            ],
            accessible_instance_ids={"prod"},
        )
    except ValueError as exc:
        assert "maximum size" in str(exc)
    else:
        raise AssertionError("Expected oversized attachment to be rejected")


async def test_softnix_app_channel_preserves_progress_and_tool_messages(tmp_path: Path) -> None:
    class _Config:
        allow_from = ["*"]

    workspace = tmp_path / "workspace"
    channel = SoftnixAppChannel(_Config(), MessageBus(), workspace)

    await channel.send(
        OutboundMessage(
            channel="softnix_app",
            chat_id="mobile-mob-7",
            content="Thinking through the task",
            metadata={"sender_id": "mob-7", "_progress": True},
        )
    )
    await channel.send(
        OutboundMessage(
            channel="softnix_app",
            chat_id="mobile-mob-7",
            content="read_file(\"workspace/notes.txt\")",
            metadata={"sender_id": "mob-7", "_progress": True, "_tool_hint": True},
        )
    )

    outbound_path = workspace / "mobile_relay" / "outbound.jsonl"
    payloads = [json.loads(line) for line in outbound_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [item["type"] for item in payloads] == ["progress", "tool"]

    event_path = workspace / "mobile_relay" / "events" / "mob-7.jsonl"
    event_payloads = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [item["type"] for item in event_payloads] == ["progress", "tool"]


def test_web_chat_login_ticket_exchange_uses_mobile_device_context(tmp_path: Path) -> None:
    service, _workspace = _make_mobile_service(tmp_path)
    service.auth_store.upsert_mobile_device("prod", "mob-1", "Tester", device_token="mobtok-1")

    login = service.create_web_chat_login(ip="127.0.0.1", user_agent="pytest")
    status = service.get_web_chat_login_status(login_ticket=login["login_ticket"])
    assert status["status"] == "pending"

    approved = service.approve_web_chat_login(
        login_ticket=login["login_ticket"],
        device=service.auth_store.get_mobile_device("prod", "mob-1") or {},
        active_session_id="mobile-mob-1#thread:root-1",
        accessible_instance_ids={"prod"},
    )
    assert approved["device_id"] == "mob-1"

    exchanged = service.exchange_web_chat_login(
        login_ticket=login["login_ticket"],
        ip="127.0.0.1",
        user_agent="pytest",
    )
    assert exchanged["device"]["instance_id"] == "prod"
    assert exchanged["session"]["active_session_id"] == "mobile-mob-1#thread:root-1"

    context = service.get_authenticated_web_chat_session(session_id=exchanged["session"]["id"])
    assert context is not None
    assert context["device"]["device_id"] == "mob-1"
    assert context["session"]["csrf_token"]


def test_relay_web_chat_message_targets_same_device_session_scope(tmp_path: Path) -> None:
    service, workspace = _make_mobile_service(tmp_path)
    service.auth_store.upsert_mobile_device("prod", "mob-1", "Tester", device_token="mobtok-1")
    login = service.create_web_chat_login()
    service.approve_web_chat_login(
        login_ticket=login["login_ticket"],
        device=service.auth_store.get_mobile_device("prod", "mob-1") or {},
        active_session_id="mobile-mob-1-beta",
        accessible_instance_ids={"prod"},
    )
    exchanged = service.exchange_web_chat_login(login_ticket=login["login_ticket"])

    result = service.relay_web_chat_message(
        web_session_id=exchanged["session"]["id"],
        text="desktop hello",
        chat_session_id="mobile-mob-1-beta",
        message_id="msg-desktop-1",
    )

    assert result["session_id"] == "mobile-mob-1-beta"
    inbound_path = workspace / "mobile_relay" / "inbound.jsonl"
    payload = json.loads(inbound_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["sender_id"] == "mob-1"
    assert payload["session_id"] == "mobile-mob-1-beta"
    events = service.get_mobile_chat_events("prod", "mob-1", accessible_instance_ids={"prod"})
    assert any(event.get("text") == "desktop hello" for event in events)


def test_relay_web_chat_message_persists_attachment_payloads(tmp_path: Path) -> None:
    service, workspace = _make_mobile_service(tmp_path)
    service.auth_store.upsert_mobile_device("prod", "mob-1", "Tester", device_token="mobtok-1")
    login = service.create_web_chat_login()
    service.approve_web_chat_login(
        login_ticket=login["login_ticket"],
        device=service.auth_store.get_mobile_device("prod", "mob-1") or {},
        active_session_id="mobile-mob-1",
        accessible_instance_ids={"prod"},
    )
    exchanged = service.exchange_web_chat_login(login_ticket=login["login_ticket"])

    result = service.relay_web_chat_message(
        web_session_id=exchanged["session"]["id"],
        text="",
        chat_session_id="mobile-mob-1",
        message_id="msg-desktop-attachment-1",
        attachments=[
            {
                "name": "desktop-note.txt",
                "type": "text/plain",
                "data_base64": "aGVsbG8gd2Vi",
            }
        ],
    )

    assert result["attachment_count"] == 1
    assert result["attachments"][0]["url"].startswith("/admin/mobile/media?instance_id=prod")
    inbound_path = workspace / "mobile_relay" / "inbound.jsonl"
    payload = json.loads(inbound_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["sender_id"] == "mob-1"
    assert payload["attachments"][0]["name"] == "desktop-note.txt"
    saved_path = Path(payload["media"][0])
    assert saved_path.read_text(encoding="utf-8") == "hello web"


async def test_mobile_chat_bootstrap_uses_event_log_and_after_cursor(tmp_path: Path) -> None:
    class _Config:
        allow_from = ["*"]

    service, workspace = _make_mobile_service(tmp_path)
    service.auth_store.upsert_mobile_device("prod", "mob-1", "Tester", device_token="mobtok-1")
    service.relay_mobile_message(
        "prod",
        "mob-1",
        "hello from mobile",
        session_id="mobile-mob-1-alpha",
        message_id="msg-mobile-1",
        accessible_instance_ids={"prod"},
    )

    channel = SoftnixAppChannel(_Config(), MessageBus(), workspace)
    await channel.send(
        OutboundMessage(
            channel="softnix_app",
            chat_id="mobile-mob-1-alpha",
            content="hello from agent",
            metadata={"sender_id": "mob-1"},
        )
    )

    bootstrap = service.get_mobile_chat_bootstrap(
        "prod",
        "mob-1",
        preferred_active_session_id="mobile-mob-1-alpha",
        accessible_instance_ids={"prod"},
    )
    assert bootstrap["active_session_id"] == "mobile-mob-1-alpha"
    assert len(bootstrap["events"]) >= 2
    assert bootstrap["conversations"][0]["session_id"] == "mobile-mob-1-alpha"

    first_event_id = bootstrap["events"][0]["event_id"]
    tail = service.get_mobile_chat_events(
        "prod",
        "mob-1",
        after_event_id=first_event_id,
        accessible_instance_ids={"prod"},
    )
    assert tail
    assert all(event["event_id"] != first_event_id for event in tail)


async def test_mobile_chat_bootstrap_merges_outbound_progress_and_answers(tmp_path: Path) -> None:
    service, workspace = _make_mobile_service(tmp_path)
    service.auth_store.upsert_mobile_device("prod", "mob-1", "Tester", device_token="mobtok-1")

    manager = SessionManager(workspace)
    legacy_session = manager.get_or_create("mobile-mob-1-thread-1")
    legacy_session.add_message("user", "hello")
    legacy_session.add_message("assistant", "Done")
    manager.save(legacy_session)

    events_dir = workspace / "mobile_relay" / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "mob-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_id": "mobevt-user-1",
                        "instance_id": "prod",
                        "device_id": "mob-1",
                        "role": "user",
                        "direction": "inbound",
                        "type": "message",
                        "session_id": "mobile-mob-1-thread-1",
                        "message_id": "user-1",
                        "reply_to": None,
                        "thread_root_id": None,
                        "text": "hello",
                        "attachments": [],
                        "timestamp": "2026-04-02T03:00:00+00:00",
                    }
                ),
                json.dumps(
                    {
                        "event_id": "mobevt-progress-1",
                        "instance_id": "prod",
                        "device_id": "mob-1",
                        "role": "agent",
                        "direction": "outbound",
                        "type": "progress",
                        "session_id": "mobile-mob-1-thread-1",
                        "message_id": "mobr-progress-1",
                        "reply_to": "user-1",
                        "thread_root_id": "user-1",
                        "text": "Thinking through the task",
                        "attachments": [],
                        "timestamp": "2026-04-02T03:00:01+00:00",
                    }
                ),
                json.dumps(
                    {
                        "event_id": "mobevt-answer-1",
                        "instance_id": "prod",
                        "device_id": "mob-1",
                        "role": "agent",
                        "direction": "outbound",
                        "type": "answer",
                        "session_id": "mobile-mob-1-thread-1",
                        "message_id": "mobr-answer-1",
                        "reply_to": "user-1",
                        "thread_root_id": "user-1",
                        "text": "Done",
                        "attachments": [],
                        "timestamp": "2026-04-02T03:00:02+00:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (workspace / "mobile_relay" / "outbound.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "message_id": "mobr-progress-1",
                        "text": "Thinking through the task",
                        "type": "progress",
                        "sender_id": "mob-1",
                        "session_id": "mobile-mob-1-thread-1",
                        "reply_to": "user-1",
                        "thread_root_id": "user-1",
                        "attachments": [],
                        "timestamp": "2026-04-02T03:00:01+00:00",
                    }
                ),
                json.dumps(
                    {
                        "message_id": "mobr-answer-1",
                        "text": "Done",
                        "type": "answer",
                        "sender_id": "mob-1",
                        "session_id": "mobile-mob-1-thread-1",
                        "reply_to": "user-1",
                        "thread_root_id": "user-1",
                        "attachments": [],
                        "timestamp": "2026-04-02T03:00:02+00:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    bootstrap = service.get_mobile_chat_bootstrap(
        "prod",
        "mob-1",
        accessible_instance_ids={"prod"},
    )

    assert bootstrap["active_session_id"] == "mobile-mob-1-thread-1"
    assert [event["type"] for event in bootstrap["events"]] == ["message", "progress", "answer"]
    assert bootstrap["conversations"][0]["message_count"] == 3


async def test_mobile_chat_bootstrap_includes_bare_session_history(tmp_path: Path) -> None:
    service, workspace = _make_mobile_service(tmp_path)
    service.auth_store.upsert_mobile_device("prod", "mob-1", "Tester", device_token="mobtok-1")

    manager = SessionManager(workspace)
    session = manager.get_or_create("mobile-mob-1")
    session.add_message("user", "hello from session log")
    session.add_message("assistant", "reply from session log")
    manager.save(session)

    bootstrap = service.get_mobile_chat_bootstrap(
        "prod",
        "mob-1",
        accessible_instance_ids={"prod"},
    )

    assert bootstrap["active_session_id"] == "mobile-mob-1"
    assert any(event.get("role") == "agent" and event.get("text") == "reply from session log" for event in bootstrap["events"])


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
