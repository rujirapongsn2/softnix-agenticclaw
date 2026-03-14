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
