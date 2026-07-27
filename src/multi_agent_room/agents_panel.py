"""Agent 身份与职责 UI（M3）— 逻辑不变；Cursor 风格壳层。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from multi_agent_room.agent_service import AgentService
from multi_agent_room.model_service import ModelService
from multi_agent_room.theme import Theme, card, section_title


class AgentsPanel(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        service: Optional[AgentService] = None,
        models: Optional[ModelService] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.models = models or ModelService()
        self.service = service or AgentService(models=self.models)
        self.room_id = "demo-room"
        self._build()
        self.refresh()

    def _build(self) -> None:
        form_card = card(self)
        form_card.pack(fill=tk.X, pady=(0, 10))
        section_title(form_card, "创建 / 邀请", bg=Theme.BG_CARD).pack(
            anchor=tk.W, padx=14, pady=(12, 6)
        )

        form = ttk.Frame(form_card, style="Card.TFrame")
        form.pack(fill=tk.X, padx=14, pady=(0, 12))
        self.name_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.q_var = tk.StringVar()

        ttk.Label(form, text="显示名", style="CardMuted.TLabel").grid(
            row=0, column=0, sticky=tk.W, pady=4
        )
        ttk.Entry(form, textvariable=self.name_var, width=18).grid(
            row=0, column=1, sticky=tk.W, padx=(6, 12)
        )
        ttk.Label(form, text="绑定模型 (ready)", style="CardMuted.TLabel").grid(
            row=0, column=2, sticky=tk.W
        )
        self.model_combo = ttk.Combobox(
            form, textvariable=self.model_var, width=36, state="readonly"
        )
        self.model_combo.grid(row=0, column=3, sticky=tk.EW, padx=6)
        ttk.Button(
            form, text="创建 Agent", style="Accent.TButton", command=self._create
        ).grid(row=0, column=4, padx=4)
        ttk.Button(
            form, text="邀请入房", style="Ghost.TButton", command=self._invite
        ).grid(row=0, column=5)
        ttk.Button(
            form, text="刷新模型", style="Toolbar.TButton", command=self._reload_models
        ).grid(row=0, column=6, padx=4)
        form.columnconfigure(3, weight=1)

        camp = card(self)
        camp.pack(fill=tk.X, pady=(0, 10))
        section_title(camp, "提问与职责", bg=Theme.BG_CARD).pack(
            anchor=tk.W, padx=14, pady=(12, 6)
        )
        qf = ttk.Frame(camp, style="Card.TFrame")
        qf.pack(fill=tk.X, padx=14, pady=(0, 8))
        ttk.Entry(qf, textvariable=self.q_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8)
        )
        ttk.Button(
            qf, text="提问并开竞选", style="Accent.TButton", command=self._ask
        ).pack(side=tk.LEFT)
        ttk.Button(
            qf, text="冻结职责", style="Toolbar.TButton", command=self._freeze
        ).pack(side=tk.LEFT, padx=6)

        role_f = ttk.Frame(camp, style="Card.TFrame")
        role_f.pack(fill=tk.X, padx=14, pady=(0, 14))
        ttk.Button(
            role_f,
            text="认领首答",
            style="Ghost.TButton",
            command=lambda: self._claim("first_answerer"),
        ).pack(side=tk.LEFT)
        ttk.Button(
            role_f,
            text="认领评判",
            style="Ghost.TButton",
            command=lambda: self._claim("judge"),
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            role_f,
            text="用户指定评判",
            style="Toolbar.TButton",
            command=self._user_judge,
        ).pack(side=tk.LEFT)

        table = card(self)
        table.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        section_title(table, "成员列表", bg=Theme.BG_CARD).pack(
            anchor=tk.W, padx=14, pady=(10, 4)
        )
        cols = ("agent_id", "display_name", "model_config_id", "model_health")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=8)
        for c, h, w in (
            ("agent_id", "AgentId", 140),
            ("display_name", "名称", 120),
            ("model_config_id", "模型配置", 140),
            ("model_health", "模型健康", 180),
        ):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, stretch=c == "model_health")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 12))

        self.status = tk.StringVar(value="")
        self.hint = tk.StringVar(
            value="只能绑定「可绑定大脑」(enabled+ready)。请先在模型页探活成功。"
        )
        tk.Label(
            self,
            textvariable=self.status,
            bg=Theme.BG,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_MONO,
            wraplength=860,
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 4))
        tk.Label(
            self,
            textvariable=self.hint,
            bg=Theme.BG,
            fg=Theme.TEXT_DIM,
            font=Theme.FONT_SMALL,
            wraplength=860,
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X)

    def _reload_models(self) -> None:
        bindable = self.models.list_bindable()
        labels = [
            f"{m.config_id} | {m.display_name} | {m.status}" for m in bindable
        ]
        self.model_combo["values"] = labels
        if labels:
            cur = self.model_var.get()
            ids = {m.config_id for m in bindable}
            if not cur or (
                cur not in ids and not any(cur.startswith(i + " |") for i in ids)
            ):
                self.model_combo.current(0)
                self.model_var.set(labels[0])
            self.hint.set("已同步 ready 模型列表。")
        else:
            self.model_combo["values"] = []
            self.model_var.set("")
            self.hint.set(
                "当前没有 ready 模型。请到「模型配置」添加 API → 自动探活成功后再创建 Agent。"
            )
        self.refresh()

    def refresh(self) -> None:
        for i in self.tree.get_children():
            self.tree.delete(i)
        for a in self.service.list_agents():
            m = self.models.store.get(a.model_config_id)
            if not m:
                health = "模型缺失"
            elif not m.enabled:
                health = "已禁用"
            elif m.status == "ready":
                health = "ready（可用）"
            else:
                health = f"{m.status}（不可用）"
            self.tree.insert(
                "",
                tk.END,
                iid=a.agent_id,
                values=(a.agent_id, a.display_name, a.model_config_id, health),
            )
        bindable = self.models.list_bindable()
        labels = [f"{m.config_id} | {m.display_name} | ready" for m in bindable]
        self.model_combo["values"] = labels
        if bindable and not self.model_var.get():
            self.model_var.set(labels[0])
        elif bindable:
            cur = self.model_var.get()
            ids = {m.config_id for m in bindable}
            ok_cur = cur in ids or any(cur.startswith(i + " |") for i in ids)
            if not ok_cur:
                self.model_var.set(labels[0])
        st = self.service.roles.get_or_create(self.room_id)
        self.status.set(
            f"room={self.room_id} phase={st.phase_hint()} "
            f"question={'有' if st.user_question else '无'} "
            f"frozen={st.roles.frozen} "
            f"first={st.roles.first_answerer_agent_id} "
            f"judge={'User' if st.roles.judge_is_user else st.roles.judge_agent_id}"
        )

    def _selected(self) -> Optional[str]:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _resolve_model_config_id(self) -> str:
        raw = self.model_var.get().strip()
        if " | " in raw:
            return raw.split(" | ", 1)[0].strip()
        return raw

    def _create(self) -> None:
        try:
            mid = self._resolve_model_config_id()
            if not mid:
                raise ValueError("请选择已探活成功的模型（可绑定大脑）")
            ok, reason = self.models.can_bind(mid)
            if not ok:
                raise ValueError(
                    f"模型不可用：{reason}\n请先到「模型配置」完成探活或 Chat 测试。"
                )
            p = self.service.create_agent(
                display_name=self.name_var.get() or "Agent",
                model_config_id=mid,
            )
            messagebox.showinfo("Agent", f"已创建 {p.agent_id}（绑定 {mid}）")
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("创建失败", str(exc))

    def _invite(self) -> None:
        aid = self._selected()
        if not aid:
            return
        self.service.invite_to_room(self.room_id, aid)
        self.refresh()

    def _ask(self) -> None:
        try:
            self.service.user_ask(self.room_id, self.q_var.get())
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("提问失败", str(exc))

    def _claim(self, role: str) -> None:
        aid = self._selected()
        if not aid:
            return
        ok, reason = self.service.claim_role(self.room_id, aid, role)  # type: ignore[arg-type]
        if ok:
            messagebox.showinfo("认领", reason)
        else:
            messagebox.showwarning("认领失败", reason)
        self.refresh()

    def _freeze(self) -> None:
        ok, reason = self.service.freeze_roles(self.room_id)
        messagebox.showinfo("冻结", reason if ok else reason)
        self.refresh()

    def _user_judge(self) -> None:
        ok, reason = self.service.user_assign_roles(self.room_id, judge_is_user=True)
        messagebox.showinfo("指定", reason if ok else reason)
        self.refresh()
