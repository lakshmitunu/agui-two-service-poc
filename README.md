# AG-UI Two-Service Agent Demo

This project shows how a web chat interface can talk to a separate AI-agent service and display the agent's work as it happens.

It is a small, self-contained proof of concept (POC): it does **not** call a real language model, require an API key, or search a real knowledge base. Instead, deterministic mock agents make the behavior easy to run, inspect, and test.

## What the demo does

A user enters a question in a Streamlit chat interface. A FastAPI service receives the request, chooses a specialist, and streams progress and the answer back to the UI using the [AG-UI protocol](https://docs.ag-ui.com/) over Server-Sent Events (SSE).

There are two example specialists:

- **Math specialist** — evaluates basic arithmetic using `+`, `-`, `*`, and `/`.
- **Knowledge specialist** — simulates a search of approved enterprise documents.

For example:

```text
Calculate 24 * 7
```

is routed to the math specialist, while:

```text
What is our leave policy?
```

is routed to the knowledge specialist.

## Architecture

```text
Browser
  |
  v
Streamlit UI (port 8501)
  |  POST /agent with RunAgentInput
  v
FastAPI agent service (port 8000)
  |
  +-- Master router
        |-- Math specialist
        +-- Knowledge specialist

FastAPI streams typed AG-UI events back to Streamlit over SSE.
```

The services have separate responsibilities:

| Component | Responsibility |
| --- | --- |
| `streamlit_app.py` | Chat interface, live progress, streamed answer, sources, and optional developer diagnostics |
| `agent_service/main.py` | `/agent` API, AG-UI event creation, SSE streaming, run state, and mock usage metrics |
| `agent_service/specialists.py` | Routing rules and the two deterministic specialist implementations |
| `ui_debug.py` | Human-readable formatting for the developer debug panel |

## Quick start with Docker

### Prerequisites

- Docker Desktop, or Docker Engine with Docker Compose
- Ports `8000` and `8501` available

From this directory, run:

```bash
docker compose up --build
```

Then open <http://localhost:8501> and try either example question shown in the sidebar.

The API documentation is available at <http://localhost:8000/docs>, and its health check is at <http://localhost:8000/health>.

Stop the services with `Ctrl+C`, followed by:

```bash
docker compose down
```

## Run without Docker

### Prerequisites

- Python 3.12 or newer

Create an environment and install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the agent service in one terminal:

```bash
source .venv/bin/activate
uvicorn agent_service.main:app --port 8000
```

Start the Streamlit UI in a second terminal:

```bash
source .venv/bin/activate
AGENT_URL=http://localhost:8000/agent streamlit run streamlit_app.py
```

Open <http://localhost:8501>.

## What happens after a question is submitted

1. Streamlit keeps the original text in the chat but replaces email addresses with `[MASKED_EMAIL]` in the copy sent to the agent service.
2. Streamlit sends the conversation as an AG-UI `RunAgentInput` request to `POST /agent`.
3. The master router selects the math or knowledge specialist using simple keyword and arithmetic-expression rules.
4. The selected specialist produces a deterministic result and mock tool activity.
5. FastAPI streams typed AG-UI events as SSE, including progress, tool calls, answer chunks, state, and metrics.
6. Streamlit renders the answer chunks immediately and saves the completed response in the chat session.

The email replacement is only a visible POC example. It is **not** production-grade data-loss-prevention or sensitive-data handling.

## User and developer views

By default, the UI shows only user-friendly progress, the streamed answer, and any sources.

Turn on **Developer debug** in the sidebar to see:

- the master/specialist execution tree;
- a grouped event timeline;
- tool arguments and results;
- the final assembled response;
- synchronized state; and
- token and estimated-cost metrics.

Enable **Show raw protocol events** inside the debug view to inspect the exact AG-UI JSON.

## AG-UI event flow

A successful request emits events in roughly this order:

```text
RUN_STARTED
STEP_STARTED                  master-routing
CUSTOM                        specialist-selected
STEP_FINISHED                 master-routing
SUBAGENT_STARTED
ACTIVITY_SNAPSHOT
TEXT_MESSAGE_START
TOOL_CALL_START
TOOL_CALL_ARGS                streamed in multiple chunks
TOOL_CALL_END
TOOL_CALL_RESULT
TEXT_MESSAGE_CONTENT          streamed in multiple chunks
TEXT_MESSAGE_END
CUSTOM                        cost-metrics
STATE_SNAPSHOT
SUBAGENT_FINISHED
RUN_FINISHED
```

SSE is the transport that delivers the stream. AG-UI defines the meaning and structure of each event in that stream.

The service collects every `TEXT_MESSAGE_CONTENT.delta` while streaming it. Their exact concatenation is returned as `RUN_FINISHED.result.finalResponse`, which the UI treats as the authoritative completed answer.

Token usage is stored in the standard `RUN_FINISHED.usage` field. Because AG-UI does not define a standard monetary-cost field, the demo sends a separate custom `cost-metrics` event. Both the token counts and cost are mock estimates, not provider billing data.

## API endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `http://localhost:8000/agent` | Accept an AG-UI `RunAgentInput` object and return an SSE event stream |
| `GET` | `http://localhost:8000/health` | Confirm that the agent service is running |
| `GET` | `http://localhost:8000/docs` | Open the interactive FastAPI/OpenAPI documentation |

## Tests

After installing the Python dependencies, run:

```bash
python -m unittest discover -s tests -v
```

The tests cover specialist routing and responses, the streamed tool-event lifecycle, final-response assembly, usage metrics, and developer-debug formatting.

## Project layout

```text
.
|-- agent_service/
|   |-- main.py             # FastAPI API and AG-UI stream
|   `-- specialists.py      # Router and mock specialists
|-- tests/                  # Unit and stream-level tests
|-- streamlit_app.py        # User interface and SSE client
|-- ui_debug.py             # Developer-debug renderers
|-- docker-compose.yml      # Runs both services
|-- Dockerfile              # Shared Python container image
`-- requirements.txt        # Python dependencies
```

## POC limitations

- No real LLM or agent framework is connected.
- The knowledge specialist does not retrieve real documents.
- Routing uses basic string rules rather than model reasoning.
- Tools, token counts, provider details, and cost are simulated.
- Chat and synchronized state live only in the current Streamlit session.
- Authentication, authorization, persistent storage, rate limiting, and production observability are not included.

## Taking it toward production

The integration boundary is intentionally stable: keep the `/agent` `RunAgentInput` request and AG-UI event stream while replacing the mock internals.

Typical next steps are:

1. Replace `route_specialist()` with a real master/router agent.
2. Replace the mock specialists with real agents and tool integrations.
3. Connect the knowledge specialist to an approved document source.
4. Calculate usage and cost from the actual model provider.
5. Add authentication, durable conversation state, error monitoring, and production data controls.
6. If a gateway or reverse proxy is added, configure it to pass the SSE response without buffering.
