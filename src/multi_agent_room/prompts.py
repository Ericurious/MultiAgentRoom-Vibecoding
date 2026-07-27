"""T-AGENT：Prompt 模板（W1–W4 / J1），对齐 spec §5.7。"""

from __future__ import annotations

from typing import Any, Optional

from multi_agent_room.redact import sanitize_messages

ROOM_RULES_SUMMARY = (
    "房间规则摘要：模型间仅经白板交流；输出须结构化（PROTO）；"
    "工人不做 JudgeApprove；评议不参与过程闲聊；原话钉选不可静默改写。"
)

# M5-9 / T-M5-10：评议六维（不含文采偏好）
SIX_DIM_REVIEW_HINT = (
    "评议六维（系统提示）：题意对齐 / 正确性 / 完整性 / 可行性 / 风险 / 一致性；"
    "不含文采偏好。"
)


def _pin(question: str) -> str:
    return f"【用户原话·钉选】\n{question.strip()}"


def build_w1_prompt(
    *,
    question: str,
    room_rules: str = ROOM_RULES_SUMMARY,
    history: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, str]]:
    """W1 首答：可选多轮历史 + 本轮用户原话 → 正文。"""
    msgs: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是本房间的工人 Agent。根据对话历史与最新用户原话给出完整正文答复。"
                "不要输出闲聊；不要宣布评议通过。\n" + room_rules
            ),
        }
    ]
    for turn in history or []:
        role_raw = str(turn.get("role") or "user")
        role = "assistant" if role_raw in ("assistant", "agent") else "user"
        text = str(turn.get("text") or turn.get("content") or "").strip()
        if text:
            msgs.append({"role": role, "content": text})
    msgs.append({"role": "user", "content": _pin(question)})
    return sanitize_messages(msgs)


def build_w2_prompt(
    *,
    question: str,
    first_answer: str,
    room_rules: str = ROOM_RULES_SUMMARY,
) -> list[dict[str, str]]:
    """W2 静默检查：原话 + 首答全文 → SilentCheckPass 或实质 PATCH。"""
    return sanitize_messages(
        [
            {
                "role": "system",
                "content": (
                    "你是工人，对刚产出的首答做静默自检。"
                    "若无问题，只输出 JSON："
                    '{"type":"SilentCheckPass","version":<n>,"doc_id":"<id>"}；'
                    "若有实质问题，输出 Patch JSON（须含 target,category,claim,replace）。"
                    "禁止闲聊。\n" + room_rules
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{_pin(question)}\n\n【刚产出的首答全文】\n{first_answer.strip()}"
                ),
            },
        ]
    )


def build_w3_prompt(
    *,
    question: str,
    current_doc: str,
    changed_set: Optional[list[str]] = None,
    room_rules: str = ROOM_RULES_SUMMARY,
) -> list[dict[str, str]]:
    """W3 审阅响应：原话 + 当前稿 + 可选 ChangedSet → Read / Abstain / PATCH。"""
    cs = ""
    if changed_set:
        cs = "\n【ChangedSet】仅关注：" + ", ".join(changed_set)
    return sanitize_messages(
        [
            {
                "role": "system",
                "content": (
                    "你是审阅工人。输出 JSON：Read / Abstain / Patch 之一。"
                    "Patch 必须含 target,category,claim,replace。禁止闲聊与 JudgeApprove。\n"
                    + SIX_DIM_REVIEW_HINT
                    + "\n"
                    + room_rules
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{_pin(question)}\n\n【当前共享稿】\n{current_doc.strip()}{cs}"
                ),
            },
        ]
    )


def build_w4_prompt(
    *,
    question: str,
    current_doc: str,
    changed_set: list[str],
    room_rules: str = ROOM_RULES_SUMMARY,
) -> list[dict[str, str]]:
    """W4 确认轮：强调仅可改 ChangedSet。"""
    cs = ", ".join(changed_set) if changed_set else "(空)"
    return sanitize_messages(
        [
            {
                "role": "system",
                "content": (
                    "确认轮：你只能对 ChangedSet 内区块提出 PATCH 或 Read。"
                    f"ChangedSet=[{cs}]。超出范围的修改无效。"
                    "输出 JSON：Read 或 Patch。禁止闲聊。\n" + room_rules
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{_pin(question)}\n\n【当前共享稿】\n{current_doc.strip()}\n"
                    f"【ChangedSet 约束】{cs}"
                ),
            },
        ]
    )


def build_j1_prompt(
    *,
    question: str,
    current_doc: str,
    pending_patches: list[dict[str, Any]],
    open_rejects: Optional[list[str]] = None,
    room_rules: str = ROOM_RULES_SUMMARY,
) -> list[dict[str, str]]:
    """J1 评议：原话+当前稿+待合入+打回；不含过程闲聊/工人私有思考。"""
    queue_txt = "\n".join(
        f"- {p.get('target')}: claim={p.get('claim')}" for p in pending_patches
    ) or "(无)"
    rejects = "\n".join(open_rejects or []) or "(无)"
    return sanitize_messages(
        [
            {
                "role": "system",
                "content": (
                    "你是评议 Agent。不参与过程闲聊。可选输出 JSON："
                    "Accept / Merge / R1 / R2 / R3 / JudgeApprove。"
                    "上下文不得依赖工人私有思考区。\n"
                    + SIX_DIM_REVIEW_HINT
                    + "\n"
                    + room_rules
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{_pin(question)}\n\n【当前共享稿】\n{current_doc.strip()}\n\n"
                    f"【待合入队列】\n{queue_txt}\n\n【开打回】\n{rejects}"
                ),
            },
        ]
    )


def prompt_contains_private_leak(messages: list[dict[str, str]], secret: str) -> bool:
    if not secret:
        return False
    blob = "\n".join(m.get("content", "") for m in messages)
    return secret in blob
