# Architecture

How the MCP gateway, the two MCP servers and the dashboard fit together. Diagrams are Mermaid
(rendered by GitHub, VS Code, and `architecture.html` in this folder).

## 1. System architecture

```mermaid
flowchart TB
    client["MCP client<br/>(Claude, IDE, agent)"]
    browser["Browser<br/>(dashboard, Swagger)"]

    subgraph docker["Docker network: mcp-net"]
        direction LR

        subgraph gw["mcp-gateway :8000  (FastAPI + FastMCP)"]
            direction TB
            reqlog["RequestLogMiddleware<br/>request received / completed"]
            mcp["MCP endpoint /mcp<br/>GatewayMiddleware: logs, timing, metrics"]
            api["REST API /api/*<br/>dashboard + Swagger /docs"]
            router["Prefix routing<br/>weather_* , notes_*"]
            pool["UpstreamPool<br/>one long-lived session per server"]
            health["HealthMonitor<br/>probes every 30s"]
            metrics[("MetricsStore<br/>in memory")]
            config[/"config/servers.json"/]

            reqlog --> mcp
            reqlog --> api
            mcp --> router --> pool
            mcp -. "record call" .-> metrics
            api -. "read" .-> metrics
            health -. "status + tool schemas" .-> api
            config -. "server list" .-> router
        end

        subgraph w["weather-server :8000"]
            direction TB
            wmw["ToolTimingMiddleware"]
            wt["get_current<br/>get_forecast<br/>get_alerts"]
            wmw --> wt
        end

        subgraph n["notes-server :8000"]
            direction TB
            nmw["ToolTimingMiddleware"]
            nt["create_note<br/>list_notes<br/>search_notes<br/>delete_note"]
            nmw --> nt
        end
    end

    client -- "HTTP POST /mcp<br/>JSON-RPC" --> reqlog
    browser -- "HTTP /, /api, /docs" --> reqlog
    pool -- "streamable HTTP /mcp" --> wmw
    pool -- "streamable HTTP /mcp" --> nmw
    health -. "tools/list" .-> wmw
    health -. "tools/list" .-> nmw

    classDef edge fill:#e9f2ef,stroke:#167765,color:#102c33
    classDef core fill:#ffffff,stroke:#167765,color:#102c33
    classDef srv fill:#fbf1e4,stroke:#b07a2a,color:#412402
    classDef store fill:#edf1f8,stroke:#5571a7,color:#172a37
    class client,browser edge
    class reqlog,mcp,api,router,pool,health core
    class wmw,wt,nmw,nt srv
    class metrics,config store
```

| Component | Responsibility |
|---|---|
| `RequestLogMiddleware` | Logs every HTTP request: received, completed, status, `elapsed_ms` |
| `GatewayMiddleware` | Logs each MCP message, times every tool call (`tool_ms`), records metrics, retries once on transport failure |
| Prefix routing | `weather_get_forecast` goes to `weather` as `get_forecast`; `notes_*` goes to `notes` |
| `UpstreamPool` | Keeps one open MCP session per server (cuts about 45 ms per call), single-flight reconnect |
| `HealthMonitor` | Probes servers, caches status and tool JSON Schemas for the dashboard |
| `MetricsStore` | Bounded in-memory call history: counts, success rate, avg, p95, per-minute series |

## 2. A tool call end to end

```mermaid
sequenceDiagram
    autonumber
    actor C as MCP client
    participant G as mcp-gateway
    participant M as GatewayMiddleware
    participant P as UpstreamPool
    participant S as notes-server
    participant D as MetricsStore

    C->>G: POST /mcp  tools/call notes_create_note
    Note over G: LOG request received id=ab12cd34
    G->>M: on_call_tool
    Note over M: LOG tool call received<br/>server=notes tool=create_note
    M->>M: start timer, resolve prefix notes_
    M->>P: forward create_note (reuse open session)
    P->>S: POST /mcp  tools/call create_note
    Note over S: LOG tool call received
    S->>S: run tool under asyncio.Lock
    Note over S: LOG tool call completed tool_ms=52
    S-->>P: result
    P-->>M: result
    M->>D: record(notes, create_note, ok, 98 ms)
    Note over M: LOG tool call completed tool_ms=98
    M-->>G: result
    Note over G: LOG request completed status=200 elapsed_ms=128
    G-->>C: JSON-RPC result
```

Gateway `tool_ms` minus server `tool_ms` is the gateway overhead (about 17 ms in local tests).

## 3. Upstream failure: timeout, reconnect, retry once

```mermaid
sequenceDiagram
    autonumber
    actor C as MCP client
    participant M as GatewayMiddleware
    participant P as UpstreamPool
    participant S as weather-server

    Note over S: container restarts, old session is now invalid
    C->>M: tools/call weather_get_current
    M->>P: forward (generation = 3)
    P->>S: call on stale session
    S--xP: error or no reply (timeout 8s)
    M->>M: LOG upstream call failed, reconnecting
    M->>P: reset(weather, generation 3)
    Note over P: per-server lock: first caller reconnects,<br/>concurrent callers see generation 4 and skip
    P->>S: open new session
    S-->>P: initialized
    M->>P: retry call once
    P->>S: tools/call get_current
    S-->>P: result
    M-->>C: result (or an error if the retry also fails)
```

## 4. Dashboard and health probing

```mermaid
sequenceDiagram
    autonumber
    actor U as You (browser)
    participant G as mcp-gateway
    participant H as HealthMonitor
    participant S as weather / notes servers
    participant D as MetricsStore

    loop every 30s
        H->>S: tools/list
        S-->>H: tools + JSON Schemas
        H->>H: status up or down, probe_ms
    end

    U->>G: GET /  (static dashboard)
    loop every 5s
        U->>G: GET /api/overview, servers, tools, metrics, calls
        G->>H: read status and tool contracts
        G->>D: snapshot (counts, p95, series)
        G-->>U: JSON (typed models, see /docs)
    end
    U->>G: click a tool
    Note over U: popup shows input and output JSON Schema
```
