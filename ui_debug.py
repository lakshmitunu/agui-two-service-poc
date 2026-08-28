"""Human-readable developer diagnostics for the Streamlit AG-UI client."""

from __future__ import annotations

import json
from typing import Any


def display_name(value: str | None) -> str:
    return (value or "pending").replace("-", " ").replace("_", " ").title()


def event_description(event: dict[str, Any]) -> str:
    event_type = event.get("type", "UNKNOWN")
    if event_type == "RUN_STARTED":
        return "Run started"
    if event_type == "STEP_STARTED":
        return f"Started: {display_name(event.get('stepName'))}"
    if event_type == "STEP_FINISHED":
        return f"Completed: {display_name(event.get('stepName'))}"
    if event_type == "CUSTOM" and event.get("name") == "specialist-selected":
        specialist = event.get("value", {}).get("specialist")
        return f"Selected {display_name(specialist)}"
    if event_type == "SUBAGENT_STARTED":
        return f"Started {display_name(event.get('name'))}"
    if event_type == "SUBAGENT_FINISHED":
        return "Specialist completed"
    if event_type == "ACTIVITY_SNAPSHOT":
        return event.get("content", {}).get("message", "Activity updated")
    if event_type == "TOOL_CALL_START":
        return f"Tool started: {event.get('toolCallName', 'unknown')}"
    if event_type == "TOOL_CALL_ARGS":
        return "Tool argument chunk"
    if event_type == "TOOL_CALL_END":
        return "Tool arguments complete"
    if event_type == "TOOL_CALL_RESULT":
        return "Tool result received"
    if event_type == "TEXT_MESSAGE_START":
        return "Answer streaming started"
    if event_type == "TEXT_MESSAGE_CONTENT":
        return "Answer text chunk"
    if event_type == "TEXT_MESSAGE_END":
        return "Answer streaming completed"
    if event_type == "STATE_SNAPSHOT":
        return "Synchronized state updated"
    if event_type == "CUSTOM" and event.get("name") == "cost-metrics":
        return "Cost estimate received"
    if event_type == "RUN_FINISHED":
        return "Run finished"
    if event_type == "RUN_ERROR":
        return f"Run failed: {event.get('message', 'unknown error')}"
    return display_name(event_type)


def grouped_timeline(timed_events: list[dict[str, Any]]) -> str:
    """Group consecutive high-volume delta events into readable timeline rows."""
    grouped: list[dict[str, Any]] = []
    groupable = {"TEXT_MESSAGE_CONTENT", "TOOL_CALL_ARGS"}
    for item in timed_events:
        event = item["event"]
        event_type = event.get("type", "UNKNOWN")
        if grouped and event_type in groupable and grouped[-1]["type"] == event_type:
            grouped[-1]["count"] += 1
            continue
        grouped.append(
            {
                "type": event_type,
                "elapsed": item["elapsed"],
                "description": event_description(event),
                "count": 1,
            }
        )

    if not grouped:
        return "_Waiting for events…_"
    lines = []
    for item in grouped:
        icon = "❌" if item["type"] == "RUN_ERROR" else "✓"
        count = f" × {item['count']}" if item["count"] > 1 else ""
        lines.append(f"{icon} `{item['elapsed']:.2f}s` {item['description']}{count}")
    return "  \n".join(lines)


def routing_markdown(route: dict[str, Any]) -> str:
    return (
        f"**{display_name(route.get('master', 'master agent'))}** → "
        f"**{display_name(route.get('specialist'))}**"
    )


def agent_tree_markdown(master_status: str, agents: dict[str, dict[str, Any]]) -> str:
    """Render any number of attributed subagent invocations as a readable tree."""
    master_icon = {"running": "⏳", "complete": "✓", "error": "❌"}.get(master_status, "•")
    lines = [f"- {master_icon} **Master Agent** · {display_name(master_status)}"]
    if not agents:
        lines.append("  - _Waiting for specialist selection…_")
        return "\n".join(lines)

    for agent in agents.values():
        status = agent.get("status", "running")
        icon = {"running": "⏳", "complete": "✓", "error": "❌"}.get(status, "•")
        duration = f" · {agent['duration']:.2f}s" if agent.get("duration") is not None else ""
        lines.append(f"  - {icon} **{display_name(agent.get('name'))}** · {display_name(status)}{duration}")
        metadata = agent.get("metadata", {})
        if metadata.get("agentId"):
            lines.append(
                f"    - Agent ID: `{metadata['agentId']}` · Run: `{agent.get('subagentRunId', '—')}`"
            )
        for tool_name in agent.get("tools", []):
            lines.append(f"    - Tool: `{tool_name}`")
        if agent.get("error"):
            lines.append(f"    - Error: {agent['error']}")
    return "\n".join(lines)


def tool_markdown(tool: dict[str, Any]) -> str:
    if tool.get("status") == "waiting":
        return "_Waiting for tool activity…_"
    arguments = tool.get("arguments", "")
    try:
        arguments_display = json.dumps(json.loads(arguments), indent=2)
    except (TypeError, json.JSONDecodeError):
        arguments_display = arguments or "Waiting for arguments…"
    result_display = json.dumps(tool.get("result", {}), indent=2)
    status_icon = "✓" if tool.get("status") == "complete" else "⏳"
    return (
        f"**{tool.get('name', 'unknown')}** · {status_icon} {display_name(tool.get('status'))}\n\n"
        f"Arguments\n```json\n{arguments_display}\n```\n"
        f"Result\n```json\n{result_display}\n```"
    )


def metrics_markdown(metrics: dict[str, Any]) -> str:
    usage = (metrics.get("standardTokenUsage") or [{}])[0]
    cost = metrics.get("cost", {})
    if not usage and not cost:
        return "_Waiting for usage data…_"
    rows = [
        ("Provider", usage.get("provider") or cost.get("provider") or "—"),
        ("Model", usage.get("model") or cost.get("model") or "—"),
        ("Input tokens", usage.get("inputTokens", "—")),
        ("Output tokens", usage.get("outputTokens", "—")),
        ("Total tokens", usage.get("totalTokens", "—")),
        ("Estimated cost", f"${cost['estimatedCostUsd']:.6f}" if "estimatedCostUsd" in cost else "—"),
    ]
    return "| Metric | Value |\n|---|---:|\n" + "\n".join(f"| {key} | {value} |" for key, value in rows)


def state_markdown(state: dict[str, Any]) -> str:
    if not state:
        return "_Waiting for synchronized state…_"
    rows = [
        ("Specialist", display_name(state.get("selectedSpecialist"))),
        ("Last tool", state.get("lastTool", "—")),
        ("Status", display_name(state.get("status"))),
        ("Sources", len(state.get("sources", []))),
    ]
    return "| Field | Value |\n|---|---|\n" + "\n".join(f"| {key} | {value} |" for key, value in rows)


def run_summary_markdown(
    *, thread_id: str, run_id: str, status: str, elapsed: float | None = None
) -> str:
    icon = {"running": "⏳", "complete": "✓", "error": "❌"}.get(status, "•")
    duration = f" · {elapsed:.2f}s" if elapsed is not None else ""
    return (
        f"### {icon} Run {display_name(status)}{duration}\n"
        f"`run {run_id}`  \n`thread {thread_id}`"
    )


def final_response_markdown(result: dict[str, Any], chunk_count: int) -> str:
    final_response = result.get("finalResponse")
    if not final_response:
        return "_Waiting for the final response…_"
    return f"**{chunk_count} text chunks combined**\n\n> {final_response}"
