"""本机 Web UI 服务（参考 Cat Café：web 前端 + 本地 API；业务服务不变）。"""

from __future__ import annotations

import json
import threading
import webbrowser
from dataclasses import asdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from multi_agent_room.agent_service import AgentService
from multi_agent_room.logging_setup import log_event
from multi_agent_room.model_service import ModelService
from multi_agent_room.room_service import JUDGE_COMMANDS, RoomService

STATIC_DIR = Path(__file__).resolve().parent / "web_static"


class AppContext:
    def __init__(self) -> None:
        self.models = ModelService()
        self.agents = AgentService(models=self.models)
        self.rooms = RoomService(agents=self.agents, models=self.models)


def _json(handler: SimpleHTTPRequestHandler, code: int, payload: Any) -> None:
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json(handler: SimpleHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    body = handler.rfile.read(length)
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def _model_public(m: Any) -> dict[str, Any]:
    d = m.to_public_dict() if hasattr(m, "to_public_dict") else asdict(m)
    return d


class CafeHandler(SimpleHTTPRequestHandler):
    ctx: AppContext

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # 安静一点，避免刷屏
        if args and str(args[0]).startswith(("200", "304")):
            return
        log_event("web_http", fmt % args)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            self._api_get(path, parse_qs(parsed.query))
            return
        # static
        if path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        rel = path.lstrip("/").replace("\\", "/")
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())):
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        ctype = "text/plain; charset=utf-8"
        if target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif target.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        self._serve_file(target, ctype)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error(404)
            return
        try:
            data = _read_json(self)
            self._api_post(parsed.path, data)
        except Exception as exc:  # noqa: BLE001
            _json(self, 400, {"ok": False, "error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error(404)
            return
        try:
            self._api_delete(parsed.path)
        except Exception as exc:  # noqa: BLE001
            _json(self, 400, {"ok": False, "error": str(exc)})

    def _serve_file(self, path: Path, content_type: str) -> None:
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _api_get(self, path: str, qs: dict[str, list[str]]) -> None:
        ctx = self.ctx
        if path == "/api/health":
            _json(self, 200, {"ok": True, "ui": "cafe-web"})
            return
        if path == "/api/models":
            _json(self, 200, {"ok": True, "items": [_model_public(m) for m in ctx.models.list_models()]})
            return
        if path == "/api/models/bindable":
            items = [_model_public(m) for m in ctx.models.list_bindable()]
            _json(self, 200, {"ok": True, "items": items})
            return
        if path == "/api/agents":
            items = []
            for a in ctx.agents.list_agents():
                m = ctx.models.store.get(a.model_config_id)
                health = "模型缺失"
                if m:
                    if not m.enabled:
                        health = "已禁用"
                    elif m.status == "ready":
                        health = "ready"
                    else:
                        health = m.status
                items.append(
                    {
                        "agent_id": a.agent_id,
                        "display_name": a.display_name,
                        "model_config_id": a.model_config_id,
                        "health": health,
                    }
                )
            _json(self, 200, {"ok": True, "items": items})
            return
        if path == "/api/rooms":
            rooms = []
            for r in ctx.rooms.list_rooms():
                rooms.append(asdict(r))
            cur = ctx.rooms.current_room()
            _json(
                self,
                200,
                {
                    "ok": True,
                    "items": rooms,
                    "current_id": cur.room_id if cur else None,
                    "judge_commands": list(JUDGE_COMMANDS),
                },
            )
            return
        if path.startswith("/api/rooms/") and path.endswith("/snapshot"):
            rid = path[len("/api/rooms/") : -len("/snapshot")]
            room = ctx.rooms.get_room(rid)
            if not room:
                _json(self, 404, {"ok": False, "error": "房间不存在"})
                return
            try:
                ctx.rooms.enter_room(rid)
            except Exception:  # noqa: BLE001
                pass
            doc = ctx.rooms.get_doc(rid)
            doc_lines = doc.render_lines() if hasattr(doc, "render_lines") else [str(doc)]
            feed: list[dict[str, str]] = []
            turns = list(getattr(room, "chat_turns", None) or [])
            if turns:
                for t in turns:
                    role = str(t.get("role") or "user")
                    tag = "agent" if role in ("assistant", "agent") else "user"
                    text = str(t.get("text") or "").strip()
                    if text:
                        feed.append({"tag": tag, "text": text})
            else:
                # 兼容旧房间：尚无 chat_turns 时回退钉选+当前稿
                if room.pinned_question:
                    feed.append({"tag": "user", "text": room.pinned_question})
                for line in doc_lines:
                    feed.append({"tag": "agent", "text": line})
            for ev in ctx.rooms.timeline.list_events(rid, include_collapsed=False):
                feed.append(
                    {
                        "tag": "sys",
                        "text": f"{ev.kind}: {ev.summary}",
                    }
                )
            ready = [
                {
                    "agent_id": a.agent_id,
                    "display_name": a.display_name,
                    "model_config_id": a.model_config_id,
                }
                for a in ctx.rooms.list_ready_agents()
            ]
            skills = [
                {
                    "skill_id": s.skill_id,
                    "name": s.name,
                    "risk": s.risk,
                    "enabled": s.enabled,
                    "default_room_auth": s.default_room_auth,
                    "description": s.description,
                }
                for s in ctx.rooms.tools.skills.list_skills()
            ]
            _json(
                self,
                200,
                {
                    "ok": True,
                    "room": asdict(room),
                    "doc_lines": doc_lines,
                    "final": ctx.rooms.final_slot_text(rid),
                    "ready_agents": ready,
                    "feed": feed,
                    "chat_turns": turns,
                    "agenda": room.agenda_text() if hasattr(room, "agenda_text") else "",
                    "lock": room.qualification_lock_text()
                    if hasattr(room, "qualification_lock_text")
                    else "",
                    "tools": {
                        "workspace_path": room.workspace_path,
                        "skills": skills,
                        "note": (
                            "Cursor 式工具已配置：dir_list / glob_search / file_read / "
                            "file_write / search_replace / file_delete。"
                            "绑定工作区后回复走工具循环；真实落盘、禁软链接；表格请用 .csv。"
                            "须重启服务后生效。右侧事件监控可见 ToolCall。"
                        ),
                    },
                },
            )
            return
        _json(self, 404, {"ok": False, "error": f"unknown GET {path}"})

    def _api_post(self, path: str, data: dict[str, Any]) -> None:
        ctx = self.ctx
        if path == "/api/models/add":
            cfg, result, listed = ctx.models.add_from_endpoint(
                base_url=str(data.get("base_url") or ""),
                api_key=str(data.get("api_key") or ""),
                display_name=str(data.get("display_name") or ""),
                model_id=str(data.get("model_id") or ""),
                timeout_ms=int(data.get("timeout_ms") or 15000),
                auto_probe=True,
            )
            _json(
                self,
                200,
                {
                    "ok": True,
                    "model": _model_public(cfg),
                    "probe": asdict(result) if result else None,
                    "listed": {
                        "ok": listed.ok,
                        "ui_text": listed.ui_text,
                        "model_ids": listed.model_ids,
                        "message": listed.message,
                    },
                },
            )
            return
        if path == "/api/models/discover":
            listed = ctx.models.discover_remote_models(
                base_url=str(data.get("base_url") or ""),
                api_key=str(data.get("api_key") or ""),
                timeout_ms=int(data.get("timeout_ms") or 15000),
            )
            _json(
                self,
                200,
                {
                    "ok": listed.ok,
                    "ui_text": listed.ui_text,
                    "model_ids": listed.model_ids,
                    "message": listed.message,
                    "code": listed.code,
                },
            )
            return
        if path.startswith("/api/models/") and path.endswith("/probe"):
            cid = path[len("/api/models/") : -len("/probe")]
            cfg, result = ctx.models.probe(cid)
            _json(
                self,
                200,
                {"ok": result.ok, "model": _model_public(cfg), "probe": asdict(result)},
            )
            return
        if path.startswith("/api/models/") and path.endswith("/chat_test"):
            cid = path[len("/api/models/") : -len("/chat_test")]
            cfg, result = ctx.models.chat_test(
                cid, str(data.get("prompt") or "请用一句话回复：pong")
            )
            _json(
                self,
                200,
                {
                    "ok": result.ok,
                    "model": _model_public(cfg),
                    "reply": result.reply,
                    "message": result.message,
                    "ui_text": result.ui_text,
                },
            )
            return
        if path.startswith("/api/models/") and path.endswith("/enabled"):
            cid = path[len("/api/models/") : -len("/enabled")]
            enabled = bool(data.get("enabled", True))
            cfg = ctx.models.set_enabled(cid, enabled)
            _json(self, 200, {"ok": True, "model": _model_public(cfg)})
            return
        if path == "/api/agents":
            mid = str(data.get("model_config_id") or "").strip()
            name = str(data.get("display_name") or "Agent").strip() or "Agent"
            ok, reason = ctx.models.can_bind(mid)
            if not ok:
                _json(self, 400, {"ok": False, "error": reason})
                return
            p = ctx.agents.create_agent(display_name=name, model_config_id=mid)
            _json(
                self,
                200,
                {
                    "ok": True,
                    "agent": {
                        "agent_id": p.agent_id,
                        "display_name": p.display_name,
                        "model_config_id": p.model_config_id,
                    },
                },
            )
            return
        if path == "/api/rooms":
            title = str(data.get("title") or "新房间").strip() or "新房间"
            room = ctx.rooms.create_room(title)
            _json(self, 200, {"ok": True, "room": asdict(room)})
            return
        if path.startswith("/api/rooms/") and path.endswith("/enter"):
            rid = path[len("/api/rooms/") : -len("/enter")]
            room = ctx.rooms.enter_room(rid)
            _json(self, 200, {"ok": True, "room": asdict(room)})
            return
        if path.startswith("/api/rooms/") and path.endswith("/workspace"):
            rid = path[len("/api/rooms/") : -len("/workspace")]
            path_raw = str(data.get("path") or "").strip()
            if not path_raw:
                _json(self, 400, {"ok": False, "error": "path 不能为空"})
                return
            try:
                room = ctx.rooms.set_workspace(rid, path_raw)
            except (OSError, ValueError) as exc:
                _json(self, 400, {"ok": False, "error": str(exc)})
                return
            # 绑定后授权本房写文件（仍受阶段门控）
            try:
                ctx.rooms.tools.skills.authorize("file.write", room_id=rid)
                ctx.rooms.tools.skills.authorize("file.read", room_id=rid)
                ctx.rooms.tools.skills.authorize("dir.list", room_id=rid)
                ctx.rooms.tools.skills.authorize("search.replace", room_id=rid)
                ctx.rooms.tools.skills.authorize("file.delete", room_id=rid)
                ctx.rooms.tools.skills.authorize("glob.search", room_id=rid)
            except Exception:  # noqa: BLE001
                pass
            _json(
                self,
                200,
                {"ok": True, "room": asdict(room), "workspace_path": room.workspace_path},
            )
            return
        if path.startswith("/api/rooms/") and path.endswith("/ask"):
            rid = path[len("/api/rooms/") : -len("/ask")]
            try:
                result = ctx.rooms.ask_and_generate(
                    rid,
                    str(data.get("question") or ""),
                    auto_w1=True,
                    attachments=list(data.get("attachments") or []),
                )
            except ValueError as exc:
                _json(self, 400, {"ok": False, "error": str(exc)})
                return
            except PermissionError as exc:
                _json(self, 400, {"ok": False, "error": str(exc)})
                return
            room = result["room"]
            _json(
                self,
                200,
                {
                    "ok": True,
                    "room": asdict(room),
                    "mode": result.get("mode"),
                    "degrade_a": result.get("degrade_a"),
                    "w1_ok": result.get("w1_ok"),
                    "w1_reply": result.get("w1_reply"),
                    "w1_error": result.get("w1_error"),
                    "ui_text": result.get("ui_text"),
                    "tool_steps": result.get("tool_steps") or [],
                    "attachments": result.get("attachments") or [],
                },
            )
            return
        if path.startswith("/api/rooms/") and path.endswith("/invite"):
            rid = path[len("/api/rooms/") : -len("/invite")]
            room = ctx.rooms.invite_ready_agent(rid, str(data.get("agent_id") or ""))
            _json(self, 200, {"ok": True, "room": asdict(room)})
            return
        if path.startswith("/api/rooms/") and path.endswith("/demo_review"):
            rid = path[len("/api/rooms/") : -len("/demo_review")]
            room = ctx.rooms.mark_gate_passed_demo(rid)
            _json(self, 200, {"ok": True, "room": asdict(room)})
            return
        if path.startswith("/api/rooms/") and path.endswith("/judge"):
            rid = path[len("/api/rooms/") : -len("/judge")]
            cmd = str(data.get("command") or "")
            room = ctx.rooms.judge_command(rid, cmd)
            _json(self, 200, {"ok": True, "room": asdict(room)})
            return
        if path.startswith("/api/rooms/") and path.endswith("/interrupt"):
            rid = path[len("/api/rooms/") : -len("/interrupt")]
            room = ctx.rooms.interrupt(rid)
            _json(self, 200, {"ok": True, "room": asdict(room)})
            return
        if path.startswith("/api/rooms/") and path.endswith("/resume"):
            rid = path[len("/api/rooms/") : -len("/resume")]
            room = ctx.rooms.resume(rid)
            _json(self, 200, {"ok": True, "room": asdict(room)})
            return
        _json(self, 404, {"ok": False, "error": f"unknown POST {path}"})

    def _api_delete(self, path: str) -> None:
        ctx = self.ctx
        if path.startswith("/api/agents/"):
            aid = path[len("/api/agents/") :]
            try:
                ctx.agents.delete_agent(aid)
            except KeyError as exc:
                _json(self, 404, {"ok": False, "error": str(exc)})
                return
            _json(self, 200, {"ok": True})
            return
        if path.startswith("/api/models/"):
            cid = path[len("/api/models/") :]
            ctx.models.delete_model(cid)
            _json(self, 200, {"ok": True})
            return
        _json(self, 404, {"ok": False, "error": f"unknown DELETE {path}"})


def start_web_ui(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> ThreadingHTTPServer:
    if not STATIC_DIR.is_dir():
        raise FileNotFoundError(f"缺少 Web 静态资源目录: {STATIC_DIR}")
    ctx = AppContext()
    handler = type("BoundCafeHandler", (CafeHandler,), {"ctx": ctx})
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    log_event("web_ui_start", f"url={url}")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    return server


def serve_forever(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    server = start_web_ui(host=host, port=port, open_browser=open_browser)
    print(f"MultiAgentRoom Web UI → http://{host}:{port}/")
    print("按 Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log_event("web_ui_stop", "server closed")
