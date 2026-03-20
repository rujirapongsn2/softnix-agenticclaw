"""Softnix admin filesystem layout helpers."""

from __future__ import annotations

import json
import shutil
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config
from nanobot.utils.helpers import ensure_dir, sync_workspace_templates


_INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{1,63}$")
_DEFAULT_GATEWAY_PORT = 18790
_DEFAULT_SANDBOX_IMAGE = "softnixclaw:latest"
_DEFAULT_SANDBOX_PROFILE = "balanced"
_SANDBOX_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "strict": {
        "mode": "sandbox",
        "sandbox": {
            "profile": "strict",
            "image": _DEFAULT_SANDBOX_IMAGE,
            "execution_strategy": "persistent",
            "cpu_limit": "1.0",
            "memory_limit": "1g",
            "pids_limit": 128,
            "tmpfs_size_mb": 64,
            "network_policy": "none",
            "timeout_seconds": 60,
        },
    },
    "balanced": {
        "mode": "sandbox",
        "sandbox": {
            "profile": "balanced",
            "image": _DEFAULT_SANDBOX_IMAGE,
            "execution_strategy": "persistent",
            "cpu_limit": "2.0",
            "memory_limit": "2g",
            "pids_limit": 256,
            "tmpfs_size_mb": 128,
            "network_policy": "default",
            "timeout_seconds": 90,
        },
    },
    "fast": {
        "mode": "host",
        "sandbox": {
            "profile": "fast",
            "image": _DEFAULT_SANDBOX_IMAGE,
            "execution_strategy": "tool_ephemeral",
            "cpu_limit": "",
            "memory_limit": "",
            "pids_limit": 512,
            "tmpfs_size_mb": 256,
            "network_policy": "default",
            "timeout_seconds": 180,
        },
    },
}


def get_softnix_home(base_dir: Path | None = None) -> Path:
    """Return the Softnix admin home directory."""
    return (base_dir or (Path.home() / ".softnix")).expanduser()


def get_softnix_admin_dir(base_dir: Path | None = None) -> Path:
    """Return the Softnix admin metadata directory."""
    return get_softnix_home(base_dir) / "admin"


def get_softnix_instances_dir(base_dir: Path | None = None) -> Path:
    """Return the Softnix instances directory."""
    return get_softnix_home(base_dir) / "instances"


def get_softnix_registry_path(base_dir: Path | None = None) -> Path:
    """Return the default Softnix instances registry path."""
    return get_softnix_admin_dir(base_dir) / "instances.json"


def load_instances_registry(registry_path: Path) -> dict[str, Any]:
    """Load the instances registry or return an empty structure."""
    if registry_path.exists():
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        data.setdefault("instances", [])
        return data
    return {"instances": []}


