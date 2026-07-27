"""主壳层：左侧 Activity 栏 + 主视图 + 底栏（Cursor / VS Code 式）。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from multi_agent_room import APP_DISPLAY_NAME, __version__
from multi_agent_room.config import AppConfig, save_config
from multi_agent_room.logging_setup import (
    latest_log_file,
    log_event,
    log_fatal,
    log_judge_approve,
    log_merge_accept,
    log_probe_result,
    log_protocol_reject,
    log_reject_verdict,
)
from multi_agent_room.paths import (
    ensure_workspace_dir,
    get_appdata_root,
    get_config_dir,
    get_data_dir,
    get_logs_dir,
)
from multi_agent_room.theme import Theme, apply_theme, card, page_header

# key, 短标签, 提示
NAV_ITEMS = (
    ("room", "聊天", "聊天室"),
    ("models", "模型", "模型配置"),
    ("agents", "成员", "Agent 成员"),
    ("workspace", "工作区", "工作区与宿主"),
)


class AppShell(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        config: AppConfig,
        on_status: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.config = config
        self.on_status = on_status
        self._nav_btns: dict[str, ttk.Button] = {}
        self._nav_indicators: dict[str, tk.Frame] = {}
        self._views: dict[str, tk.Widget] = {}
        self._current = ""
        self.status_var = tk.StringVar(value="就绪")

        apply_theme(master.winfo_toplevel())
        self._build()

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # —— 左侧 Activity Rail ——
        rail = tk.Frame(self, bg=Theme.BG_RAIL, width=64)
        rail.grid(row=0, column=0, sticky="ns")
        rail.grid_propagate(False)

        brand = tk.Frame(rail, bg=Theme.BG_RAIL)
        brand.pack(fill=tk.X, pady=(14, 18))
        tk.Label(
            brand,
            text="MR",
            bg=Theme.BG_RAIL,
            fg=Theme.ACCENT,
            font=Theme.FONT_BRAND,
        ).pack()
        tk.Label(
            brand,
            text="Room",
            bg=Theme.BG_RAIL,
            fg=Theme.TEXT_DIM,
            font=Theme.FONT_TINY,
        ).pack()

        for key, short, _full in NAV_ITEMS:
            row = tk.Frame(rail, bg=Theme.BG_RAIL)
            row.pack(fill=tk.X, pady=2)

            indicator = tk.Frame(row, bg=Theme.BG_RAIL, width=3)
            indicator.pack(side=tk.LEFT, fill=tk.Y)
            self._nav_indicators[key] = indicator

            btn = ttk.Button(
                row,
                text=short,
                style="Nav.TButton",
                command=lambda k=key: self.show(k),
            )
            btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
            self._nav_btns[key] = btn

        # —— 主区 + 底栏 ——
        main = tk.Frame(self, bg=Theme.BG)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        top = tk.Frame(main, bg=Theme.BG_ELEVATED, height=44)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)
        tk.Frame(top, bg=Theme.BORDER, height=1).pack(side=tk.BOTTOM, fill=tk.X)

        self.title_var = tk.StringVar(value=APP_DISPLAY_NAME)
        tk.Label(
            top,
            textvariable=self.title_var,
            bg=Theme.BG_ELEVATED,
            fg=Theme.TEXT,
            font=Theme.FONT_UI_BOLD,
            anchor="w",
        ).pack(side=tk.LEFT, padx=16, pady=10)
        tk.Label(
            top,
            text=f"v{__version__}",
            bg=Theme.BG_ELEVATED,
            fg=Theme.TEXT_DIM,
            font=Theme.FONT_TINY,
        ).pack(side=tk.RIGHT, padx=16)

        self.body = tk.Frame(main, bg=Theme.BG)
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)

        self.room_host = tk.Frame(self.body, bg=Theme.BG)
        self.models_host = tk.Frame(self.body, bg=Theme.BG)
        self.agents_host = tk.Frame(self.body, bg=Theme.BG)
        self.workspace_host = tk.Frame(self.body, bg=Theme.BG)
        for host in (
            self.room_host,
            self.models_host,
            self.agents_host,
            self.workspace_host,
        ):
            host.grid(row=0, column=0, sticky="nsew")

        self._views = {
            "room": self.room_host,
            "models": self.models_host,
            "agents": self.agents_host,
            "workspace": self.workspace_host,
        }
        self._build_workspace_view()

        # Status bar (Cursor / VS Code bottom)
        status = tk.Frame(main, bg=Theme.BG_ELEVATED, height=26)
        status.grid(row=2, column=0, sticky="ew")
        status.grid_propagate(False)
        tk.Frame(status, bg=Theme.BORDER, height=1).pack(side=tk.TOP, fill=tk.X)
        tk.Label(
            status,
            textvariable=self.status_var,
            bg=Theme.BG_ELEVATED,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_TINY,
            anchor="w",
        ).pack(side=tk.LEFT, padx=12)
        tk.Label(
            status,
            text="本地 · MultiAgentRoom",
            bg=Theme.BG_ELEVATED,
            fg=Theme.TEXT_DIM,
            font=Theme.FONT_TINY,
        ).pack(side=tk.RIGHT, padx=12)

    def _build_workspace_view(self) -> None:
        host = self.workspace_host
        wrap = tk.Frame(host, bg=Theme.BG)
        wrap.pack(fill=tk.BOTH, expand=True, padx=24, pady=20)
        page_header(
            wrap,
            "工作区与宿主",
            "本机目录约定与默认工作区路径（房间可另行绑定）。",
        )

        panel = card(wrap)
        panel.pack(fill=tk.BOTH, expand=True)

        for label, path in [
            ("AppData 根", get_appdata_root()),
            ("config", get_config_dir()),
            ("data", get_data_dir()),
            ("logs", get_logs_dir()),
        ]:
            row = tk.Frame(panel, bg=Theme.BG_CARD)
            row.pack(fill=tk.X, padx=16, pady=6)
            tk.Label(
                row,
                text=label,
                width=12,
                anchor="w",
                bg=Theme.BG_CARD,
                fg=Theme.TEXT_MUTED,
                font=Theme.FONT_SMALL,
            ).pack(side=tk.LEFT)
            tk.Label(
                row,
                text=str(path),
                anchor="w",
                bg=Theme.BG_CARD,
                fg=Theme.TEXT,
                font=Theme.FONT_MONO,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Frame(panel, bg=Theme.BORDER, height=1).pack(fill=tk.X, padx=16, pady=10)

        ws = tk.Frame(panel, bg=Theme.BG_CARD)
        ws.pack(fill=tk.X, padx=16, pady=(0, 8))
        self.workspace_var = tk.StringVar(value=self.config.workspace_path or "")
        ttk.Entry(ws, textvariable=self.workspace_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8)
        )
        ttk.Button(ws, text="选择目录…", style="Ghost.TButton", command=self._choose_workspace).pack(
            side=tk.LEFT
        )
        ttk.Button(
            ws, text="保存", style="Accent.TButton", command=self._save_workspace
        ).pack(side=tk.LEFT, padx=(8, 0))

        btns = tk.Frame(panel, bg=Theme.BG_CARD)
        btns.pack(fill=tk.X, padx=16, pady=(4, 16))
        ttk.Button(btns, text="打开日志目录", style="Toolbar.TButton", command=self._open_logs).pack(
            side=tk.LEFT
        )
        ttk.Button(
            btns,
            text="写入探测事件日志",
            style="Toolbar.TButton",
            command=self._emit_sample_events,
        ).pack(side=tk.LEFT, padx=8)
        self.ws_status = tk.StringVar(value="就绪")
        tk.Label(
            panel,
            textvariable=self.ws_status,
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_SMALL,
        ).pack(anchor="w", padx=16, pady=(0, 14))

    def show(self, key: str) -> None:
        if key not in self._views:
            return
        self._current = key
        for k, host in self._views.items():
            if k == key:
                host.tkraise()
            btn = self._nav_btns.get(k)
            ind = self._nav_indicators.get(k)
            active = k == key
            if btn:
                btn.configure(style="NavActive.TButton" if active else "Nav.TButton")
            if ind:
                ind.configure(bg=Theme.ACCENT if active else Theme.BG_RAIL)
        label = next((n for kid, _s, n in NAV_ITEMS if kid == key), key)
        self.title_var.set(label)
        self.status_var.set(f"视图 · {label}")
        if self.on_status:
            self.on_status(f"视图：{label}")

    def _choose_workspace(self) -> None:
        initial = self.workspace_var.get() or str(Path.home())
        chosen = filedialog.askdirectory(initialdir=initial, mustexist=False)
        if chosen:
            self.workspace_var.set(chosen)

    def _save_workspace(self) -> None:
        raw = self.workspace_var.get().strip()
        if not raw:
            messagebox.showwarning(APP_DISPLAY_NAME, "请先选择工作区路径")
            return
        try:
            path = ensure_workspace_dir(raw)
            self.config.set_workspace(path)
            self.config.bind_room_workspace("demo-room", path)
            save_config(self.config)
            self.workspace_var.set(str(path))
            log_event("workspace_bound", f"path={path}", room_id="demo-room")
            self.ws_status.set(f"已保存：{path}")
            self.status_var.set(f"工作区已保存 · {path}")
            messagebox.showinfo(APP_DISPLAY_NAME, f"工作区已保存：\n{path}")
        except OSError as exc:
            log_fatal(exc)
            messagebox.showerror(APP_DISPLAY_NAME, f"保存失败：{exc}")

    def _open_logs(self) -> None:
        logs = get_logs_dir()
        try:
            import os

            os.startfile(logs)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_DISPLAY_NAME, str(exc))

    def _emit_sample_events(self) -> None:
        log_probe_result("cfg-demo", "ok", "smoke")
        log_protocol_reject("trivial", patch_id="p-1")
        log_merge_accept("p-2", "B12")
        log_reject_verdict("R1", "target=B03")
        log_judge_approve("demo-room")
        log_event("probe_result", "api_key=sk-should-redactBearer token test")
        lf = latest_log_file()
        self.ws_status.set(f"已写入样例事件 → {lf}")
        self.status_var.set("样例日志已写入")
        messagebox.showinfo(APP_DISPLAY_NAME, f"样例日志已写入：\n{lf}")
