# Global Control Policy PRD and Feature Requirement Specification

## Document Status

- Status: Draft for implementation
- Owner: Product / Platform / Security
- Target system: Softnix Admin + nanobot runtime
- Last updated: 2026-03-18

## Executive Summary

This document defines a global content control policy system for AI usage across all instances managed by Softnix Admin.

The feature acts as a centralized control-plane policy layer, not as workspace memory and not as per-instance prompt customization.

Its main purpose is to:

- enforce organization-wide AI content safety rules across every instance
- allow privileged administrators to define and maintain reusable detection rules
- block, warn, mask, or escalate unsafe content before it reaches the model, before it is sent to tools, and before it is returned to end users
- provide auditability and operational visibility for policy hits and policy changes

The policy engine should support rule-based intent detection with extensibility for stronger classifiers later. The first version should prioritize deterministic matching and operational safety over ML complexity.

## Problem Statement

The current project already has:

- workspace-scoped memory and prompt files
- admin RBAC
- runtime audit logging
- centralized Softnix Admin control-plane structure

However, the current architecture does not provide a single, organization-wide policy layer that:

- applies to all instances
- is managed from the Security area of the admin control plane
- is not editable by end users through workspace access
- is enforced consistently across user input, model output, and tool execution

If content safety rules are stored in workspace memory, they become:

- instance-specific instead of global
- easier to tamper with
- mixed with prompt behavior instead of policy enforcement
- harder to audit and maintain centrally

## Product Goal

Create a global control policy feature that allows the organization to define content safety intents once and enforce them consistently across all instances.

## Success Criteria

The feature is successful when:

- one policy change affects all active instances without manual duplication
- policy rules are editable only by authorized high-privilege users
- unsafe content can be blocked or sanitized before model invocation
- policy hits are visible in audit logs and admin reporting
- the runtime can continue functioning safely even if the policy file is invalid or unavailable

## Non-Goals

This feature is not intended in v1 to:

- replace all provider-native guardrails
- classify complex semantic abuse using a dedicated ML model
- support tenant-specific overrides
- perform DLP-grade enterprise document scanning across file repositories
- replace moderation or human review workflows entirely

## Users and Stakeholders

### Primary Users

- Owner: defines and approves organization-wide policy
- Security Admin: updates rules, reviews incidents, audits usage
- Platform Admin: operates instances and monitors enforcement

### Indirect Users

- Instance operators
- AI end users across connected channels
- Compliance and audit teams

## Key Principles

1. Policy must be enforced by the runtime, not by prompt wording alone.
2. Global policy must live in the control plane, not in workspaces.
3. Detection must be deterministic-first, then extensible.
4. Unsafe content should be stopped as early as possible in the pipeline.
5. Every policy change and enforcement decision must be auditable.
6. Fail-safe behavior is mandatory for malformed or missing policy state.

## Scope

### In Scope

- global content control policy storage
- admin UI for viewing and editing policy
- RBAC for read and update operations
- runtime enforcement for inbound content
- runtime enforcement for outbound content
- runtime enforcement for selected tool calls
- audit logging for policy updates and policy hits
- rule schema and validation
- policy cache and reload behavior

### Out of Scope for v1

- per-instance override rules
- per-channel override rules
- UI simulator with full regex debugger
- model-based semantic classifier for ambiguous intent
- provider-specific guardrail synchronization

## Terminology

- Global Control Policy: the single source of truth that applies to all instances
- Intent Rule: one policy rule representing a content category or prohibited pattern
- Detector: one matching mechanism inside a rule such as keyword, phrase, regex, or typed detector
- Enforcement Point: a stage where policy is checked, such as input, output, tool call, or memory write
- Action: system response when a rule matches

## Business Context

Softnix Admin already acts as the central control plane. Its documented structure separates:

- `~/.softnix/admin` for central administrative data
- `~/.softnix/instances/<instance-id>` for per-instance runtime data

This feature must follow that separation.

## Proposed Solution Overview

Implement a new global policy subsystem with the following design:

- storage location in control plane:
  `~/.softnix/admin/security/content-intent-policy.json`
