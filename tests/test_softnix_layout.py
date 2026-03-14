import json
from pathlib import Path

import pytest

from nanobot.admin.layout import (
    bootstrap_softnix_instance,
    get_softnix_registry_path,
    update_softnix_instance,
)


def test_bootstrap_softnix_instance_creates_layout(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = bootstrap_softnix_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        repo_root=repo_root,
        base_dir=tmp_path / ".softnix",
    )

    instance_home = result["instance_home"]
    assert (instance_home / "config.json").exists()
    assert (instance_home / "instance.json").exists()
    assert (instance_home / "workspace" / "AGENTS.md").exists()
    assert (instance_home / "workspace" / "memory" / "MEMORY.md").exists()
    assert (instance_home / "workspace" / "skills" / "weather" / "SKILL.md").exists()
    soul = (instance_home / "workspace" / "SOUL.md").read_text(encoding="utf-8")
    heartbeat = (instance_home / "workspace" / "HEARTBEAT.md").read_text(encoding="utf-8")
    assert "I am Acme Production" in soul
    assert "your Acme Production agent" in heartbeat
    assert (instance_home / "scripts" / "start.sh").exists()
    assert (instance_home / "scripts" / "status.sh").exists()
    start_script = (instance_home / "scripts" / "start.sh").read_text(encoding="utf-8")
    assert 'PORT="18790"' in start_script
    assert 'IMAGE="softnixclaw:latest"' in start_script
    assert 'docker run -d --init' in start_script
    assert '--pids-limit "256"' in start_script
    assert '--tmpfs /tmp:rw,noexec,nosuid,size=128m' in start_script
    config = json.loads((instance_home / "config.json").read_text(encoding="utf-8"))
    assert config["runtime"]["mode"] == "sandbox"

    registry_path = get_softnix_registry_path(tmp_path / ".softnix")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["instances"][0]["id"] == "acme-prod"
    assert registry["instances"][0]["gateway_port"] == 18790
    assert registry["instances"][0]["runtime"]["mode"] == "sandbox"
    assert registry["instances"][0]["lifecycle"]["status"] == [
        str(instance_home / "scripts" / "status.sh")
    ]


def test_bootstrap_softnix_instance_reuses_registry_entry_with_force(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_dir = tmp_path / ".softnix"

    bootstrap_softnix_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        repo_root=repo_root,
        base_dir=base_dir,
    )
    result = bootstrap_softnix_instance(
        instance_id="acme-prod",
        name="Acme Production Updated",
        owner="acme",
        env="prod",
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        repo_root=repo_root,
        base_dir=base_dir,
        force=True,
    )

    registry = json.loads(result["registry_path"].read_text(encoding="utf-8"))
    assert len(registry["instances"]) == 1
    assert registry["instances"][0]["name"] == "Acme Production Updated"


def test_update_softnix_instance_syncs_workspace_identity(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_dir = tmp_path / ".softnix"

    result = bootstrap_softnix_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        repo_root=repo_root,
        base_dir=base_dir,
    )
    registry_path = result["registry_path"]
    update_softnix_instance(
        registry_path=registry_path,
        instance_id="acme-prod",
        name="Acme Prime",
    )

    soul = (result["instance_home"] / "workspace" / "SOUL.md").read_text(encoding="utf-8")
    user = (result["instance_home"] / "workspace" / "USER.md").read_text(encoding="utf-8")
    assert "I am Acme Prime" in soul
    assert "Acme Prime's behavior" in user


def test_update_softnix_instance_can_switch_to_host_runtime(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_dir = tmp_path / ".softnix"

    result = bootstrap_softnix_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        repo_root=repo_root,
        base_dir=base_dir,
    )
    registry_path = result["registry_path"]
    update_softnix_instance(
        registry_path=registry_path,
        instance_id="acme-prod",
        runtime_mode="host",
    )

    start_script = (result["instance_home"] / "scripts" / "start.sh").read_text(encoding="utf-8")
    status_script = (result["instance_home"] / "scripts" / "status.sh").read_text(encoding="utf-8")
    config = json.loads((result["instance_home"] / "config.json").read_text(encoding="utf-8"))
    assert 'nohup "$NANOBOT" gateway' in start_script
    assert 'pgrep -f "nanobot gateway --config $CONFIG"' in start_script
    assert 'cleanup_stale_pid() {' in start_script
    assert 'find_host_gateway_pid() {' in start_script
    assert 'is_host_gateway_running() {' in start_script
    assert 'gateway failed to start' in start_script
    assert 'find_host_gateway_pid() {' in status_script
    assert 'pgrep -f "nanobot gateway --config $CONFIG"' in status_script
    assert 'CIDFILE="' in start_script
    assert 'docker rm -f "$CONTAINER_NAME"' in start_script
    assert config["runtime"]["mode"] == "host"


