#!/usr/bin/env python3
"""Print a copy/paste RunPod bootstrap command without storing secrets."""

from __future__ import annotations

import argparse
import shlex


def q(value: str) -> str:
    return shlex.quote(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a RunPod bootstrap command")
    parser.add_argument("--control-plane-url", required=True)
    parser.add_argument("--runner-id", default="runpod-gemma")
    parser.add_argument("--repo-url", help="git repo containing apps/openclaw-web-control-plane")
    parser.add_argument("--app-dir", default="/workspace/openclaw-web-control-plane")
    parser.add_argument("--model-id", default="gemma-local")
    parser.add_argument("--model-name", default="Gemma local")
    parser.add_argument("--model-repo", default="google/gemma-3-4b-it")
    parser.add_argument("--model-dir", default="/workspace/models/gemma")
    parser.add_argument("--script-url", help="raw URL to bootstrap_runpod.sh")
    args = parser.parse_args()

    lines = [
        "export OPENCLAW_ADMIN_TOKEN='<paste-control-plane-token-here>'",
        "export HF_TOKEN='<paste-huggingface-token-if-needed>'",
        f"export OPENCLAW_CONTROL_PLANE_URL={q(args.control_plane_url)}",
        f"export OPENCLAW_RUNNER_ID={q(args.runner_id)}",
        f"export OPENCLAW_APP_DIR={q(args.app_dir)}",
        f"export OPENCLAW_MODEL_ID={q(args.model_id)}",
        f"export OPENCLAW_MODEL_NAME={q(args.model_name)}",
        f"export OPENCLAW_MODEL_REPO={q(args.model_repo)}",
        f"export OPENCLAW_MODEL_DIR={q(args.model_dir)}",
    ]
    if args.repo_url:
        lines.append(f"export OPENCLAW_REPO_URL={q(args.repo_url)}")
    if args.script_url:
        lines.append(f"curl -fsSL {q(args.script_url)} | bash")
    else:
        lines.append("bash scripts/bootstrap_runpod.sh")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