- access path through admin service APIs only
- runtime read-only loading by all instances
- centralized schema validation and versioning
- deterministic detection engine with support for:
  - keywords
  - phrases
  - regex patterns
  - typed detectors such as PII categories
- enforcement actions:
  - allow
  - warn
  - mask
  - block
  - escalate

## Why Global Instead of Per-Instance

The stated requirement is that one policy definition must affect every instance. Therefore:

- policy cannot live under `workspace`
- policy cannot live under `instance_home`
- policy must be loaded from the control plane
- the runtime must treat it as authoritative baseline policy

This aligns naturally with the existing Softnix Admin control-plane model.

## Product Requirements

### PR-1 Global Applicability

The system shall apply the effective policy to all instances managed by Softnix Admin.

### PR-2 Centralized Storage

The system shall store the policy in a global admin-managed path outside all workspaces.

### PR-3 Restricted Mutability

The system shall allow only privileged users to edit the policy from the Security menu.

### PR-4 Read-Only Runtime Access

The agent runtime shall be able to read policy state but shall not be able to mutate it through normal tools or workspace editing.

### PR-5 Multi-Stage Enforcement

The system shall support enforcement at:

- inbound user content
- outbound assistant content
- selected tool arguments
- selected persistence operations

### PR-6 Auditability

The system shall log:

- every policy create or update event
- every policy validation failure
- every enforcement hit
- every block, mask, warn, or escalate action
- every important security-relevant policy lifecycle event into the Security Audit Log
- per-instance policy detection activity so administrators can identify which instance triggered which rule

### PR-7 Operational Safety

The system shall behave safely if the policy file is missing, invalid, or temporarily unreadable.

## Functional Requirements

### FR-1 Policy Storage

The system shall create and maintain a global policy file at:

```text
~/.softnix/admin/security/content-intent-policy.json
```

Alternative acceptable implementation:

- store policy metadata in the same directory with additional backups or versions
- expose the current effective version through the admin service

### FR-2 Policy Schema

The global policy shall contain:

- metadata
- version
- global defaults
- rules array
- exception definitions
- audit metadata

The policy shall also support explicit operating modes:

- `off`: do not enforce and do not log hits except health diagnostics
- `monitor`: do not block or mask, but log matches and show impact preview
- `enforce`: execute rule actions normally

Minimum top-level fields:

```json
{
  "version": 1,
  "updated_at": "2026-03-18T10:00:00Z",
  "updated_by": {
    "user_id": "owner-123",
    "username": "owner"
  },
  "enabled": true,
  "mode": "enforce",
  "defaults": {
    "on_error": "fail_closed_for_block_rules",
    "log_hits": true,
    "cache_ttl_seconds": 30,
    "normalization": {
      "trim": true,
      "collapse_whitespace": true,
      "case_fold": true
    }
  },
  "rules": []
}
```

### FR-3 Rule Schema

Each rule shall support at minimum:

- `rule_id`
- `name`
- `category`
- `enabled`
- `builtin`
- `description`
- `scope`
- `severity`
- `priority`
- `action`
- `detectors`
- `message_template`
- `created_at`
- `updated_at`

Suggested rule shape:

```json
{
  "rule_id": "pii-email",
  "name": "Email Address Detection",
  "category": "pii",
  "enabled": true,
  "builtin": true,
  "description": "Detect email addresses in input, output, and message tool payloads.",
  "scope": ["input", "output", "tool_args"],
  "severity": "high",
  "priority": 100,
  "action": "mask",
  "message_template": "Sensitive personal data was sanitized according to organization policy.",
  "detectors": [
    {
      "type": "pii",
      "pii_types": ["email"]
    }
  ],
  "exceptions": [],
  "created_at": "2026-03-18T10:00:00Z",
  "updated_at": "2026-03-18T10:00:00Z"
}
```

### FR-4 Supported Categories

The system shall support built-in categories:

- profanity
- pii
- secrets
- prompt_injection
- unsafe_instruction
- custom

The system may allow arbitrary custom categories for future extensibility.

### FR-5 Supported Detector Types

