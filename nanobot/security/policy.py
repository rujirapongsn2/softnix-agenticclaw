"""Global content control policy storage and enforcement."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_VALID_MODES = {"off", "monitor", "enforce"}
_VALID_ACTIONS = {"allow", "warn", "mask", "block", "escalate"}
_VALID_SCOPES = {"input", "output", "tool_args", "memory_write"}
_VALID_CATEGORIES = {"profanity", "pii", "secrets", "prompt_injection", "unsafe_instruction", "custom"}
_VALID_DETECTORS = {"keyword", "phrase", "regex", "pii"}

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?){2,4}\d{3,4})")
_NATIONAL_ID_RE = re.compile(r"\b\d{13}\b")
_CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_ACCOUNT_NUMBER_RE = re.compile(r"\b\d{9,16}\b")
_API_KEY_LIKE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z\-_]{20,}|ghp_[0-9A-Za-z]{20,}|xox[baprs]-[0-9A-Za-z-]{10,})\b"
)
_ACCESS_TOKEN_LIKE_RE = re.compile(
    r"\b(?:Bearer\s+[A-Za-z0-9\-._~+/]+=*|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})\b"
)

_DEFAULT_PROFANITY = ["fuck", "shit", "bitch", "asshole", "เหี้ย", "เชี่ย", "ควย", "สัส", "ห่า", "ไอ้สัตว์"]

_POLICY_CARD_TEMPLATES: list[dict[str, Any]] = [
    {
        "group": "Security",
        "cards": [
            {
                "rule_id": "builtin-prompt-injection",
                "name": "Prompt Injection / Jailbreak",
                "category": "prompt_injection",
                "description": "Detect instructions that try to override the system or steal hidden context.",
                "severity": "critical",
                "priority": 240,
                "action": "block",
                "scope": ["input", "output", "tool_args"],
                "message_template": "Content was blocked because it appears to contain prompt injection or jailbreak attempts.",
                "detectors": [
                    {
                        "type": "phrase",
                        "values": [
                            "ignore previous instructions",
                            "forget all previous instructions",
                            "reveal system prompt",
                            "bypass policy",
                            "override instructions",
                            "ignore the system",
                        ],
                    }
                ],
                "enabled": False,
                "builtin": True,
            },
            {
                "rule_id": "builtin-sensitive-memory",
                "name": "Sensitive Memory Requests",
                "category": "unsafe_instruction",
                "description": "Refuse requests to store, remember, or share sensitive data.",
                "severity": "high",
                "priority": 230,
                "action": "block",
                "scope": ["input", "output", "memory_write"],
                "message_template": "Sensitive data cannot be stored or shared in memory.",
                "detectors": [
                    {
                        "type": "keyword",
                        "values": ["remember", "save", "store", "keep", "record", "memorize", "retain", "จำ", "บันทึก", "เก็บ", "เซฟ", "ส่งสรุป", "share", "forward", "export"],
                        "match_mode": "substring",
                    }
                ],
                "enabled": False,
                "builtin": True,
            },
        ],
    },
    {
        "group": "Privacy",
        "cards": [
            {
                "rule_id": "builtin-pii-phone",
                "name": "Phone PII",
                "category": "pii",
                "description": "Detect phone numbers and redact them before storage or output.",
                "severity": "high",
                "priority": 180,
                "action": "mask",
                "scope": ["input", "output", "tool_args", "memory_write"],
                "message_template": "Sensitive personal data was sanitized according to organization policy.",
                "detectors": [{"type": "pii", "pii_types": ["phone"]}],
                "enabled": False,
                "builtin": True,
            },
            {
                "rule_id": "builtin-pii-national-id",
                "name": "National ID / Tax ID",
                "category": "pii",
                "description": "Detect national identity numbers and tax identifiers.",
                "severity": "critical",
                "priority": 190,
                "action": "block",
                "scope": ["input", "output", "tool_args", "memory_write"],
                "message_template": "Content was blocked because it appears to contain government-issued identifiers.",
                "detectors": [{"type": "pii", "pii_types": ["national_id"]}],
                "enabled": False,
                "builtin": True,
            },
            {
                "rule_id": "builtin-pii-payment",
                "name": "Bank / Payment Data",
                "category": "pii",
                "description": "Detect bank, card, and payment details.",
                "severity": "critical",
                "priority": 200,
                "action": "block",
                "scope": ["input", "output", "tool_args", "memory_write"],
                "message_template": "Content was blocked because it appears to contain payment-related data.",
                "detectors": [{"type": "pii", "pii_types": ["credit_card", "account_number"]}],
                "enabled": False,
                "builtin": True,
            },
        ],
    },
    {
        "group": "Content Safety",
        "cards": [
            {
                "rule_id": "builtin-harassment",
                "name": "Harassment / Abuse",
                "category": "profanity",
                "description": "Detect abusive or harassing language and issue a warning or block depending on policy.",
                "severity": "medium",
                "priority": 70,
                "action": "warn",
                "scope": ["input", "output"],
                "message_template": "Content matched the organization language policy.",
                "detectors": [
                    {
                        "type": "keyword",
                        "values": ["idiot", "stupid", "moron", "trash", "asshole", "dumb", "โง่", "เหี้ย", "เชี่ย", "ควย", "สัส"],
                        "match_mode": "substring",
                    }
                ],
                "enabled": False,
                "builtin": True,
            },
        ],
    },
    {
        "group": "Enterprise",
        "cards": [
            {
                "rule_id": "builtin-confidential-data",
                "name": "Confidential / Internal Data",
                "category": "custom",
                "description": "Detect requests that expose internal, confidential, or proprietary information.",
                "severity": "high",
                "priority": 220,
                "action": "block",
                "scope": ["input", "output", "tool_args", "memory_write"],
                "message_template": "Content was blocked because it appears to contain confidential information.",
                "detectors": [
                    {
                        "type": "phrase",
                        "values": [
                            "internal only",
                            "confidential",
                            "do not share",
                            "for internal use only",
                            "customer list",
                            "roadmap",
                            "proprietary",
                            "secret project",
                        ],
                    }
                ],
                "enabled": False,
                "builtin": True,
            },
            {
                "rule_id": "builtin-external-share",
                "name": "External Share / Export",
                "category": "custom",
                "description": "Detect attempts to export, forward, or share sensitive content externally.",
                "severity": "high",
                "priority": 210,
                "action": "block",
                "scope": ["input", "output", "tool_args", "memory_write"],
                "message_template": "Content was blocked because it appears to request external sharing of sensitive data.",
                "detectors": [
                    {
                        "type": "keyword",
                        "values": ["share", "send", "forward", "export", "download", "email", "ส่ง", "แชร์", "ส่งต่อ", "สรุป", "export"],
                        "match_mode": "substring",
                    }
                ],
                "enabled": False,
                "builtin": True,
            },
        ],
    },
]


def _policy_card_rules(*, enabled_defaults: bool = True) -> list[dict[str, Any]]:
    now = _iso_now()
    rules: list[dict[str, Any]] = []
    for section in _POLICY_CARD_TEMPLATES:
        for template in section.get("cards", []):
            rule = copy.deepcopy(template)
            rule["created_at"] = now
            rule["updated_at"] = now
            if enabled_defaults:
                rule["enabled"] = bool(rule.get("enabled", False))
            else:
                rule["enabled"] = False
            rules.append(rule)
    return rules


def get_policy_catalog() -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for section in _POLICY_CARD_TEMPLATES:
        cards = []
        for template in section.get("cards", []):
            card = copy.deepcopy(template)
            card["enabled"] = False
            cards.append(card)
        catalog.append(
            {
                "group": str(section.get("group") or "General"),
                "cards": cards,
            }
        )
    return catalog


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _normalize_text(text: str, settings: dict[str, Any] | None) -> str:
    normalized = str(text or "")
    cfg = settings or {}
    if cfg.get("trim", True):
        normalized = normalized.strip()
    if cfg.get("collapse_whitespace", True):
        normalized = re.sub(r"\s+", " ", normalized)
    if cfg.get("case_fold", True):
        normalized = normalized.casefold()
    return normalized


def _mask_spans(text: str, spans: list[tuple[int, int, str]]) -> str:
    if not spans:
        return text
    merged: list[tuple[int, int, str]] = []
    for start, end, label in sorted(spans, key=lambda item: (item[0], item[1])):
        if not merged or start > merged[-1][1]:
            merged.append((start, end, label))
            continue
        prev_start, prev_end, prev_label = merged[-1]
        merged[-1] = (prev_start, max(prev_end, end), prev_label or label)

    chunks: list[str] = []
    cursor = 0
    for start, end, label in merged:
        if start > cursor:
            chunks.append(text[cursor:start])
        chunks.append(f"[REDACTED_{(label or 'CONTENT').upper()}]")
        cursor = end
    if cursor < len(text):
        chunks.append(text[cursor:])
    return "".join(chunks)


def build_default_policy() -> dict[str, Any]:
    now = _iso_now()
    return {
        "version": 1,
        "updated_at": now,
        "updated_by": {"user_id": "system", "username": "system"},
        "enabled": True,
        "mode": "enforce",
        "defaults": {
            "on_error": "fail_closed_for_block_rules",
            "log_hits": True,
            "cache_ttl_seconds": 30,
            "normalization": {
                "trim": True,
                "collapse_whitespace": True,
                "case_fold": True,
            },
        },
        "rules": [
            {
                "rule_id": "builtin-profanity",
                "name": "Profanity",
                "category": "profanity",
                "enabled": True,
                "builtin": True,
                "description": "Detect common profanity in user input and outbound content.",
                "scope": ["input", "output"],
                "severity": "medium",
                "priority": 50,
                "action": "warn",
                "message_template": "Content matched organization language policy.",
                "detectors": [{"type": "keyword", "values": _DEFAULT_PROFANITY, "match_mode": "substring"}],
                "exceptions": [],
                "created_at": now,
                "updated_at": now,
            },
            {
                "rule_id": "builtin-pii-email",
                "name": "Email PII",
                "category": "pii",
                "enabled": True,
                "builtin": True,
                "description": "Detect email addresses.",
                "scope": ["input", "output", "tool_args", "memory_write"],
                "severity": "high",
                "priority": 100,
                "action": "mask",
                "message_template": "Sensitive personal data was sanitized according to organization policy.",
                "detectors": [{"type": "pii", "pii_types": ["email"]}],
                "exceptions": [],
                "created_at": now,
                "updated_at": now,
            },
            {
                "rule_id": "builtin-secret-token",
                "name": "Secret Token Leak",
                "category": "secrets",
                "enabled": True,
                "builtin": True,
                "description": "Detect API keys and access tokens.",
                "scope": ["input", "output", "tool_args", "memory_write"],
                "severity": "critical",
                "priority": 200,
                "action": "block",
                "message_template": "Content was blocked because it appears to contain secrets or access credentials.",
                "detectors": [{"type": "pii", "pii_types": ["api_key_like", "access_token_like"]}],
                "exceptions": [],
                "created_at": now,
                "updated_at": now,
            },
            *[
                {
                    **rule,
                    "enabled": False,
                }
                for rule in _policy_card_rules(enabled_defaults=False)
            ],
        ],
    }


class PolicyValidationError(ValueError):
    """Raised when policy payload is invalid."""

    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.warnings = warnings or []


@dataclass
class PolicyDecision:
    matched: bool = False
    mode: str = "off"
    action: str = "allow"
    scope: str = ""
    text: str = ""
    sanitized_text: str = ""
    matched_rules: list[str] = field(default_factory=list)
    severity: str = ""
    message: str = ""
    blocked: bool = False
    monitor_only: bool = False
    payload: dict[str, Any] = field(default_factory=dict)

    def effective_text(self) -> str:
        return self.sanitized_text if self.sanitized_text else self.text


class GlobalControlPolicyStore:
    """Load, validate, and save the global control policy."""

    def __init__(self, path: Path):
        self.path = path.expanduser()

    def ensure_exists(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(build_default_policy(), indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self) -> dict[str, Any]:
        self.ensure_exists()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PolicyValidationError([f"Unable to read policy: {exc}"]) from exc
        normalized, errors, _warnings = self.validate(raw)
        if errors:
            raise PolicyValidationError(errors, _warnings)
        return normalized

    def save(self, payload: dict[str, Any], *, actor: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized, errors, warnings = self.validate(payload)
        if errors:
            raise PolicyValidationError(errors, warnings)
        current_version = 0
        if self.path.exists():
            try:
                current_version = int(json.loads(self.path.read_text(encoding="utf-8")).get("version") or 0)
            except Exception:
                current_version = 0
        normalized["version"] = max(current_version + 1, int(normalized.get("version") or 1))
        normalized["updated_at"] = _iso_now()
        normalized["updated_by"] = {
            "user_id": str((actor or {}).get("user_id") or "system"),
            "username": str((actor or {}).get("username") or "system"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(self.path)
        return normalized

    def validate(self, payload: dict[str, Any] | None) -> tuple[dict[str, Any], list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        data = copy.deepcopy(payload or {})
        if not isinstance(data, dict):
            return {}, ["Policy payload must be an object"], []
        data.setdefault("version", 1)
        data.setdefault("updated_at", _iso_now())
        data.setdefault("updated_by", {"user_id": "system", "username": "system"})
        data["enabled"] = bool(data.get("enabled", True))
        mode = str(data.get("mode") or "enforce").strip().lower()
        if mode not in _VALID_MODES:
            errors.append("mode must be one of: off, monitor, enforce")
        data["mode"] = mode if mode in _VALID_MODES else "enforce"

        defaults = data.get("defaults")
        if not isinstance(defaults, dict):
            defaults = {}
        defaults.setdefault("on_error", "fail_closed_for_block_rules")
        defaults.setdefault("log_hits", True)
        defaults.setdefault("cache_ttl_seconds", 30)
        normalization = defaults.get("normalization")
        if not isinstance(normalization, dict):
            normalization = {}
        normalization.setdefault("trim", True)
        normalization.setdefault("collapse_whitespace", True)
        normalization.setdefault("case_fold", True)
        defaults["normalization"] = normalization
        data["defaults"] = defaults

        rules = data.get("rules")
        if not isinstance(rules, list):
            errors.append("rules must be an array")
            rules = []
        seen_rule_ids: set[str] = set()
        normalized_rules: list[dict[str, Any]] = []
        for idx, raw_rule in enumerate(rules, start=1):
            if not isinstance(raw_rule, dict):
                errors.append(f"rules[{idx}] must be an object")
                continue
            rule = copy.deepcopy(raw_rule)
            rule_id = str(rule.get("rule_id") or "").strip()
            if not rule_id:
                errors.append(f"rules[{idx}].rule_id is required")
            elif rule_id in seen_rule_ids:
                errors.append(f"Duplicate rule_id: {rule_id}")
            else:
                seen_rule_ids.add(rule_id)
            rule["rule_id"] = rule_id
            rule["name"] = str(rule.get("name") or rule_id or f"Rule {idx}")
            category = str(rule.get("category") or "custom").strip().lower()
            if category not in _VALID_CATEGORIES:
                warnings.append(f"rules[{idx}] uses non-standard category '{category}'")
            rule["category"] = category
            rule["enabled"] = bool(rule.get("enabled", True))
            rule["builtin"] = bool(rule.get("builtin", False))
            rule["description"] = str(rule.get("description") or "")
            severity = str(rule.get("severity") or "medium").strip().lower()
            rule["severity"] = severity
            try:
                rule["priority"] = int(rule.get("priority") or 0)
            except (TypeError, ValueError):
                errors.append(f"rules[{idx}].priority must be an integer")
                rule["priority"] = 0
            action = str(rule.get("action") or "allow").strip().lower()
            if action not in _VALID_ACTIONS:
                errors.append(f"rules[{idx}].action must be one of: {', '.join(sorted(_VALID_ACTIONS))}")
            rule["action"] = action if action in _VALID_ACTIONS else "allow"
            scopes = rule.get("scope")
            if not isinstance(scopes, list) or not scopes:
                errors.append(f"rules[{idx}].scope must be a non-empty array")
                scopes = ["input"]
            normalized_scopes: list[str] = []
            for scope in scopes:
                scope_value = str(scope or "").strip().lower()
                if scope_value not in _VALID_SCOPES:
                    errors.append(f"rules[{idx}] contains invalid scope '{scope_value}'")
                    continue
                normalized_scopes.append(scope_value)
            rule["scope"] = sorted(set(normalized_scopes))
            rule["message_template"] = str(rule.get("message_template") or "")
            rule["exceptions"] = rule.get("exceptions") if isinstance(rule.get("exceptions"), list) else []
            detectors = rule.get("detectors")
            if not isinstance(detectors, list) or not detectors:
                errors.append(f"rules[{idx}].detectors must be a non-empty array")
                detectors = []
            normalized_detectors: list[dict[str, Any]] = []
            for detector_idx, raw_detector in enumerate(detectors, start=1):
                if not isinstance(raw_detector, dict):
                    errors.append(f"rules[{idx}].detectors[{detector_idx}] must be an object")
                    continue
                detector = copy.deepcopy(raw_detector)
                detector_type = str(detector.get("type") or "").strip().lower()
                if detector_type not in _VALID_DETECTORS:
                    errors.append(f"rules[{idx}].detectors[{detector_idx}].type is invalid")
                    continue
                detector["type"] = detector_type
                if detector_type in {"keyword", "phrase"}:
                    values = detector.get("values")
                    if not isinstance(values, list) or not any(str(v or "").strip() for v in values):
                        errors.append(f"rules[{idx}].detectors[{detector_idx}].values must be a non-empty array")
                        values = []
                    detector["values"] = [str(v).strip() for v in values if str(v or "").strip()]
                    detector["match_mode"] = str(detector.get("match_mode") or ("token" if detector_type == "keyword" else "substring")).strip().lower()
                elif detector_type == "regex":
                    patterns = detector.get("patterns")
                    if not isinstance(patterns, list) or not patterns:
                        errors.append(f"rules[{idx}].detectors[{detector_idx}].patterns must be a non-empty array")
                        patterns = []
                    normalized_patterns: list[dict[str, Any]] = []
                    for pattern_idx, item in enumerate(patterns, start=1):
                        if isinstance(item, str):
                            pattern_obj = {"pattern": item, "flags": "i"}
                        elif isinstance(item, dict):
                            pattern_obj = {"pattern": str(item.get("pattern") or ""), "flags": str(item.get("flags") or "i")}
                        else:
                            errors.append(f"rules[{idx}].detectors[{detector_idx}].patterns[{pattern_idx}] is invalid")
                            continue
                        if not pattern_obj["pattern"]:
                            errors.append(f"rules[{idx}].detectors[{detector_idx}].patterns[{pattern_idx}] must include pattern")
                            continue
                        try:
                            re.compile(pattern_obj["pattern"], _regex_flags(pattern_obj["flags"]))
                        except re.error as exc:
                            errors.append(f"Invalid regex for rule '{rule_id or idx}': {exc}")
                            continue
                        normalized_patterns.append(pattern_obj)
                    detector["patterns"] = normalized_patterns
                elif detector_type == "pii":
                    pii_types = detector.get("pii_types")
                    if not isinstance(pii_types, list) or not pii_types:
                        errors.append(f"rules[{idx}].detectors[{detector_idx}].pii_types must be a non-empty array")
                        pii_types = []
                    detector["pii_types"] = [str(v).strip().lower() for v in pii_types if str(v or "").strip()]
                normalized_detectors.append(detector)
            if not normalized_detectors:
                warnings.append(f"rules[{idx}] has no active detectors")
            rule["detectors"] = normalized_detectors
            rule.setdefault("created_at", _iso_now())
            rule["updated_at"] = str(rule.get("updated_at") or _iso_now())
            normalized_rules.append(rule)

        # Ensure the curated catalog is always available as disabled cards unless the
        # policy already defines that rule.
        existing_rule_ids = {str(rule.get("rule_id") or "") for rule in normalized_rules if str(rule.get("rule_id") or "")}
        for section in _POLICY_CARD_TEMPLATES:
            for template in section.get("cards", []):
                rule_id = str(template.get("rule_id") or "").strip()
                if not rule_id or rule_id in existing_rule_ids:
                    continue
                card = copy.deepcopy(template)
                card.setdefault("group", str(section.get("group") or "General"))
                card["enabled"] = False
                card["builtin"] = True
                card["created_at"] = data.get("updated_at") or _iso_now()
                card["updated_at"] = data.get("updated_at") or _iso_now()
                normalized_rules.append(card)
        data["rules"] = normalized_rules
        return data, errors, warnings

    def summarize(self, policy: dict[str, Any]) -> dict[str, Any]:
        rules = [rule for rule in policy.get("rules", []) if isinstance(rule, dict)]
        enabled_rules = [rule for rule in rules if rule.get("enabled")]
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "enabled": bool(policy.get("enabled", True)),
            "mode": str(policy.get("mode") or "enforce"),
            "version": int(policy.get("version") or 1),
            "updated_at": str(policy.get("updated_at") or ""),
            "updated_by": policy.get("updated_by") or {},
            "rule_count": len(rules),
            "enabled_rule_count": len(enabled_rules),
        }


class PolicyCache:
    """Simple mtime-based cache for the active policy."""

    def __init__(self, store: GlobalControlPolicyStore):
        self.store = store
        self._cached_policy: dict[str, Any] | None = None
        self._cached_mtime: float | None = None
        self._cached_version: int | None = None

    def get(self) -> dict[str, Any]:
        try:
            current_mtime = self.store.path.stat().st_mtime if self.store.path.exists() else None
        except OSError:
            current_mtime = None
        if self._cached_policy is not None and current_mtime == self._cached_mtime:
            return copy.deepcopy(self._cached_policy)
        policy = self.store.load()
        self._cached_policy = copy.deepcopy(policy)
        self._cached_mtime = current_mtime
        self._cached_version = int(policy.get("version") or 1)
        return policy

    @property
    def version(self) -> int | None:
        return self._cached_version


def _regex_flags(flags: str | None) -> int:
    value = 0
    for char in str(flags or ""):
        if char == "i":
            value |= re.IGNORECASE
        elif char == "m":
            value |= re.MULTILINE
    return value


def _pii_patterns(pii_type: str) -> re.Pattern[str] | None:
    return {
        "email": _EMAIL_RE,
        "phone": _PHONE_RE,
        "national_id": _NATIONAL_ID_RE,
        "credit_card": _CREDIT_CARD_RE,
        "account_number": _ACCOUNT_NUMBER_RE,
        "api_key_like": _API_KEY_LIKE_RE,
        "access_token_like": _ACCESS_TOKEN_LIKE_RE,
    }.get(pii_type)


def _severity_rank(value: str) -> int:
    return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(str(value or "").lower(), 0)


def _action_rank(value: str) -> int:
    return {"allow": 0, "warn": 1, "mask": 2, "block": 3, "escalate": 4}.get(str(value or "").lower(), 0)


def _match_keyword(detector: dict[str, Any], original_text: str, normalized_text: str, label: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    values = [str(value or "") for value in detector.get("values", [])]
    match_mode = str(detector.get("match_mode") or "token").lower()
    for value in values:
        target = value.strip()
        if not target:
            continue
        if match_mode == "substring":
            for match in re.finditer(re.escape(target), original_text, flags=re.IGNORECASE):
                spans.append((match.start(), match.end(), label))
            continue
        token_pattern = re.compile(rf"(?<!\w){re.escape(target)}(?!\w)", re.IGNORECASE)
        for match in token_pattern.finditer(original_text):
            spans.append((match.start(), match.end(), label))
    return spans


def _match_phrase(detector: dict[str, Any], original_text: str, label: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for value in detector.get("values", []):
        phrase = str(value or "").strip()
        if not phrase:
            continue
        for match in re.finditer(re.escape(phrase), original_text, flags=re.IGNORECASE):
            spans.append((match.start(), match.end(), label))
    return spans


def _match_regex(detector: dict[str, Any], original_text: str, label: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for pattern in detector.get("patterns", []):
        raw = str((pattern or {}).get("pattern") or "")
        if not raw:
            continue
        try:
            compiled = re.compile(raw, _regex_flags((pattern or {}).get("flags")))
        except re.error:
            continue
        for match in compiled.finditer(original_text):
            spans.append((match.start(), match.end(), label))
    return spans


def _match_pii(detector: dict[str, Any], original_text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for pii_type in detector.get("pii_types", []):
        compiled = _pii_patterns(str(pii_type or "").lower())
        if compiled is None:
            continue
        for match in compiled.finditer(original_text):
            spans.append((match.start(), match.end(), str(pii_type or "pii").upper()))
    return spans


def infer_global_policy_path(config_path: Path | None = None) -> Path:
    path = (config_path or (Path.home() / ".nanobot" / "config.json")).expanduser().resolve()
    config_dir = path.parent
    if config_dir.parent.name == "instances":
        return config_dir.parent.parent / "admin" / "security" / "content-intent-policy.json"
    return config_dir / ".nanobot-admin" / "security" / "content-intent-policy.json"


class GlobalPolicyEnforcer:
    """Evaluates the active policy against content."""

    def __init__(self, cache: PolicyCache):
        self.cache = cache

    def current_policy(self) -> dict[str, Any]:
        return self.cache.get()

    def enforce_text(
        self,
        text: str,
        *,
        scope: str,
        channel: str | None = None,
        session_key: str | None = None,
        tool_name: str | None = None,
    ) -> PolicyDecision:
        policy = self.current_policy()
        mode = str(policy.get("mode") or "off")
        if not bool(policy.get("enabled", True)) or mode == "off":
            return PolicyDecision(matched=False, mode=mode, action="allow", scope=scope, text=text, sanitized_text=text)

        defaults = policy.get("defaults") if isinstance(policy.get("defaults"), dict) else {}
        normalized_text = _normalize_text(text, defaults.get("normalization"))
        original_text = str(text or "")
        matches: list[tuple[dict[str, Any], list[tuple[int, int, str]]]] = []
        for rule in sorted(policy.get("rules", []), key=lambda item: (int(item.get("priority") or 0), _action_rank(item.get("action"))), reverse=True):
            if not isinstance(rule, dict) or not rule.get("enabled"):
                continue
            if scope not in set(rule.get("scope") or []):
                continue
            spans: list[tuple[int, int, str]] = []
            for detector in rule.get("detectors", []):
                dtype = str(detector.get("type") or "")
                if dtype == "keyword":
                    spans.extend(_match_keyword(detector, original_text, normalized_text, str(rule.get("category") or "CONTENT")))
                elif dtype == "phrase":
                    spans.extend(_match_phrase(detector, original_text, str(rule.get("category") or "CONTENT")))
                elif dtype == "regex":
                    spans.extend(_match_regex(detector, original_text, str(rule.get("category") or "CONTENT")))
                elif dtype == "pii":
                    spans.extend(_match_pii(detector, original_text))
            if spans:
                matches.append((rule, spans))

        if not matches:
            return PolicyDecision(matched=False, mode=mode, action="allow", scope=scope, text=text, sanitized_text=text)

        matched_rules = [str(rule.get("rule_id") or "") for rule, _spans in matches if str(rule.get("rule_id") or "")]
        primary_rule = max(matches, key=lambda item: (_action_rank(item[0].get("action")), _severity_rank(item[0].get("severity")), int(item[0].get("priority") or 0)))[0]
        action = str(primary_rule.get("action") or "allow")
        severity = str(primary_rule.get("severity") or "")
        spans = [span for _rule, detector_spans in matches for span in detector_spans]
        sanitized_text = _mask_spans(original_text, spans) if action == "mask" else original_text
        if mode == "monitor":
            return PolicyDecision(
                matched=True,
                mode=mode,
                action=action,
                scope=scope,
                text=original_text,
                sanitized_text=original_text,
                matched_rules=matched_rules,
                severity=severity,
                message=str(primary_rule.get("message_template") or ""),
                blocked=False,
                monitor_only=True,
                payload={
                    "channel": channel or "",
                    "session_key": session_key or "",
                    "tool_name": tool_name or "",
                },
            )
        return PolicyDecision(
            matched=True,
            mode=mode,
            action=action,
            scope=scope,
            text=original_text,
            sanitized_text=sanitized_text,
            matched_rules=matched_rules,
            severity=severity,
            message=str(primary_rule.get("message_template") or ""),
            blocked=action in {"block", "escalate"},
            monitor_only=False,
            payload={
                "channel": channel or "",
                "session_key": session_key or "",
                "tool_name": tool_name or "",
            },
        )

    def sanitize_memory_text(self, text: str) -> str:
        decision = self.enforce_text(text, scope="memory_write")
        if decision.blocked:
            return ""
        return decision.effective_text()
