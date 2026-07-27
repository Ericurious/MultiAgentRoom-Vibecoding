"""T-M10-03b：MCP Client 宿主生命周期（连接 / 发现 / 策略包）。

P1a：进程内 stub 传输——配置声明的 tools 在 connect 后经策略包过滤注册；
不依赖远端 MCP；失败回房间可读错误且不阻塞纯 Chat。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from multi_agent_room.logging_setup import log_event


@dataclass
class PolicyPack:
    policy_pack_id: str
    allow_tools: set[str] = field(default_factory=set)
    allow_resources: set[str] = field(default_factory=set)

    def allows_tool(self, name: str) -> bool:
        if not self.allow_tools:
            return False
        return name in self.allow_tools or "*" in self.allow_tools


@dataclass
class McpServerConfig:
    server_id: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    policy_pack_id: str = "default"
    # stub：声明可发现工具名 → 简单回显实现
    declared_tools: list[str] = field(default_factory=list)


@dataclass
class McpSession:
    config: McpServerConfig
    connected: bool = False
    tools: list[str] = field(default_factory=list)
    last_error: str = ""
    pid: Optional[int] = None  # stub 无真实子进程；预留


class McpHost:
    def __init__(self) -> None:
        self.policies: dict[str, PolicyPack] = {
            "default": PolicyPack("default", allow_tools={"echo", "ping"}),
        }
        self.servers: dict[str, McpServerConfig] = {}
        self.sessions: dict[str, McpSession] = {}

    def add_policy(self, pack: PolicyPack) -> None:
        self.policies[pack.policy_pack_id] = pack

    def configure(self, cfg: McpServerConfig) -> McpServerConfig:
        self.servers[cfg.server_id] = cfg
        log_event("mcp_configure", f"id={cfg.server_id} policy={cfg.policy_pack_id}")
        return cfg

    def connect(self, server_id: str) -> McpSession:
        cfg = self.servers.get(server_id)
        if not cfg:
            raise KeyError(f"未配置 MCP Server: {server_id}")
        pack = self.policies.get(cfg.policy_pack_id)
        if not pack:
            sess = McpSession(config=cfg, connected=False, last_error="策略包不存在")
            self.sessions[server_id] = sess
            log_event("mcp_connect_fail", sess.last_error, room_id=None)
            return sess
        # stub 发现：declared_tools；若空则用 echo
        discovered = list(cfg.declared_tools) or ["echo"]
        filtered = [t for t in discovered if pack.allows_tool(t)]
        sess = McpSession(
            config=cfg,
            connected=True,
            tools=filtered,
            last_error="" if filtered or discovered else "无可用工具",
        )
        # 策略过滤后为空仍算已连接，但 list 为空
        self.sessions[server_id] = sess
        log_event(
            "mcp_connect",
            f"id={server_id} tools={filtered}",
        )
        return sess

    def list_tools(self, server_id: str) -> list[str]:
        sess = self.sessions.get(server_id)
        if not sess or not sess.connected:
            raise RuntimeError(f"MCP 未连接: {server_id}")
        return list(sess.tools)

    def call_tool(
        self,
        server_id: str,
        tool: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        sess = self.sessions.get(server_id)
        if not sess or not sess.connected:
            raise RuntimeError(f"MCP 未连接: {server_id}")
        if tool not in sess.tools:
            raise PermissionError(f"工具不在策略包允许列表: {tool}")
        args = arguments or {}
        # stub 实现
        if tool in ("echo", "ping"):
            return {
                "ok": True,
                "tool": tool,
                "result": args.get("text") or args.get("message") or "pong",
                "server_id": server_id,
            }
        return {
            "ok": True,
            "tool": tool,
            "result": {"echo_args": args},
            "server_id": server_id,
        }

    def shutdown(self, server_id: str, *, force: bool = False) -> None:
        sess = self.sessions.pop(server_id, None)
        if not sess:
            return
        sess.connected = False
        if force and sess.pid:
            log_event("mcp_force_kill", f"pid={sess.pid}")
        log_event("mcp_shutdown", f"id={server_id} force={force}")
        time.sleep(0)  # 优雅点：占位