def save_instances_registry(registry_path: Path, registry: dict[str, Any]) -> None:
    """Persist the instances registry."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry.setdefault("instances", [])
    registry["instances"] = sorted(registry["instances"], key=lambda item: item.get("id") or "")
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")


def _validate_gateway_port(port: int) -> int:
    if not (1 <= int(port) <= 65535):
        raise ValueError("Gateway Port must be between 1 and 65535")
    return int(port)


def _extract_entry_gateway_port(entry: dict[str, Any]) -> int | None:
    config_path = Path(entry.get("config") or "").expanduser()
    if not config_path.exists():
        value = entry.get("gateway_port")
        return value if isinstance(value, int) else None
    try:
        config = load_config(config_path)
    except Exception:
        value = entry.get("gateway_port")
        return value if isinstance(value, int) else None
    return int(config.gateway.port)


def _normalize_runtime_mode(value: str | None, *, default: str = "sandbox") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in {"host", "sandbox"}:
        raise ValueError("Runtime Mode must be either 'host' or 'sandbox'")
    return normalized


def _normalize_network_policy(value: str | None, *, default: str = "default") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in {"default", "none"}:
        raise ValueError("Sandbox Network Policy must be either 'default' or 'none'")
    return normalized


def _normalize_sandbox_profile(value: str | None, *, default: str = _DEFAULT_SANDBOX_PROFILE) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in _SANDBOX_PROFILE_DEFAULTS:
        raise ValueError("Sandbox Profile must be 'strict', 'balanced', or 'fast'")
    return normalized


def _normalize_timeout_seconds(value: int | None, *, default: int = 30) -> int:
    timeout = int(value if value is not None else default)
    if timeout < 1:
        raise ValueError("Sandbox Timeout Seconds must be at least 1")
    return timeout


def _normalize_positive_int(value: int | None, *, default: int, field_label: str) -> int:
    normalized = int(value if value is not None else default)
    if normalized < 1:
        raise ValueError(f"{field_label} must be at least 1")
    return normalized


def _runtime_settings_from_config(config: Config) -> dict[str, Any]:
    sandbox = config.runtime.sandbox
    return {
        "mode": config.runtime.mode,
        "sandbox": {
            "profile": getattr(sandbox, "profile", _DEFAULT_SANDBOX_PROFILE) or _DEFAULT_SANDBOX_PROFILE,
            "image": sandbox.image or _DEFAULT_SANDBOX_IMAGE,
            "execution_strategy": sandbox.execution_strategy or "persistent",
            "cpu_limit": sandbox.cpu_limit or "",
            "memory_limit": sandbox.memory_limit or "",
            "pids_limit": int(sandbox.pids_limit),
            "tmpfs_size_mb": int(sandbox.tmpfs_size_mb),
            "network_policy": sandbox.network_policy,
            "timeout_seconds": int(sandbox.timeout_seconds),
        },
    }


def _apply_runtime_settings(
    config: Config,
    *,
    runtime_mode: str | None = None,
    sandbox_profile: str | None = None,
    sandbox_image: str | None = None,
    sandbox_execution_strategy: str | None = None,
    sandbox_cpu_limit: str | None = None,
    sandbox_memory_limit: str | None = None,
    sandbox_pids_limit: int | None = None,
    sandbox_tmpfs_size_mb: int | None = None,
    sandbox_network_policy: str | None = None,
    sandbox_timeout_seconds: int | None = None,
) -> dict[str, Any]:
    if sandbox_profile is not None:
        profile = _normalize_sandbox_profile(
            sandbox_profile,
            default=getattr(config.runtime.sandbox, "profile", _DEFAULT_SANDBOX_PROFILE),
        )
        defaults = _SANDBOX_PROFILE_DEFAULTS[profile]
        config.runtime.mode = defaults["mode"]
        config.runtime.sandbox.profile = profile
        config.runtime.sandbox.image = defaults["sandbox"]["image"]
        config.runtime.sandbox.execution_strategy = defaults["sandbox"]["execution_strategy"]
        config.runtime.sandbox.cpu_limit = defaults["sandbox"]["cpu_limit"]
        config.runtime.sandbox.memory_limit = defaults["sandbox"]["memory_limit"]
        config.runtime.sandbox.pids_limit = defaults["sandbox"]["pids_limit"]
        config.runtime.sandbox.tmpfs_size_mb = defaults["sandbox"]["tmpfs_size_mb"]
        config.runtime.sandbox.network_policy = defaults["sandbox"]["network_policy"]
        config.runtime.sandbox.timeout_seconds = defaults["sandbox"]["timeout_seconds"]
    if runtime_mode is not None:
        config.runtime.mode = _normalize_runtime_mode(runtime_mode, default=config.runtime.mode)
    if sandbox_image is not None:
        image = sandbox_image.strip()
        config.runtime.sandbox.image = image or _DEFAULT_SANDBOX_IMAGE
    if sandbox_execution_strategy is not None:
        strategy = sandbox_execution_strategy.strip().lower()
        if strategy not in {"persistent", "tool_ephemeral"}:
            raise ValueError("Sandbox Execution Strategy must be 'persistent' or 'tool_ephemeral'")
        config.runtime.sandbox.execution_strategy = strategy
    if sandbox_cpu_limit is not None:
        config.runtime.sandbox.cpu_limit = sandbox_cpu_limit.strip()
    if sandbox_memory_limit is not None:
        config.runtime.sandbox.memory_limit = sandbox_memory_limit.strip()
    if sandbox_pids_limit is not None:
        config.runtime.sandbox.pids_limit = _normalize_positive_int(
            sandbox_pids_limit,
            default=config.runtime.sandbox.pids_limit,
            field_label="Sandbox PIDs Limit",
        )
    if sandbox_tmpfs_size_mb is not None:
        config.runtime.sandbox.tmpfs_size_mb = _normalize_positive_int(
            sandbox_tmpfs_size_mb,
            default=config.runtime.sandbox.tmpfs_size_mb,
            field_label="Sandbox tmpfs Size (MB)",
        )
    if sandbox_network_policy is not None:
        config.runtime.sandbox.network_policy = _normalize_network_policy(
            sandbox_network_policy,
            default=config.runtime.sandbox.network_policy,
        )
    if sandbox_timeout_seconds is not None:
        config.runtime.sandbox.timeout_seconds = _normalize_timeout_seconds(
            sandbox_timeout_seconds,
            default=config.runtime.sandbox.timeout_seconds,
        )
    if not getattr(config.runtime.sandbox, "profile", "").strip():
        config.runtime.sandbox.profile = _DEFAULT_SANDBOX_PROFILE
    if not config.runtime.sandbox.image.strip():
        config.runtime.sandbox.image = _DEFAULT_SANDBOX_IMAGE
    return _runtime_settings_from_config(config)


def _instance_container_name(instance_id: str) -> str:
    return f"softnix-{instance_id}-gateway"


def _collect_registry_gateway_ports(
    registry: dict[str, Any], *, exclude_instance_id: str | None = None
) -> set[int]:
    used: set[int] = set()
    for item in registry.get("instances", []):
        if item.get("id") == exclude_instance_id:
            continue
        port = _extract_entry_gateway_port(item)
        if isinstance(port, int):
            used.add(port)
    return used


def _next_available_gateway_port(used_ports: set[int], start: int = _DEFAULT_GATEWAY_PORT) -> int:
    candidate = max(start, 1)
    while candidate in used_ports and candidate <= 65535:
        candidate += 1
    if candidate > 65535:
        raise ValueError("No available Gateway Port found in range 1-65535")
    return candidate


def _resolve_gateway_port(*, desired: int | None, used_ports: set[int], strict: bool) -> int:
    if desired is None:
        desired = _DEFAULT_GATEWAY_PORT
    port = _validate_gateway_port(desired)
    if port not in used_ports:
        return port
    if strict:
        raise ValueError(f"Gateway Port {port} is already used by another instance")
    return _next_available_gateway_port(used_ports, start=port + 1)


def infer_softnix_home_from_registry(registry_path: Path) -> Path:
    """Infer the Softnix home from the registry path."""
    return registry_path.expanduser().resolve().parent.parent


def build_instance_paths(instance_id: str, base_dir: Path | None = None) -> dict[str, Path]:
    """Compute canonical instance paths for one instance id."""
    normalized_id = instance_id.strip()
    home = get_softnix_home(base_dir)
    instance_home = get_softnix_instances_dir(home) / normalized_id
    return {
        "softnix_home": home,
        "instance_home": instance_home,
        "config_path": instance_home / "config.json",
        "workspace_path": instance_home / "workspace",
    }


def validate_softnix_instance_inputs(
    *,
    instance_id: str,
    name: str,
    owner: str,
    env: str,
    repo_root: Path,
    nanobot_bin: str,
    base_dir: Path | None = None,
    source_config: Path | None = None,
) -> dict[str, Path]:
    """Validate create-instance inputs against the Softnix layout rules."""
    normalized_id = instance_id.strip()
    if not normalized_id:
        raise ValueError("Instance ID must not be empty")
    if not _INSTANCE_ID_RE.fullmatch(normalized_id):
        raise ValueError("Instance ID must use lowercase letters, numbers, '-' or '_' only")
    if not name.strip():
        raise ValueError("Name must not be empty")
    if not owner.strip():
        raise ValueError("Owner must not be empty")
    if not env.strip():
        raise ValueError("Environment must not be empty")

    repo_root = repo_root.expanduser()
    if not repo_root.exists() or not repo_root.is_dir():
        raise ValueError(f"Repo Root does not exist or is not a directory: {repo_root}")

    if not nanobot_bin.strip():
        raise ValueError("nanobot Binary must not be empty")

    paths = build_instance_paths(normalized_id, base_dir)
    target_config_path = paths["config_path"].expanduser().resolve()
    target_instance_home = paths["instance_home"].expanduser().resolve()

    if source_config:
        source_config = source_config.expanduser()
        if not source_config.exists() or not source_config.is_file():
            raise ValueError(f"Source Config does not exist: {source_config}")
        source_resolved = source_config.resolve()
        if source_resolved == target_config_path:
            raise ValueError("Source Config must not be the same as the target config path")
        if target_instance_home in source_resolved.parents:
            raise ValueError("Source Config must not point inside the target instance directory")

    return paths


def bootstrap_softnix_instance(
    *,
    instance_id: str,
    name: str,
    owner: str,
    env: str,
    nanobot_bin: str,
    repo_root: Path,
    base_dir: Path | None = None,
    source_config: Path | None = None,
    gateway_port: int | None = None,
    runtime_mode: str | None = None,
    sandbox_profile: str | None = _DEFAULT_SANDBOX_PROFILE,
    sandbox_image: str | None = None,
    sandbox_execution_strategy: str | None = None,
    sandbox_cpu_limit: str | None = None,
    sandbox_memory_limit: str | None = None,
    sandbox_pids_limit: int | None = None,
    sandbox_tmpfs_size_mb: int | None = None,
    sandbox_network_policy: str | None = None,
    sandbox_timeout_seconds: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create one Softnix instance home and register it in the admin registry."""
    normalized_id = instance_id.strip()
    paths = validate_softnix_instance_inputs(
        instance_id=normalized_id,
        name=name,
        owner=owner,
        env=env,
        repo_root=repo_root,
        nanobot_bin=nanobot_bin,
        base_dir=base_dir,
        source_config=source_config,
    )

    home = paths["softnix_home"]
    admin_dir = ensure_dir(get_softnix_admin_dir(home))
    ensure_dir(admin_dir / "backups")
    ensure_dir(admin_dir / "audit")
    instances_dir = ensure_dir(get_softnix_instances_dir(home))
    instance_home = paths["instance_home"]

    if instance_home.exists() and not force:
        raise ValueError(f"Instance home already exists: {instance_home}")

    workspace_dir = ensure_dir(paths["workspace_path"])
    ensure_dir(workspace_dir / "sessions")
    ensure_dir(workspace_dir / "cron")
    sync_workspace_templates(workspace_dir, silent=True, agent_name=name, apply_identity=True)
    ensure_dir(instance_home / "run")
    ensure_dir(instance_home / "logs")
    scripts_dir = ensure_dir(instance_home / "scripts")

    registry_path = get_softnix_registry_path(home)
    registry = load_instances_registry(registry_path)
    used_ports = _collect_registry_gateway_ports(registry, exclude_instance_id=normalized_id)

    config = _load_source_config(source_config)
    config.agents.defaults.workspace = str(workspace_dir)
    runtime_settings = _apply_runtime_settings(
        config,
        runtime_mode=runtime_mode,
        sandbox_profile=sandbox_profile,
        sandbox_image=sandbox_image,
        sandbox_execution_strategy=sandbox_execution_strategy,
        sandbox_cpu_limit=sandbox_cpu_limit,
        sandbox_memory_limit=sandbox_memory_limit,
        sandbox_pids_limit=sandbox_pids_limit,
        sandbox_tmpfs_size_mb=sandbox_tmpfs_size_mb,
        sandbox_network_policy=sandbox_network_policy,
        sandbox_timeout_seconds=sandbox_timeout_seconds,
    )
    selected_gateway_port = _resolve_gateway_port(
        desired=gateway_port if gateway_port is not None else int(config.gateway.port),
        used_ports=used_ports,
        strict=gateway_port is not None,
    )
    config.gateway.port = selected_gateway_port
    config_path = paths["config_path"]
    save_config(config, config_path)

    metadata = {
        "id": normalized_id,
        "owner": owner,
        "env": env,
        "display_name": name,
        "description": f"Softnix-managed instance for {owner} ({env})",
        "created_at": datetime.now().astimezone().isoformat(),
        "tags": ["softnix", env],
        "ports": {
            "gateway": selected_gateway_port,
            "admin": None,
        },
        "runtime": runtime_settings,
    }
    (instance_home / "instance.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    script_paths = _write_lifecycle_scripts(
        scripts_dir=scripts_dir,
        instance_home=instance_home,
        nanobot_bin=nanobot_bin,
        repo_root=repo_root,
        gateway_port=selected_gateway_port,
        instance_id=normalized_id,
        runtime_settings=runtime_settings,
    )

    registry_entry = {
        "id": normalized_id,
        "name": name,
        "owner": owner,
        "env": env,
        "nanobot_bin": nanobot_bin,
        "instance_home": str(instance_home),
        "config": str(config_path),
        "workspace": str(workspace_dir),
        "cwd": str(repo_root),
        "gateway_port": selected_gateway_port,
        "runtime": runtime_settings,
        "lifecycle": {
            "start": [str(script_paths["start"])],
            "stop": [str(script_paths["stop"])],
            "restart": [str(script_paths["restart"])],
            "status": [str(script_paths["status"])],
        },
    }

    remaining = [item for item in registry["instances"] if item.get("id") != normalized_id]
    remaining.append(registry_entry)
    registry["instances"] = remaining
    save_instances_registry(registry_path, registry)

    return {
        "softnix_home": home,
        "registry_path": registry_path,
        "instance_home": instance_home,
        "config_path": config_path,
        "workspace_path": workspace_dir,
        "scripts": {name: str(path) for name, path in script_paths.items()},
        "registry_entry": registry_entry,
    }