The system shall support in v1:

- `keyword`
- `phrase`
- `regex`
- `pii`

Detector-specific requirements:

- `keyword`: supports case-insensitive exact token or normalized token matching
- `phrase`: supports substring match after normalization
- `regex`: supports validated compiled regex with flags controlled by schema
- `pii`: supports typed deterministic matchers implemented by code

The system should reserve room for future detector types:

- classifier
- dictionary
- exact_match
- structured_token

### FR-6 PII Detector Requirements

The PII detector shall support typed matching for at least:

- email
- phone
- national_id
- credit_card
- account_number
- api_key_like
- access_token_like

Notes:

- exact country-specific patterns may start minimal in v1
- typed detectors should be implemented deterministically, not by keyword only

### FR-7 Scope Model

Each rule shall define where it applies using `scope`.

Supported scope values in v1:

- `input`
- `output`
- `tool_args`
- `memory_write`

### FR-8 Actions

Supported actions:

- `allow`
- `warn`
- `mask`
- `block`
- `escalate`

Action semantics:

- `allow`: log optional match but do not alter content
- `warn`: allow flow and attach warning metadata
- `mask`: sanitize matched values before continuing
- `block`: stop the flow and return policy response
- `escalate`: stop or hold and mark for privileged review

### FR-8.1 Action Behavior by Policy Mode

When policy mode is:

- `off`: runtime bypasses enforcement except health checks
- `monitor`: runtime evaluates rules and logs results, but does not alter content
- `enforce`: runtime applies action exactly as configured

### FR-9 Rule Priority

The system shall resolve rule conflicts by:

1. higher priority first
2. more severe action first
3. block before mask before warn before allow

If multiple `mask` rules apply, the runtime shall sanitize all matched regions before continuing.

If both `mask` and `block` match, `block` shall win.

### FR-10 Built-In Rules

The system shall ship with starter built-in rules for:

- profanity keywords
- common PII types
- API key and token leakage patterns

### FR-11 Custom Rules

The admin UI shall allow privileged users to define custom rules using:

- keywords
- phrases
- regex
- action
- severity
- scope

### FR-12 Policy Validation

The system shall validate policy data before saving, including:

- schema correctness
- unique `rule_id`
- valid regex compilation
- valid action enum
- valid scope enum
- valid detector configuration

Validation should also warn on:

- duplicate patterns across enabled rules
- rules with empty detector arrays
- rules with unreachable effect due to lower priority and identical scope
- patterns that are too broad and likely to cause high false positives

### FR-13 Preview and Validation API

The system should provide a validation endpoint that accepts draft policy payload and returns:

- valid or invalid status
- field errors
- regex errors
- warnings such as duplicate patterns or unreachable rules

### FR-14 Effective Version

The system shall expose the current policy version and updated timestamp so runtimes can refresh correctly.

### FR-15 Read Path for Runtime

Every active instance shall load policy state from the global control plane through a read path independent of workspace files.

### FR-16 Cache and Reload

The runtime shall cache policy state but refresh when:

- file modification time changes
- version changes
- cache TTL expires

The runtime should avoid reloading from disk on every request if the cache is fresh.

### FR-17 Inbound Enforcement

Before content is included in model messages, the runtime shall evaluate inbound user content against rules with `input` scope.

Possible outcomes:

- pass through unchanged
- sanitized content passed through
- blocked response returned directly
- warning metadata attached to runtime flow

Input enforcement must run on:

- direct user text
- channel-delivered text payload
- message text extracted from structured attachments when applicable

### FR-18 Outbound Enforcement

After model output is produced and before it is sent to the end user, the runtime shall evaluate rules with `output` scope.

If output is blocked:

- original model output must not be forwarded to the channel
- the user receives a safe replacement response
- an audit event is emitted

### FR-19 Tool Argument Enforcement

Before executing sensitive tools, the runtime shall evaluate tool arguments against rules with `tool_args` scope.

Minimum tools to cover in v1:

- `message`
- `exec`
- `web_fetch`
- `write_file`
- `edit_file`

Tool argument enforcement should inspect:

- outbound text sent by `message`
- shell command string for `exec`
- URL and selected text payloads for `web_fetch`
- file content payloads for `write_file`
- replacement content payloads for `edit_file`

### FR-20 Memory Write Enforcement

Before content is written to long-term memory or history, the runtime shall evaluate rules with `memory_write` scope to prevent storing sensitive content unintentionally.

### FR-21 Policy Failure Modes

The system shall support explicit failure behavior in policy defaults:

- `fail_open`
- `fail_closed_for_block_rules`
- `fail_closed_all`

Recommended v1 default:

- `fail_closed_for_block_rules`

Meaning of the recommended default:

- if a valid cached policy exists, continue using cached policy
- if no valid cached policy exists, apply a minimal built-in blocklist for critical secret leakage only
- do not silently disable enforcement

### FR-22 User-Facing Messages

The system shall allow configurable user-facing messages for blocked or sanitized content.

Examples:

- block response
- mask response
- escalation response

### FR-23 Audit Logging

The system shall write audit entries for:

- policy created
- policy updated
- policy validation failed
- policy enforcement matched
- policy enforcement blocked
- policy enforcement masked
- policy enforcement escalated

The system shall record these entries in the Security Audit Log as first-class security events.

Important security events that must be written to the Security Audit Log include:

- policy created
- policy updated
- policy enabled or disabled
- policy mode changed
- policy validation failed
- policy reload failed
- policy fallback mode activated
- runtime detected a matching rule
- runtime blocked content due to policy
- runtime masked content due to policy
- runtime escalated content due to policy
- unauthorized attempt to modify global policy

### FR-23.1 Per-Instance Policy Detection Tracking

The system shall persist policy detection activity with instance attribution.

Each policy detection record shall include at minimum:

- `instance_id`
- `instance_name` if available
- `session_key` if available
- `channel`
- `scope`
- `rule_ids`
- `action`
- `outcome`
- `policy_version`
- `timestamp`

The system shall support querying recent policy detections for each instance.

### FR-23.2 Security Audit Log Integration

The Security Audit Log shall act as the authoritative operational log for policy governance events.

Requirements:

- policy management events must be visible in the Security Audit Log
- runtime enforcement events with security relevance must be visible in the Security Audit Log
- entries must be filterable by `instance_id`, `action`, `scope`, `rule_id`, and time range
- the UI must allow operators to distinguish between configuration-change events and runtime-detection events

### FR-24 Security View Integration

The admin UI Security section shall show:

- current policy status
- current version
- last updated by
- last updated at
- total rules
- enabled rules
- recent enforcement hits
- recent block events
- recent Security Audit Log entries related to policy activity
- policy detected summary per instance

### FR-24.1 Per-Instance Detection View

The admin UI shall provide a view that shows policy detected activity by instance.

Minimum fields:

- instance name
- total detections
- total blocked events
- total masked events
- latest detection timestamp
- top matched rules

The view should support drill-down into recent detection events for a selected instance.

### FR-25 Search and Filter in UI

The UI should support filtering rules by:

- category
- enabled status
- action
- scope
- builtin vs custom

### FR-26 Draft Editing Safety

The UI shall not overwrite the active policy with invalid draft content.

### FR-27 Backup and Recovery

On each successful save, the system should keep a backup copy or versioned snapshot for rollback.

## Non-Functional Requirements

### NFR-1 Performance

- policy check overhead should be low enough to avoid meaningful degradation in normal chat flow
- deterministic rule evaluation should target sub-50ms median for ordinary message sizes in v1

### NFR-2 Availability

- transient policy read failures must not crash the agent loop
- runtime should continue with documented fallback behavior

### NFR-3 Observability

- all enforcement outcomes must be measurable through audit logs
- the admin view should expose recent incidents
- administrators should be able to identify which instances are generating the most policy detections

### NFR-4 Security

- policy mutation endpoints must require privileged RBAC
- runtime should not expose write access to the policy store via workspace tools

### NFR-5 Maintainability

- policy engine implementation should be modular and testable
- detector logic should be isolated from admin UI concerns

### NFR-6 Consistency