def test_sandbox_start_script_cleans_stale_host_pid(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_dir = tmp_path / ".softnix"

    result = bootstrap_softnix_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        repo_root=repo_root,
        base_dir=base_dir,
    )
    start_script = (result["instance_home"] / "scripts" / "start.sh").read_text(encoding="utf-8")
    assert 'PIDFILE="' in start_script
    assert 'rm -f "$PIDFILE"' in start_script


def test_bootstrap_softnix_instance_applies_fast_sandbox_profile_defaults(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = bootstrap_softnix_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        repo_root=repo_root,
        base_dir=tmp_path / ".softnix",
        sandbox_profile="fast",
    )

    config = json.loads((result["instance_home"] / "config.json").read_text(encoding="utf-8"))
    assert config["runtime"]["mode"] == "host"
    assert config["runtime"]["sandbox"]["profile"] == "fast"
    assert config["runtime"]["sandbox"]["executionStrategy"] == "tool_ephemeral"
    assert config["runtime"]["sandbox"]["networkPolicy"] == "default"
    assert config["runtime"]["sandbox"]["timeoutSeconds"] == 180


def test_update_softnix_instance_applies_strict_profile_defaults(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_dir = tmp_path / ".softnix"

    result = bootstrap_softnix_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        repo_root=repo_root,
        base_dir=base_dir,
    )
    update_softnix_instance(
        registry_path=result["registry_path"],
        instance_id="acme-prod",
        sandbox_profile="strict",
    )

    config = json.loads((result["instance_home"] / "config.json").read_text(encoding="utf-8"))
    assert config["runtime"]["mode"] == "sandbox"
    assert config["runtime"]["sandbox"]["profile"] == "strict"
    assert config["runtime"]["sandbox"]["networkPolicy"] == "none"
    assert config["runtime"]["sandbox"]["pidsLimit"] == 128


def test_bootstrap_softnix_instance_auto_assigns_next_gateway_port(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_dir = tmp_path / ".softnix"

    first = bootstrap_softnix_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        repo_root=repo_root,
        base_dir=base_dir,
    )
    second = bootstrap_softnix_instance(
        instance_id="acme-uat",
        name="Acme UAT",
        owner="acme",
        env="uat",
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        repo_root=repo_root,
        base_dir=base_dir,
    )

    first_start = (first["instance_home"] / "scripts" / "start.sh").read_text(encoding="utf-8")
    second_start = (second["instance_home"] / "scripts" / "start.sh").read_text(encoding="utf-8")
    assert 'PORT="18790"' in first_start
    assert 'PORT="18791"' in second_start


def test_bootstrap_softnix_instance_rejects_source_config_equal_to_target(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_dir = tmp_path / ".softnix"
    target_config = base_dir / "instances" / "acme-prod" / "config.json"
    target_config.parent.mkdir(parents=True, exist_ok=True)
    target_config.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="same as the target config path"):
        bootstrap_softnix_instance(
            instance_id="acme-prod",
            name="Acme Production",
            owner="acme",
            env="prod",
            nanobot_bin="/opt/anaconda3/bin/nanobot",
            repo_root=repo_root,
            base_dir=base_dir,
            source_config=target_config,
            force=True,
        )


def test_bootstrap_softnix_instance_rejects_source_config_inside_target_home(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_dir = tmp_path / ".softnix"
    source_config = base_dir / "instances" / "acme-prod" / "templates" / "seed.json"
    source_config.parent.mkdir(parents=True, exist_ok=True)
    source_config.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the target instance directory"):
        bootstrap_softnix_instance(
            instance_id="acme-prod",
            name="Acme Production",
            owner="acme",
            env="prod",
            nanobot_bin="/opt/anaconda3/bin/nanobot",
            repo_root=repo_root,
            base_dir=base_dir,
            source_config=source_config,
        )
