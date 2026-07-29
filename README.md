# OpenClaw Web Control Plane

Local-first prototype for a persistent OpenClaw web workspace.

The control plane stores jobs, runner status, logs, and artifacts. Local runners
do the actual machine work. This keeps cloud infrastructure focused on state and
coordination instead of becoming a general remote shell.

## Default Deployment Shape

- Control plane: Heroku
- Compute: one or more local OpenClaw runners
- Storage MVP: SQLite + artifact folder
- Storage later: Postgres + S3/R2 or Google Drive mirror
- ChatGPT/Codex access: MCP facade exposed by the control plane

## Local Quick Start

```bash
cd apps/openclaw-web-control-plane
OPENCLAW_ADMIN_TOKEN=dev-token python3 server/control_plane.py --host 127.0.0.1 --port 8788
```

In another terminal:

```bash
cd apps/openclaw-web-control-plane
OPENCLAW_ADMIN_TOKEN=dev-token python3 runner/local_runner.py --once
```

Create a job:

```bash
curl -fsS http://127.0.0.1:8788/api/jobs \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d @examples/jobs/hello-python.json
```

Open the dashboard at `http://127.0.0.1:8788/`.

## Current Capabilities

- Persistent SQLite job state
- Runner registration and heartbeat
- Runner machine inventory for common tool availability
- Runner local model inventory and selected-model routing
- RunPod bootstrap script for GPU runner + Gemma setup
- Queue, claim, execute, complete loop
- Python job capability
- Job event log
- Minimal MCP-style JSON-RPC facade
- Static web dashboard

## Docs

- [Architecture](docs/architecture.md)
- [Deployment](docs/deployment.md)
- [RunPod runner onboarding](docs/runpod-runner.md)

## Non-Goals For MVP

- Arbitrary remote shell from the cloud
- Multi-user OAuth
- Secret manager
- Full artifact upload/download protocol
- Websocket streaming

Those come after the job lease and recovery loop is proven.
