"""T-M5：净变更计量（spec §6.1.1）。"""

from __future__ import annotations

import re
from typing import Literal

TextKind = Literal["zh", "en", "code"]


_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def detect_kind(text: str) -> TextKind:
    if "```" in text or re.search(r"^\s*(def|class|function|import|#include)\b", text, re.M):
        return "code"
    cjk = len(_CJK.findall(text))
    words = len(_LATIN_WORD.findall(text))
    if cjk >= words:
        return "zh"
    return "en"


def _units(text: str, kind: TextKind) -> list[str]:
    if kind == "zh":
        # 汉字按字；拉丁词按词
        units: list[str] = []
        buf = ""
        for ch in text:
            if _CJK.match(ch):
                if buf.strip():
                    units.extend(_LATIN_WORD.findall(buf))
                    buf = ""
                if not ch.isspace():
                    units.append(ch)
            else:
                buf += ch
        if buf.strip():
            units.extend(_LATIN_WORD.findall(buf))
        return units
    if kind == "en":
        return _LATIN_WORD.findall(text)
    # code: non-empty lines as coarse units + tokens
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tokens = _TOKEN.findall(text)
    # represent as synthetic multiset via list
    return [f"L:{i}:{ln.strip()}" for i, ln in enumerate(lines)] + [
        f"T:{t}" for t in tokens
    ]


def net_change(old: str, new: str, *, kind: TextKind | None = None) -> float:
    """删除单位数 + 新增单位数（P0 近似）。代码取 max(行变更, token/4)。"""
    old = old or ""
    new = new or ""
    k = kind or detect_kind(old + new)
    if k == "code":
        old_lines = [ln for ln in old.splitlines() if ln.strip()]
        new_lines = [ln for ln in new.splitlines() if ln.strip()]
        # crude line set diff
        from collections import Counter

        oc, nc = Counter(old_lines), Counter(new_lines)
        line_del = sum((oc - nc).values())
        line_add = sum((nc - oc).values())
        line_chg = line_del + line_add
        ot, nt = Counter(_TOKEN.findall(old)), Counter(_TOKEN.findall(new))
        tok_chg = sum((ot - nt).values()) + sum((nt - ot).values())
        return float(max(line_chg, tok_chg / 4.0))

    ou, nu = _units(old, k), _units(new, k)
    from collections import Counter

    oc, nc = Counter(ou), Counter(nu)
    return float(sum((oc - nc).values()) + sum((nc - oc).values()))


def similarity(old: str, new: str) -> float:
    """1 - net/(len_units(old)+1)。"""
    k = detect_kind(old + new)
    n = net_change(old, new, kind=k)
    base = max(len(_units(old, k)), 1)
    return max(0.0, 1.0 - n / (base + 1))
