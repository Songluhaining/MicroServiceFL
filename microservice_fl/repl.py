"""A node-free interactive REPL for watching the fault-localization agent.

OpenHarness's default interactive UI is a React/Ink TUI that needs Node.js. This
is a minimal pure-Python REPL over the *same* runtime (``build_runtime`` /
``handle_line``) that prints each ``fl_*`` tool call and its result, so you can
watch the localization happen step by step without installing Node.

Run it via ``run_fl.ps1`` (interactive mode) or directly::

    python -m microservice_fl.repl

Type an incident and press Enter, e.g.::

    /locate time=2026-06-05T01:03:31Z~2026-06-05T01:06:59Z symptom=/admin-api/system/mail-account/delete-list slow

``/exit`` (or Ctrl-C) quits. Headless ``oh -p`` is unaffected; this is only for
interactive observation.
"""

from __future__ import annotations

import asyncio
import sys


def _preview(text: str, limit: int = 600) -> str:
    text = (text or "").strip().replace("\r", "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n    ... ({len(text)} chars total, truncated)"


async def _run() -> None:
    from openharness.ui.runtime import (
        build_runtime,
        close_runtime,
        handle_line,
        start_runtime,
    )
    from openharness.engine.stream_events import (
        AssistantTextDelta,
        AssistantTurnComplete,
        ErrorEvent,
        StatusEvent,
        ToolExecutionCompleted,
        ToolExecutionStarted,
    )

    async def _permission(_tool_name: str, _reason: str) -> bool:
        return True

    async def _ask(_question: str) -> str:
        return ""

    async def _print_system(message: str) -> None:
        if message:
            print(message, flush=True)

    async def _render(event: object) -> None:
        if isinstance(event, AssistantTextDelta):
            sys.stdout.write(event.text)
            sys.stdout.flush()
        elif isinstance(event, AssistantTurnComplete):
            sys.stdout.write("\n")
            sys.stdout.flush()
        elif isinstance(event, ToolExecutionStarted):
            print(f"\n  >> tool: {event.tool_name}  {event.tool_input}", flush=True)
        elif isinstance(event, ToolExecutionCompleted):
            tag = "ERROR" if event.is_error else "ok"
            print(f"  << [{tag}]\n    {_preview(event.output)}\n", flush=True)
        elif isinstance(event, ErrorEvent):
            print(f"[error] {event.message}", flush=True)
        elif isinstance(event, StatusEvent) and event.message:
            print(event.message, flush=True)

    async def _clear() -> None:
        return None

    bundle = await build_runtime(
        prompt=None,
        cwd=".",
        permission_mode="full_auto",  # PermissionMode enum value (CLI's "auto")
        permission_prompt=_permission,
        ask_user_prompt=_ask,
    )
    await start_runtime(bundle)
    print("MicroServiceFL REPL - type an incident, e.g.:")
    print("  /locate time=<start>~<end> symptom=<endpoint or description>")
    print("  (/exit or Ctrl-C to quit)")
    try:
        while True:
            try:
                line = (await asyncio.to_thread(input, "\n> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in {"/exit", "/quit"}:
                break
            await handle_line(
                bundle,
                line,
                print_system=_print_system,
                render_event=_render,
                clear_output=_clear,
            )
    finally:
        await close_runtime(bundle)


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
