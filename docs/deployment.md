# Deployment Notes

## Recommended Default

Use Heroku for the control plane and local machines for runners.

Heroku gives the right first deployment surface for:

- HTTP API
- dashboard
- MCP facade
- future websocket/SSE streaming
- Postgres upgrade path

## Prototype Heroku Settings

Optional but recommended config:

```bash
heroku config:set OPENCLAW_ALLOWED_IPS=<your-public-ip-or-cidr>
```

The app no longer needs `OPENCLAW_ADMIN_TOKEN` in Heroku config. If no env token
is present, the dashboard shows a first-run setup panel where you type a launch
token. The server stores only a salted hash in its local state.

Important: without `OPENCLAW_ALLOWED_IPS`, the first-run setup page is reachable
publicly until you set a token. For real use, configure the IP allowlist before
attaching any runner.

Recommended next config after Postgres is added:

```bash
heroku addons:create heroku-postgresql:essential-0
```

The current MVP uses SQLite and filesystem storage, which is fine locally but not
durable on Heroku dynos. Before relying on Heroku for real persistence, move jobs
to Postgres and artifacts to S3/R2 or Google Drive.

## Runner

Run on any trusted local machine:

```bash
OPENCLAW_CONTROL_PLANE_URL=https://<app>.herokuapp.com \
OPENCLAW_ADMIN_TOKEN=<launch-token-you-typed-in-the-dashboard> \
OPENCLAW_RUNNER_ID=<machine-name> \
python3 runner/local_runner.py
```

For GPU machines, use [RunPod runner onboarding](runpod-runner.md). The bootstrap
script there installs basic packages, prepares a Gemma model config, optionally
downloads from Hugging Face, registers the runner, and starts the runner loop.

## Storage Options

- **Local SQLite/filesystem**: best for MVP and development.
- **Heroku Postgres + S3/R2**: best production default.
- **Google Drive**: useful as a user-visible mirror for memory exports,
  generated artifacts, and recovery bundles.
- **HF Space persistent storage**: useful for demo deployments, less ideal for
  general job routing and multi-runner coordination.

## Next Hardening Steps

- Add Postgres adapter.
- Add artifact download API.
- Add job lease expiry and requeue.
- Add idempotency keys.
- Split admin token from runner-scoped tokens.
- Add Drive/R2 artifact mirror.
- Add richer MCP tool schemas.
- Add RunPod startup script or container image for GPU runners.
