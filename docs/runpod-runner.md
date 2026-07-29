# RunPod Runner Onboarding

Goal: turn a fresh RunPod GPU instance into an OpenClaw runner that can pick up
GPU-heavy jobs while the control plane keeps durable state.

## 1. Start A RunPod

Recommended base image:

- Ubuntu
- NVIDIA CUDA runtime or PyTorch image
- Python 3.10+
- enough persistent volume for model/cache/artifacts

## 2. Fast Path: Bootstrap Script

On a fresh RunPod, the goal is to paste one command block, not hand-install
everything.

```bash
export OPENCLAW_CONTROL_PLANE_URL="https://<your-control-plane>.herokuapp.com"
export OPENCLAW_ADMIN_TOKEN="<runner-or-admin-token>"
export HF_TOKEN="<huggingface-token-if-gemma-is-gated>"
export OPENCLAW_REPO_URL="<git-repo-containing-this-workspace>"
export OPENCLAW_RUNNER_ID="runpod-gemma"
export OPENCLAW_MODEL_ID="gemma-local"
export OPENCLAW_MODEL_NAME="Gemma local"
export OPENCLAW_MODEL_REPO="google/gemma-3-4b-it"
export OPENCLAW_MODEL_DIR="/workspace/models/gemma"
bash scripts/bootstrap_runpod.sh
```

If the app folder is not already on the RunPod, provide `OPENCLAW_REPO_URL`.
The bootstrap script will clone it, install basic packages, install
`huggingface_hub`, download the model when `HF_TOKEN` is present, write
`runner/config.json`, register the runner, and start the runner loop.

You can generate a paste-ready command template locally:

```bash
python3 scripts/make_runpod_bootstrap_command.py \
  --control-plane-url https://<your-control-plane>.herokuapp.com \
  --runner-id runpod-gemma \
  --repo-url <git-repo-containing-this-workspace>
```

## 3. Manual Fallback

Install basics yourself:

```bash
apt-get update
apt-get install -y git curl ripgrep ffmpeg
python3 -m pip install --upgrade pip huggingface_hub
nvidia-smi
```

Clone or copy this workspace/app:

```bash
git clone <your-repo-url> openclaw-workspace
cd openclaw-workspace/apps/openclaw-web-control-plane
```

For a quick manual transfer, copying only this app folder is enough for the MVP.

Then configure and start the runner.

## 4. Manual Model Config

Create a runner config:

```bash
cp runner/config.example.json runner/config.json
```

Edit `runner/config.json` so the default points at the model you want. For
Gemma on a RunPod volume, a simple config can look like:

```json
{
  "default_model_id": "gemma-local",
  "model_search_paths": ["/workspace/models", "/runpod-volume/models"],
  "models": [
    {
      "id": "gemma-local",
      "name": "Gemma local",
      "provider": "local",
      "path": "/workspace/models/gemma"
    }
  ]
}
```

You can also use env vars instead of editing the file:

```bash
export OPENCLAW_DEFAULT_MODEL=gemma-local
export OPENCLAW_MODEL_PATHS=/workspace/models:/runpod-volume/models
```

```bash
export OPENCLAW_CONTROL_PLANE_URL="https://<your-control-plane>.herokuapp.com"
export OPENCLAW_ADMIN_TOKEN="<runner-or-admin-token>"
export OPENCLAW_RUNNER_ID="runpod-$(hostname)"
python3 runner/local_runner.py
```

For local testing against a forwarded/local server:

```bash
OPENCLAW_CONTROL_PLANE_URL=http://127.0.0.1:8788 \
OPENCLAW_ADMIN_TOKEN=dev-token \
OPENCLAW_RUNNER_ID=runpod-test \
python3 runner/local_runner.py
```

## 5. Expected Readiness

The control plane dashboard should show:

- `Python jobs`: ready
- `GPU Python / RunPod`: ready
- `Media generation`: ready if `ffmpeg` is installed
- model inventory containing `gemma-local`, with the dashboard selected model
  set to either runner default or a chosen Gemma entry

Raw tool inventory should show:

- `python3`: available
- `pip`: available
- `git`: available
- `curl`: available
- `rg`: available
- `nvidia-smi`: available
- `ffmpeg`: available if installed
- `huggingface-cli`: available if installed

## 6. How Jobs Route

The runner derives capabilities from installed tools:

- `python`: `python3`
- `gpu_python`: `python3` + `nvidia-smi`
- `media`: `python3` + `ffmpeg`
- `browser`: `node` + `npx` + Chrome/Chromium

So a RunPod with `nvidia-smi` automatically advertises `gpu_python`.

## 7. "Take Vacation With Gemma" Flow

Target behavior:

1. You provide an existing RunPod shell or, later, a RunPod key out of band.
2. Paste the bootstrap command.
3. The runner starts with `OPENCLAW_RUNNER_ID=runpod-gemma`.
4. The runner inventory reports `nvidia-smi` and local Gemma model paths.
5. The control plane marks `GPU Python / RunPod` ready.
6. The dashboard selected model is set to `gemma-local`.
7. Heavy jobs route to the `gpu_python` capability and receive:
   - `OPENCLAW_SELECTED_MODEL_ID`
   - `OPENCLAW_SELECTED_MODEL_NAME`
   - `OPENCLAW_SELECTED_MODEL_PATH`
   - `OPENCLAW_SELECTED_MODEL_PROVIDER`

The MVP implements the shell/bootstrap path. Provisioning a RunPod from an API key should be a
separate capability so secrets never land in source files.

That future provisioning capability should accept the RunPod key only through
environment/session input, create or locate a pod, open a command channel, run
the bootstrap block above, then forget the key.

## 8. Next Hardening Needed

Before using this for expensive long jobs:

- Add runner-scoped tokens instead of one admin token.
- Add job lease expiry/requeue.
- Add artifact streaming for large files instead of base64-in-complete-payload.
- Add per-job working directory persistence for interrupted jobs.
- Add model cache paths and disk-space checks.
