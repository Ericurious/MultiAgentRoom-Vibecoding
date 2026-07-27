"""Cursor 风格工具循环：原生 tools + 文本协议回退 + 反幻觉。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from multi_agent_room.logging_setup import log_event
from multi_agent_room.tool_host import RoomToolContext, ToolHost, ToolResult

# OpenAI 兼容 tools（对齐 Cursor 常用文件类工具）
CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "dir_list",
            "description": "列出工作区目录（跳过软链接）。默认 path='.'。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_search",
            "description": "按 glob 模式查找文件，如 **/*.py、*.csv。",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "读取工作区文本文件（禁止软链接）。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": (
                "新建或覆盖真实文本文件。表格请写 .csv（不要用 .xlsx，无法生成真 Excel 二进制）。"
                "content 必须是完整文件正文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_replace",
            "description": "在已有文件中精确替换一段文本（类似 Cursor StrReplace）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_delete",
            "description": "删除工作区内的普通文件（禁止软链接/目录）。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]

_TOOL_TO_SKILL = {
    "dir_list": "dir.list",
    "glob_search": "glob.search",
    "file_read": "file.read",
    "file_write": "file.write",
    "search_replace": "search.replace",
    "file_delete": "file.delete",
}

_TEXT_TOOL_RE = re.compile(
    r"<<<TOOL\s*(\{.*?\})\s*>>>",
    re.DOTALL | re.IGNORECASE,
)
_CLAIM_FILE_RE = re.compile(
    r"(已(?:经)?(?:生成|保存|写入|创建|落地|写好)|successfully (?:created|wrote|saved)|saved (?:to|as))"
    r".{0,120}\.(xlsx|xls|csv|txt|md|py|json|html|css|js|ts|tsx)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class ToolStep:
    name: str
    arguments: dict[str, Any]
    ok: bool
    message: str
    data: Any = None


@dataclass
class ToolLoopResult:
    ok: bool
    reply: str
    steps: list[ToolStep] = field(default_factory=list)
    error: str = ""
    rounds: int = 0
    model_called: bool = False


ChatFn = Callable[..., Any]


class AgentToolLoop:
    """原生 function calling；若模型不调工具则解析 <<<TOOL{...}>>> 文本协议。"""

    def __init__(
        self,
        *,
        chat_fn: ChatFn,
        tools: ToolHost,
        ctx: RoomToolContext,
        timeline_append=None,
        max_rounds: int = 10,
    ) -> None:
        self.chat_fn = chat_fn
        self.tools = tools
        self.ctx = ctx
        self.timeline_append = timeline_append
        self.max_rounds = max_rounds

    def run(self, messages: list[dict[str, Any]], *, config_id: str) -> ToolLoopResult:
        msgs: list[dict[str, Any]] = [dict(m) for m in messages]
        steps: list[ToolStep] = []
        called = False
        nudge_used = 0

        log_event(
            "tool_loop_start",
            f"ws={self.ctx.workspace_path} max={self.max_rounds}",
            room_id=self.ctx.room_id,
        )

        for rnd in range(1, self.max_rounds + 1):
            # 前两轮尽量要求工具；之后 auto
            choice: Any = "auto"
            if rnd == 1:
                choice = "required"
            cfg, result = self.chat_fn(
                config_id,
                msgs,
                max_tokens=4096,
                tools=CHAT_TOOLS,
                tool_choice=choice,
            )
            # 若 required 不被支持，降级 auto 再试一次
            if not result.ok and rnd == 1 and "tool" in (result.message or "").lower():
                cfg, result = self.chat_fn(
                    config_id,
                    msgs,
                    max_tokens=4096,
                    tools=CHAT_TOOLS,
                    tool_choice="auto",
                )
            called = True
            if not result.ok:
                # 无 tools 支持时：去掉 tools 再聊，改走文本协议
                cfg, result = self.chat_fn(
                    config_id,
                    msgs
                    + [
                        {
                            "role": "system",
                            "content": (
                                "当前通道可能不支持原生 function calling。"
                                "你必须用文本工具块，格式严格为：\n"
                                '<<<TOOL\n{"name":"file_write","arguments":{"path":"a.csv","content":"..."} }\n>>>'
                            ),
                        }
                    ],
                    max_tokens=4096,
                )
                if not result.ok:
                    return ToolLoopResult(
                        False,
                        "",
                        steps=steps,
                        error=result.ui_text or result.message,
                        rounds=rnd,
                        model_called=True,
                    )

            native_calls = list(getattr(result, "tool_calls", None) or [])
            content = (result.reply or "").strip()
            text_calls = _parse_text_tool_calls(content)
            tool_calls = native_calls or text_calls
            raw_msg = getattr(result, "raw_message", None) or {}

            log_event(
                "tool_loop_round",
                f"round={rnd} native={len(native_calls)} text={len(text_calls)} chars={len(content)}",
                room_id=self.ctx.room_id,
            )

            if tool_calls:
                # 清理正文里的文本工具块，避免污染最终回复
                clean_content = _TEXT_TOOL_RE.sub("", content).strip() if text_calls else content
                if native_calls:
                    asst: dict[str, Any] = {
                        "role": "assistant",
                        "content": clean_content or None,
                        "tool_calls": raw_msg.get("tool_calls")
                        or [
                            {
                                "id": tc.get("id") or f"call_{i}",
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(
                                        tc.get("arguments") or {}, ensure_ascii=False
                                    ),
                                },
                            }
                            for i, tc in enumerate(tool_calls)
                        ],
                    }
                    msgs.append(asst)
                    for tc in tool_calls:
                        payload, step = self._exec_one(tc)
                        steps.append(step)
                        msgs.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id") or f"call_{tc.get('name')}",
                                "content": json.dumps(payload, ensure_ascii=False)[:12000],
                            }
                        )
                else:
                    # 文本协议：以 user 回执注入结果
                    msgs.append({"role": "assistant", "content": content})
                    receipts = []
                    for tc in tool_calls:
                        payload, step = self._exec_one(tc)
                        steps.append(step)
                        receipts.append(payload)
                    msgs.append(
                        {
                            "role": "user",
                            "content": (
                                "【工具执行结果，非用户发言】\n"
                                + json.dumps(receipts, ensure_ascii=False)[:12000]
                                + "\n请根据真实结果继续：需要则再输出 <<<TOOL...>>> ，"
                                "全部完成后给出最终中文答复。"
                                "禁止在未成功 file_write 时声称文件已生成。"
                            ),
                        }
                    )
                continue

            # 无工具调用：检查是否在瞎编「已写入文件」
            wrote_ok = any(
                s.ok and s.name in ("file_write", "search_replace") for s in steps
            )
            if _CLAIM_FILE_RE.search(content) and not wrote_ok and nudge_used < 2:
                nudge_used += 1
                msgs.append({"role": "assistant", "content": content})
                msgs.append(
                    {
                        "role": "user",
                        "content": (
                            "【系统拦截】你声称文件已生成/保存，但本轮尚未成功调用 file_write。"
                            "禁止虚构落盘。请立即用工具写入真实文件："
                            '<<<TOOL\n{"name":"file_write","arguments":{"path":"工作区表格.csv","content":"列1,列2\\n1,2\\n"}}\n>>>'
                            "表格请用 .csv（不要 .xlsx）。写完后再用 dir_list 核实。"
                        ),
                    }
                )
                continue

            # 正常结束
            final = _TEXT_TOOL_RE.sub("", content).strip()
            log_event(
                "tool_loop_done",
                f"rounds={rnd} steps={len(steps)}",
                room_id=self.ctx.room_id,
            )
            return ToolLoopResult(
                True,
                final,
                steps=steps,
                rounds=rnd,
                model_called=called,
            )

        return ToolLoopResult(
            False,
            "",
            steps=steps,
            error=f"工具循环超过 {self.max_rounds} 轮仍未结束",
            rounds=self.max_rounds,
            model_called=called,
        )

    def _exec_one(self, tc: dict[str, Any]) -> tuple[dict[str, Any], ToolStep]:
        name = str(tc.get("name") or "")
        args = tc.get("arguments") if isinstance(tc.get("arguments"), dict) else {}
        if not args and isinstance(tc.get("arguments"), str):
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
        # xlsx 劝退：自动改写为 csv 提示失败
        if name == "file_write" and str(args.get("path") or "").lower().endswith(
            (".xlsx", ".xls")
        ):
            tip = (
                "不支持写入真 Excel 二进制。请改用 .csv 路径，例如 工作区表格.csv"
            )
            step = ToolStep(name, args or {}, False, tip)
            if self.timeline_append:
                self.timeline_append(self.ctx.room_id, "ToolCall", f"{name} rejected: xlsx")
            return {"ok": False, "error": tip}, step

        skill_id = _TOOL_TO_SKILL.get(name)
        if not skill_id:
            step = ToolStep(name, args or {}, False, f"未知工具: {name}")
            return {"ok": False, "error": f"未知工具: {name}"}, step

        tr = self.tools.invoke_skill(
            skill_id,
            args or {},
            self.ctx,
            user_confirmed=False,
            timeline_append=self.timeline_append,
        )
        step = ToolStep(
            name=name,
            arguments=args or {},
            ok=bool(tr.ok),
            message=tr.message,
            data=tr.data,
        )
        if self.timeline_append:
            self.timeline_append(
                self.ctx.room_id,
                "ToolCall",
                f"{name} ok={tr.ok} {tr.message}",
            )
        payload = {
            "ok": tr.ok,
            "message": tr.message,
            "code": tr.code,
            "data": _trim_data(tr.data),
        }
        return payload, step


def _parse_text_tool_calls(content: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for i, m in enumerate(_TEXT_TOOL_RE.finditer(content or "")):
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        name = str(obj.get("name") or obj.get("tool") or "")
        args = obj.get("arguments") if isinstance(obj.get("arguments"), dict) else {}
        if not args:
            # 允许扁平 {name, path, content}
            args = {
                k: v
                for k, v in obj.items()
                if k not in ("name", "tool")
            }
        if name:
            found.append({"id": f"text_{i}", "name": name, "arguments": args})
    return found


def _trim_data(data: Any) -> Any:
    if data is None:
        return None
    if isinstance(data, dict):
        out = dict(data)
        if "content" in out and isinstance(out["content"], str) and len(out["content"]) > 8000:
            out["content"] = out["content"][:8000] + "\n…(truncated)"
            out["truncated"] = True
        return out
    return data


def build_agent_system_prompt(*, workspace: str, has_attachments: bool) -> str:
    attach = (
        "用户附件已落到 `_mar_inbox/`，请先 dir_list/file_read 查看。"
        if has_attachments
        else ""
    )
    return (
        "你是 MultiAgentRoom 的工人 Agent，工具流程对齐 Cursor Agent。\n"
        f"工作区（真实本地目录）：{workspace}\n"
        "可用工具：dir_list, glob_search, file_read, file_write, search_replace, file_delete。\n"
        "硬性规则：\n"
        "1. 涉及目录/文件必须调用工具；禁止口头声称已写入而未调用 file_write。\n"
        "2. 只写真实文件，禁止软链接；表格用 .csv，不要用 .xlsx。\n"
        "3. 优先原生 function calling；若通道不支持，使用文本块：\n"
        '   <<<TOOL\n   {"name":"file_write","arguments":{"path":"a.csv","content":"a,b\\n1,2\\n"}}\n   >>>\n'
        "4. 写完应用 dir_list 或 file_read 核实，再向用户汇报真实结果。\n"
        "5. 不要宣布评议通过。\n"
        f"{attach}"
    )
