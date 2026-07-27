"""T-M4-02：共享稿切块（Markdown 优先 + 纯文本空行/窗口）。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    block_type: str = "para"  # heading|para|code|list|text


_FENCE = re.compile(r"^```")
_HEADING = re.compile(r"^#{1,6}\s+\S")
_LIST = re.compile(r"^(\s*[-*+]|\s*\d+\.)\s+")


def looks_like_markdown(text: str) -> bool:
    if "```" in text:
        return True
    lines = text.splitlines()
    hits = sum(1 for ln in lines if _HEADING.match(ln) or _LIST.match(ln))
    return hits >= 1


def chunk_text(
    text: str,
    *,
    max_para_chars: int = 800,
    max_para_lines: int = 40,
    window_chars: int = 500,
) -> list[Chunk]:
    """按 spec M4 规则切块。"""
    raw = (text or "").strip()
    if not raw:
        return [Chunk("(空)", "text")]

    if looks_like_markdown(raw):
        return _chunk_markdown(raw, max_para_chars, max_para_lines, window_chars)
    return _chunk_plain(raw, max_para_chars, max_para_lines, window_chars)


def _chunk_markdown(
    text: str,
    max_para_chars: int,
    max_para_lines: int,
    window_chars: int,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    lines = text.splitlines()
    buf: list[str] = []
    in_fence = False
    fence_buf: list[str] = []

    def flush_para() -> None:
        nonlocal buf
        if not buf:
            return
        para = "\n".join(buf).strip()
        buf = []
        if not para:
            return
        chunks.extend(
            _split_long(para, "para", max_para_chars, max_para_lines, window_chars)
        )

    for ln in lines:
        if _FENCE.match(ln.strip()):
            if not in_fence:
                flush_para()
                in_fence = True
                fence_buf = [ln]
            else:
                fence_buf.append(ln)
                chunks.append(Chunk("\n".join(fence_buf), "code"))
                fence_buf = []
                in_fence = False
            continue
        if in_fence:
            fence_buf.append(ln)
            continue
        if _HEADING.match(ln):
            flush_para()
            chunks.append(Chunk(ln.strip(), "heading"))
            continue
        if _LIST.match(ln):
            # 列表项：与后续连续列表行合并为一块
            flush_para()
            list_lines = [ln]
            # peek handled by continuing in loop awkwardly — accumulate while next are list
            # simpler: treat each list line as own chunk for stability
            chunks.append(Chunk(ln.rstrip(), "list"))
            continue
        if not ln.strip():
            flush_para()
            continue
        buf.append(ln)
    if in_fence and fence_buf:
        chunks.append(Chunk("\n".join(fence_buf), "code"))
    flush_para()
    return chunks or [Chunk(text, "text")]


def _chunk_plain(
    text: str,
    max_para_chars: int,
    max_para_lines: int,
    window_chars: int,
) -> list[Chunk]:
    # 空行分段
    parts = re.split(r"\n\s*\n+", text)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return _window_split(text, window_chars)
    out: list[Chunk] = []
    for p in parts:
        out.extend(
            _split_long(p, "text", max_para_chars, max_para_lines, window_chars)
        )
    return out


def _split_long(
    para: str,
    typ: str,
    max_chars: int,
    max_lines: int,
    window_chars: int,
) -> list[Chunk]:
    lines = para.splitlines()
    if len(para) <= max_chars and len(lines) <= max_lines:
        return [Chunk(para, typ)]
    # 按句号/换行硬切
    pieces = re.split(r"(?<=[。！？.!?])\s+|\n+", para)
    pieces = [x.strip() for x in pieces if x.strip()]
    if len(pieces) <= 1:
        return _window_split(para, window_chars, typ)
    out: list[Chunk] = []
    buf = ""
    for piece in pieces:
        cand = (buf + " " + piece).strip() if buf else piece
        if len(cand) > max_chars and buf:
            out.append(Chunk(buf, typ))
            buf = piece
        else:
            buf = cand
    if buf:
        out.append(Chunk(buf, typ))
    # 仍过长则窗口
    final: list[Chunk] = []
    for c in out:
        if len(c.text) > max_chars:
            final.extend(_window_split(c.text, window_chars, typ))
        else:
            final.append(c)
    return final


def _window_split(
    text: str, window_chars: int, typ: str = "text"
) -> list[Chunk]:
    if len(text) <= window_chars:
        return [Chunk(text, typ)]
    out: list[Chunk] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + window_chars, n)
        if end < n:
            # 边界向近空白回退
            back = text.rfind(" ", i + window_chars // 2, end)
            if back > i:
                end = back
        chunk = text[i:end].strip()
        if chunk:
            out.append(Chunk(chunk, typ))
        i = end if end > i else i + window_chars
    return out or [Chunk(text, typ)]