def _load_source_config(source_config: Path | None) -> Config:
    """Load source config, with fallback to default-prod instance if available."""
    # If explicit source config provided, use it
    if source_config and source_config.expanduser().exists():
        return load_config(source_config.expanduser())
    
    # Fallback: Try to load from default-prod instance if it exists
    # This ensures new instances inherit the same provider/model settings
    default_prod_config = Path.home() / ".softnix" / "instances" / "default-prod" / "config.json"
    if default_prod_config.exists():
        try:
            return load_config(default_prod_config)
        except Exception:
            # If loading fails, fall through to empty Config()
            pass
    
    # Return empty default config if no source available
    return Config()


def update_softnix_instance(
    *,
    registry_path: Path,
    instance_id: str,
    name: str | None = None,
    owner: str | None = None,
    env: str | None = None,
    repo_root: Path | None = None,
    nanobot_bin: str | None = None,
    gateway_port: int | None = None,
    runtime_mode: str | None = None,
    sandbox_profile: str | None = None,
    sandbox_image: str | None = None,
    sandbox_execution_strategy: str | None = None,
    sandbox_cpu_limit: str | None = None,
    sandbox_memory_limit: str | None = None,
    sandbox_pids_limit: int | None = None,
    sandbox_tmpfs_size_mb: int | None = None,
    sandbox_network_policy: str | None = None,
    sandbox_timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Update one instance entry and its local metadata/scripts."""
    registry = load_instances_registry(registry_path)
    entry = next((item for item in registry["instances"] if item.get("id") == instance_id), None)
    if entry is None:
        raise ValueError(f"Unknown instance '{instance_id}'")

    if name is not None:
        entry["name"] = name.strip() or entry.get("name") or instance_id
    if owner is not None:
        entry["owner"] = owner.strip()
    if env is not None:
        entry["env"] = env.strip()
    if repo_root is not None:
        entry["cwd"] = str(repo_root.expanduser())
    if nanobot_bin is not None:
        entry["nanobot_bin"] = nanobot_bin.strip()
    if gateway_port is not None:
        used_ports = _collect_registry_gateway_ports(registry, exclude_instance_id=instance_id)
        entry["gateway_port"] = _resolve_gateway_port(
            desired=gateway_port,
            used_ports=used_ports,
            strict=True,
        )

    instance_home = Path(entry.get("instance_home") or "").expanduser()
    if instance_home.exists():
        config_path = Path(entry.get("config") or "").expanduser()
        selected_gateway_port = _extract_entry_gateway_port(entry) or _DEFAULT_GATEWAY_PORT
        if config_path.exists():
            config = load_config(config_path)
            if gateway_port is not None:
                config.gateway.port = _validate_gateway_port(entry["gateway_port"])
            runtime_settings = _apply_runtime_settings(
                config,
                runtime_mode=runtime_mode,
                sandbox_profile=sandbox_profile,
                sandbox_image=sandbox_image,
                sandbox_execution_strategy=sandbox_execution_strategy,
                sandbox_cpu_limit=sandbox_cpu_limit,
                sandbox_memory_limit=sandbox_memory_limit,
                sandbox_pids_limit=sandbox_pids_limit,
                sandbox_tmpfs_size_mb=sandbox_tmpfs_size_mb,
                sandbox_network_policy=sandbox_network_policy,
                sandbox_timeout_seconds=sandbox_timeout_seconds,
            )
            save_config(config, config_path)
            selected_gateway_port = int(config.gateway.port)
            entry["gateway_port"] = selected_gateway_port
            entry["runtime"] = runtime_settings
        else:
            runtime_settings = entry.get("runtime") if isinstance(entry.get("runtime"), dict) else {
                "mode": _normalize_runtime_mode(runtime_mode, default="host") if runtime_mode is not None else "host",
                "sandbox": {
                    "profile": _normalize_sandbox_profile(sandbox_profile, default=_DEFAULT_SANDBOX_PROFILE),
                    "image": (sandbox_image or _DEFAULT_SANDBOX_IMAGE).strip() or _DEFAULT_SANDBOX_IMAGE,
                    "execution_strategy": (sandbox_execution_strategy or "persistent").strip().lower() or "persistent",
                    "cpu_limit": (sandbox_cpu_limit or "").strip(),
                    "memory_limit": (sandbox_memory_limit or "").strip(),
                    "pids_limit": _normalize_positive_int(sandbox_pids_limit, default=256, field_label="Sandbox PIDs Limit"),
                    "tmpfs_size_mb": _normalize_positive_int(sandbox_tmpfs_size_mb, default=128, field_label="Sandbox tmpfs Size (MB)"),
                    "network_policy": _normalize_network_policy(sandbox_network_policy, default="default"),
                    "timeout_seconds": _normalize_timeout_seconds(sandbox_timeout_seconds, default=30),
                },
            }

        workspace_path = Path(entry.get("workspace") or instance_home / "workspace").expanduser()
        if workspace_path.exists():
            sync_workspace_templates(
                workspace_path,
                silent=True,
                agent_name=entry.get("name") or instance_id,
                apply_identity=True,
            )

        metadata_path = instance_home / "instance.json"
        metadata = {}
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        ports = metadata.get("ports") if isinstance(metadata.get("ports"), dict) else {}
        ports["gateway"] = selected_gateway_port
        ports.setdefault("admin", None)
        metadata.update(
            {
                "id": entry["id"],
                "owner": entry.get("owner"),
                "env": entry.get("env"),
                "display_name": entry.get("name"),
                "description": f"Softnix-managed instance for {entry.get('owner')} ({entry.get('env')})",
                "ports": ports,
                "runtime": runtime_settings,
            }
        )
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        script_paths = _write_lifecycle_scripts(
            scripts_dir=instance_home / "scripts",
            instance_home=instance_home,
            nanobot_bin=entry.get("nanobot_bin") or "/opt/anaconda3/bin/nanobot",
            repo_root=Path(entry.get("cwd") or ".").expanduser(),
            gateway_port=selected_gateway_port,
            instance_id=instance_id,
            runtime_settings=runtime_settings,
        )
        entry["lifecycle"] = {
            "start": [str(script_paths["start"])],
            "stop": [str(script_paths["stop"])],
            "restart": [str(script_paths["restart"])],
            "status": [str(script_paths["status"])],
        }

    save_instances_registry(registry_path, registry)
    return entry


def delete_softnix_instance(
    *,
    registry_path: Path,
    instance_id: str,
    purge_files: bool = False,
) -> dict[str, Any]:
    """Delete one instance from the registry, optionally purging its files."""
    registry = load_instances_registry(registry_path)
    entry = next((item for item in registry["instances"] if item.get("id") == instance_id), None)
    if entry is None:
        raise ValueError(f"Unknown instance '{instance_id}'")

    instance_home = Path(entry.get("instance_home") or "").expanduser()
    registry["instances"] = [item for item in registry["instances"] if item.get("id") != instance_id]
    save_instances_registry(registry_path, registry)

    if purge_files and instance_home.exists():
        shutil.rmtree(instance_home)

    return {
        "instance_id": instance_id,
        "purged_files": purge_files,
        "instance_home": str(instance_home) if instance_home else None,
    }


def _write_lifecycle_scripts(
    *,
    scripts_dir: Path,
    instance_home: Path,
    nanobot_bin: str,
    repo_root: Path,
    gateway_port: int,
    instance_id: str,
    runtime_settings: dict[str, Any],
) -> dict[str, Path]:
    pidfile = instance_home / "run" / "gateway.pid"
    cidfile = instance_home / "run" / "gateway.cid"
    log_file = instance_home / "logs" / "gateway.log"
    err_file = instance_home / "logs" / "gateway.err.log"
    config_path = instance_home / "config.json"
    workspace_path = instance_home / "workspace"
    container_name = _instance_container_name(instance_id)
    runtime_mode = runtime_settings.get("mode") or "host"
    sandbox = runtime_settings.get("sandbox") if isinstance(runtime_settings.get("sandbox"), dict) else {}
    sandbox_image = str(sandbox.get("image") or _DEFAULT_SANDBOX_IMAGE)
    sandbox_execution_strategy = str(sandbox.get("execution_strategy") or "persistent").strip().lower()
    sandbox_cpu_limit = str(sandbox.get("cpu_limit") or "")
    sandbox_memory_limit = str(sandbox.get("memory_limit") or "")
    sandbox_pids_limit = _normalize_positive_int(sandbox.get("pids_limit"), default=256, field_label="Sandbox PIDs Limit")
    sandbox_tmpfs_size_mb = _normalize_positive_int(sandbox.get("tmpfs_size_mb"), default=128, field_label="Sandbox tmpfs Size (MB)")
    sandbox_network_policy = _normalize_network_policy(sandbox.get("network_policy"), default="default")
    sandbox_timeout_seconds = _normalize_timeout_seconds(sandbox.get("timeout_seconds"), default=30)

    scripts = {
        "start": scripts_dir / "start.sh",
        "stop": scripts_dir / "stop.sh",
        "restart": scripts_dir / "restart.sh",
        "status": scripts_dir / "status.sh",
    }
    if runtime_mode == "sandbox":
        bodies = {
            "start": f"""#!/usr/bin/env bash
set -euo pipefail

CIDFILE="{cidfile}"
PIDFILE="{pidfile}"
INSTANCE_HOME="{instance_home}"
ADMIN_HOME="{instance_home.parent.parent / 'admin'}"
CONFIG="{config_path}"
WORKSPACE="{workspace_path}"
PORT="{gateway_port}"
IMAGE="{sandbox_image}"
EXECUTION_STRATEGY="{sandbox_execution_strategy}"
CPU_LIMIT="{sandbox_cpu_limit}"
MEMORY_LIMIT="{sandbox_memory_limit}"
NETWORK_POLICY="{sandbox_network_policy}"
DNS_SERVERS="${{SANDBOX_DNS_SERVERS:-1.1.1.1,8.8.8.8}}"
CONTAINER_NAME="{container_name}"
SANDBOX_HOME="$WORKSPACE/.sandbox-home"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found"
  exit 1
fi

# Clean up stale host-mode state before starting sandbox mode.
if [ -f "$PIDFILE" ]; then
  HOST_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$HOST_PID" ] && kill -0 "$HOST_PID" 2>/dev/null; then
    kill "$HOST_PID" 2>/dev/null || true
    sleep 1
    if kill -0 "$HOST_PID" 2>/dev/null; then
      kill -9 "$HOST_PID" 2>/dev/null || true
    fi
  fi
  rm -f "$PIDFILE"
fi

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  STATUS="$(docker inspect -f '{{{{.State.Status}}}}' "$CONTAINER_NAME" 2>/dev/null || echo unknown)"
  if [ "$STATUS" = "running" ]; then
    echo "$(docker inspect -f '{{{{.Id}}}}' "$CONTAINER_NAME")" > "$CIDFILE"
    echo "already running"
    exit 0
  fi
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

mkdir -p "$SANDBOX_HOME" "$SANDBOX_HOME/.cache/pip" "$(dirname "$CIDFILE")"

cmd=(
  docker run -d --init
  --name "$CONTAINER_NAME"
  --hostname "$CONTAINER_NAME"
  --user "$(id -u):$(id -g)"
  --cap-drop=ALL
  --security-opt no-new-privileges
  --read-only
  --tmpfs /tmp:rw,noexec,nosuid,size={sandbox_tmpfs_size_mb}m
  -e "HOME=$SANDBOX_HOME"
  -e "PIP_CACHE_DIR=$SANDBOX_HOME/.cache/pip"
  -e "PATH=/usr/local/bin:/usr/bin:/bin:$SANDBOX_HOME/.local/bin"
  -v "$INSTANCE_HOME:$INSTANCE_HOME"
  -v "${{ADMIN_HOME}}:${{ADMIN_HOME}}:ro"
  -w "$WORKSPACE"
  -p "$PORT:$PORT"
  --pids-limit "{sandbox_pids_limit}"
)

if [ -n "$CPU_LIMIT" ]; then
  cmd+=(--cpus "$CPU_LIMIT")
fi

if [ -n "$MEMORY_LIMIT" ]; then
  cmd+=(--memory "$MEMORY_LIMIT")
fi

if [ "$NETWORK_POLICY" = "none" ]; then
  cmd+=(--network none)
fi

if [ "$NETWORK_POLICY" != "none" ] && [ -n "$DNS_SERVERS" ]; then
  OLDIFS="$IFS"
  IFS=',' read -r -a dns_list <<< "$DNS_SERVERS"
  for dns in "${{dns_list[@]}}"; do
    dns="$(printf "%s" "$dns" | tr -d '[:space:]')"
    if [ -n "$dns" ]; then
      cmd+=(--dns "$dns")
    fi
  done
  IFS="$OLDIFS"
fi

cmd+=("$IMAGE" gateway --config "$CONFIG" --workspace "$WORKSPACE" -p "$PORT")
"${{cmd[@]}}" >/dev/null

docker inspect -f '{{{{.Id}}}}' "$CONTAINER_NAME" > "$CIDFILE"
echo "started"
""",
            "stop": f"""#!/usr/bin/env bash
set -euo pipefail

CIDFILE="{cidfile}"
PIDFILE="{pidfile}"
CONTAINER_NAME="{container_name}"
TIMEOUT="{sandbox_timeout_seconds}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found"
  exit 1
fi

if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  rm -f "$CIDFILE"
  echo "stopped"
  exit 0
fi

docker stop -t "$TIMEOUT" "$CONTAINER_NAME" >/dev/null
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
rm -f "$CIDFILE"

# Best-effort cleanup if stale host-mode pid is still present.
if [ -f "$PIDFILE" ]; then
  HOST_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$HOST_PID" ] && kill -0 "$HOST_PID" 2>/dev/null; then
    kill "$HOST_PID" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
fi

echo "stopped"
""",
            "restart": f"""#!/usr/bin/env bash
set -euo pipefail

"{scripts['stop']}" || true
"{scripts['start']}"
""",
            "status": f"""#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="{container_name}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found"
  exit 1
fi

if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "stopped"
  exit 1
fi

STATUS="$(docker inspect -f '{{{{.State.Status}}}}' "$CONTAINER_NAME" 2>/dev/null || echo unknown)"
if [ "$STATUS" = "running" ]; then
  echo "running"
  exit 0
fi

echo "$STATUS"
exit 1
""",
        }
    else:
        bodies = {
            "start": f"""#!/usr/bin/env bash
set -euo pipefail

PIDFILE="{pidfile}"
CIDFILE="{cidfile}"
LOGFILE="{log_file}"
ERRFILE="{err_file}"
CONFIG="{config_path}"
WORKSPACE="{workspace_path}"
REPO="{repo_root}"
NANOBOT="{nanobot_bin}"
PORT="{gateway_port}"
CONTAINER_NAME="{container_name}"

find_host_gateway_pid() {{
  local pid cmd candidate
  if [ -f "$PIDFILE" ]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *" gateway "* ]] && [[ "$cmd" == *"$CONFIG"* ]]; then
        printf '%s\\n' "$pid"
        return 0
      fi
    fi
  fi
  if command -v pgrep >/dev/null 2>&1; then
    while IFS= read -r candidate; do
      if ! [[ "$candidate" =~ ^[0-9]+$ ]]; then
        continue
      fi
      cmd="$(ps -p "$candidate" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *" gateway "* ]] && [[ "$cmd" == *"$CONFIG"* ]]; then
        printf '%s\\n' "$candidate" > "$PIDFILE"
        printf '%s\\n' "$candidate"
        return 0
      fi
    done < <(pgrep -f "nanobot gateway --config $CONFIG" || true)
  fi
  rm -f "$PIDFILE"
  return 1
}}

