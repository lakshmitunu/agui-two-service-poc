"""FastAPI service that accepts RunAgentInput and streams official AG-UI events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import uuid4

from ag_ui.core import (
    ActivitySnapshotEvent,
    CustomEvent,
    EventType,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    SubagentErrorEvent,
    SubagentFinishedEvent,
    SubagentStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    TokenUsage,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from agent_service.specialists import route_specialist, run_specialist

app = FastAPI(title="Master + specialists AG-UI service", version="0.1.0")


def latest_user_text(input_data: RunAgentInput) -> str:
    for message in reversed(input_data.messages):
        if message.role == "user":
            content = message.content
            return content if isinstance(content, str) else str(content or "")
    return ""


async def agui_events(input_data: RunAgentInput, encoder: EventEncoder) -> AsyncIterator[str]:
    question = latest_user_text(input_data)
    subagent_run_id = str(uuid4())
    activity_id = str(uuid4())
    message_id = str(uuid4())
    tool_call_id = str(uuid4())
    tool_result_message_id = str(uuid4())
    subagent_started = False

    try:
        yield encoder.encode(
            RunStartedEvent(
                type=EventType.RUN_STARTED,
                thread_id=input_data.thread_id,
                run_id=input_data.run_id,
            )
        )

        yield encoder.encode(StepStartedEvent(type=EventType.STEP_STARTED, step_name="master-routing"))
        await asyncio.sleep(0.35)
        specialist_name = route_specialist(question)
        yield encoder.encode(
            CustomEvent(
                type=EventType.CUSTOM,
                name="specialist-selected",
                value={"specialist": specialist_name},
            )
        )
        yield encoder.encode(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name="master-routing"))

        specialist_metadata = {
            "agentId": specialist_name,
            "agentVersion": "poc-1.0",
            "domain": "arithmetic" if specialist_name == "math-specialist" else "enterprise-knowledge",
        }
        subagent_started = True
        yield encoder.encode(
            SubagentStartedEvent(
                type=EventType.SUBAGENT_STARTED,
                subagent_run_id=subagent_run_id,
                name=specialist_name,
                description="Specialist selected by the master agent",
                metadata=specialist_metadata,
            )
        )

        result = run_specialist(specialist_name, question)
        yield encoder.encode(
            ActivitySnapshotEvent(
                type=EventType.ACTIVITY_SNAPSHOT,
                message_id=activity_id,
                activity_type="SPECIALIST_PROGRESS",
                content={"status": "working", "message": result.activity},
                subagent_run_id=subagent_run_id,
            )
        )
        await asyncio.sleep(0.55)

        yield encoder.encode(
            TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START,
                message_id=message_id,
                role="assistant",
                subagent_run_id=subagent_run_id,
            )
        )

        tool_name = "calculator" if specialist_name == "math-specialist" else "search_managed_kb"
        tool_args = (
            {"expressionFromQuestion": question}
            if specialist_name == "math-specialist"
            else {"query": question, "topK": 3}
        )
        serialized_args = json.dumps(tool_args, separators=(",", ":"))
        split_at = max(1, len(serialized_args) // 2)
        yield encoder.encode(
            ToolCallStartEvent(
                type=EventType.TOOL_CALL_START,
                tool_call_id=tool_call_id,
                tool_call_name=tool_name,
                parent_message_id=message_id,
                subagent_run_id=subagent_run_id,
            )
        )
        for args_delta in (serialized_args[:split_at], serialized_args[split_at:]):
            if args_delta:
                yield encoder.encode(
                    ToolCallArgsEvent(
                        type=EventType.TOOL_CALL_ARGS,
                        tool_call_id=tool_call_id,
                        delta=args_delta,
                        subagent_run_id=subagent_run_id,
                    )
                )
                await asyncio.sleep(0.25)
        yield encoder.encode(
            ToolCallEndEvent(
                type=EventType.TOOL_CALL_END,
                tool_call_id=tool_call_id,
                subagent_run_id=subagent_run_id,
            )
        )
        tool_result = (
            {"status": "success", "calculationCompleted": True}
            if specialist_name == "math-specialist"
            else {
                "status": "success",
                "matches": 1,
                "documents": [{"title": "Mock enterprise knowledge base", "score": 0.94}],
            }
        )
        yield encoder.encode(
            ToolCallResultEvent(
                type=EventType.TOOL_CALL_RESULT,
                message_id=tool_result_message_id,
                tool_call_id=tool_call_id,
                content=json.dumps(tool_result, separators=(",", ":")),
                role="tool",
                subagent_run_id=subagent_run_id,
            )
        )
        await asyncio.sleep(0.3)

        final_response_chunks: list[str] = []
        answer_words = result.answer.split(" ")
        for index, word in enumerate(answer_words):
            delta = word + (" " if index < len(answer_words) - 1 else "")
            final_response_chunks.append(delta)
            yield encoder.encode(
                TextMessageContentEvent(
                    type=EventType.TEXT_MESSAGE_CONTENT,
                    message_id=message_id,
                    delta=delta,
                    subagent_run_id=subagent_run_id,
                )
            )
            await asyncio.sleep(0.07)
        yield encoder.encode(
            TextMessageEndEvent(
                type=EventType.TEXT_MESSAGE_END,
                message_id=message_id,
                subagent_run_id=subagent_run_id,
            )
        )

        input_tokens = max(1, len(question.split()))
        output_tokens = max(1, len(result.answer.split()))
        total_tokens = input_tokens + output_tokens
        estimated_cost_usd = round((input_tokens * 0.000001) + (output_tokens * 0.000002), 6)
        cost_metrics = {
            "provider": "mock-provider",
            "model": "poc-model-v1",
            "estimatedCostUsd": estimated_cost_usd,
            "costSource": "POC formula; not a provider-billed value",
        }
        yield encoder.encode(
            CustomEvent(
                type=EventType.CUSTOM,
                name="cost-metrics",
                value=cost_metrics,
            )
        )

        state = {
            "selectedSpecialist": specialist_name,
            "status": "complete",
            "lastTool": tool_name,
            "sources": (
                [{"title": "Mock enterprise knowledge base", "uri": "kb://poc/document-1"}]
                if specialist_name == "knowledge-specialist"
                else []
            ),
        }
        yield encoder.encode(
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot=state,
                subagent_run_id=subagent_run_id,
            )
        )
        yield encoder.encode(
            SubagentFinishedEvent(
                type=EventType.SUBAGENT_FINISHED,
                subagent_run_id=subagent_run_id,
                result={"specialist": specialist_name},
            )
        )
        yield encoder.encode(
            RunFinishedEvent(
                type=EventType.RUN_FINISHED,
                thread_id=input_data.thread_id,
                run_id=input_data.run_id,
                result={
                    "finalResponse": "".join(final_response_chunks),
                    "selectedSpecialist": specialist_name,
                    "tool": tool_name,
                },
                usage=[
                    TokenUsage(
                        provider="mock-provider",
                        model="poc-model-v1",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                    )
                ],
            )
        )
    except Exception as exc:
        if subagent_started:
            yield encoder.encode(
                SubagentErrorEvent(
                    type=EventType.SUBAGENT_ERROR,
                    subagent_run_id=subagent_run_id,
                    message=str(exc),
                    code="POC_SPECIALIST_ERROR",
                )
            )
        yield encoder.encode(
            RunErrorEvent(type=EventType.RUN_ERROR, message=str(exc), code="POC_AGENT_ERROR")
        )


@app.post("/agent")
async def run_agent(input_data: RunAgentInput, request: Request) -> StreamingResponse:
    encoder = EventEncoder(accept=request.headers.get("accept"))
    return StreamingResponse(
        agui_events(input_data, encoder),
        media_type=encoder.get_content_type(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
