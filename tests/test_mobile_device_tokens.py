import json
from pathlib import Path

from nanobot.admin.service import AdminService


def test_mobile_registration_issues_device_token_and_masks_it_from_listing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"workspace": str(workspace)}},
                "channels": {"softnix_app": {"enabled": False, "allow_from": []}},
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
    pairing = service.get_mobile_pairing_data("prod")

    result = service.register_mobile_client("prod", "mob-1", pairing.get("pairing_token"), "Tester")

    assert result["device_token"].startswith("mobtok-")
    devices = service.list_mobile_devices("prod")
    assert len(devices) == 1
    assert devices[0]["device_id"] == "mob-1"
    assert "device_token" not in devices[0]