- all instances should converge to the same active policy version within the configured cache TTL without restart

### NFR-7 Safe Logging

- the system should log masked previews instead of raw matched content when the content includes secrets or PII

## Architecture Requirements

## Storage Design

Global storage path:

```text
~/.softnix/admin/security/content-intent-policy.json
```

Optional companion files:

```text
~/.softnix/admin/security/content-intent-policy.backup.<timestamp>.json
~/.softnix/admin/security/content-intent-policy.audit.jsonl
```

Optional extension for future versioning:

```text
~/.softnix/admin/security/content-intent-policy.versions/<version>.json
```

## Proposed Runtime Components

Suggested internal components:

- `GlobalControlPolicyStore`
- `PolicyValidator`
- `IntentDetectionEngine`
- `PolicyEnforcer`
- `PolicyCache`
- `PolicyDecision`

Responsibilities:

- `GlobalControlPolicyStore`: load and save policy from control-plane path
- `PolicyValidator`: validate schema and detector configuration
- `IntentDetectionEngine`: execute deterministic detection
- `PolicyEnforcer`: evaluate action and build enforcement decision
- `PolicyCache`: cache current active policy for runtime efficiency
- `PolicyDecision`: normalized result object returned by enforcement points

## Matching Semantics

To avoid inconsistent behavior across detectors, the system should normalize text before matching based on policy defaults.

Recommended normalization behavior in v1:

- trim leading and trailing whitespace
- collapse repeated whitespace to single spaces
- case-fold English text
- preserve original text for output reconstruction and masking

Recommended keyword semantics in v1:

- support whole-token matching by default
- allow optional substring mode only when explicitly configured
- avoid naive substring matching for profanity if it causes obvious false positives

Recommended regex semantics in v1:

- compile on save, not at request time
- reject catastrophic or obviously unsafe regex constructs where feasible
- store flags in structured form rather than inline free-form strings

## Enforcement Architecture

The system should enforce at four points:

1. inbound request
2. outbound response
3. tool invocation
4. memory persistence

## Recommended Agent Workflow

### 1. Policy Load

When the runtime receives a message:

- load cached policy snapshot
- refresh if stale or version changed

### 2. Input Enforcement

Before building the final provider message:

- inspect raw user content
- apply matching rules for `input`
- produce one of:
  - allow
  - warn
  - mask and continue
  - block immediately

If the policy is in `monitor` mode:

- do not alter content
- log what would have happened in enforce mode

### 3. LLM Call

Only sanitized or allowed content may be sent to the provider.

### 4. Output Enforcement

After assistant content is produced:

- inspect generated text
- apply `output` rules
- sanitize or block if required

### 5. Tool Enforcement

Before executing tool calls:

- inspect relevant arguments
- apply `tool_args` rules
- block or sanitize before tool execution

Tool enforcement should run after the model proposes a tool call and before the tool is executed by the runtime.

### 6. Persistence Enforcement

Before memory consolidation or any persistent text write:

- inspect content for `memory_write`
- remove or prevent persistence of controlled content

### 7. Audit

At each enforcement point:

- log match metadata
- log action taken
- log affected scope

Where applicable, the system should also log:

- tool name
- channel
- instance identifier
- policy version used for the decision

## Recommended Decision Model

The policy engine should return a structured decision:

```json
{
  "status": "matched",
  "scope": "input",
  "action": "mask",
  "matched_rules": ["pii-email", "secret-api-key"],
  "sanitized_text": "Please contact [REDACTED_EMAIL].",
  "user_message": "Sensitive personal data was sanitized according to organization policy.",
  "audit": {
    "hit_count": 2,
    "highest_severity": "critical"
  }
}
```

## UI Requirements

## Security Menu Additions

Add a dedicated area in Security:

- Global Control Policy
- Policy Rules
- Recent Hits
- Policy Change History

## UI Capabilities

The UI shall support:

- view active policy metadata
- view and edit rules
- create custom rule
- enable or disable rule
- change action and scope
- validate draft
- save policy
- view recent enforcement hits
- view recent update audit log

The UI should also support a read-only mode for non-editing users.

