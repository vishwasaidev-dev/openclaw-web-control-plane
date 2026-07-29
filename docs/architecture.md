# Architecture

## Decision

Default to a Heroku-hosted control plane and local machine runners.

HF Space remains useful for demos and Gradio-style UIs. ChatGPT is a client via
MCP/plugin, not the durable backend. Google Drive is a good optional storage
mirror, especially for artifacts and memory exports, but the system should not
depend on Drive for its core queue.

## Components

- **Control plane**: HTTP API, dashboard, MCP facade, durable job state.
- **Local runner**: outbound polling agent that advertises capabilities and runs
  approved job types.
- **Storage**: SQLite and filesystem in MVP; Postgres and object storage later.
- **Connector**: MCP facade exposes tools such as `create_job`, `list_jobs`, and
  `get_job` to ChatGPT/Codex-like clients.

## Security Model

The cloud does not run arbitrary shell commands on a laptop. It queues capability
requests. Runners decide what capabilities they support.

MVP capabilities:

- `python`: execute a bounded Python file in a temporary working directory.
- `node inventory`: runner registration reports OS/platform and common local
  tool availability so the control plane can tell whether a machine can satisfy
  a skill/tool requirement.

Later capabilities:

- `workspace_search`
- `browser_task`
- `python_sandbox`
- `artifact_sync`
- `memory_read`
- `memory_write`
- domain-specific local tools

## Recovery Model

Each job has a durable row with:

- objective
- capability
- payload
- status
- runner lease
- result
- event log

If a machine dies, the control plane still has the job and events. The next
runner can pick up queued jobs. A later version should add lease expiry for
`running` jobs so they can be requeued automatically.

## Deployment Phases

1. Local MVP: stdlib server + local runner + SQLite.
2. Heroku control plane: same API, persistent Postgres.
3. Artifact storage: S3/R2 or Google Drive mirror.
4. MCP connector hardening: idempotency keys, scoped tools, richer schemas.
5. Multi-runner recovery: leases, retries, runner labels, capability routing.
