"""T-M10-01：Skill 注册 / 开关 / 授权。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from multi_agent_room.logging_setup import log_event

RiskLevel = Literal["read", "write", "high"]


@dataclass
class SkillDef:
    skill_id: str
    name: str
    tool_name: str
    risk: RiskLevel = "read"
    enabled: bool = True
    description: str = ""
    # 空集合 = 尚未授权任何人/房（须显式授权）
    authorized_agents: set[str] = field(default_factory=set)
    authorized_rooms: set[str] = field(default_factory=set)
    # True = 房间级默认授权（内置只读可开）
    default_room_auth: bool = False
    schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "skillId": self.skill_id,
            "name": self.name,
            "toolName": self.tool_name,
            "risk": self.risk,
            "enabled": self.enabled,
            "description": self.description,
            "defaultRoomAuth": self.default_room_auth,
        }


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillDef] = {}

    def register(self, skill: SkillDef) -> SkillDef:
        self._skills[skill.skill_id] = skill
        log_event("skill_register", f"id={skill.skill_id} risk={skill.risk}")
        return skill

    def get(self, skill_id: str) -> Optional[SkillDef]:
        return self._skills.get(skill_id)

    def list_skills(self) -> list[SkillDef]:
        return list(self._skills.values())

    def set_enabled(self, skill_id: str, enabled: bool) -> SkillDef:
        sk = self._require(skill_id)
        sk.enabled = enabled
        log_event("skill_toggle", f"id={skill_id} enabled={enabled}")
        return sk

    def authorize(
        self,
        skill_id: str,
        *,
        room_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> SkillDef:
        sk = self._require(skill_id)
        if room_id:
            sk.authorized_rooms.add(room_id)
        if agent_id:
            sk.authorized_agents.add(agent_id)
        log_event(
            "skill_authorize",
            f"id={skill_id} room={room_id or '-'} agent={agent_id or '-'}",
        )
        return sk

    def revoke(
        self,
        skill_id: str,
        *,
        room_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> SkillDef:
        sk = self._require(skill_id)
        if room_id:
            sk.authorized_rooms.discard(room_id)
        if agent_id:
            sk.authorized_agents.discard(agent_id)
        return sk

    def check_trigger(
        self,
        skill_id: str,
        *,
        room_id: str,
        agent_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """M10-A：未授权 / 关闭则不可触发。"""
        sk = self._skills.get(skill_id)
        if not sk:
            return False, f"未知 Skill: {skill_id}"
        if not sk.enabled:
            return False, f"Skill 已关闭: {skill_id}"
        if sk.default_room_auth:
            return True, "ok"
        if room_id in sk.authorized_rooms:
            return True, "ok"
        if agent_id and agent_id in sk.authorized_agents:
            return True, "ok"
        return False, f"未授权 Skill 无法触发: {skill_id}"

    def _require(self, skill_id: str) -> SkillDef:
        sk = self._skills.get(skill_id)
        if not sk:
            raise KeyError(f"未知 Skill: {skill_id}")
        return sk


def builtin_skills() -> list[SkillDef]:
    """内置 Skill；写/高危默认不授权、不 default_room_auth。"""
    return [
        SkillDef(
            skill_id="file.read",
            name="读文件",
            tool_name="file.read",
            risk="read",
            default_room_auth=True,
            description="工作区内只读",
            schema={
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
        ),
        SkillDef(
            skill_id="dir.list",
            name="列目录",
            tool_name="dir.list",
            risk="read",
            default_room_auth=True,
            description="列出工作区目录",
            schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        ),
        SkillDef(
            skill_id="file.write",
            name="写文件",
            tool_name="file.write",
            risk="write",
            default_room_auth=False,
            description="工作区内写真实文件（禁软链接）",
            schema={
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        ),
        SkillDef(
            skill_id="search.replace",
            name="精确替换",
            tool_name="search.replace",
            risk="write",
            default_room_auth=False,
            description="文件内 StrReplace",
            schema={
                "type": "object",
                "required": ["path", "old_string", "new_string"],
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
            },
        ),
        SkillDef(
            skill_id="file.delete",
            name="删文件",
            tool_name="file.delete",
            risk="write",
            default_room_auth=False,
            description="删除工作区普通文件",
            schema={
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
        ),
        SkillDef(
            skill_id="glob.search",
            name="Glob 搜索",
            tool_name="glob.search",
            risk="read",
            default_room_auth=True,
            description="按模式查找文件",
            schema={
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
            },
        ),
        SkillDef(
            skill_id="terminal.run",
            name="终端执行",
            tool_name="terminal.run",
            risk="high",
            default_room_auth=False,
            description="白名单终端；高危需确认",
            schema={
                "type": "object",
                "required": ["argv"],
                "properties": {
                    "argv": {"type": "array"},
                    "timeout_sec": {"type": "number"},
                },
            },
        ),
        SkillDef(
            skill_id="mcp.call",
            name="MCP 工具调用",
            tool_name="mcp.call",
            risk="write",
            default_room_auth=False,
            schema={
                "type": "object",
                "required": ["server_id", "tool"],
                "properties": {
                    "server_id": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
            },
        ),
    ]
