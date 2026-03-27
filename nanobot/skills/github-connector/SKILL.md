---
name: github-connector
description: "Use the GitHub connector tools for repository, issue, pull request, workflow, and repository search tasks."
metadata: {"nanobot":{"emoji":"🐙","always":true}}
---

# GitHub Connector

Use the GitHub connector when the task is about a repository, issue, pull request, workflow run, or repository search.

## When To Use It

- Inspect repository metadata
- List or read issues
- Inspect pull requests
- Check workflow runs
- Search repositories

## Usage Rules

- Prefer the GitHub connector tools over ad-hoc web scraping when they are available.
- If the connector is unavailable, fall back to the existing GitHub CLI skill.
- Runtime tool names are prefixed as `mcp_github_*`; use those tool names when calling tools.
- If the user does not specify a repository, first check `mcp_github_get_connector_context` to see the configured `effective_repo`.
- Only ask for `owner/repo` when `mcp_github_get_connector_context` shows no effective repo.
- Summarize GitHub data directly and clearly. Do not paste raw JSON unless the user asks for it.

## Safety

- Avoid write actions unless the user explicitly asks for them.
- Treat token-backed repository access as sensitive.
- If the repository is private or the response is incomplete, say so explicitly.

## Common Patterns

- `mcp_github_get_connector_context` before repo-omitted requests
- `mcp_github_whoami` for token validation
- `mcp_github_get_repository` for repo metadata
- `mcp_github_list_issues` for triage and status
- `mcp_github_get_issue` for issue details
- `mcp_github_get_pull_request` for PR review
- `mcp_github_get_latest_commit` or `mcp_github_list_commits` for recent commit checks
- `mcp_github_list_workflow_runs` for CI/CD status
- `mcp_github_search_repositories` for discovery

## Repo Selection

- If the user does not specify a repository, prefer the connector's `effective_repo` from `mcp_github_get_connector_context`.
- If `effective_repo` is empty, the connector will try the current git remote automatically.
- If both are empty, ask for `owner/repo` explicitly instead of guessing.
