---
name: gmail-connector
description: "Use the Gmail connector tools for inbox search, message inspection, thread reading, label discovery, draft creation, and email sending tasks."
metadata: {"nanobot":{"emoji":"📧","always":true}}
---

# Gmail Connector

Use the Gmail connector when the task is about reading Gmail, searching messages, inspecting threads and labels, drafting email, or sending email.

## Usage Rules

- Runtime tool names are prefixed as `mcp_gmail_*`; use those tool names when calling tools.
- If the user does not specify a mailbox or user ID, first check `mcp_gmail_get_connector_context` to see the configured `effective_user_id`.
- Prefer the Gmail connector tools over ad-hoc web scraping when they are available.
- Summarize Gmail data directly and clearly. Do not paste raw JSON unless the user asks for it.
- If the task is about email, inbox, unread mail, attachments, sender, subject, label, thread context, drafting, or sending, use Gmail first and do not route it to GitHub or Notion.
- Use `mcp_gmail_create_draft` when the user wants a draft or review step before sending.
- Use `mcp_gmail_send_message` only when the user explicitly wants the email sent now.
- Draft and send require a Gmail write scope such as `gmail.compose` or `gmail.send`; a read-only token is not enough.
- For long-running instances, prefer a refreshable OAuth setup. If the connector has refresh credentials, it can recover automatically from an expired access token.
- If the user asks to send mail and the token lacks permission, report the validation failure plainly.

## Common Patterns

- `mcp_gmail_get_connector_context` before mailbox-omitted requests
- `mcp_gmail_whoami` for token validation
- `mcp_gmail_list_messages` for inbox search and query-based retrieval
- `mcp_gmail_get_message` for full message inspection
- `mcp_gmail_get_thread` for conversation context
- `mcp_gmail_list_labels` for mailbox label discovery
- `mcp_gmail_create_draft` for composing a draft email
- `mcp_gmail_send_message` for immediate mail delivery

## Mailbox Selection

- If the user does not specify a mailbox, prefer the connector's `effective_user_id`.
- If no user ID is configured, Gmail defaults to `me`.
- Use Gmail query syntax in `mcp_gmail_list_messages` for filters such as `from:`, `is:unread`, `has:attachment`, and `older_than:`.

## Safety

- Avoid write actions unless the user explicitly asks for them.
- Treat token-backed mailbox access as sensitive.
- If the mailbox is inaccessible or the token lacks permission, say that explicitly instead of guessing.
