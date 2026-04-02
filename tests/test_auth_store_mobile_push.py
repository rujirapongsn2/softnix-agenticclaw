from pathlib import Path

from nanobot.admin.auth_store import AdminAuthStore


def test_mobile_push_subscription_round_trip(tmp_path: Path) -> None:
    store = AdminAuthStore(tmp_path)

    record = store.upsert_mobile_push_subscription(
        instance_id="demo",
        device_id="mob-1",
        endpoint="https://push.example.test/subscription",
        subscription={
            "endpoint": "https://push.example.test/subscription",
            "keys": {
                "p256dh": "abc123",
                "auth": "def456",
            },
        },
        user_agent="Safari",
    )

    assert record["device_id"] == "mob-1"
    stored = store.list_mobile_push_subscriptions("demo", "mob-1")
    assert len(stored) == 1
    assert stored[0]["endpoint"] == "https://push.example.test/subscription"
    assert stored[0]["subscription"]["keys"]["p256dh"] == "abc123"

    assert store.delete_mobile_push_subscription(instance_id="demo", device_id="mob-1") is True
    assert store.list_mobile_push_subscriptions("demo", "mob-1") == []


def test_mobile_transfer_token_round_trip(tmp_path: Path) -> None:
    store = AdminAuthStore(tmp_path)
    store.create_mobile_transfer_token(
        token="xfer-123456",
        expires_at="2999-01-01T00:00:00+00:00",
        payload={
            "device": {"device_id": "mob-1", "instance_id": "demo"},
            "activeSessionId": "mobile-mob-1",
            "conversations": {"mobile-mob-1": {"messages": []}},
        },
    )

    payload = store.consume_mobile_transfer_token("xfer-123456")
    assert payload is not None
    assert payload["device"]["device_id"] == "mob-1"
    assert payload["activeSessionId"] == "mobile-mob-1"
    assert store.consume_mobile_transfer_token("xfer-123456") is None


def test_mobile_device_token_round_trip(tmp_path: Path) -> None:
    store = AdminAuthStore(tmp_path)
    store.upsert_mobile_device("demo", "mob-1", "Tester", device_token="mobtok-abc123")

    devices = store.list_mobile_devices("demo")
    assert len(devices) == 1
    assert "device_token" not in devices[0]
    assert devices[0]["device_id"] == "mob-1"

    matched = store.get_mobile_device_by_token("mobtok-abc123", instance_id="demo")
    assert matched is not None
    assert matched["device_id"] == "mob-1"


def test_web_chat_login_ticket_and_session_round_trip(tmp_path: Path) -> None:
    store = AdminAuthStore(tmp_path)
    store.create_web_chat_login_ticket(
        ticket="wclogin-demo",
        expires_at="2999-01-01T00:00:00+00:00",
        ip="127.0.0.1",
        user_agent="Firefox",
    )

    pending = store.get_web_chat_login_ticket("wclogin-demo")
    assert pending is not None
    assert pending["status"] == "pending"

    approved = store.approve_web_chat_login_ticket(
        ticket="wclogin-demo",
        instance_id="demo",
        device_id="mob-1",
        device_label="Tester",
        active_session_id="mobile-mob-1",
    )
    assert approved is not None
    assert approved["status"] == "approved"
    assert approved["device_id"] == "mob-1"

    consumed = store.consume_web_chat_login_ticket("wclogin-demo")
    assert consumed is not None
    assert consumed["status"] == "exchanged"
    assert store.consume_web_chat_login_ticket("wclogin-demo") is None

    session = store.create_web_chat_session(
        session_id="wc-session-1",
        instance_id="demo",
        device_id="mob-1",
        device_label="Tester",
        active_session_id="mobile-mob-1",
        csrf_token="csrf-demo",
    )
    assert session["device_id"] == "mob-1"
    fetched = store.get_web_chat_session("wc-session-1")
    assert fetched is not None
    assert fetched["csrf_token"] == "csrf-demo"
    touched = store.touch_web_chat_session("wc-session-1", active_session_id="mobile-mob-1#thread:alpha")
    assert touched is not None
    assert touched["active_session_id"] == "mobile-mob-1#thread:alpha"
    assert store.revoke_web_chat_sessions_for_device(instance_id="demo", device_id="mob-1") == 1
    assert store.get_web_chat_session("wc-session-1") is None


