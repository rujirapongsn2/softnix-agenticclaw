# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## cron — Scheduled Reminders

- Please refer to cron skill for usage.

## message — Media Attachments

- `media` supports both local file paths and remote `http(s)` URLs.
- Use `message(media=[...])` when the user asks to see an image, hear audio, or open a generated artifact.
- If you mention a local media path directly in assistant text, Softnix Mobile may auto-attach it, but explicit `media=[...]` is more reliable.
- When the user asks for images "in the workspace", prefer original user/workspace artifacts first.
- Avoid defaulting to relay/cache copies under `mobile_relay/outbound_media`, `mobile_relay/uploads`, `.sandbox-home`, or `.nanobot` unless the user explicitly asked for those files.
- If multiple copies of the same image exist, prefer the most canonical source path rather than every generated relay copy.
