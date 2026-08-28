"""Streamlit AG-UI client with separate user and developer experiences."""

from __future__ import annotations

import json
import os
import re
import time
from uuid import uuid4

import httpx
import streamlit as st

from ui_debug import (
    agent_tree_markdown,
    final_response_markdown,
    grouped_timeline,
    metrics_markdown,
    run_summary_markdown,
    state_markdown,
    tool_markdown,
)

AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8000/agent")


def placeholder_mask(text: str) -> str:
    """POC-only masking: replace email addresses before invoking the agent."""
    return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[MASKED_EMAIL]", text)


def user_status_for_event(event: dict) -> str | None:
    """Translate protocol details into concise, non-technical user progress."""
    event_type = event.get("type")
    if event_type == "RUN_STARTED":
        return "Understanding your question…"
    if event_type == "STEP_STARTED":
        return "Selecting the right knowledge source…"
    if event_type == "SUBAGENT_STARTED":
        return "Processing your request…"
    if event_type == "ACTIVITY_SNAPSHOT":
        return "Searching approved information…"
    if event_type == "TOOL_CALL_START":
        return "Checking the relevant source…"
    if event_type == "TOOL_CALL_RESULT":
        return "Relevant information found"
    if event_type == "TEXT_MESSAGE_START":
        return "Preparing your answer…"
    return None


def sources_markdown(sources: list[dict]) -> str:
    if not sources:
        return ""
    lines = ["**Sources**"]
    for index, source in enumerate(sources, start=1):
        lines.append(f"{index}. {source.get('title', 'Source')}")
    return "\n\n".join(lines)


def messages_for_agent(messages: list[dict]) -> list[dict]:
    """Keep original text in the UI while sending masked user text to the agent."""
    return [
        {
            "id": message["id"],
            "role": message["role"],
            "content": message.get("agent_content", message["content"]),
        }
        for message in messages
    ]


st.set_page_config(page_title="Enterprise Assistant", page_icon="💬", layout="centered")
st.title("Enterprise Assistant")
st.caption("Ask questions and receive answers grounded in approved information.")

if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []
if "agent_state" not in st.session_state:
    st.session_state.agent_state = {}
if "run_metrics" not in st.session_state:
    st.session_state.run_metrics = {}

with st.sidebar:
    st.subheader("Example questions")
    st.code("Calculate 24 * 7", language=None)
    st.code("What is our leave policy?", language=None)
    st.divider()
    developer_debug = st.toggle(
        "Developer debug",
        value=False,
        help="Show routing, tools, raw AG-UI events, state, and metrics.",
    )
    if developer_debug:
        st.caption("Debug information is enabled for this session.")
        show_raw_events = st.toggle(
            "Show raw protocol events",
            value=False,
            help="Show exact AG-UI JSON after the human-readable diagnostics.",
        )
    else:
        show_raw_events = False

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("sources"):
            st.markdown(sources_markdown(message["sources"]))

