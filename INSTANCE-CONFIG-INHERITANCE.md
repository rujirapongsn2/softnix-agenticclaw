# Instance Config Inheritance Behavior

## Overview

When creating a new Softnix instance, the configuration values need to come from somewhere. This document explains the config inheritance behavior.

## Config Source Priority

When you run `nanobot softnix-init --instance-id new-instance`, the config is loaded in this order:

### 1. **Explicit Source Config** (Highest Priority)
If you specify `--source-config`, that file is used:
```bash
nanobot softnix-init \
  --instance-id my-bot \
  --source-config /path/to/my-config.json
```

### 2. **default-prod Instance Config** (NEW - Default Behavior)
If no `--source-config` is provided AND `~/.softnix/instances/default-prod/config.json` exists, it will be used as the template:
```bash
# This will copy config from default-prod
nanobot softnix-init --instance-id new-instance --name "New" --owner user --env prod
```

### 3. **Schema Defaults** (Lowest Priority)
If neither of the above are available, an empty `Config()` object is used with schema defaults.

## Why Copy from default-prod?

### Problem Before
Previously, each new instance started with **empty schema defaults**, which meant:
- Different provider settings
- Different model configurations
- Different channel settings
- Manual re-configuration for every instance

### Solution After
Now, new instances **inherit from default-prod** by default:
- ✅ Same provider configuration (OpenAI, Anthropic, etc.)
- ✅ Same model settings
- ✅ Same channel configurations
- ✅ Same security settings
- ✅ Only instance-specific paths differ (workspace, etc.)

## Example

### Before the Change

```bash
# Create default-prod with OpenAI
nanobot softnix-init --instance-id default-prod --source-config ~/.nanobot/config.json

# Create new instance - gets EMPTY config
nanobot softnix-init --instance-id team-a-prod --name "Team A" --owner team-a
# ❌ team-a-prod has NO provider configured!
```

### After the Change

```bash
# Create default-prod with OpenAI
nanobot softnix-init --instance-id default-prod --source-config ~/.nanobot/config.json

# Create new instance - COPIES from default-prod
nanobot softnix-init --instance-id team-a-prod --name "Team A" --owner team-a
# ✅ team-a-prod has same OpenAI config as default-prod!
```

## Technical Implementation

The change is in `nanobot/admin/layout.py`:

```python
def _load_source_config(source_config: Path | None) -> Config:
    """Load source config, with fallback to default-prod instance if available."""
    # 1. If explicit source config provided, use it
    if source_config and source_config.expanduser().exists():
        return load_config(source_config.expanduser())
    
    # 2. Fallback: Try to load from default-prod instance
    default_prod_config = Path.home() / ".softnix" / "instances" / "default-prod" / "config.json"
    if default_prod_config.exists():
        try:
            return load_config(default_prod_config)
        except Exception:
            pass
    
    # 3. Return empty default config if no source available
    return Config()
```

## What Gets Copied

From `default-prod/config.json`:
- ✅ Provider settings (API keys, endpoints)
- ✅ Model configuration (default model, temperature, max tokens)
- ✅ Channel settings (Telegram, WhatsApp, Discord, etc.)
- ✅ Security settings (allowFrom lists, etc.)
- ✅ Tool configurations
- ✅ MCP server settings

What gets **re-generated** for the new instance:
- ✅ Workspace path (points to new instance's workspace)
- ✅ Gateway port (auto-assigned if not specified)
- ✅ Runtime settings (from sandbox profile)

## Customization

### Use a Different Source Instance

If you want to copy from a different instance:
```bash
nanobot softnix-init \
  --instance-id new-instance \
  --source-config ~/.softnix/instances/team-a-prod/config.json
```

### Start with Empty Config

If you want completely fresh config:
```bash
# Make sure default-prod doesn't exist
rm -rf ~/.softnix/instances/default-prod

# Now new instances will use schema defaults
nanobot softnix-init --instance-id fresh-instance ...
```

### Override Specific Settings

After creation, override what you need:
```bash
# Create instance (copies from default-prod)
nanobot softnix-init --instance-id my-instance ...

# Then edit config
nano ~/.softnix/instances/my-instance/config.json

# Or use Admin UI to modify providers, channels, etc.
```

## Migration Guide

### For Existing Installations

If you already have instances and want to leverage this:

```bash
# 1. Ensure default-prod has the config you want as template
nano ~/.softnix/instances/default-prod/config.json

# 2. Create new instances - they'll inherit from default-prod
nanobot softnix-init --instance-id new-bot ...

# 3. Existing instances are NOT affected
# They keep their current config
```

### For Fresh Installations

The installer v2 automatically handles this:

```bash
# Installer creates default-prod first
bash scripts/install_softnix_host_v2.sh -y

# Any additional instances will inherit from default-prod
nanobot softnix-init --instance-id team-bot ...
```

## Benefits

| Aspect | Before | After |
|--------|--------|-------|
| **Provider setup** | Manual per instance | Automatic inheritance |
| **Model config** | Reset to defaults | Consistent across instances |
| **Channel settings** | Re-configure each time | Copied automatically |
| **Security settings** | Easy to miss | Consistent policy |
| **Time to deploy** | 10+ minutes per instance | 1-2 minutes |
| **Error prone** | High (manual config) | Low (automated) |

## Best Practices

### 1. **Maintain default-prod as Golden Config**
```bash
# Keep default-prod as your "template" instance
# with all providers, channels, and settings configured
nano ~/.softnix/instances/default-prod/config.json
```

### 2. **Use Instance-Specific Overrides**
```bash
# For instances that need different settings
nanobot softnix-init --instance-id special-bot ...
# Then edit config for special requirements
```

### 3. **Document Your Template**
```bash
# Add comments to default-prod's instance.json
{
  "id": "default-prod",
  "description": "Template instance - changes here propagate to new instances"
}
```

### 4. **Version Control Your Config**
```bash
# Backup your default-prod config to git
cp ~/.softnix/instances/default-prod/config.json ~/backups/
```

## Troubleshooting

### New Instance Doesn't Have Provider Settings

**Check if default-prod exists:**
```bash
ls -la ~/.softnix/instances/default-prod/config.json
```

**Check default-prod has provider configured:**
```bash
cat ~/.softnix/instances/default-prod/config.json | grep -A5 '"providers"'
```

**Create default-prod if missing:**
```bash
nanobot softnix-init \
  --instance-id default-prod \
  --name "Default Production" \
  --owner default \
  --env prod \
  --source-config ~/.nanobot/config.json
```

### Want to Use Old Behavior (Empty Config)

**Option 1: Remove default-prod**
```bash
rm -rf ~/.softnix/instances/default-prod
```

**Option 2: Specify explicit empty source**
```bash
# Create a minimal config
echo '{}' > /tmp/empty-config.json

# Use it as source
nanobot softnix-init --instance-id fresh --source-config /tmp/empty-config.json
```

## See Also

- [README-Softnix.md](README-Softnix.md) - Instance management guide
- [INSTALLER-V2-IMPROVEMENTS.md](INSTALLER-V2-IMPROVEMENTS.md) - Installer improvements
- [SECURITY.md](SECURITY.md) - Security best practices
