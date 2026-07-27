"""Cursor 风格壳层主题（仅 UI；业务逻辑不依赖具体色值）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


def _pick_font(candidates: list[str], size: int, weight: str = "normal") -> tuple:
    """从候选字体里挑系统已安装的。"""
    available = {f.lower() for f in tkfont.families()}
    for name in candidates:
        if name.lower() in available:
            if weight == "bold":
                return (name, size, "bold")
            return (name, size)
    # 回退
    if weight == "bold":
        return ("Segoe UI", size, "bold")
    return ("Segoe UI", size)


class Theme:
    """近似 Cursor / VS Code 深色编辑器：安静底色、细边框、蓝强调、紧凑字号。"""

    # Surfaces
    BG = "#141414"
    BG_ELEVATED = "#1a1a1a"
    BG_SIDE = "#181818"
    BG_CARD = "#1e1e1e"
    BG_INPUT = "#0f0f0f"
    BG_RAIL = "#0c0c0c"
    BG_HOVER = "#262626"
    BG_ACTIVE = "#2a2a2a"
    BORDER = "#2e2e2e"
    BORDER_FOCUS = "#3d3d3d"

    # Text
    TEXT = "#e8e8e8"
    TEXT_MUTED = "#8a8a8a"
    TEXT_DIM = "#5c5c5c"

    # Accent (Cursor-ish cool blue, not purple)
    ACCENT = "#81a1c1"
    ACCENT_HOVER = "#9bb4cf"
    ACCENT_SOFT = "#243040"
    ACCENT_FG = "#d6e4f0"

    SUCCESS = "#7aa86a"
    WARNING = "#c9a227"
    DANGER = "#c76b6b"
    CHIP = "#252525"

    USER_BUBBLE = "#243040"
    AGENT_BUBBLE = "#222222"
    PIN_BG = "#1a1f24"

    # Filled after apply_theme()
    FONT_UI: tuple = ("Segoe UI", 11)
    FONT_UI_BOLD: tuple = ("Segoe UI", 11, "bold")
    FONT_TITLE: tuple = ("Segoe UI", 18)
    FONT_H2: tuple = ("Segoe UI", 13, "bold")
    FONT_SMALL: tuple = ("Segoe UI", 10)
    FONT_TINY: tuple = ("Segoe UI", 9)
    FONT_MONO: tuple = ("Consolas", 10)
    FONT_BRAND: tuple = ("Segoe UI", 12, "bold")


def _init_fonts() -> None:
    Theme.FONT_UI = _pick_font(
        ["Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI"],
        11,
    )
    Theme.FONT_UI_BOLD = _pick_font(
        ["Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI"],
        11,
        "bold",
    )
    Theme.FONT_TITLE = _pick_font(
        ["Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", "Microsoft YaHei UI"],
        17,
        "bold",
    )
    Theme.FONT_H2 = _pick_font(
        ["Segoe UI Variable Text", "Segoe UI Semibold", "Segoe UI"],
        13,
        "bold",
    )
    Theme.FONT_SMALL = _pick_font(
        ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"],
        10,
    )
    Theme.FONT_TINY = _pick_font(
        ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"],
        9,
    )
    Theme.FONT_MONO = _pick_font(
        ["Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas", "Courier New"],
        10,
    )
    Theme.FONT_BRAND = _pick_font(
        ["Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI"],
        12,
        "bold",
    )


def apply_theme(root: tk.Misc) -> ttk.Style:
    _init_fonts()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(
        ".",
        background=Theme.BG,
        foreground=Theme.TEXT,
        font=Theme.FONT_UI,
        borderwidth=0,
        focuscolor=Theme.BORDER_FOCUS,
    )
    style.configure("TFrame", background=Theme.BG)
    style.configure("Card.TFrame", background=Theme.BG_CARD)
    style.configure("Side.TFrame", background=Theme.BG_SIDE)
    style.configure("Rail.TFrame", background=Theme.BG_RAIL)
    style.configure("Elevated.TFrame", background=Theme.BG_ELEVATED)

    style.configure(
        "TLabel",
        background=Theme.BG,
        foreground=Theme.TEXT,
        font=Theme.FONT_UI,
    )
    style.configure("Side.TLabel", background=Theme.BG_SIDE, foreground=Theme.TEXT)
    style.configure("Card.TLabel", background=Theme.BG_CARD, foreground=Theme.TEXT)
    style.configure(
        "Muted.TLabel",
        background=Theme.BG,
        foreground=Theme.TEXT_MUTED,
        font=Theme.FONT_SMALL,
    )
    style.configure(
        "CardMuted.TLabel",
        background=Theme.BG_CARD,
        foreground=Theme.TEXT_MUTED,
        font=Theme.FONT_SMALL,
    )
    style.configure(
        "Title.TLabel",
        background=Theme.BG,
        foreground=Theme.TEXT,
        font=Theme.FONT_TITLE,
    )
    style.configure(
        "H2.TLabel",
        background=Theme.BG_CARD,
        foreground=Theme.TEXT,
        font=Theme.FONT_H2,
    )
    style.configure(
        "Brand.TLabel",
        background=Theme.BG_RAIL,
        foreground=Theme.TEXT,
        font=Theme.FONT_BRAND,
    )
    style.configure(
        "Status.TLabel",
        background=Theme.BG_ELEVATED,
        foreground=Theme.TEXT_MUTED,
        font=Theme.FONT_TINY,
    )
    style.configure(
        "Success.TLabel",
        background=Theme.BG_CARD,
        foreground=Theme.SUCCESS,
        font=Theme.FONT_UI_BOLD,
    )
    style.configure(
        "Danger.TLabel",
        background=Theme.BG_CARD,
        foreground=Theme.DANGER,
        font=Theme.FONT_UI_BOLD,
    )

    # Buttons — quiet, Cursor-like
    style.configure(
        "TButton",
        background=Theme.CHIP,
        foreground=Theme.TEXT,
        borderwidth=0,
        focuscolor="",
        padding=(12, 7),
        font=Theme.FONT_UI,
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("active", Theme.BG_HOVER), ("pressed", Theme.BG_ACTIVE)],
        foreground=[("disabled", Theme.TEXT_DIM)],
    )
    style.configure(
        "Accent.TButton",
        background=Theme.ACCENT,
        foreground="#0c0c0c",
        padding=(14, 8),
        font=Theme.FONT_UI_BOLD,
    )
    style.map(
        "Accent.TButton",
        background=[("active", Theme.ACCENT_HOVER), ("pressed", Theme.ACCENT)],
        foreground=[("disabled", Theme.TEXT_DIM)],
    )
    style.configure(
        "Ghost.TButton",
        background=Theme.BG_CARD,
        foreground=Theme.TEXT_MUTED,
        padding=(10, 6),
    )
    style.map(
        "Ghost.TButton",
        background=[("active", Theme.BG_HOVER)],
        foreground=[("active", Theme.TEXT)],
    )
    style.configure(
        "Nav.TButton",
        background=Theme.BG_RAIL,
        foreground=Theme.TEXT_MUTED,
        padding=(10, 12),
        font=Theme.FONT_SMALL,
    )
    style.map(
        "Nav.TButton",
        background=[("active", Theme.BG_HOVER)],
        foreground=[("active", Theme.TEXT)],
    )
    style.configure(
        "NavActive.TButton",
        background=Theme.BG_ACTIVE,
        foreground=Theme.ACCENT_FG,
        padding=(10, 12),
        font=Theme.FONT_UI_BOLD,
    )
    style.map(
        "NavActive.TButton",
        background=[("active", Theme.BG_ACTIVE)],
        foreground=[("active", Theme.TEXT)],
    )
    style.configure(
        "Toolbar.TButton",
        background=Theme.BG_CARD,
        foreground=Theme.TEXT_MUTED,
        padding=(10, 5),
        font=Theme.FONT_SMALL,
    )
    style.map(
        "Toolbar.TButton",
        background=[("active", Theme.BG_HOVER)],
        foreground=[("active", Theme.TEXT)],
    )

    style.configure(
        "TEntry",
        fieldbackground=Theme.BG_INPUT,
        foreground=Theme.TEXT,
        insertcolor=Theme.TEXT,
        bordercolor=Theme.BORDER,
        lightcolor=Theme.BORDER,
        darkcolor=Theme.BORDER,
        padding=8,
        font=Theme.FONT_UI,
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", Theme.ACCENT)],
        lightcolor=[("focus", Theme.ACCENT)],
        darkcolor=[("focus", Theme.ACCENT)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=Theme.BG_INPUT,
        foreground=Theme.TEXT,
        background=Theme.BG_INPUT,
        arrowcolor=Theme.TEXT_MUTED,
        bordercolor=Theme.BORDER,
        lightcolor=Theme.BORDER,
        darkcolor=Theme.BORDER,
        padding=6,
        font=Theme.FONT_UI,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", Theme.BG_INPUT)],
        foreground=[("readonly", Theme.TEXT)],
        bordercolor=[("focus", Theme.ACCENT)],
    )

    style.configure(
        "TLabelframe",
        background=Theme.BG_CARD,
        foreground=Theme.TEXT_MUTED,
        bordercolor=Theme.BORDER,
        relief="solid",
        borderwidth=1,
    )
    style.configure(
        "TLabelframe.Label",
        background=Theme.BG_CARD,
        foreground=Theme.TEXT_MUTED,
        font=Theme.FONT_SMALL,
    )
    style.configure(
        "Card.TLabelframe",
        background=Theme.BG_CARD,
        bordercolor=Theme.BORDER,
    )
    style.configure(
        "Card.TLabelframe.Label",
        background=Theme.BG_CARD,
        foreground=Theme.TEXT_MUTED,
        font=Theme.FONT_SMALL,
    )

    style.configure(
        "Treeview",
        background=Theme.BG_CARD,
        fieldbackground=Theme.BG_CARD,
        foreground=Theme.TEXT,
        bordercolor=Theme.BORDER,
        rowheight=28,
        font=Theme.FONT_SMALL,
    )
    style.configure(
        "Treeview.Heading",
        background=Theme.BG_SIDE,
        foreground=Theme.TEXT_MUTED,
        font=Theme.FONT_SMALL,
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Treeview",
        background=[("selected", Theme.ACCENT_SOFT)],
        foreground=[("selected", Theme.ACCENT_FG)],
    )
    style.map(
        "Treeview.Heading",
        background=[("active", Theme.BG_HOVER)],
    )

    style.configure(
        "Horizontal.TScrollbar",
        background=Theme.BG_SIDE,
        troughcolor=Theme.BG,
        bordercolor=Theme.BORDER,
        arrowcolor=Theme.TEXT_DIM,
    )
    style.configure(
        "Vertical.TScrollbar",
        background=Theme.BG_SIDE,
        troughcolor=Theme.BG,
        bordercolor=Theme.BORDER,
        arrowcolor=Theme.TEXT_DIM,
    )
    style.configure(
        "TCheckbutton",
        background=Theme.BG_CARD,
        foreground=Theme.TEXT_MUTED,
        font=Theme.FONT_SMALL,
        focuscolor="",
    )
    style.map(
        "TCheckbutton",
        background=[("active", Theme.BG_CARD)],
        foreground=[("active", Theme.TEXT)],
    )
    style.configure("TNotebook", background=Theme.BG, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=Theme.BG_SIDE,
        foreground=Theme.TEXT_MUTED,
        padding=(14, 8),
        font=Theme.FONT_UI,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", Theme.BG_CARD)],
        foreground=[("selected", Theme.TEXT)],
    )
    style.configure(
        "TSeparator",
        background=Theme.BORDER,
    )

    if isinstance(root, tk.Tk):
        root.configure(bg=Theme.BG)
        try:
            root.option_add("*Text.background", Theme.BG_INPUT)
            root.option_add("*Text.foreground", Theme.TEXT)
            root.option_add("*Text.insertBackground", Theme.TEXT)
            root.option_add("*Text.selectBackground", Theme.ACCENT_SOFT)
            root.option_add("*Text.selectForeground", Theme.ACCENT_FG)
            root.option_add("*Text.font", Theme.FONT_MONO)
            root.option_add("*Listbox.background", Theme.BG_CARD)
            root.option_add("*Listbox.foreground", Theme.TEXT)
            root.option_add("*Listbox.selectBackground", Theme.ACCENT_SOFT)
            root.option_add("*Listbox.selectForeground", Theme.ACCENT_FG)
            root.option_add("*Listbox.font", Theme.FONT_UI)
        except tk.TclError:
            pass
    return style


def card(parent: tk.Misc, **pack) -> tk.Frame:
    f = tk.Frame(
        parent,
        bg=Theme.BG_CARD,
        highlightbackground=Theme.BORDER,
        highlightthickness=1,
        bd=0,
    )
    if pack:
        f.pack(**pack)
    return f


def divider(parent: tk.Misc, *, bg: str = Theme.BG) -> tk.Frame:
    line = tk.Frame(parent, bg=Theme.BORDER, height=1)
    line.pack(fill=tk.X)
    return line


def section_title(parent: tk.Misc, text: str, *, bg: str = Theme.BG_CARD) -> tk.Label:
    return tk.Label(
        parent,
        text=text.upper(),
        bg=bg,
        fg=Theme.TEXT_MUTED,
        font=Theme.FONT_TINY,
        anchor="w",
    )


def page_header(parent: tk.Misc, title: str, subtitle: str = "") -> tk.Frame:
    wrap = tk.Frame(parent, bg=Theme.BG)
    wrap.pack(fill=tk.X, pady=(0, 12))
    tk.Label(
        wrap,
        text=title,
        bg=Theme.BG,
        fg=Theme.TEXT,
        font=Theme.FONT_TITLE,
        anchor="w",
    ).pack(anchor="w")
    if subtitle:
        tk.Label(
            wrap,
            text=subtitle,
            bg=Theme.BG,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_SMALL,
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))
    return wrap


def style_text_widget(widget: tk.Text, *, bg: str | None = None) -> None:
    widget.configure(
        bg=bg or Theme.BG_INPUT,
        fg=Theme.TEXT,
        insertbackground=Theme.TEXT,
        selectbackground=Theme.ACCENT_SOFT,
        selectforeground=Theme.ACCENT_FG,
        font=Theme.FONT_MONO,
        relief=tk.FLAT,
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=Theme.BORDER,
        highlightcolor=Theme.ACCENT,
        padx=10,
        pady=8,
    )


def style_listbox(widget: tk.Listbox) -> None:
    widget.configure(
        bg=Theme.BG_CARD,
        fg=Theme.TEXT,
        selectbackground=Theme.ACCENT_SOFT,
        selectforeground=Theme.ACCENT_FG,
        activestyle="none",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=Theme.BORDER,
        highlightcolor=Theme.ACCENT,
        font=Theme.FONT_UI,
        relief=tk.FLAT,
    )
