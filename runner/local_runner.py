#!/usr/bin/env python3
"""Local OpenClaw runner MVP."""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_URL = "http://127.0.0.1:8788"
APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = APP_ROOT / "runner" / "config.json"


class Client:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def post(self, path: str, payload: dict, timeout: int = 30) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def event(self, job_id: str, level: str, message: str) -> None:
        self.post(f"/api/jobs/{job_id}/events", {"level": level, "message": message})


def run_python_job(job: dict, client: Client, runner_config: dict | None = None) -> tuple[str, dict]:
    payload = job.get("payload") or {}
    runner_config = runner_config or {}
    selected_model = runner_config.get("selected_model") if isinstance(runner_config.get("selected_model"), dict) else {}
    source = str(payload.get("source") or "")
    timeout = int(payload.get("timeout_seconds") or 30)
    if not source.strip():
        return "failed", {"error": "payload.source is required"}

    with tempfile.TemporaryDirectory(prefix="openclaw-job-") as tmp:
        tmp_path = Path(tmp)
        script = tmp_path / "job.py"
        script.write_text(source, encoding="utf-8")
        client.event(job["id"], "info", f"executing python job in {tmp_path}")
        try:
            proc = subprocess.run(
                ["python3", str(script)],
                cwd=tmp_path,
                env=job_environment(selected_model),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
            artifact_files = []
            for path in sorted(tmp_path.iterdir()):
                if path.name == "job.py" or not path.is_file():
                    continue
                artifact_files.append({"name": path.name, "base64": base64.b64encode(path.read_bytes()).decode("ascii")})
            status = "complete" if proc.returncode == 0 else "failed"
            return status, {
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "artifact_files": artifact_files,
            }
        except subprocess.TimeoutExpired as exc:
            return "failed", {"error": f"timeout after {timeout}s", "stdout": exc.stdout, "stderr": exc.stderr}


def job_environment(selected_model: dict) -> dict[str, str]:
    env = os.environ.copy()
    if selected_model:
        env["OPENCLAW_SELECTED_MODEL_ID"] = str(selected_model.get("id") or "")
        env["OPENCLAW_SELECTED_MODEL_NAME"] = str(selected_model.get("name") or "")
        env["OPENCLAW_SELECTED_MODEL_PATH"] = str(selected_model.get("path") or "")
        env["OPENCLAW_SELECTED_MODEL_PROVIDER"] = str(selected_model.get("provider") or "local")
    return env


def load_runner_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def tool_version(command: str, args: list[str] | None = None) -> dict:
    path = shutil.which(command)
    if not path:
        return {"available": False, "path": None, "version": None}
    version = None
    try:
        proc = subprocess.run(
            [path, *(args or ["--version"])],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=3,
            check=False,
        )
        version = (proc.stdout or "").strip().splitlines()[0][:160] if proc.stdout else None
    except Exception as exc:
        version = f"version check failed: {type(exc).__name__}"
    return {"available": True, "path": path, "version": version}


def model_search_paths(config: dict) -> list[Path]:
    values = []
    env_paths = os.environ.get("OPENCLAW_MODEL_PATHS")
    if env_paths:
        values.extend(item for item in env_paths.split(":") if item)
    values.extend(str(item) for item in config.get("model_search_paths", []) if item)
    values.extend(["/workspace/models", "/runpod-volume/models", "/models"])
    seen = set()
    paths = []
    for value in values:
        path = Path(os.path.expanduser(value))
        key = str(path)
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def configured_models(config: dict) -> list[dict]:
    items = []
    for item in config.get("models", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        model = dict(item)
        model.setdefault("provider", "local")
        if model.get("path"):
            model["available"] = Path(os.path.expanduser(str(model["path"]))).exists()
            model["path"] = str(Path(os.path.expanduser(str(model["path"]))))
        else:
            model["available"] = True
        items.append(model)
    return items


def discovered_models(config: dict) -> list[dict]:
    discovered = []
    max_entries = int(config.get("max_discovered_models") or 80)
    for root in model_search_paths(config):
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if len(discovered) >= max_entries:
                return discovered
            if not path.is_dir():
                continue
            markers = ["config.json", "README.md", "model.safetensors", "pytorch_model.bin"]
            if not any((path / marker).exists() for marker in markers) and not list(path.glob("*.safetensors")):
                continue
            model_id = path.name
            discovered.append({
                "id": model_id,
                "name": model_id,
                "provider": "local",
                "path": str(path),
                "available": True,
                "source": "discovered",
            })
    return discovered


def model_inventory(config: dict) -> dict:
    configured = configured_models(config)
    known = {item["id"]: item for item in configured}
    for item in discovered_models(config):
        known.setdefault(item["id"], item)
    default_model_id = os.environ.get("OPENCLAW_DEFAULT_MODEL") or config.get("default_model_id")
    return {
        "default_model_id": default_model_id,
        "search_paths": [str(path) for path in model_search_paths(config)],
        "items": sorted(known.values(), key=lambda item: str(item.get("id", "")).lower()),
    }


def machine_inventory(config: dict) -> dict:
    checks = {
        "bash": ["--version"],
        "python3": ["--version"],
        "pip": ["--version"],
        "git": ["--version"],
        "rg": ["--version"],
        "curl": ["--version"],
        "node": ["--version"],
        "npm": ["--version"],
        "npx": ["--version"],
        "heroku": ["--version"],
        "ffmpeg": ["-version"],
        "docker": ["--version"],
        "google-chrome": ["--version"],
        "chromium": ["--version"],
        "nvidia-smi": ["--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
        "nvcc": ["--version"],
        "huggingface-cli": ["--version"],
    }
    tools = {name: tool_version(name, args) for name, args in checks.items()}
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cwd": os.getcwd(),
        "cpu_count": os.cpu_count(),
        "tools": tools,
        "models": model_inventory(config),
    }


def derive_capabilities(inventory: dict) -> list[str]:
    tools = inventory.get("tools", {})
    has = lambda name: bool(tools.get(name, {}).get("available"))
    capabilities = []
    if has("python3"):
        capabilities.append("python")
    if has("node") and has("npm"):
        capabilities.append("node")
    if has("node") and has("npx") and (has("google-chrome") or has("chromium")):
        capabilities.append("browser")
    if has("python3") and has("nvidia-smi"):
        capabilities.append("gpu_python")
    if has("python3") and has("ffmpeg"):
        capabilities.append("media")
    if has("git") and has("heroku"):
        capabilities.append("deploy_heroku")
    if has("docker"):
        capabilities.append("container")
    return capabilities or ["python"]


def process_once(client: Client, node_id: str, capabilities: list[str], config: dict) -> bool:
    metadata = machine_inventory(config)
    capabilities = sorted(set(capabilities + derive_capabilities(metadata)))
    client.post("/api/runner/register", {"node_id": node_id, "capabilities": capabilities, "metadata": metadata})
    polled = client.post("/api/runner/poll", {"node_id": node_id, "capabilities": capabilities, "metadata": metadata})
    job = polled.get("job")
    if not job:
        return False

    client.event(job["id"], "info", f"{node_id} started {job['capability']} job")
    if job["capability"] == "python":
        status, result = run_python_job(job, client, polled.get("runner_config") or {})
    else:
        status, result = "failed", {"error": f"unsupported capability {job['capability']}"}
    client.post(
        f"/api/jobs/{job['id']}/complete",
        {"runner_id": node_id, "status": status, "result": result},
        timeout=30,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("OPENCLAW_CONTROL_PLANE_URL", DEFAULT_URL))
    parser.add_argument("--token", default=os.environ.get("OPENCLAW_ADMIN_TOKEN", "dev-token"))
    parser.add_argument("--node-id", default=os.environ.get("OPENCLAW_RUNNER_ID", f"{socket.gethostname()}-runner"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--config", default=os.environ.get("OPENCLAW_RUNNER_CONFIG", str(DEFAULT_CONFIG_PATH)))
    args = parser.parse_args()

    client = Client(args.url, args.token)
    config = load_runner_config(Path(args.config))
    capabilities = ["python"]
    print(f"runner {args.node_id} connected to {args.url} with capabilities={capabilities}")
    while True:
        try:
            did_work = process_once(client, args.node_id, capabilities, config)
            if args.once:
                print("processed one job" if did_work else "no queued job")
                return 0
            time.sleep(0.2 if did_work else args.interval)
        except urllib.error.URLError as exc:
            print(f"control plane unavailable: {exc}")
            if args.once:
                return 2
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