cleanup_stale_pid() {{
  find_host_gateway_pid >/dev/null 2>&1 || true
}}

is_host_gateway_running() {{
  find_host_gateway_pid >/dev/null 2>&1
}}

# Clean up stale sandbox-mode container/state before starting host mode.
if command -v docker >/dev/null 2>&1; then
  if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  fi
fi
rm -f "$CIDFILE"

cleanup_stale_pid
if is_host_gateway_running; then
  echo "already running"
  exit 0
fi

mkdir -p "$(dirname "$PIDFILE")" "$(dirname "$LOGFILE")" "$(dirname "$ERRFILE")"
cd "$REPO"
nohup "$NANOBOT" gateway --config "$CONFIG" --workspace "$WORKSPACE" -p "$PORT" >>"$LOGFILE" 2>>"$ERRFILE" &
echo $! > "$PIDFILE"

for _ in $(seq 1 20); do
  if is_host_gateway_running; then
    echo "started"
    exit 0
  fi
  sleep 0.5
done

cleanup_stale_pid
echo "gateway failed to start" >&2
tail -n 20 "$ERRFILE" 2>/dev/null >&2 || true
exit 1
""",
            "stop": f"""#!/usr/bin/env bash
set -euo pipefail

PIDFILE="{pidfile}"
CIDFILE="{cidfile}"
CONTAINER_NAME="{container_name}"
CONFIG="{config_path}"
NANOBOT="{nanobot_bin}"

