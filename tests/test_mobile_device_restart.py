import json
from pathlib import Path

from nanobot.admin.service import AdminService


def test_delete_mobile_device_restarts_registry_instance(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    instance_home = tmp_path / "instance-home"
    scripts_dir = instance_home / "scripts"
    scripts_dir.mkdir(parents=True)
    marker = instance_home / "restart-marker.txt"
    restart_script = scripts_dir / "restart.sh"
    restart_script.write_text(
        "#!/bin/sh\n"
        f"echo restarted > '{marker}'\n",
        encoding="utf-8",
    )
    restart_script.chmod(0o755)

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "gateway": {"port": 18600},
                "agents": {"defaults": {"workspace": str(workspace), "model": "openai/gpt-4.1-mini", "provider": "auto"}},
                "runtime": {"mode": "host"},
                "channels": {"softnix_app": {"enabled": True, "allow_from": ["mobile-tester-01"]}},
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
                        "instance_home": str(instance_home),
                        "lifecycle": {
                            "restart": [str(restart_script)],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    service = AdminService(registry_path=registry_path)
    service.auth_store.upsert_mobile_device("prod", "mobile-tester-01", "Tester")

    result = service.delete_mobile_device("prod", "mobile-tester-01")

    updated_config = json.loads(config_path.read_text(encoding="utf-8"))
    softnix_cfg = updated_config["channels"].get("softnix_app") or updated_config["channels"].get("softnixApp")
    assert softnix_cfg is not None
    assert softnix_cfg.get("allow_from") == [] or softnix_cfg.get("allowFrom") == []
    assert marker.exists()
    assert result["instance_restart"]["attempted"] is True
    assert result["instance_restart"]["ok"] is True
