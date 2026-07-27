"""模型纳管 UI（逻辑不变；Cursor 风格壳层）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable, Optional

from multi_agent_room.model_service import ModelService
from multi_agent_room.theme import Theme, card, section_title, style_text_widget

STATUS_COLORS = {
    "ready": Theme.SUCCESS,
    "failed": Theme.DANGER,
    "probing": Theme.WARNING,
    "unknown": Theme.TEXT_MUTED,
}


class ModelsPanel(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        service: Optional[ModelService] = None,
        on_change: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.service = service or ModelService()
        self.on_change = on_change
        self._discovered: list[str] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        # 添加卡片
        form_card = card(self)
        form_card.pack(fill=tk.X, pady=(0, 10))
        section_title(form_card, "添加端点", bg=Theme.BG_CARD).pack(
            anchor=tk.W, padx=14, pady=(12, 4)
        )
        ttk.Label(
            form_card,
            text="OpenAI 兼容口即可。填地址与 Key → 拉取模型或一键添加并探活。",
            style="CardMuted.TLabel",
            wraplength=720,
        ).pack(anchor=tk.W, padx=14, pady=(0, 8))

        form = ttk.Frame(form_card, style="Card.TFrame")
        form.pack(fill=tk.X, padx=14, pady=(0, 4))

        self.vars = {
            "base_url": tk.StringVar(value="https://api.deepseek.com"),
            "api_key": tk.StringVar(),
            "display_name": tk.StringVar(),
            "model_id": tk.StringVar(),
            "timeout_ms": tk.StringVar(value="15000"),
        }

        ttk.Label(form, text="API 地址", style="CardMuted.TLabel", width=10).grid(
            row=0, column=0, sticky=tk.W, pady=4
        )
        ttk.Entry(form, textvariable=self.vars["base_url"]).grid(
            row=0, column=1, sticky=tk.EW, pady=4, padx=(0, 8)
        )
        ttk.Label(form, text="API Key", style="CardMuted.TLabel", width=10).grid(
            row=1, column=0, sticky=tk.W, pady=4
        )
        ttk.Entry(form, textvariable=self.vars["api_key"], show="*").grid(
            row=1, column=1, sticky=tk.EW, pady=4, padx=(0, 8)
        )
        form.columnconfigure(1, weight=1)

        adv = ttk.Frame(form_card, style="Card.TFrame")
        adv.pack(fill=tk.X, padx=14, pady=(4, 8))
        ttk.Label(adv, text="显示名", style="CardMuted.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(adv, textvariable=self.vars["display_name"], width=22).grid(
            row=0, column=1, sticky=tk.W, padx=(6, 12)
        )
        ttk.Label(adv, text="模型", style="CardMuted.TLabel").grid(row=0, column=2, sticky=tk.W)
        self.model_combo = ttk.Combobox(
            adv, textvariable=self.vars["model_id"], width=28, values=()
        )
        self.model_combo.grid(row=0, column=3, sticky=tk.EW)
        ttk.Label(adv, text="超时", style="CardMuted.TLabel").grid(
            row=0, column=4, sticky=tk.W, padx=(12, 0)
        )
        ttk.Entry(adv, textvariable=self.vars["timeout_ms"], width=8).grid(
            row=0, column=5, sticky=tk.W, padx=6
        )
        adv.columnconfigure(3, weight=1)

        btns = ttk.Frame(form_card, style="Card.TFrame")
        btns.pack(fill=tk.X, padx=14, pady=(0, 14))
        ttk.Button(btns, text="拉取可用模型", style="Ghost.TButton", command=self._discover).pack(
            side=tk.LEFT
        )
        ttk.Button(
            btns, text="一键添加并探活", style="Accent.TButton", command=self._add
        ).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="重新探活", style="Toolbar.TButton", command=self._probe_selected).pack(
            side=tk.LEFT
        )
        ttk.Button(btns, text="启用/禁用", style="Toolbar.TButton", command=self._toggle_enabled).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(btns, text="删除", style="Toolbar.TButton", command=self._delete).pack(
            side=tk.LEFT
        )
        ttk.Button(btns, text="刷新", style="Toolbar.TButton", command=self.refresh).pack(
            side=tk.LEFT, padx=6
        )

        # 健康
        health = card(self)
        health.pack(fill=tk.X, pady=(0, 10))
        section_title(health, "健康", bg=Theme.BG_CARD).pack(
            anchor=tk.W, padx=14, pady=(10, 2)
        )
        self.health_var = tk.StringVar(value="Live — 尚未探活")
        self.health_label = tk.Label(
            health,
            textvariable=self.health_var,
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_UI_BOLD,
            anchor="w",
        )
        self.health_label.pack(fill=tk.X, padx=14)
        self.bindable_var = tk.StringVar(value="可绑定大脑：无")
        tk.Label(
            health,
            textvariable=self.bindable_var,
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_SMALL,
            anchor="w",
        ).pack(fill=tk.X, padx=14, pady=(2, 12))

        # 表格
        table = card(self)
        table.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        section_title(table, "已配置模型", bg=Theme.BG_CARD).pack(
            anchor=tk.W, padx=14, pady=(10, 4)
        )
        cols = (
            "config_id",
            "display_name",
            "base_url",
            "model_id",
            "status",
            "enabled",
            "error",
        )
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=5)
        headings = {
            "config_id": "ID",
            "display_name": "名称",
            "base_url": "API 地址",
            "model_id": "模型",
            "status": "状态",
            "enabled": "启用",
            "error": "错误摘要",
        }
        widths = {
            "config_id": 110,
            "display_name": 90,
            "base_url": 200,
            "model_id": 120,
            "status": 70,
            "enabled": 48,
            "error": 160,
        }
        for c in cols:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], stretch=c in ("base_url", "error"))
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self.tree.bind("<Double-1>", lambda _e: self._show_error_detail())

        # 报错 + Chat 并排感
        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.BOTH, expand=True)
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)
        bottom.rowconfigure(0, weight=1)

        err_box = card(bottom)
        err_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        section_title(err_box, "完整报错 · 可反复查看", bg=Theme.BG_CARD).pack(
            anchor=tk.W, padx=12, pady=(10, 4)
        )
        self.error_out = scrolledtext.ScrolledText(err_box, height=7, wrap=tk.WORD)
        self.error_out.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        style_text_widget(self.error_out)
        self._set_error_text("选中一条模型后，完整错误会显示在这里。\n")

        chat = card(bottom)
        chat.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        section_title(chat, "Chat 测试", bg=Theme.BG_CARD).pack(
            anchor=tk.W, padx=12, pady=(10, 4)
        )
        self.chat_prompt = tk.StringVar(value="请用一句话回复：pong")
        row = ttk.Frame(chat, style="Card.TFrame")
        row.pack(fill=tk.X, padx=10)
        ttk.Entry(row, textvariable=self.chat_prompt).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8)
        )
        ttk.Button(
            row, text="发送", style="Accent.TButton", command=self._chat_test
        ).pack(side=tk.LEFT)
        self.chat_out = scrolledtext.ScrolledText(chat, height=6, wrap=tk.WORD)
        self.chat_out.pack(fill=tk.BOTH, expand=True, padx=10, pady=(8, 10))
        style_text_widget(self.chat_out)
        self.chat_out.insert(tk.END, "选中模型后发送 Chat 测试。\n")
        self.chat_out.configure(state=tk.DISABLED)

        self.msg_var = tk.StringVar(value="")
        self.msg_label = tk.Label(
            self,
            textvariable=self.msg_var,
            bg=Theme.BG,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_SMALL,
            anchor="w",
        )
        self.msg_label.pack(fill=tk.X, pady=(8, 0))

    def _set_error_text(self, text: str) -> None:
        self.error_out.configure(state=tk.NORMAL)
        self.error_out.delete("1.0", tk.END)
        self.error_out.insert(tk.END, text)
        self.error_out.configure(state=tk.DISABLED)

    def _show_error_for(self, cid: Optional[str]) -> None:
        if not cid:
            self._set_error_text("未选中模型。\n")
            return
        cfg = self.service.store.get(cid)
        if not cfg:
            self._set_error_text("模型不存在。\n")
            return
        lines = [
            f"ID: {cfg.config_id}",
            f"名称: {cfg.display_name}",
            f"API: {cfg.base_url}",
            f"模型: {cfg.model_id}",
            f"状态: {cfg.status}",
            f"错误码: {cfg.last_error_code or '（无）'}",
            f"最近探活: {cfg.last_probe_at or '（无）'}",
            "",
            "—— 完整错误原文 ——",
            cfg.last_error.strip() if cfg.last_error else "（无错误，若为 ready 属正常）",
            "",
        ]
        self._set_error_text("\n".join(lines))

    def _show_error_detail(self) -> None:
        self._show_error_for(self._selected_id())

    def _on_select(self) -> None:
        self._update_health_from_selection()
        self._show_error_for(self._selected_id())

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for m in self.service.list_models():
            summary = ""
            if m.last_error:
                one = m.last_error.replace("\n", " ")
                summary = (m.last_error_code or "") + " " + one[:48]
            self.tree.insert(
                "",
                tk.END,
                iid=m.config_id,
                values=(
                    m.config_id,
                    m.display_name,
                    m.base_url,
                    m.model_id,
                    m.status,
                    "是" if m.enabled else "否",
                    summary.strip(),
                ),
                tags=(m.status,),
            )
        for status, color in STATUS_COLORS.items():
            self.tree.tag_configure(status, foreground=color)

        bindable = self.service.list_bindable()
        if bindable:
            names = ", ".join(f"{b.display_name}({b.config_id})" for b in bindable)
            self.bindable_var.set(f"可绑定大脑：{names}")
        else:
            self.bindable_var.set("可绑定大脑：无（需启用且状态=ready）")
        self._update_health_summary()
        sel = self._selected_id()
        if sel:
            self._show_error_for(sel)
        if self.on_change:
            self.on_change()

    def _paint_health(self, text: str, color: str) -> None:
        self.health_var.set(text)
        self.health_label.configure(fg=color)

    def _update_health_summary(self) -> None:
        models = self.service.list_models()
        if not models:
            self._paint_health("Live — 无模型", Theme.TEXT_MUTED)
            return
        ready_n = sum(1 for m in models if m.status == "ready" and m.enabled)
        failed_n = sum(1 for m in models if m.status == "failed")
        if ready_n:
            self._paint_health(
                f"Live — up（就绪 {ready_n}/{len(models)}）", Theme.SUCCESS
            )
        elif failed_n:
            self._paint_health(
                f"Live — down（失败 {failed_n}/{len(models)}，看下方完整报错）",
                Theme.DANGER,
            )
        else:
            self._paint_health(
                "Live — 未探活（点「一键添加并探活」或「重新探活」）",
                Theme.WARNING,
            )

    def _update_health_from_selection(self) -> None:
        cid = self._selected_id()
        if not cid:
            self._update_health_summary()
            return
        cfg = self.service.store.get(cid)
        if not cfg:
            return
        if cfg.status == "ready":
            self._paint_health(
                f"Live — up · {cfg.display_name} · {cfg.model_id}", Theme.SUCCESS
            )
        elif cfg.status == "failed":
            self._paint_health(
                f"Live — down · {cfg.display_name} · {cfg.last_error_code or 'error'}",
                Theme.DANGER,
            )
        else:
            self._paint_health(
                f"Live — {cfg.status} · {cfg.display_name}", Theme.WARNING
            )

    def _selected_id(self) -> Optional[str]:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _timeout(self) -> int:
        try:
            return int(self.vars["timeout_ms"].get().strip() or "15000")
        except ValueError:
            return 15000

    def _discover(self) -> None:
        key = self.vars["api_key"].get().strip()
        base = self.vars["base_url"].get().strip()
        if not key or not base:
            messagebox.showwarning("拉取模型", "请先填写 API 地址和 API Key")
            return
        try:
            listed = self.service.discover_remote_models(
                base_url=base, api_key=key, timeout_ms=self._timeout()
            )
            if not listed.ok:
                self._set_error_text(
                    f"拉取模型失败\ncode={listed.code}\n\n{listed.message}\n"
                )
                self._set_msg(listed.ui_text, ok=False)
                messagebox.showwarning("拉取失败", f"{listed.ui_text}\n见下方完整报错。")
                return
            self._discovered = listed.model_ids
            self.model_combo.configure(values=self._discovered)
            if not self.vars["model_id"].get().strip() and self._discovered:
                from multi_agent_room.adapters import pick_preferred_model

                self.vars["model_id"].set(pick_preferred_model(self._discovered))
            preview = ", ".join(self._discovered[:20])
            more = f" …共 {len(self._discovered)} 个" if len(self._discovered) > 20 else ""
            self._set_error_text(
                f"拉取成功，可用模型：\n{preview}{more}\n\n"
                f"已填入推荐：{self.vars['model_id'].get()}\n"
                "可在「模型」下拉里改选，再点「一键添加并探活」。\n"
            )
            self._set_msg(listed.ui_text, ok=True)
        except Exception as exc:  # noqa: BLE001
            self._set_msg(str(exc), ok=False)
            messagebox.showerror("拉取异常", str(exc))

    def _add(self) -> None:
        try:
            key = self.vars["api_key"].get().strip()
            base = self.vars["base_url"].get().strip()
            if not key or not base:
                messagebox.showwarning("添加", "只需填：API 地址 + API Key")
                return
            self._set_msg("正在拉取模型并探活…", ok=True)
            self.update_idletasks()
            cfg, result, listed = self.service.add_from_endpoint(
                base_url=base,
                api_key=key,
                display_name=self.vars["display_name"].get(),
                model_id=self.vars["model_id"].get(),
                timeout_ms=self._timeout(),
                auto_probe=True,
            )
            self.vars["api_key"].set("")
            if listed.ok:
                self._discovered = listed.model_ids
                self.model_combo.configure(values=self._discovered)
                self.vars["model_id"].set(cfg.model_id)
            self.refresh()
            if self.tree.exists(cfg.config_id):
                self.tree.selection_set(cfg.config_id)
            self._on_select()
            if result and result.ok:
                self._set_msg(
                    f"已添加并探活成功：{cfg.display_name} · {cfg.model_id}", ok=True
                )
                messagebox.showinfo(
                    "成功",
                    f"{result.ui_text}\n\n"
                    f"显示名：{cfg.display_name}\n"
                    f"API：{cfg.base_url}\n"
                    f"模型：{cfg.model_id}\n\n"
                    "已可绑定为 Agent 大脑。",
                )
            else:
                ui = result.ui_text if result else "探活未执行"
                self._set_msg(f"已保存但探活失败：{ui}", ok=False)
                messagebox.showwarning(
                    "探活失败",
                    f"{ui}\n\n完整错误见下方「完整报错」面板，可反复查看。\n"
                    "也可改选模型后点「重新探活」，或用 Chat 测试。",
                )
        except Exception as exc:  # noqa: BLE001
            self._set_msg(str(exc), ok=False)
            self._set_error_text(str(exc))
            messagebox.showerror("添加失败", str(exc))

    def _probe_selected(self) -> None:
        cid = self._selected_id()
        if not cid:
            messagebox.showwarning("探活", "请先选中一条模型")
            return
        try:
            self._set_msg("探活中…", ok=True)
            self.update_idletasks()
            cfg, result = self.service.probe(cid)
            self.refresh()
            if self.tree.exists(cfg.config_id):
                self.tree.selection_set(cfg.config_id)
            self._on_select()
            self._set_msg(result.ui_text, ok=result.ok)
            if not result.ok:
                messagebox.showwarning(
                    "探活失败",
                    f"{result.ui_text}\n\n完整错误已写入下方面板，可反复查看。",
                )
            else:
                messagebox.showinfo(
                    "探活成功",
                    f"{result.ui_text}\n模型：{cfg.model_id}\nAPI：{cfg.base_url}",
                )
        except Exception as exc:  # noqa: BLE001
            self._set_msg(str(exc), ok=False)
            self._set_error_text(str(exc))
            messagebox.showerror("探活异常", str(exc))

    def _chat_test(self) -> None:
        cid = self._selected_id()
        if not cid:
            messagebox.showwarning("Chat 测试", "请先选中一条模型")
            return
        prompt = self.chat_prompt.get().strip() or "请用一句话回复：pong"
        try:
            cfg, result = self.service.chat_test(cid, prompt)
            self.refresh()
            if self.tree.exists(cfg.config_id):
                self.tree.selection_set(cfg.config_id)
            self._on_select()
            self.chat_out.configure(state=tk.NORMAL)
            self.chat_out.delete("1.0", tk.END)
            if result.ok:
                self.chat_out.insert(
                    tk.END,
                    f"[成功] {cfg.display_name} · {cfg.model_id}\n"
                    f"状态 → ready\n\n助手回复：\n{result.reply}\n",
                )
                self._set_msg(result.ui_text, ok=True)
            else:
                self.chat_out.insert(
                    tk.END,
                    f"[失败] {result.ui_text}\ncode={result.code}\n\n{result.message}\n",
                )
                self._set_error_text(
                    f"Chat 测试失败\ncode={result.code}\n\n{result.message}\n"
                )
                self._set_msg(result.ui_text, ok=False)
                messagebox.showwarning(
                    "Chat 测试失败", f"{result.ui_text}\n完整错误见下方面板。"
                )
            self.chat_out.configure(state=tk.DISABLED)
        except Exception as exc:  # noqa: BLE001
            self._set_msg(str(exc), ok=False)
            messagebox.showerror("Chat 测试异常", str(exc))

    def _toggle_enabled(self) -> None:
        cid = self._selected_id()
        if not cid:
            return
        cfg = self.service.store.get(cid)
        if not cfg:
            return
        self.service.set_enabled(cid, not cfg.enabled)
        ok, reason = self.service.can_bind(cid)
        self._set_msg(
            f"已切换启用={not cfg.enabled}；可绑定={ok}（{reason}）",
            ok=True,
        )
        self.refresh()

    def _delete(self) -> None:
        cid = self._selected_id()
        if not cid:
            return
        if not messagebox.askyesno("删除", f"确认删除 {cid}？"):
            return
        self.service.delete_model(cid)
        self.refresh()
        self._set_error_text("已删除。选中其他模型可查看其完整报错。\n")

    def _set_msg(self, text: str, *, ok: bool) -> None:
        self.msg_var.set(text)
        self.msg_label.configure(fg=Theme.SUCCESS if ok else Theme.DANGER)
