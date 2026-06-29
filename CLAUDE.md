# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OpenHarness is an open-source Python agent harness — the infrastructure layer (tools, skills, memory, permissions, multi-agent coordination) wrapped around an LLM to make it a functional coding agent. It ships two console apps:

- **`oh`** (aliases `openharness`, `openh`) — the interactive/headless coding agent. Entry point: `openharness.cli:app`. On Windows PowerShell use `openh`; `oh` collides with the `Out-Host` alias.
- **`ohmo`** — a personal-agent app built on top of OpenHarness that runs a gateway connecting to chat platforms (Feishu/Slack/Telegram/Discord). Entry point: `ohmo.cli:app`. Lives in the top-level `ohmo/` package (not under `src/`).

## Commands

```bash
# Dev setup (uv is the package manager)
uv sync --extra dev

# Lint (what CI runs)
uv run ruff check src tests scripts

# Run the full unit/integration suite
uv run pytest -q

# Run a single test file / test / by keyword
uv run pytest tests/test_engine -q
uv run pytest tests/test_tools/test_bash_tool.py::test_name -q
uv run pytest -k "permission and deny" -q

# Type check (NOT a required green check yet; mypy strict is configured but the repo isn't fully clean)
uv run mypy src/openharness

# Frontend (React/Ink TUI) checks
cd frontend/terminal && npm ci && npx tsc --noEmit
```

E2E scripts in `scripts/` make **real model calls** and are not part of `pytest`. Run them directly with `python`, e.g. `python scripts/test_harness_features.py`, `python scripts/test_cli_flags.py`, `python scripts/test_real_skills_plugins.py`.

Tests use `asyncio_mode = "auto"` (pytest-asyncio) — async test functions need no decorator. `testpaths = ["tests"]`. Test directories mirror `src/openharness/` subsystems one-to-one (`tests/test_engine`, `tests/test_tools`, etc.).

## Architecture

The whole system is one **agent loop**: stream a model response → if `stop_reason == "tool_use"`, run the tool calls (through permissions + hooks) → append results → loop. Everything else is infrastructure feeding that loop.

### Layers, roughly outermost to innermost

- **`cli.py`** — Typer app. Parses flags/subcommands (`oh setup | provider | auth | mcp | plugin`), implements `--dry-run` (resolves settings/auth/skills/tools/MCP *without* calling the model or running tools), and `--output-format text|json|stream-json` for headless use (`-p`).
- **`ui/`** — `runtime.py` defines `RuntimeBundle` and `build_runtime`/`start_runtime`/`handle_line`/`close_runtime`, the seam everything (CLI, ohmo, tests) goes through to drive a session. The React/Ink TUI frontend lives in `frontend/terminal/` (TypeScript) and talks to a Python backend host (`ui/backend_host.py`) over a line protocol.
- **`engine/`** — `query_engine.py` (`QueryEngine`) owns conversation history and the tool-aware loop; `query.py` runs a single query turn; `messages.py` is the message/content-block model; `cost_tracker.py` and `stream_events.py` handle usage and the streamed event types consumed by the UI.
- **`api/`** — provider abstraction. `client.py` defines `SupportsStreamingMessages`; concrete clients are `codex_client.py`, `openai_client.py`, `copilot_client.py` (plus `copilot_auth.py`). `provider.py` + `registry.py` resolve a provider **profile** to a client. Providers are modeled as named profiles/"workflows" (Anthropic-compatible, Claude/Codex subscription bridges, OpenAI-compatible, GitHub Copilot).
- **`tools/`** — every capability is a `BaseTool` (`tools/base.py`) with a Pydantic `input_model`, an `async execute(arguments, context) -> ToolResult`, and a JSON Schema auto-derived for the model. `ToolRegistry` holds them. To add a tool: subclass `BaseTool`, set `name`/`description`/`input_model`, register it. File names map to tools (`bash_tool.py`, `file_edit_tool.py`, `agent_tool.py`, `task_*_tool.py`, …).
- **`permissions/`** — `PermissionChecker` gates every tool call before execution (modes: default/auto/plan; path rules; denied commands; built-in sensitive-path protection). Configured via `settings.json`.
- **`hooks/`** — PreToolUse/PostToolUse lifecycle events (`HookExecutor`, `HookEvent`) fired around tool execution.
- **`skills/`** — on-demand `.md` knowledge loaded only when needed; compatible with the `anthropics/skills` `SKILL.md` layout. Discovered from bundled, user (`~/.openharness`, `~/.claude`, `~/.agents`), and project (`<project>/.openharness|.agents|.claude`) locations.
- **`plugins/`** — commands + hooks + agents + MCP servers; compatible with claude-code plugin format.
- **`commands/`** — slash commands (`/help`, `/commit`, `/plan`, `/resume`, …).
- **`prompts/`** — system prompt assembly: `system_prompt.py`, `claudemd.py` (CLAUDE.md discovery/injection), `context.py`, `environment.py`.
- **`mcp/`** — Model Context Protocol client (stdio + HTTP transport, auto-reconnect).
- **`memory/`** — persistent cross-session knowledge (MEMORY.md-style).
- **`config/`** — multi-layer settings (`settings.py`, `schema.py`, `paths.py`) with migrations.

### Multi-agent (two related subsystems — don't confuse them)

- **`coordinator/`** — coordinator mode and agent definitions; injects coordinator context into a session.
- **`swarm/`** — actual subagent execution: `in_process.py` and `subprocess_backend.py` backends, `registry.py` (team registry), `mailbox.py` (inter-agent messaging), `worktree.py` (git-worktree isolation), `team_lifecycle.py`, `lockfile.py`, `permission_sync.py`.
- **`tasks/`** — background task lifecycle (the `TaskCreate/Get/List/Update/Stop/Output` tools).
- **`bridge/`** — `BridgeSessionManager` spawns and tracks child `oh` sessions (used by subprocess teammates / `ohmo`).

### ohmo specifics

`ohmo/` reuses the OpenHarness runtime (`build_runtime` et al.) but adds its own workspace and gateway:

- `ohmo/workspace.py` — `~/.ohmo` workspace (`soul.md`, `identity.md`, `user.md`, `BOOTSTRAP.md`, `memory/`, `gateway.json`).
- `ohmo/gateway/` — `service.py`/`runtime.py`/`router.py`/`bridge.py` run the long-lived gateway; `src/openharness/channels/` holds the platform adapters (Slack/Telegram/Discord/Feishu) it routes through.
- `ohmo/session_storage.py`, `ohmo/memory.py`, `ohmo/prompts.py` — ohmo-specific session/memory/prompt backends.

## Conventions

- Python `>=3.10`; ruff line-length 100, `target-version = py311`. `from __future__ import annotations` is used throughout.
- mypy is configured `strict` but not yet a required green gate for the whole repo — improving type coverage is welcome, failing mypy is not blocking.
- When CLI flags, provider workflows, or compatibility claims change, update `README.md` and add an `Unreleased` entry in `CHANGELOG.md` (per CONTRIBUTING).
- Keep PRs scoped; add/update tests in the mirrored `tests/<subsystem>/` dir when behavior changes.
