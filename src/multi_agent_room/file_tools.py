"""T-M10-02：工作区文件工具 — 真实文件落盘，禁止软链接。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_room.paths import normalize_workspace_path


def _assert_no_symlink(path: Path, *, label: str = "路径") -> None:
    """拒绝软链接及其祖先链上的软链接（不允许通过 symlink 越界）。"""
    cur = path
    # 检查自身与所有祖先
    for p in [cur, *cur.parents]:
        try:
            if p.exists() and p.is_symlink():
                raise PermissionError(f"禁止软链接：{label} 含 symlink → {p}")
        except OSError:
            continue
        if p == p.anchor or str(p) in ("/", ""):
            break


def resolve_in_workspace(workspace: str | Path, rel: str) -> Path:
    root = normalize_workspace_path(workspace)
    _assert_no_symlink(root, label="工作区根")
    target = (
        normalize_workspace_path(root / rel)
        if not Path(rel).is_absolute()
        else normalize_workspace_path(rel)
    )
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"越界写盘拒绝: {target} 不在 {root}") from exc
    _assert_no_symlink(target, label=str(rel))
    # 若中间组件是 symlink，relative_to 可能仍落在 root 下；再扫一遍
    try:
        resolved = target.resolve(strict=False)
        root_res = root.resolve(strict=False)
        resolved.relative_to(root_res)
        if resolved != target and target.exists() and target.is_symlink():
            raise PermissionError(f"禁止软链接目标: {rel}")
    except ValueError as exc:
        raise PermissionError(f"解析后越界拒绝: {target}") from exc
    return target


def file_list(
    workspace: str | Path, path: str = ".", *, max_entries: int = 200
) -> dict[str, Any]:
    """列出工作区相对目录下的条目（只读；跳过软链接项）。"""
    root = normalize_workspace_path(workspace)
    _assert_no_symlink(root, label="工作区根")
    rel = (path or ".").strip() or "."
    target = resolve_in_workspace(workspace, rel) if rel not in (".", "") else root
    if target.is_symlink():
        raise PermissionError(f"禁止列出软链接目录: {path}")
    if not target.exists():
        raise FileNotFoundError(f"路径不存在: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"不是目录: {path}")
    entries: list[dict[str, Any]] = []
    skipped_links = 0
    for i, child in enumerate(
        sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    ):
        if child.is_symlink():
            skipped_links += 1
            continue
        if len(entries) >= max_entries:
            break
        try:
            rel_p = child.relative_to(root).as_posix()
        except ValueError:
            continue
        entries.append(
            {
                "name": child.name,
                "path": rel_p,
                "kind": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else 0,
            }
        )
    return {
        "path": "." if target == root else target.relative_to(root).as_posix(),
        "entries": entries,
        "skipped_symlinks": skipped_links,
        "truncated": len(entries) >= max_entries,
    }


def file_read(workspace: str | Path, path: str, *, max_bytes: int = 512_000) -> dict[str, Any]:
    p = resolve_in_workspace(workspace, path)
    if p.is_symlink():
        raise PermissionError(f"禁止读取软链接: {path}")
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    data = p.read_bytes()
    truncated = False
    if len(data) > max_bytes:
        data = data[:max_bytes]
        truncated = True
    text = data.decode("utf-8", errors="replace")
    return {
        "path": str(p),
        "rel": path,
        "content": text,
        "truncated": truncated,
        "size": p.stat().st_size,
    }


def file_write(
    workspace: str | Path, path: str, content: str
) -> dict[str, Any]:
    """写入真实文件；若目标已是软链接则拒绝（不跟链、不替换为链接）。"""
    p = resolve_in_workspace(workspace, path)
    if p.exists() and p.is_symlink():
        raise PermissionError(f"禁止写入软链接路径: {path}（请先删除链接）")
    # 父目录不得是 symlink
    if p.parent.exists():
        _assert_no_symlink(p.parent, label=f"父目录({path})")
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.parent.is_symlink():
        raise PermissionError(f"禁止在软链接目录内写文件: {path}")
    before = ""
    if p.exists() and p.is_file() and not p.is_symlink():
        before = p.read_text(encoding="utf-8", errors="replace")
    # 原子写到临时真实文件再 replace（不会创建 symlink）
    tmp = p.with_name(p.name + ".mar_tmp")
    if tmp.exists() or tmp.is_symlink():
        if tmp.is_symlink():
            raise PermissionError(f"临时路径为软链接，拒绝写入: {tmp.name}")
        tmp.unlink()
    tmp.write_text(content, encoding="utf-8", newline="\n")
    tmp.replace(p)
    if p.is_symlink():
        # 极端情况：replace 后变成 link（不应发生）
        raise PermissionError(f"写入后检测到软链接，已拒绝保留: {path}")
    diff_summary = f"bytes={len(content.encode('utf-8'))} prev_len={len(before)}"
    return {
        "path": str(p),
        "rel": path,
        "diff_summary": diff_summary,
        "ok": True,
        "symlink": False,
    }


def file_delete(workspace: str | Path, path: str) -> dict[str, Any]:
    p = resolve_in_workspace(workspace, path)
    if p.is_symlink():
        raise PermissionError(f"禁止操作软链接: {path}")
    if not p.exists():
        raise FileNotFoundError(f"不存在: {path}")
    if p.is_dir():
        raise IsADirectoryError(f"是目录，拒绝删除: {path}")
    p.unlink()
    return {"path": str(p), "rel": path, "ok": True, "deleted": True}


def search_replace(
    workspace: str | Path,
    path: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
) -> dict[str, Any]:
    data = file_read(workspace, path)
    text = data["content"]
    if old_string not in text:
        raise ValueError("old_string 未在文件中找到")
    count = text.count(old_string)
    if not replace_all and count > 1:
        raise ValueError(f"old_string 出现 {count} 次；请设 replace_all=true 或提供唯一上下文")
    if replace_all:
        updated = text.replace(old_string, new_string)
        n = count
    else:
        updated = text.replace(old_string, new_string, 1)
        n = 1
    written = file_write(workspace, path, updated)
    return {
        **written,
        "replacements": n,
        "ok": True,
    }


def glob_search(
    workspace: str | Path,
    pattern: str = "**/*",
    *,
    max_results: int = 200,
) -> dict[str, Any]:
    root = normalize_workspace_path(workspace)
    _assert_no_symlink(root, label="工作区根")
    pat = (pattern or "**/*").strip() or "**/*"
    hits: list[str] = []
    for p in root.glob(pat):
        if p.is_symlink():
            continue
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        hits.append(rel + ("/" if p.is_dir() else ""))
        if len(hits) >= max_results:
            break
    hits.sort()
    return {"pattern": pat, "matches": hits, "truncated": len(hits) >= max_results}