## UI Editing Model

Recommended editing flow:

1. load active policy
2. edit in structured form
3. validate draft
4. save draft as active policy
5. display save result and version increment

Recommended safety controls:

- dirty state warning before navigation
- server-side validation result panel
- optional confirmation before activating new policy

## RBAC Requirements

Current permission design already includes `security.read` but does not cleanly separate global policy mutation.

Required new permissions:

- `security.read`
- `security.update`
- `security.audit.read`

Recommended role mapping:

- Viewer: `security.read`
- Operator: `security.read`, optionally `security.audit.read`
- Admin: `security.read`, `security.audit.read`
- Owner: `security.read`, `security.audit.read`, `security.update`

If the team prefers Admin to edit policy, that decision must be explicit and documented.

## API Requirements

Recommended endpoints:

### GET `/admin/security/policies/global`

Returns:

- active policy
- version
- metadata

### PATCH `/admin/security/policies/global`

Updates the global policy after validation.

Request:

- full policy payload or explicit patch model

Response:

- saved policy
- new version
- updated metadata

Behavior requirements:

- save must be atomic
- partial writes must not corrupt active policy
- failed save must leave previous active policy untouched

### POST `/admin/security/policies/global/validate`

Validates a draft payload without activating it.

Response:

- valid boolean
- errors array
- warnings array

### GET `/admin/security/policies/global/audit`

Returns recent policy update and enforcement events.

This endpoint shall read from the Security Audit Log view of policy-related events.

### GET `/admin/security/policies/global/hits`

Returns recent policy hit summary with filters.

Suggested filters:

- rule_id
- action
- scope
- instance_id
- time range

### GET `/admin/security/policies/global/detections-by-instance`

Returns aggregated policy detection activity grouped by instance.

Suggested response fields:

- `instance_id`
- `instance_name`
- `detection_count`
- `blocked_count`
- `masked_count`
- `warn_count`
- `latest_detected_at`
- `top_rules`

## API Validation Rules

- reject invalid regex
- reject duplicate rule IDs
- reject unsupported action or scope values
- reject malformed detector definitions
- reject save when payload is not schema-valid

## Audit and Event Requirements

Recommended audit event names:

- `security.policy_global_created`
- `security.policy_global_updated`
- `security.policy_global_enabled`
- `security.policy_global_disabled`
- `security.policy_mode_changed`
- `security.policy_global_validation_failed`
- `security.policy_reload_failed`
- `security.policy_fallback_activated`
- `security.policy_enforcement_hit`
- `security.policy_enforcement_masked`
- `security.policy_enforcement_blocked`
- `security.policy_enforcement_escalated`
- `security.policy_update_denied`

Required audit fields:

- timestamp
- actor
- instance_id if applicable
- instance_name if available
- session_key if applicable
- scope
- rule_ids
- action
- outcome
- preview or masked preview

Recommended additional fields:

- policy_version
- detector_types
- matched_count
- tool_name when applicable
- event_family such as `policy_admin` or `policy_runtime`
- channel when applicable

Important:

- logs must avoid storing raw secrets or raw PII in plaintext when the action is mask or block

## Data Privacy Requirements

- redacted or masked values should be preferred in logs
- raw blocked content should not be copied verbatim into audit logs if it contains secrets or regulated PII
- user-facing responses should not reveal internal policy implementation details

## Error Handling Requirements

### Invalid Policy on Save

- return validation errors
- keep active policy unchanged
- log validation failure
- return field-level diagnostics for UI display

### Missing Policy at Runtime

- load built-in safe baseline or previously cached valid version
- log degradation event

### Corrupt Policy File at Runtime

- do not crash request processing
- use last known valid cached policy if available
- otherwise use documented fallback mode

The runtime should emit a health signal so the Security view can show degraded policy state.

## Migration and Rollout Requirements

### Phase 1

- implement global policy storage
- implement read-only runtime loading
- implement inbound and outbound enforcement
- implement admin UI view and edit
- implement monitor mode

### Phase 2

- implement tool argument enforcement
- implement hit dashboards and trend reporting
- expand built-in detectors