find_host_gateway_pid() {{
  local pid cmd candidate
  if [ -f "$PIDFILE" ]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *" gateway "* ]] && [[ "$cmd" == *"$CONFIG"* ]]; then
        printf '%s\\n' "$pid"
        return 0
      fi
    fi
  fi
  if command -v pgrep >/dev/null 2>&1; then
    while IFS= read -r candidate; do
      if ! [[ "$candidate" =~ ^[0-9]+$ ]]; then
        continue
      fi
      cmd="$(ps -p "$candidate" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *" gateway "* ]] && [[ "$cmd" == *"$CONFIG"* ]]; then
        printf '%s\\n' "$candidate" > "$PIDFILE"
        printf '%s\\n' "$candidate"
        return 0
      fi
    done < <(pgrep -f "nanobot gateway --config $CONFIG" || true)
  fi
  rm -f "$PIDFILE"
  return 1
}}

cleanup_stale_pid() {{
  find_host_gateway_pid >/dev/null 2>&1 || true
}}

is_host_gateway_running() {{
  find_host_gateway_pid >/dev/null 2>&1
}}

cleanup_stale_pid
if [ ! -f "$PIDFILE" ]; then
  if command -v docker >/dev/null 2>&1; then
    if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
      docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "$CIDFILE"
  echo "stopped"
  exit 0
