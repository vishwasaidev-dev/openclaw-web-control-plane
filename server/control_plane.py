#!/usr/bin/env python3
"""OpenClaw web control plane MVP.

Stdlib-only HTTP service with durable SQLite state. It is intentionally small so
the first version can run locally, on Heroku, or inside a basic container.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
REQUIREMENTS_PATH = ROOT / "config" / "tool_requirements.json"


def now() -> float:
    return time.time()


def json_dumps(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_requirements() -> dict:
    if not REQUIREMENTS_PATH.exists():
        return {}
    return json.loads(REQUIREMENTS_PATH.read_text(encoding="utf-8"))


def evaluate_requirements(metadata: dict, capabilities: list[str]) -> dict:
    catalog = load_requirements()
    tools = metadata.get("tools") if isinstance(metadata.get("tools"), dict) else {}

    def has_tool(name: str) -> bool:
        return bool(tools.get(name, {}).get("available"))

    results = {}
    for key, spec in catalog.items():
        required = [str(item) for item in spec.get("required_tools", [])]
        any_groups = spec.get("required_any_tools", [])
        missing = [tool for tool in required if not has_tool(tool)]
        missing_any = []
        for group in any_groups:
            choices = [str(item) for item in group]
            if choices and not any(has_tool(choice) for choice in choices):
                missing_any.append(choices)
        capability = str(spec.get("capability") or key)
        capability_ok = capability in capabilities
        ok = not missing and not missing_any and capability_ok
        results[key] = {
            "label": spec.get("label") or key,
            "capability": capability,
            "ok": ok,
            "capability_ok": capability_ok,
            "missing_tools": missing,
            "missing_any_tools": missing_any,
            "optional_missing": [tool for tool in spec.get("optional_tools", []) if not has_tool(str(tool))],
        }
    return results


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "control-plane.sqlite3"
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists jobs (
                    id text primary key,
                    objective text not null,
                    capability text not null,
                    payload_json text not null,
                    status text not null,
                    runner_id text,
                    result_json text,
                    created_at real not null,
                    updated_at real not null
                );
                create table if not exists events (
                    id integer primary key autoincrement,
                    job_id text not null,
                    ts real not null,
                    level text not null,
                    message text not null
                );
                create table if not exists nodes (
                    id text primary key,
                    capabilities_json text not null,
                    metadata_json text not null default '{}',
                    status text not null,
                    last_seen real not null
                );
                create table if not exists node_settings (
                    node_id text primary key,
                    selected_model_id text,
                    updated_at real not null
                );
                """
            )
            columns = {row["name"] for row in conn.execute("pragma table_info(nodes)").fetchall()}
            if "metadata_json" not in columns:
                conn.execute("alter table nodes add column metadata_json text not null default '{}'")

    def create_job(self, objective: str, capability: str, payload: dict) -> dict:
        job_id = "job_" + uuid.uuid4().hex[:16]
        ts = now()
        with self.connect() as conn:
            conn.execute(
                """
                insert into jobs (id, objective, capability, payload_json, status, created_at, updated_at)
                values (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (job_id, objective, capability, json.dumps(payload), ts, ts),
            )
            conn.execute(
                "insert into events (job_id, ts, level, message) values (?, ?, 'info', ?)",
                (job_id, ts, "job queued"),
            )
        return self.get_job(job_id, include_events=True)

    def list_jobs(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("select * from jobs order by created_at desc limit 100").fetchall()
        return [self.row_to_job(row) for row in rows]

    def get_job(self, job_id: str, *, include_events: bool = False) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("select * from jobs where id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            job = self.row_to_job(row)
            if include_events:
                events = conn.execute(
                    "select ts, level, message from events where job_id = ? order by id",
                    (job_id,),
                ).fetchall()
                job["events"] = [dict(event) for event in events]
            return job

    def row_to_job(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "objective": row["objective"],
            "capability": row["capability"],
            "payload": json.loads(row["payload_json"]),
            "status": row["status"],
            "runner_id": row["runner_id"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def register_node(self, node_id: str, capabilities: list[str], metadata: dict | None = None) -> dict:
        ts = now()
        metadata = metadata or {}
        with self.connect() as conn:
            conn.execute(
                """
                insert into nodes (id, capabilities_json, metadata_json, status, last_seen)
                values (?, ?, ?, 'online', ?)
                on conflict(id) do update set
                    capabilities_json=excluded.capabilities_json,
                    metadata_json=excluded.metadata_json,
                    status='online',
                    last_seen=excluded.last_seen
                """,
                (node_id, json.dumps(capabilities), json.dumps(metadata), ts),
            )
        return {"id": node_id, "capabilities": capabilities, "metadata": metadata, "status": "online", "last_seen": ts}

    def list_nodes(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select nodes.*, node_settings.selected_model_id
                from nodes
                left join node_settings on node_settings.node_id = nodes.id
                order by nodes.last_seen desc
                """
            ).fetchall()
        nodes = []
        for row in rows:
            capabilities = json.loads(row["capabilities_json"])
            metadata = json.loads(row["metadata_json"] or "{}")
            selected_model = self.resolve_selected_model(metadata, row["selected_model_id"])
            nodes.append({
                "id": row["id"],
                "capabilities": capabilities,
                "metadata": metadata,
                "selected_model": selected_model,
                "compatibility": evaluate_requirements(metadata, capabilities),
                "status": row["status"],
                "last_seen": row["last_seen"],
            })
        return nodes

    def poll_job(self, node_id: str, capabilities: list[str], metadata: dict | None = None) -> dict | None:
        self.register_node(node_id, capabilities, metadata)
        with self.connect() as conn:
            for capability in capabilities:
                row = conn.execute(
                    "select * from jobs where status = 'queued' and capability = ? order by created_at limit 1",
                    (capability,),
                ).fetchone()
                if row is None:
                    continue
                ts = now()
                conn.execute(
                    "update jobs set status='running', runner_id=?, updated_at=? where id=?",
                    (node_id, ts, row["id"]),
                )
                conn.execute(
                    "insert into events (job_id, ts, level, message) values (?, ?, 'info', ?)",
                    (row["id"], ts, f"claimed by {node_id}"),
                )
                break
            else:
                return None
        return self.get_job(row["id"], include_events=True)

    def get_runner_config(self, node_id: str, metadata: dict | None = None) -> dict:
        selected_model_id = None
        with self.connect() as conn:
            row = conn.execute("select selected_model_id from node_settings where node_id = ?", (node_id,)).fetchone()
            if row is not None:
                selected_model_id = row["selected_model_id"]
        return {"selected_model": self.resolve_selected_model(metadata or {}, selected_model_id)}

    def set_node_model(self, node_id: str, selected_model_id: str | None) -> dict:
        ts = now()
        with self.connect() as conn:
            conn.execute(
                """
                insert into node_settings (node_id, selected_model_id, updated_at)
                values (?, ?, ?)
                on conflict(node_id) do update set
                    selected_model_id=excluded.selected_model_id,
                    updated_at=excluded.updated_at
                """,
                (node_id, selected_model_id, ts),
            )
            row = conn.execute("select metadata_json from nodes where id = ?", (node_id,)).fetchone()
        metadata = json.loads(row["metadata_json"] or "{}") if row else {}
        return {"node_id": node_id, "selected_model": self.resolve_selected_model(metadata, selected_model_id), "updated_at": ts}

    def resolve_selected_model(self, metadata: dict, selected_model_id: str | None) -> dict | None:
        models = metadata.get("models") if isinstance(metadata.get("models"), dict) else {}
        items = models.get("items") if isinstance(models.get("items"), list) else []
        default_id = models.get("default_model_id")
        target_id = selected_model_id or default_id
        if not target_id:
            return None
        for item in items:
            if isinstance(item, dict) and item.get("id") == target_id:
                selected = dict(item)
                selected["source"] = "control-plane" if selected_model_id else "runner-default"
                return selected
        return {"id": target_id, "name": target_id, "available": False, "source": "control-plane" if selected_model_id else "runner-default"}

    def add_event(self, job_id: str, level: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "insert into events (job_id, ts, level, message) values (?, ?, ?, ?)",
                (job_id, now(), level, message),
            )

    def complete_job(self, job_id: str, runner_id: str, status: str, result: dict) -> dict | None:
        ts = now()
        result = self.persist_artifacts(job_id, result)
        with self.connect() as conn:
            row = conn.execute("select * from jobs where id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            conn.execute(
                "update jobs set status=?, runner_id=?, result_json=?, updated_at=? where id=?",
                (status, runner_id, json.dumps(result), ts, job_id),
            )
            conn.execute(
                "insert into events (job_id, ts, level, message) values (?, ?, 'info', ?)",
                (job_id, ts, f"job {status}"),
            )
        return self.get_job(job_id, include_events=True)

    def persist_artifacts(self, job_id: str, result: dict) -> dict:
        encoded_files = result.pop("artifact_files", [])
        if not isinstance(encoded_files, list):
            return result
        artifact_dir = self.data_dir / "artifacts" / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts = list(result.get("artifacts") or [])
        for item in encoded_files:
            if not isinstance(item, dict):
                continue
            name = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(item.get("name") or "artifact")).strip("._")
            data_b64 = str(item.get("base64") or "")
            if not name or not data_b64:
                continue
            content = base64.b64decode(data_b64)
            path = artifact_dir / name
            path.write_bytes(content)
            artifacts.append({"name": name, "bytes": len(content), "path": str(path.relative_to(self.data_dir))})
        result["artifacts"] = artifacts
        return result


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenClawControlPlane/0.1"

    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.admin_token  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"status": "ok", "service": "openclaw-control-plane"})
            return
        if parsed.path == "/":
            self.send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self.send_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/api/jobs":
            if not self.require_auth():
                return
            self.send_json({"jobs": self.store.list_jobs()})
            return
        if parsed.path == "/api/nodes":
            if not self.require_auth():
                return
            self.send_json({"nodes": self.store.list_nodes()})
            return
        if parsed.path == "/api/requirements":
            if not self.require_auth():
                return
            self.send_json({"requirements": load_requirements()})
            return
        if parsed.path.startswith("/api/nodes/") and parsed.path.endswith("/model"):
            if not self.require_auth():
                return
            node_id = parsed.path.split("/")[3]
            nodes = [node for node in self.store.list_nodes() if node["id"] == node_id]
            if not nodes:
                self.send_error_json(HTTPStatus.NOT_FOUND, "node not found")
                return
            self.send_json({"node_id": node_id, "selected_model": nodes[0].get("selected_model")})
            return
        if parsed.path.startswith("/api/jobs/"):
            if not self.require_auth():
                return
            job_id = parsed.path.split("/")[3]
            job = self.store.get_job(job_id, include_events=True)
            if job is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "job not found")
                return
            self.send_json(job)
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/mcp":
            self.handle_mcp()
            return
        if not self.require_auth():
            return
        data = self.read_json()
        if parsed.path == "/api/jobs":
            objective = str(data.get("objective") or "").strip()
            capability = str(data.get("capability") or "").strip()
            payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
            if not objective or not capability:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "objective and capability are required")
                return
            self.send_json(self.store.create_job(objective, capability, payload), HTTPStatus.CREATED)
            return
        if parsed.path == "/api/runner/register":
            node_id = str(data.get("node_id") or "").strip()
            capabilities = data.get("capabilities")
            if not node_id or not isinstance(capabilities, list):
                self.send_error_json(HTTPStatus.BAD_REQUEST, "node_id and capabilities are required")
                return
            metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
            self.send_json(self.store.register_node(node_id, [str(item) for item in capabilities], metadata))
            return
        if parsed.path == "/api/runner/poll":
            node_id = str(data.get("node_id") or "").strip()
            capabilities = data.get("capabilities")
            if not node_id or not isinstance(capabilities, list):
                self.send_error_json(HTTPStatus.BAD_REQUEST, "node_id and capabilities are required")
                return
            metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
            job = self.store.poll_job(node_id, [str(item) for item in capabilities], metadata)
            self.send_json({"job": job, "runner_config": self.store.get_runner_config(node_id, metadata)})
            return
        if parsed.path.startswith("/api/nodes/") and parsed.path.endswith("/model"):
            node_id = parsed.path.split("/")[3]
            selected_model_id = data.get("selected_model_id")
            if selected_model_id is not None:
                selected_model_id = str(selected_model_id).strip() or None
            self.send_json(self.store.set_node_model(node_id, selected_model_id))
            return
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "events":
            self.store.add_event(parts[2], str(data.get("level") or "info"), str(data.get("message") or ""))
            self.send_json({"ok": True})
            return
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "complete":
            job = self.store.complete_job(
                parts[2],
                str(data.get("runner_id") or ""),
                str(data.get("status") or "complete"),
                data.get("result") if isinstance(data.get("result"), dict) else {},
            )
            if job is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "job not found")
                return
            self.send_json(job)
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def handle_mcp(self) -> None:
        if not self.require_auth():
            return
        request = self.read_json()
        method = request.get("method")
        req_id = request.get("id")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "openclaw-control-plane", "version": "0.1"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {"name": "create_job", "description": "Create a durable OpenClaw job", "inputSchema": {"type": "object"}},
                        {"name": "list_jobs", "description": "List recent jobs", "inputSchema": {"type": "object"}},
                        {"name": "get_job", "description": "Get one job by id", "inputSchema": {"type": "object"}},
                    ]
                }
            elif method == "tools/call":
                params = request.get("params") or {}
                result = self.call_mcp_tool(str(params.get("name")), params.get("arguments") or {})
            else:
                raise ValueError(f"unsupported method {method}")
            self.send_json({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Exception as exc:
            self.send_json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(exc)}})

    def call_mcp_tool(self, name: str, args: dict) -> dict:
        if name == "create_job":
            job = self.store.create_job(
                str(args.get("objective") or "MCP-created job"),
                str(args.get("capability") or "python"),
                args.get("payload") if isinstance(args.get("payload"), dict) else {},
            )
            return {"content": [{"type": "text", "text": json.dumps(job, indent=2)}], "structuredContent": job}
        if name == "list_jobs":
            jobs = self.store.list_jobs()
            return {"content": [{"type": "text", "text": json.dumps(jobs, indent=2)}], "structuredContent": {"jobs": jobs}}
        if name == "get_job":
            job_id = str(args.get("job_id") or "")
            job = self.store.get_job(job_id, include_events=True)
            if job is None:
                raise ValueError("job not found")
            return {"content": [{"type": "text", "text": json.dumps(job, indent=2)}], "structuredContent": job}
        raise ValueError(f"unknown tool {name}")

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        return value

    def require_auth(self) -> bool:
        if not self.token:
            return True
        expected = f"Bearer {self.token}"
        if self.headers.get("Authorization") == expected:
            return True
        self.send_error_json(HTTPStatus.UNAUTHORIZED, "unauthorized")
        return False

    def send_json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json_dumps(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[control-plane] {self.address_string()} {fmt % args}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8788")))
    parser.add_argument("--data-dir", default=os.environ.get("OPENCLAW_DATA_DIR", str(DEFAULT_DATA_DIR)))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.store = Store(Path(args.data_dir))  # type: ignore[attr-defined]
    server.admin_token = os.environ.get("OPENCLAW_ADMIN_TOKEN", "dev-token")  # type: ignore[attr-defined]
    print(f"OpenClaw control plane listening on http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