### Phase 3

- add optional classifier for ambiguous matches
- consider scoped overrides if genuinely required

## Testing Requirements

### Unit Tests

- policy schema validation
- regex compilation and validation
- keyword matching
- phrase matching
- pii detector matching
- action resolution
- rule priority resolution
- fail-open and fail-closed behaviors
- monitor vs enforce mode behavior
- normalization behavior

### Integration Tests

- owner can save valid policy
- non-privileged user cannot save policy
- all instances load new policy version
- blocked input never reaches provider
- masked input reaches provider only in sanitized form
- blocked output never reaches channel
- blocked tool arg never executes tool
- policy version refresh propagates across multiple running instances

### Admin UI Tests

- load active policy
- show validation errors
- save valid policy
- prevent invalid save
- show recent hits and audit entries
- show per-instance detection summary
- filter Security Audit Log by instance and rule

### Security Tests

- attempt workspace-based mutation should not alter global policy
- runtime should not expose writable path to policy through normal user workflows
- logs should not leak raw blocked secrets
- Security Audit Log should contain policy lifecycle and runtime detection events
- per-instance detection query should return correct instance attribution

## Acceptance Criteria

### AC-1

Given a valid global policy update by an authorized Owner, when the policy is saved, then the new policy becomes active for all instances.

### AC-2

Given a user without `security.update`, when they attempt to modify the policy, then the system denies the action and records the attempt.

### AC-3

Given an input that matches a `block` rule in `input` scope, when the message is received, then the provider is not called and the user receives a policy response.

### AC-4

Given an input that matches a `mask` rule in `input` scope, when the message is processed, then the provider receives sanitized text only.

### AC-5

Given an assistant response that matches a `block` rule in `output` scope, when the response is ready to send, then the original output is withheld and a safe response is returned.

### AC-6

Given a sensitive tool argument that matches a `block` rule, when a tool call is about to execute, then the tool is not executed and the event is audited.

### AC-7

Given an invalid policy draft, when validation fails, then the active policy remains unchanged.

### AC-8

Given a runtime policy file read failure, when a message is processed, then the runtime follows configured fallback behavior and remains available.

### AC-9

Given policy mode is `monitor`, when content matches a `block` rule, then the content is not blocked but the system records a monitor-mode hit showing the would-be action.

### AC-10

Given multiple running instances with cached policy state, when a new global policy version is saved, then all instances apply the new version within the configured cache TTL.

### AC-11

Given a policy match occurs in one instance, when the detection is recorded, then the Security Audit Log contains an entry with that instance attribution and policy action.

### AC-12

Given multiple instances generate policy detections, when an administrator opens the per-instance detection view, then they can identify detection counts and recent events for each instance.

## Open Decisions

The following items should be finalized before implementation:

1. Should `security.update` belong only to Owner or also to Admin?
2. Should `escalate` block immediately in v1, or create a review queue?
3. Which PII formats are mandatory for Thailand-specific deployments in v1?
4. Should there be a small built-in immutable baseline that cannot be disabled from UI?
5. Should policy edits require confirmation or dual approval?

## Recommended v1 Decisions

- editing permission: Owner only
- `escalate`: behave as block plus audit marker in v1
- PII coverage: email, phone, API-key-like, token-like first
- immutable baseline: optional but recommended for secret leakage
- approval workflow: not required in v1

## Implementation Notes for Engineering

- do not model this feature as workspace memory
- do not rely on prompt-only instructions for enforcement
- insert enforcement before provider calls and before tool execution
- make runtime integration additive and modular
- keep rule matching pure and deterministic for testability

## Suggested Deliverables

Engineering should be able to build from this spec with the following concrete outputs:

- policy schema and validator
- global policy store
- runtime enforcement hooks
- admin API endpoints
- admin UI Security policy editor
- audit event support
- automated tests

## Final Recommendation

Build v1 as a global deterministic control-plane policy system with strict RBAC and auditability. Keep the architecture global-only and centralized. Do not mix policy with workspace memory. Treat semantic classification as a future enhancement after the deterministic enforcement path is stable in production.