fi

PID="$(find_host_gateway_pid || true)"
if [ -z "$PID" ]; then
  PID="$(cat "$PIDFILE" 2>/dev/null || true)"
fi
if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in $(seq 1 20); do
    if ! kill -0 "$PID" 2>/dev/null; then
      rm -f "$PIDFILE"
      echo "stopped"
      exit 0
    fi
    sleep 1
  done
  echo "process did not stop cleanly"
  exit 1
fi

rm -f "$PIDFILE"
if command -v docker >/dev/null 2>&1; then
  if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  fi
fi
rm -f "$CIDFILE"
echo "stopped"
""",
            "restart": f"""#!/usr/bin/env bash
set -euo pipefail

"{scripts['stop']}" || true
"{scripts['start']}"
""",
            "status": f"""#!/usr/bin/env bash
set -euo pipefail

PIDFILE="{pidfile}"
CONFIG="{config_path}"
NANOBOT="{nanobot_bin}"

find_host_gateway_pid() {{
  local pid cmd candidate
  if [ -f "$PIDFILE" ]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *" gateway "* ]] && [[ "$cmd" == *"$CONFIG"* ]]; then
        printf '%s\\n' "$pid"
        return 0
      fi
    fi
  fi
  if command -v pgrep >/dev/null 2>&1; then
    while IFS= read -r candidate; do
      if ! [[ "$candidate" =~ ^[0-9]+$ ]]; then
        continue
      fi
      cmd="$(ps -p "$candidate" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *" gateway "* ]] && [[ "$cmd" == *"$CONFIG"* ]]; then
        printf '%s\\n' "$candidate" > "$PIDFILE"
        printf '%s\\n' "$candidate"
        return 0
      fi
    done < <(pgrep -f "nanobot gateway --config $CONFIG" || true)
  fi
  rm -f "$PIDFILE"
  return 1
}}

is_host_gateway_running() {{
  find_host_gateway_pid >/dev/null 2>&1
}}

if is_host_gateway_running; then
  echo "running"
  exit 0
fi

echo "stopped"
exit 1
""",
        }

    for name, path in scripts.items():
        path.write_text(bodies[name], encoding="utf-8")
        path.chmod(0o755)

    return scripts
