"""Gmail MCP server for the built-in Gmail connector preset."""

from __future__ import annotations

from base64 import urlsafe_b64encode
from email.message import EmailMessage
from email.utils import format_datetime
from email.policy import SMTP
from datetime import datetime, timezone
import os
from dataclasses import dataclass
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

GMAIL_API_BASE_DEFAULT = "https://gmail.googleapis.com/gmail/v1"
GMAIL_USER_ID_DEFAULT = "me"
GMAIL_USER_AGENT = "nanobot-gmail-connector/1.0"
GMAIL_WRITE_SCOPES = {
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
}


@dataclass(frozen=True)
class GmailClient:
    """Small Gmail REST API client used by the MCP server and validation flow."""

    token: str
    api_base: str = GMAIL_API_BASE_DEFAULT
    user_id: str = GMAIL_USER_ID_DEFAULT
    transport: httpx.BaseTransport | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.api_base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": GMAIL_USER_AGENT,
            },
            timeout=20.0,
            transport=self.transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Any:
        if not self.token:
            raise ValueError("Gmail access token is required")
        with self._client() as client:
            response = client.request(method, path, params=params, json=json_data)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = _extract_response_detail(response)
                if detail:
                    raise httpx.HTTPStatusError(
                        f"{exc}: {detail}",
                        request=exc.request,
                        response=exc.response,
                    ) from exc
                raise
            if not response.content:
                return {}
            return response.json()

    def _resolve_user_id(self, user_id: str | None = None) -> str:
        resolved = str(user_id or self.user_id or GMAIL_USER_ID_DEFAULT).strip()
        return resolved or GMAIL_USER_ID_DEFAULT

    def whoami(self) -> dict[str, Any]:
        return self._request("GET", f"/users/{self._resolve_user_id()}/profile")

    def list_messages(
        self,
        query: str = "",
        *,
        label_ids: list[str] | None = None,
        max_results: int = 10,
        page_token: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "q": str(query or "").strip(),
            "maxResults": int(max_results),
        }
        if label_ids:
            params["labelIds"] = [str(item).strip() for item in label_ids if str(item or "").strip()]
        if page_token:
            params["pageToken"] = str(page_token).strip()
        return self._request("GET", f"/users/{self._resolve_user_id(user_id)}/messages", params=params)

    def get_message(self, message_id: str, *, format: str = "full", user_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/users/{self._resolve_user_id(user_id)}/messages/{str(message_id).strip()}",
            params={"format": format},
        )

    def get_thread(self, thread_id: str, *, format: str = "full", user_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/users/{self._resolve_user_id(user_id)}/threads/{str(thread_id).strip()}",
            params={"format": format},
        )

    def list_labels(self, user_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/users/{self._resolve_user_id(user_id)}/labels")

    def token_scopes(self) -> set[str]:
        with httpx.Client(
            timeout=20.0,
            transport=self.transport,
            headers={"Accept": "application/json", "User-Agent": GMAIL_USER_AGENT},
        ) as client:
            response = client.get("https://oauth2.googleapis.com/tokeninfo", params={"access_token": self.token})
            response.raise_for_status()
            data = response.json()
        scopes = str(data.get("scope") or "").split()
        return {scope.strip() for scope in scopes if scope.strip()}

    def ensure_write_scope(self) -> set[str]:
        scopes = self.token_scopes()
        if scopes.intersection(GMAIL_WRITE_SCOPES):
            return scopes
        raise ValueError(
            "Gmail token does not include a write scope. Regenerate the token with gmail.compose or gmail.send "
            "for draft/send support."
        )

    def create_draft(
        self,
        *,
        to: str,
        subject: str,
        body: str = "",
        cc: str | None = None,
        bcc: str | None = None,
        body_html: str | None = None,
        reply_to: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        payload = self._build_message_payload(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            body_html=body_html,
            reply_to=reply_to,
            thread_id=thread_id,
            from_address=self._resolve_from_address(user_id),
        )
        self.ensure_write_scope()
        return self._request(
            "POST",
            f"/users/{self._resolve_user_id(user_id)}/drafts",
            json_data={"message": payload},
        )

    def send_message(
        self,
        *,
        to: str,
        subject: str,
        body: str = "",
        cc: str | None = None,
        bcc: str | None = None,
        body_html: str | None = None,
        reply_to: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        payload = self._build_message_payload(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            body_html=body_html,
            reply_to=reply_to,
            thread_id=thread_id,
            from_address=self._resolve_from_address(user_id),
        )
        self.ensure_write_scope()
        return self._request(
            "POST",
            f"/users/{self._resolve_user_id(user_id)}/messages/send",
            json_data=payload,
        )

    def _build_message_payload(
        self,
        *,
        to: str,
        subject: str,
        body: str = "",
        cc: str | None = None,
        bcc: str | None = None,
        body_html: str | None = None,
        reply_to: str | None = None,
        thread_id: str | None = None,
        from_address: str | None = None,
    ) -> dict[str, Any]:
        message = EmailMessage()
        to_list = _normalize_recipients(to)
        cc_list = _normalize_recipients(cc)
        bcc_list = _normalize_recipients(bcc)
        if not to_list:
            raise ValueError("Gmail message recipient is required")
        message["To"] = ", ".join(to_list)
        if cc_list:
            message["Cc"] = ", ".join(cc_list)
        if bcc_list:
            message["Bcc"] = ", ".join(bcc_list)
        if from_address:
            message["From"] = str(from_address).strip()
        if reply_to:
            message["Reply-To"] = str(reply_to).strip()
        message["Subject"] = str(subject or "").strip()
        message["Date"] = format_datetime(datetime.now(timezone.utc))
        if body_html:
            message.set_content(str(body or ""))
            message.add_alternative(str(body_html), subtype="html")
        else:
            message.set_content(str(body or ""))
        raw = urlsafe_b64encode(message.as_bytes(policy=SMTP)).decode("ascii")
        payload: dict[str, Any] = {"raw": raw}
        if thread_id:
            payload["threadId"] = str(thread_id).strip()
        return payload

    def _resolve_from_address(self, user_id: str | None = None) -> str | None:
        resolved = str(user_id or self.user_id or "").strip()
        if resolved and "@" in resolved:
            return resolved
        try:
            profile = self.whoami()
        except Exception:
            return resolved or None
        email_address = str(profile.get("emailAddress") or "").strip()
        if email_address:
            return email_address
        return resolved or None


def _normalize_recipients(value: str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = value.replace(";", ",").split(",")
    else:
        items = [str(value)]
    return [item.strip() for item in items if item and item.strip()]


def _extract_response_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except Exception:
        return response.text.strip()
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            messages: list[str] = []
            message = str(error.get("message") or "").strip()
            if message:
                messages.append(message)
            errors = error.get("errors")
            if isinstance(errors, list):
                for item in errors:
                    if isinstance(item, dict):
                        reason = str(item.get("reason") or "").strip()
                        location = str(item.get("location") or "").strip()
                        if reason and location:
                            messages.append(f"{reason} at {location}")
                        elif reason:
                            messages.append(reason)
            return "; ".join(messages).strip()
    return ""


def _client_from_env() -> GmailClient:
    return GmailClient(
        token=str(os.environ.get("GMAIL_TOKEN") or "").strip(),
        api_base=str(os.environ.get("GMAIL_API_BASE") or GMAIL_API_BASE_DEFAULT).strip() or GMAIL_API_BASE_DEFAULT,
        user_id=str(os.environ.get("GMAIL_USER_ID") or GMAIL_USER_ID_DEFAULT).strip() or GMAIL_USER_ID_DEFAULT,
    )


def _connector_context() -> dict[str, Any]:
    default_user_id = str(os.environ.get("GMAIL_USER_ID") or "").strip() or GMAIL_USER_ID_DEFAULT
    return {
        "api_base": str(os.environ.get("GMAIL_API_BASE") or GMAIL_API_BASE_DEFAULT).strip() or GMAIL_API_BASE_DEFAULT,
        "has_token": bool(str(os.environ.get("GMAIL_TOKEN") or "").strip()),
        "default_user_id": default_user_id,
        "effective_user_id": default_user_id,
        "capabilities": ["read", "draft", "send"],
    }


mcp = FastMCP(
    "gmail-connector",
    instructions=(
        "Gmail connector for inbox search, message inspection, thread reading, label discovery, draft creation, and email sending tasks. "
        "Use the tools for structured Gmail access instead of ad-hoc scraping."
    ),
)


@mcp.tool(description="Return the authenticated Gmail user profile for token validation.")
def whoami() -> dict[str, Any]:
    return _client_from_env().whoami()


@mcp.tool(description="List Gmail messages using Gmail query syntax. If user_id is omitted, use the configured default user ID.")
def list_messages(
    query: str = "",
    label_ids: list[str] | None = None,
    max_results: int = 10,
    page_token: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    return _client_from_env().list_messages(
        query=query,
        label_ids=label_ids,
        max_results=max_results,
        page_token=page_token,
        user_id=user_id,
    )


@mcp.tool(description="Get one Gmail message by message ID. If user_id is omitted, use the configured default user ID.")
def get_message(message_id: str, format: str = "full", user_id: str | None = None) -> dict[str, Any]:
    return _client_from_env().get_message(message_id=message_id, format=format, user_id=user_id)


@mcp.tool(description="Get one Gmail thread by thread ID. If user_id is omitted, use the configured default user ID.")
def get_thread(thread_id: str, format: str = "full", user_id: str | None = None) -> dict[str, Any]:
    return _client_from_env().get_thread(thread_id=thread_id, format=format, user_id=user_id)


@mcp.tool(description="List labels for the configured Gmail mailbox.")
def list_labels(user_id: str | None = None) -> dict[str, Any]:
    return _client_from_env().list_labels(user_id=user_id)


@mcp.tool(description="Create a Gmail draft message. Use this when the user wants to prepare email without sending it yet.")
def create_draft(
    to: str,
    subject: str,
    body: str = "",
    cc: str | None = None,
    bcc: str | None = None,
    body_html: str | None = None,
    reply_to: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    return _client_from_env().create_draft(
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        body_html=body_html,
        reply_to=reply_to,
        thread_id=thread_id,
        user_id=user_id,
    )


@mcp.tool(description="Send a Gmail message immediately. Use this when the user explicitly asks to send email.")
def send_message(
    to: str,
    subject: str,
    body: str = "",
    cc: str | None = None,
    bcc: str | None = None,
    body_html: str | None = None,
    reply_to: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    return _client_from_env().send_message(
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        body_html=body_html,
        reply_to=reply_to,
        thread_id=thread_id,
        user_id=user_id,
    )


@mcp.tool(description="Return the Gmail connector runtime context, including configured default mailbox user ID.")
def get_connector_context() -> dict[str, Any]:
    return _connector_context()


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
