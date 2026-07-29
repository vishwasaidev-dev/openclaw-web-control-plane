#!/usr/bin/env bash
set -Eeuo pipefail

# Bootstrap a fresh RunPod into an OpenClaw GPU runner.
#
# Required env:
#   OPENCLAW_CONTROL_PLANE_URL  e.g. https://your-app.herokuapp.com
#   OPENCLAW_ADMIN_TOKEN        temporary MVP token or future runner-scoped token
#
# Optional env:
#   OPENCLAW_RUNNER_ID          default runpod-$(hostname)
#   OPENCLAW_REPO_URL           git repo to clone if app folder is missing
#   OPENCLAW_APP_DIR            default /workspace/openclaw-web-control-plane
#   OPENCLAW_MODEL_ID           default gemma-local
#   OPENCLAW_MODEL_NAME         default "Gemma local"
#   OPENCLAW_MODEL_REPO         default google/gemma-3-4b-it
#   OPENCLAW_MODEL_DIR          default /workspace/models/gemma
#   HF_TOKEN                    used for gated Hugging Face model download
#   OPENCLAW_START_RUNNER       1 starts runner after setup, 0 only prepares

log() {
  printf '[openclaw-runpod] %s\n' "$*"
}

need_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    log "missing required env: $name"
    exit 2
  fi
}

need_env OPENCLAW_CONTROL_PLANE_URL
need_env OPENCLAW_ADMIN_TOKEN

OPENCLAW_APP_DIR="${OPENCLAW_APP_DIR:-/workspace/openclaw-web-control-plane}"
OPENCLAW_RUNNER_ID="${OPENCLAW_RUNNER_ID:-runpod-$(hostname)}"
OPENCLAW_MODEL_ID="${OPENCLAW_MODEL_ID:-gemma-local}"
OPENCLAW_MODEL_NAME="${OPENCLAW_MODEL_NAME:-Gemma local}"
OPENCLAW_MODEL_REPO="${OPENCLAW_MODEL_REPO:-google/gemma-3-4b-it}"
OPENCLAW_MODEL_DIR="${OPENCLAW_MODEL_DIR:-/workspace/models/gemma}"
OPENCLAW_START_RUNNER="${OPENCLAW_START_RUNNER:-1}"

log "installing base packages"
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y git curl ripgrep ffmpeg python3 python3-pip
else
  log "apt-get not found; skipping system package install"
fi

log "checking GPU"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi || true
else
  log "nvidia-smi not found; runner will not advertise gpu_python yet"
fi

if [ ! -d "$OPENCLAW_APP_DIR" ]; then
  if [ -n "${OPENCLAW_REPO_URL:-}" ]; then
    log "cloning app repo into $OPENCLAW_APP_DIR"
    git clone "$OPENCLAW_REPO_URL" "$OPENCLAW_APP_DIR"
    if [ -d "$OPENCLAW_APP_DIR/apps/openclaw-web-control-plane" ]; then
      OPENCLAW_APP_DIR="$OPENCLAW_APP_DIR/apps/openclaw-web-control-plane"
    fi
  else
    log "$OPENCLAW_APP_DIR does not exist and OPENCLAW_REPO_URL is not set"
    log "copy this app folder to the RunPod or provide OPENCLAW_REPO_URL"
    exit 2
  fi
fi

cd "$OPENCLAW_APP_DIR"

log "installing Python helper packages"
python3 -m pip install --upgrade pip
python3 -m pip install --upgrade huggingface_hub

mkdir -p "$(dirname "$OPENCLAW_MODEL_DIR")" runner

if [ -d "$OPENCLAW_MODEL_DIR" ] && find "$OPENCLAW_MODEL_DIR" -maxdepth 1 -type f \( -name 'config.json' -o -name '*.safetensors' -o -name 'pytorch_model.bin' \) | grep -q .; then
  log "model folder already looks populated: $OPENCLAW_MODEL_DIR"
elif [ -n "${HF_TOKEN:-}" ]; then
  log "downloading $OPENCLAW_MODEL_REPO to $OPENCLAW_MODEL_DIR"
  HF_TOKEN="$HF_TOKEN" python3 - <<'PY'
import os
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id=os.environ["OPENCLAW_MODEL_REPO"],
    local_dir=os.environ["OPENCLAW_MODEL_DIR"],
    token=os.environ.get("HF_TOKEN"),
    local_dir_use_symlinks=False,
)
PY
else
  log "HF_TOKEN not set and model folder is empty; creating config that points to the intended model path"
  mkdir -p "$OPENCLAW_MODEL_DIR"
fi

log "writing runner/config.json"
python3 - <<'PY'
import json
import os
from pathlib import Path

config = {
    "default_model_id": os.environ["OPENCLAW_MODEL_ID"],
    "model_search_paths": [
        str(Path(os.environ["OPENCLAW_MODEL_DIR"]).parent),
        "/workspace/models",
        "/runpod-volume/models",
        "/models",
    ],
    "models": [
        {
            "id": os.environ["OPENCLAW_MODEL_ID"],
            "name": os.environ["OPENCLAW_MODEL_NAME"],
            "provider": "local",
            "path": os.environ["OPENCLAW_MODEL_DIR"],
            "repo": os.environ["OPENCLAW_MODEL_REPO"],
        }
    ],
    "max_discovered_models": 120,
}
Path("runner/config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY

log "verifying runner inventory"
OPENCLAW_CONTROL_PLANE_URL="$OPENCLAW_CONTROL_PLANE_URL" \
OPENCLAW_ADMIN_TOKEN="$OPENCLAW_ADMIN_TOKEN" \
OPENCLAW_RUNNER_ID="$OPENCLAW_RUNNER_ID" \
python3 runner/local_runner.py --once || true

if [ "$OPENCLAW_START_RUNNER" = "1" ]; then
  log "starting OpenClaw runner loop as $OPENCLAW_RUNNER_ID"
  exec env \
    OPENCLAW_CONTROL_PLANE_URL="$OPENCLAW_CONTROL_PLANE_URL" \
    OPENCLAW_ADMIN_TOKEN="$OPENCLAW_ADMIN_TOKEN" \
    OPENCLAW_RUNNER_ID="$OPENCLAW_RUNNER_ID" \
    python3 runner/local_runner.py
fi

log "setup complete; runner not started because OPENCLAW_START_RUNNER=$OPENCLAW_START_RUNNER"
