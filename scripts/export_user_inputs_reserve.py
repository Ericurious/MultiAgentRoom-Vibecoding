# -*- coding: utf-8 -*-
"""Export user inputs from agent transcript into a reserve markdown doc."""
from __future__ import annotations

import json
import re
from pathlib import Path

TRANSCRIPT = Path(
    r"C:\Users\gywan\.cursor\projects\d-CursorProject\agent-transcripts"
    r"\4b65a3b6-9f3d-4e68-89d6-b3c455a11839"
    r"\4b65a3b6-9f3d-4e68-89d6-b3c455a11839.jsonl"
)
OUT = Path(r"D:\CursorProject\docs\user-inputs-reserve.md")

# Messages that may exist only in the live turn / after transcript flush
EXTRA = [
    (
        "不能直接提交；这一版本还没有READme.md以及各类配置。"
        "先将所有在这个对话框中到现在为止我的输入整理成一份文档，作为储备"
    ),
]


def extract_text(content) -> tuple[str, int]:
    texts: list[str] = []
    images = 0
    if isinstance(content, str):
        return content.strip(), 0
    if not isinstance(content, list):
        return "", 0
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "image":
            images += 1
            continue
        if c.get("type") != "text":
            continue
        t = c.get("text") or ""
        m = re.search(r"<user_query>\s*(.*?)\s*</user_query>", t, re.S)
        if m:
            t = m.group(1).strip()
        if "available_subagent_types" in t[:400]:
            continue
        if "Briefly inform the user about the task result" in t:
            continue
        t = re.sub(r"<timestamp>.*?</timestamp>", "", t, flags=re.S).strip()
        t = re.sub(r"<image_files>.*?</image_files>", "", t, flags=re.S).strip()
        t = re.sub(r"^\[Image\]\s*", "", t, flags=re.M).strip()
        if t:
            texts.append(t)
    return "\n\n".join(texts).strip(), images


def main() -> None:
    msgs: list[dict] = []
    for line in TRANSCRIPT.open(encoding="utf-8"):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("role") != "user":
            continue
        msg = obj.get("message") or {}
        body, n_img = extract_text(msg.get("content"))
        if not body and not n_img:
            continue
        if body.startswith("Your team") or body.startswith("Communicate directly"):
            continue
        msgs.append({"text": body, "images": n_img})

    deduped: list[dict] = []
    for m in msgs:
        if deduped and deduped[-1]["text"] == m["text"] and deduped[-1]["images"] == m["images"]:
            continue
        deduped.append(m)

    for e in EXTRA:
        if not any(x["text"] == e for x in deduped):
            deduped.append({"text": e, "images": 0})

    lines: list[str] = [
        "# 用户输入储备文档（对话归档）",
        "",
        "> 用途：在正式补齐 README / 配置并提交 Git 之前，归档本对话中用户侧全部输入，供产品与实现回溯。",
        ">",
        "> 来源：Cursor 对话 `4b65a3b6-9f3d-4e68-89d6-b3c455a11839` 及同线程后续消息。",
        ">",
        "> 说明：仅整理**用户输入**；不含助手回复。附图以「（附图 N 张）」标注。系统/路由噪声已过滤。连续重复输入已去重。",
        "",
        f"- 条目数：{len(deduped)}",
        "- 整理日期：2026-07-25",
        "- 项目路径：`D:\\CursorProject`",
        "",
        "---",
        "",
    ]

    for i, m in enumerate(deduped, 1):
        lines.append(f"## U-{i:03d}")
        lines.append("")
        if m["images"]:
            lines.append(f"（附图 {m['images']} 张）")
            lines.append("")
        lines.append(m["text"] or "（仅附图，无文字）")
        lines.append("")
        lines.append("---")
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"count={len(deduped)} bytes={OUT.stat().st_size}")


if __name__ == "__main__":
    main()