def test_clear_mobile_state_for_instance_removes_all_instance_scoped_records(tmp_path: Path) -> None:
    store = AdminAuthStore(tmp_path)
    store.create_pairing_token("demo", "pair-demo", "2999-01-01T00:00:00+00:00")
    store.create_pairing_token("other", "pair-other", "2999-01-01T00:00:00+00:00")
    store.upsert_mobile_device("demo", "mob-1", "Tester", device_token="mobtok-demo")
    store.upsert_mobile_device("other", "mob-2", "Other", device_token="mobtok-other")
    store.upsert_mobile_push_subscription(
        instance_id="demo",
        device_id="mob-1",
        endpoint="https://push.example.test/demo",
        subscription={"endpoint": "https://push.example.test/demo", "keys": {"p256dh": "demo", "auth": "demo"}},
    )
    store.upsert_mobile_push_subscription(
        instance_id="other",
        device_id="mob-2",
        endpoint="https://push.example.test/other",
        subscription={"endpoint": "https://push.example.test/other", "keys": {"p256dh": "other", "auth": "other"}},
    )
    store.create_mobile_transfer_token(
        token="xfer-demo",
        expires_at="2999-01-01T00:00:00+00:00",
        payload={"device": {"device_id": "mob-1", "instance_id": "demo"}},
    )
    store.create_mobile_transfer_token(
        token="xfer-other",
        expires_at="2999-01-01T00:00:00+00:00",
        payload={"device": {"device_id": "mob-2", "instance_id": "other"}},
    )
    store.create_web_chat_login_ticket(
        ticket="wclogin-demo",
        expires_at="2999-01-01T00:00:00+00:00",
    )
    store.approve_web_chat_login_ticket(
        ticket="wclogin-demo",
        instance_id="demo",
        device_id="mob-1",
    )
    store.create_web_chat_login_ticket(
        ticket="wclogin-other",
        expires_at="2999-01-01T00:00:00+00:00",
    )
    store.approve_web_chat_login_ticket(
        ticket="wclogin-other",
        instance_id="other",
        device_id="mob-2",
    )
    store.create_web_chat_session(
        session_id="wc-demo",
        instance_id="demo",
        device_id="mob-1",
        csrf_token="csrf-demo",
    )
    store.create_web_chat_session(
        session_id="wc-other",
        instance_id="other",
        device_id="mob-2",
        csrf_token="csrf-other",
    )

    result = store.clear_mobile_state_for_instance("demo")

    assert result == {
        "pairing_tokens_removed": 1,
        "devices_removed": 1,
        "push_subscriptions_removed": 1,
        "transfer_tokens_removed": 1,
        "web_chat_tickets_removed": 1,
        "web_chat_sessions_removed": 1,
    }
    assert store.list_mobile_devices("demo") == []
    assert store.list_mobile_devices("other")[0]["device_id"] == "mob-2"
    assert store.list_mobile_push_subscriptions("demo") == []
    assert store.list_mobile_push_subscriptions("other")[0]["device_id"] == "mob-2"
    assert store.consume_mobile_transfer_token("xfer-demo") is None
    assert store.consume_mobile_transfer_token("xfer-other") is not None
    assert store.get_web_chat_login_ticket("wclogin-demo") is None
    assert store.get_web_chat_login_ticket("wclogin-other") is not None
    assert store.get_web_chat_session("wc-demo") is None
    assert store.get_web_chat_session("wc-other") is not None