if prompt := st.chat_input("Ask a math or knowledge question"):
    masked_prompt = placeholder_mask(prompt)
    user_message = {
        "id": str(uuid4()),
        "role": "user",
        "content": prompt,
        "agent_content": masked_prompt,
    }
    st.session_state.messages.append(user_message)

    with st.chat_message("user"):
        st.markdown(prompt)
        if masked_prompt != prompt:
            st.caption("Sensitive information was protected before processing.")

    run_id = str(uuid4())
    request_body = {
        "threadId": st.session_state.thread_id,
        "runId": run_id,
        "messages": messages_for_agent(st.session_state.messages),
        "state": st.session_state.agent_state,
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }

    assembled = ""
    final_response_from_server: str | None = None
    current_sources: list[dict] = []
    event_log: list[dict] = []
    timed_events: list[dict] = []
    text_chunk_count = 0
    started_at = time.perf_counter()
    tool_view: dict = {"status": "waiting"}
    master_status = "running"
    agent_runs: dict[str, dict] = {}

    with st.chat_message("assistant"):
        progress = st.status("Starting…", expanded=True)
        answer_slot = st.empty()
        sources_slot = st.empty()

        raw_event_slot = summary_slot = timeline_slot = None
        agent_tree_slot = tool_slot = final_response_slot = None
        metrics_slot = state_slot = None

        if developer_debug:
            with st.expander("Developer debug details", expanded=True):
                summary_slot = st.empty()
                summary_slot.markdown(
                    run_summary_markdown(
                        thread_id=st.session_state.thread_id,
                        run_id=run_id,
                        status="running",
                    )
                )
                st.markdown("**Agent execution**")
                agent_tree_slot = st.empty()
                agent_tree_slot.markdown(agent_tree_markdown(master_status, agent_runs))
                st.markdown("**Execution timeline**")
                timeline_slot = st.empty()
                timeline_slot.markdown(grouped_timeline(timed_events))
                st.markdown("**Tool execution**")
                tool_slot = st.empty()
                tool_slot.markdown(tool_markdown(tool_view))
                st.markdown("**Final response**")
                final_response_slot = st.empty()
                final_response_slot.markdown(final_response_markdown({}, 0))
                st.markdown("**Metrics**")
                metrics_slot = st.empty()
                metrics_slot.markdown(metrics_markdown(st.session_state.run_metrics))
                st.markdown("**Synchronized state**")
                state_slot = st.empty()
                state_slot.markdown(state_markdown(st.session_state.agent_state))
                if show_raw_events:
                    st.markdown("**Raw protocol events**")
                    raw_event_slot = st.empty()
                    raw_event_slot.json(event_log)

        try:
            last_user_status = None
            with httpx.stream(
                "POST",
                AGENT_URL,
                json=request_body,
                headers={"Accept": "text/event-stream"},
                timeout=60,
                trust_env=False,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line.removeprefix("data:").strip())
                    event_log.append(event)
                    timed_events.append(
                        {"elapsed": time.perf_counter() - started_at, "event": event}
                    )
                    if timeline_slot is not None:
                        timeline_slot.markdown(grouped_timeline(timed_events))
                    if raw_event_slot is not None:
                        raw_event_slot.json(event_log)

                    user_status = user_status_for_event(event)
                    if user_status and user_status != last_user_status:
                        progress.write(user_status)
                        last_user_status = user_status

                    event_type = event["type"]
                    if event_type == "TEXT_MESSAGE_CONTENT":
                        text_chunk_count += 1
                        assembled += event["delta"]
                        answer_slot.markdown(assembled + "▌")
                    elif event_type == "STATE_SNAPSHOT":
                        st.session_state.agent_state = event["snapshot"]
                        current_sources = event["snapshot"].get("sources", [])
                        if state_slot is not None:
                            state_slot.markdown(state_markdown(event["snapshot"]))
                    elif event_type == "SUBAGENT_STARTED":
                        subagent_run_id = event["subagentRunId"]
                        agent_runs[subagent_run_id] = {
                            "subagentRunId": subagent_run_id,
                            "name": event.get("name"),
                            "description": event.get("description"),
                            "metadata": event.get("metadata", {}),
                            "status": "running",
                            "startedAt": time.perf_counter() - started_at,
                            "duration": None,
                            "tools": [],
                        }
                        if agent_tree_slot is not None:
                            agent_tree_slot.markdown(agent_tree_markdown(master_status, agent_runs))
                    elif event_type in {"SUBAGENT_FINISHED", "SUBAGENT_ERROR"}:
                        subagent_run_id = event["subagentRunId"]
                        agent = agent_runs.setdefault(
                            subagent_run_id,
                            {
                                "subagentRunId": subagent_run_id,
                                "name": "unknown-agent",
                                "metadata": {},
                                "startedAt": 0.0,
                                "tools": [],
                            },
                        )
                        agent["status"] = "error" if event_type == "SUBAGENT_ERROR" else "complete"
                        agent["duration"] = (time.perf_counter() - started_at) - agent["startedAt"]
                        if event_type == "SUBAGENT_ERROR":
                            agent["error"] = event.get("message")
                        if agent_tree_slot is not None:
                            agent_tree_slot.markdown(agent_tree_markdown(master_status, agent_runs))
                    elif event_type == "TOOL_CALL_START":
                        tool_view = {
                            "status": "streaming arguments",
                            "name": event["toolCallName"],
                            "toolCallId": event["toolCallId"],
                            "arguments": "",
                        }
                        attributed_agent = agent_runs.get(event.get("subagentRunId"))
                        if attributed_agent is not None:
                            attributed_agent["tools"].append(event["toolCallName"])
                            if agent_tree_slot is not None:
                                agent_tree_slot.markdown(
                                    agent_tree_markdown(master_status, agent_runs)
                                )
                        if tool_slot is not None:
                            tool_slot.markdown(tool_markdown(tool_view))
                    elif event_type == "TOOL_CALL_ARGS":
                        tool_view["arguments"] += event["delta"]
                        if tool_slot is not None:
                            tool_slot.markdown(tool_markdown(tool_view))
                    elif event_type == "TOOL_CALL_END":
                        tool_view["status"] = "executing"
                        if tool_slot is not None:
                            tool_slot.markdown(tool_markdown(tool_view))
                    elif event_type == "TOOL_CALL_RESULT":
                        tool_view["status"] = "complete"
                        tool_view["result"] = json.loads(event["content"])
                        if tool_slot is not None:
                            tool_slot.markdown(tool_markdown(tool_view))
                    elif event_type == "CUSTOM" and event.get("name") == "cost-metrics":
                        st.session_state.run_metrics = {
                            **st.session_state.run_metrics,
                            "cost": event["value"],
                        }
                        if metrics_slot is not None:
                            metrics_slot.markdown(metrics_markdown(st.session_state.run_metrics))
                    elif event_type == "RUN_FINISHED":
                        master_status = "complete"
                        final_response_from_server = event.get("result", {}).get("finalResponse")
                        if event.get("usage"):
                            st.session_state.run_metrics = {
                                **st.session_state.run_metrics,
                                "standardTokenUsage": event["usage"],
                            }
                        if metrics_slot is not None:
                            metrics_slot.markdown(metrics_markdown(st.session_state.run_metrics))
                        if final_response_slot is not None:
                            final_response_slot.markdown(
                                final_response_markdown(event.get("result", {}), text_chunk_count)
                            )
                        if summary_slot is not None:
                            summary_slot.markdown(
                                run_summary_markdown(
                                    thread_id=st.session_state.thread_id,
                                    run_id=run_id,
                                    status="complete",
                                    elapsed=time.perf_counter() - started_at,
                                )
                            )
                        if agent_tree_slot is not None:
                            agent_tree_slot.markdown(agent_tree_markdown(master_status, agent_runs))
                    elif event_type == "RUN_ERROR":
                        raise RuntimeError(event["message"])

            completed_response = final_response_from_server or assembled
            answer_slot.markdown(completed_response)
            if current_sources:
                sources_slot.markdown(sources_markdown(current_sources))
                source_count = len(current_sources)
                suffix = "s" if source_count != 1 else ""
                progress.update(
                    label=f"Answer generated from {source_count} approved source{suffix}",
                    state="complete",
                    expanded=False,
                )
            else:
                progress.update(label="Answer ready", state="complete", expanded=False)
            st.session_state.messages.append(
                {
                    "id": str(uuid4()),
                    "role": "assistant",
                    "content": completed_response,
                    "sources": current_sources,
                }
            )
        except Exception as exc:
            progress.update(label="We could not complete this request", state="error", expanded=True)
            st.error("Please try again. If the problem continues, contact support.")
            if developer_debug:
                master_status = "error"
                if agent_tree_slot is not None:
                    agent_tree_slot.markdown(agent_tree_markdown(master_status, agent_runs))
                if summary_slot is not None:
                    summary_slot.markdown(
                        run_summary_markdown(
                            thread_id=st.session_state.thread_id,
                            run_id=run_id,
                            status="error",
                            elapsed=time.perf_counter() - started_at,
                        )
                    )
                st.exception(exc)
