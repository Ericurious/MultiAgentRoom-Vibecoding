"""共享聊天室壳层 UI（M2）— Cursor 风格三栏；服务接口不变。"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional

from multi_agent_room.agent_service import AgentService
from multi_agent_room.model_service import ModelService
from multi_agent_room.room import PHASE_HINTS
from multi_agent_room.room_service import JUDGE_COMMANDS, RoomService
from multi_agent_room.theme import Theme, card, section_title, style_listbox, style_text_widget

# 议程步骤（展示用，不改变 phase 枚举）
FLOW_STEPS = (
    ("Idle", "创建"),
    ("Campaign", "竞选"),
    ("ReviewOpen", "审阅"),
    ("AwaitingJudge", "评判"),
    ("Final", "终稿"),
)

PHASE_TO_STEP = {
    "Idle": 0,
    "Campaign": 1,
    "AwaitingFirstAnswer": 1,
    "ReviewOpen": 2,
    "Frozen": 2,
    "AwaitingUserClarify": 2,
    "ConfirmOpen": 3,
    "AwaitingJudge": 3,
    "AwaitingUserEscalation": 3,
    "Final": 4,
}


class RoomPanel(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        agents: Optional[AgentService] = None,
        models: Optional[ModelService] = None,
        service: Optional[RoomService] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, bg=Theme.BG, **kwargs)
        self.models = models or ModelService()
        self.agents = agents or AgentService(models=self.models)
        self.service = service or RoomService(agents=self.agents, models=self.models)
        self._step_labels: list[tk.Label] = []
        self._room_items: dict[str, str] = {}
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.columnconfigure(0, weight=0, minsize=240)
        self.columnconfigure(1, weight=1, minsize=420)
        self.columnconfigure(2, weight=0, minsize=280)
        self.rowconfigure(0, weight=1)

        self._build_left()
        self._build_center()
        self._build_right()

    # ---- 左：房间列表 ----
    def _build_left(self) -> None:
        left = tk.Frame(self, bg=Theme.BG_SIDE, width=250)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_propagate(False)

        head = tk.Frame(left, bg=Theme.BG_SIDE)
        head.pack(fill=tk.X, padx=14, pady=(16, 8))
        tk.Label(
            head,
            text="ROOMS",
            bg=Theme.BG_SIDE,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_TINY,
        ).pack(anchor="w")
        tk.Label(
            head,
            text="房间列表",
            bg=Theme.BG_SIDE,
            fg=Theme.TEXT,
            font=Theme.FONT_H2,
        ).pack(anchor="w")

        ttk.Button(
            left, text="+ 新房间", style="Accent.TButton", command=self._create
        ).pack(fill=tk.X, padx=14, pady=(8, 6))

        search_row = tk.Frame(left, bg=Theme.BG_SIDE)
        search_row.pack(fill=tk.X, padx=14, pady=4)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render_room_list())
        ttk.Entry(search_row, textvariable=self.search_var).pack(fill=tk.X)
        self.title_var = tk.StringVar(value="演示房间")

        list_wrap = tk.Frame(left, bg=Theme.BG_SIDE)
        list_wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.room_list = tk.Listbox(list_wrap)
        style_listbox(self.room_list)
        self.room_list.pack(fill=tk.BOTH, expand=True)
        self.room_list.bind("<<ListboxSelect>>", lambda _e: self._enter_from_list())

        invite_box = card(left)
        invite_box.pack(fill=tk.X, padx=10, pady=(0, 12))
        section_title(invite_box, "邀请就绪 Agent", bg=Theme.BG_CARD).pack(
            anchor="w", padx=10, pady=(8, 2)
        )
        self.agent_combo = ttk.Combobox(invite_box, state="readonly")
        self.agent_combo.pack(fill=tk.X, padx=10, pady=4)
        ttk.Button(
            invite_box, text="邀请入房", style="Ghost.TButton", command=self._invite
        ).pack(fill=tk.X, padx=10, pady=(4, 10))

    # ---- 中：白板 / 聊天主区 ----
    def _build_center(self) -> None:
        center = tk.Frame(self, bg=Theme.BG)
        center.grid(row=0, column=1, sticky="nsew", padx=8)
        center.rowconfigure(3, weight=1)
        center.columnconfigure(0, weight=1)

        # 标题区
        header = card(center)
        header.grid(row=0, column=0, sticky="ew", pady=(8, 6))
        top = tk.Frame(header, bg=Theme.BG_CARD)
        top.pack(fill=tk.X, padx=14, pady=(12, 4))
        self.room_title_lbl = tk.Label(
            top,
            text="选择或创建房间",
            bg=Theme.BG_CARD,
            fg=Theme.TEXT,
            font=Theme.FONT_TITLE,
            anchor="w",
        )
        self.room_title_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.phase_chip = tk.Label(
            top,
            text="Idle",
            bg=Theme.ACCENT_SOFT,
            fg=Theme.ACCENT_FG,
            font=Theme.FONT_TINY,
            padx=10,
            pady=3,
        )
        self.phase_chip.pack(side=tk.RIGHT)

        tk.Label(
            header,
            text="共享白板 · 多 Agent 协作",
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_SMALL,
            anchor="w",
        ).pack(fill=tk.X, padx=14)

        # 流程步进
        steps = tk.Frame(header, bg=Theme.BG_CARD)
        steps.pack(fill=tk.X, padx=14, pady=(10, 12))
        self._step_labels.clear()
        for i, (_pid, label) in enumerate(FLOW_STEPS):
            if i:
                tk.Label(
                    steps,
                    text="·",
                    bg=Theme.BG_CARD,
                    fg=Theme.TEXT_DIM,
                    font=Theme.FONT_SMALL,
                ).pack(side=tk.LEFT, padx=4)
            lab = tk.Label(
                steps,
                text=label,
                bg=Theme.CHIP,
                fg=Theme.TEXT_MUTED,
                font=Theme.FONT_TINY,
                padx=10,
                pady=4,
            )
            lab.pack(side=tk.LEFT)
            self._step_labels.append(lab)

        # 动作条
        actions = tk.Frame(center, bg=Theme.BG)
        actions.grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Button(
            actions,
            text="演示推进到审阅窗",
            style="Accent.TButton",
            command=self._demo_to_review,
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions, text="工作区…", style="Toolbar.TButton", command=self._choose_workspace
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            actions, text="交付", style="Toolbar.TButton", command=self._click_deliver
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            actions, text="打断 Frozen", style="Ghost.TButton", command=self._interrupt
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions, text="恢复", style="Ghost.TButton", command=self._resume
        ).pack(side=tk.LEFT, padx=4)

        # 原话钉选
        pin = card(center)
        pin.grid(row=2, column=0, sticky="ew", pady=6)
        section_title(pin, "原话钉选（只读）", bg=Theme.BG_CARD).pack(
            anchor="w", padx=12, pady=(8, 2)
        )
        self.pin_text = tk.Text(pin, height=3, wrap=tk.WORD, state=tk.DISABLED)
        style_text_widget(self.pin_text, bg=Theme.PIN_BG)
        self.pin_text.configure(font=Theme.FONT_UI)
        self.pin_text.pack(fill=tk.X, padx=12, pady=(0, 10))

        # 共享稿 + 事件流（主白板）
        board = card(center)
        board.grid(row=3, column=0, sticky="nsew", pady=4)
        board.rowconfigure(1, weight=1)
        board.columnconfigure(0, weight=1)

        board_head = tk.Frame(board, bg=Theme.BG_CARD)
        board_head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        tk.Label(
            board_head,
            text="共享稿 / 事件流",
            bg=Theme.BG_CARD,
            fg=Theme.TEXT,
            font=Theme.FONT_H2,
        ).pack(side=tk.LEFT)
        self.show_collapsed = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            board_head,
            text="显示折叠事件",
            variable=self.show_collapsed,
            command=self.refresh,
        ).pack(side=tk.RIGHT)

        self.feed = tk.Text(board, wrap=tk.WORD, state=tk.DISABLED)
        style_text_widget(self.feed, bg=Theme.BG_CARD)
        self.feed.configure(font=Theme.FONT_UI, highlightthickness=0)
        self.feed.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self.feed.tag_configure(
            "user",
            background=Theme.USER_BUBBLE,
            foreground=Theme.ACCENT_FG,
            lmargin1=48,
            lmargin2=48,
            rmargin=8,
            spacing1=4,
            spacing3=4,
        )
        self.feed.tag_configure(
            "agent",
            background=Theme.AGENT_BUBBLE,
            foreground=Theme.TEXT,
            lmargin1=8,
            lmargin2=8,
            rmargin=48,
            spacing1=4,
            spacing3=4,
        )
        self.feed.tag_configure(
            "meta", foreground=Theme.TEXT_MUTED, font=Theme.FONT_TINY
        )
        self.feed.tag_configure(
            "sys", foreground=Theme.TEXT_DIM, font=Theme.FONT_SMALL
        )

        # 输入条（Composer 感）
        input_bar = card(center)
        input_bar.grid(row=4, column=0, sticky="ew", pady=(6, 10))
        row = tk.Frame(input_bar, bg=Theme.BG_CARD)
        row.pack(fill=tk.X, padx=10, pady=10)
        self.q_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.q_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8)
        )
        ttk.Button(
            row, text="提问并钉选", style="Accent.TButton", command=self._ask
        ).pack(side=tk.RIGHT)

        # 评判台
        judge = tk.Frame(center, bg=Theme.BG)
        judge.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        tk.Label(
            judge,
            text="JUDGE",
            bg=Theme.BG,
            fg=Theme.TEXT_DIM,
            font=Theme.FONT_TINY,
        ).pack(anchor="w")
        jrow = tk.Frame(judge, bg=Theme.BG)
        jrow.pack(fill=tk.X, pady=4)
        for cmd in JUDGE_COMMANDS:
            ttk.Button(
                jrow,
                text=cmd,
                style="Toolbar.TButton",
                command=lambda c=cmd: self._judge(c),
            ).pack(side=tk.LEFT, padx=2)

    # ---- 右：状态栏 ----
    def _build_right(self) -> None:
        right = tk.Frame(self, bg=Theme.BG_SIDE, width=290)
        right.grid(row=0, column=2, sticky="nsew")
        right.grid_propagate(False)

        tk.Label(
            right,
            text="STATUS",
            bg=Theme.BG_SIDE,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_TINY,
        ).pack(anchor="w", padx=14, pady=(16, 2))
        tk.Label(
            right,
            text="状态栏",
            bg=Theme.BG_SIDE,
            fg=Theme.TEXT,
            font=Theme.FONT_H2,
        ).pack(anchor="w", padx=14, pady=(0, 4))

        self.status_mode = tk.Label(
            right,
            text="当前：空闲",
            bg=Theme.BG_SIDE,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_SMALL,
            anchor="w",
        )
        self.status_mode.pack(fill=tk.X, padx=14)

        # Agent 状态
        ag = card(right)
        ag.pack(fill=tk.X, padx=10, pady=8)
        section_title(ag, "AGENT 状态", bg=Theme.BG_CARD).pack(
            anchor="w", padx=10, pady=(8, 2)
        )
        self.agents_status = tk.Label(
            ag,
            text="（无成员）",
            bg=Theme.BG_CARD,
            fg=Theme.TEXT,
            font=Theme.FONT_SMALL,
            justify=tk.LEFT,
            anchor="nw",
            wraplength=250,
        )
        self.agents_status.pack(fill=tk.X, padx=10, pady=(0, 10))

        # 资格锁
        lock = card(right)
        lock.pack(fill=tk.X, padx=10, pady=4)
        section_title(lock, "资格锁", bg=Theme.BG_CARD).pack(
            anchor="w", padx=10, pady=(8, 2)
        )
        self.lock_var = tk.StringVar(value="尚未首答")
        tk.Label(
            lock,
            textvariable=self.lock_var,
            bg=Theme.BG_CARD,
            fg=Theme.TEXT,
            font=Theme.FONT_SMALL,
            wraplength=250,
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X, padx=10, pady=(0, 10))

        # 议程
        agenda = card(right)
        agenda.pack(fill=tk.X, padx=10, pady=4)
        section_title(agenda, "议程板", bg=Theme.BG_CARD).pack(
            anchor="w", padx=10, pady=(8, 2)
        )
        self.agenda_var = tk.StringVar(value="")
        tk.Label(
            agenda,
            textvariable=self.agenda_var,
            bg=Theme.BG_CARD,
            fg=Theme.TEXT,
            font=Theme.FONT_SMALL,
            wraplength=250,
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X, padx=10, pady=(0, 10))

        # 最终回复
        final = card(right)
        final.pack(fill=tk.X, padx=10, pady=4)
        section_title(final, "最终回复槽", bg=Theme.BG_CARD).pack(
            anchor="w", padx=10, pady=(8, 2)
        )
        self.final_var = tk.StringVar(value="未通过")
        tk.Label(
            final,
            textvariable=self.final_var,
            bg=Theme.BG_CARD,
            fg=Theme.ACCENT_FG,
            font=Theme.FONT_UI_BOLD,
            wraplength=250,
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X, padx=10, pady=(0, 10))

        # 澄清
        clarify = card(right)
        clarify.pack(fill=tk.X, padx=10, pady=4)
        section_title(clarify, "澄清问答", bg=Theme.BG_CARD).pack(
            anchor="w", padx=10, pady=(8, 2)
        )
        self.clarify_var = tk.StringVar(value="（无待答）")
        tk.Label(
            clarify,
            textvariable=self.clarify_var,
            bg=Theme.BG_CARD,
            fg=Theme.TEXT,
            font=Theme.FONT_SMALL,
            wraplength=250,
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X, padx=10)
        cf = tk.Frame(clarify, bg=Theme.BG_CARD)
        cf.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(
            cf, text="模拟提问", style="Toolbar.TButton", command=self._sim_clarify
        ).pack(side=tk.LEFT)
        ttk.Button(
            cf, text="回答…", style="Ghost.TButton", command=self._answer_clarify
        ).pack(side=tk.LEFT, padx=4)

        self.status = tk.StringVar(value="")
        tk.Label(
            right,
            textvariable=self.status,
            bg=Theme.BG_SIDE,
            fg=Theme.TEXT_MUTED,
            font=Theme.FONT_TINY,
            wraplength=260,
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X, padx=14, pady=12)

    # ---- 刷新（数据绑定不变）----
    def refresh(self) -> None:
        self._render_room_list()

        ready = self.service.list_ready_agents()
        self.agent_combo["values"] = [
            f"{a.agent_id} | {a.display_name} | {a.model_config_id}" for a in ready
        ]
        if ready and not self.agent_combo.get():
            self.agent_combo.current(0)

        cur = self.service.current_room()
        if not cur:
            self.room_title_lbl.configure(text="选择或创建房间")
            self.phase_chip.configure(text="Idle")
            self._set_text(self.pin_text, "")
            self._set_feed([])
            self.agenda_var.set("（未进入房间）")
            self.lock_var.set("资格锁：尚未首答")
            self.agents_status.configure(text="（无成员）")
            self.clarify_var.set("（无待答）")
            self.final_var.set("未通过")
            self.status_mode.configure(text="当前：空闲")
            self._paint_steps(0)
            self.status.set("请创建或进入房间")
            return

        self.room_title_lbl.configure(text=cur.title or cur.room_id)
        self.phase_chip.configure(text=cur.phase)
        self._paint_steps(PHASE_TO_STEP.get(cur.phase, 0))
        self._set_text(self.pin_text, cur.pinned_question or "")

        # 拼装主区 feed：钉选气泡 + 共享稿 + 事件
        lines: list[tuple[str, str]] = []
        if cur.pinned_question:
            lines.append(("user", f"用户\n{cur.pinned_question}"))
            lines.append(("meta", time.strftime("%H:%M · 原话钉选")))
        doc = self.service.get_doc(cur.room_id)
        for line in doc.render_lines():
            lines.append(("agent", line))
        lines.append(("meta", "共享稿视图 · blockId / version"))
        for ev in self.service.timeline.list_events(
            cur.room_id, include_collapsed=self.show_collapsed.get()
        ):
            mark = "[折叠] " if ev.collapsed else ""
            lines.append(("sys", f"{mark}{ev.kind}: {ev.summary}"))
        self._set_feed(lines)

        self.agenda_var.set(cur.agenda_text())
        self.lock_var.set(cur.qualification_lock_text())
        hint = PHASE_HINTS.get(cur.phase, cur.phase)
        self.status_mode.configure(text=f"当前：{cur.phase} · {hint}")

        names = []
        for aid in cur.invited_agent_ids:
            p = self.agents.profiles.get(aid)
            label = p.display_name if p else aid
            role = ""
            if aid == cur.first_answerer_agent_id:
                role = " · 首答"
            names.append(f"● {label}{role}\n  {aid}")
        self.agents_status.configure(
            text="\n".join(names) if names else "（无成员）"
        )

        if cur.pending_clarify:
            self.clarify_var.set(cur.pending_clarify)
        else:
            self.clarify_var.set("（无待答）")
        self.final_var.set(self.service.final_slot_text(cur.room_id) or "（空）")
        self.status.set(
            f"{cur.room_id} · frozen={cur.frozen} · reviewOpen={cur.review_window_open}"
        )

    def _render_room_list(self) -> None:
        q = (self.search_var.get() or "").strip().lower()
        rooms = self.service.list_rooms()
        cur = self.service.current_room()
        self.room_list.delete(0, tk.END)
        self._room_items.clear()
        for r in rooms:
            label = f"{r.title}\n{r.room_id} · {r.phase}"
            flat = f"{r.title} {r.room_id} {r.phase}".lower()
            if q and q not in flat:
                continue
            # Listbox 单行
            display = f"  {r.title}   · {r.phase}"
            self.room_list.insert(tk.END, display)
            self._room_items[display] = r.room_id
        if cur:
            for i in range(self.room_list.size()):
                item = self.room_list.get(i)
                if self._room_items.get(item) == cur.room_id:
                    self.room_list.selection_clear(0, tk.END)
                    self.room_list.selection_set(i)
                    self.room_list.see(i)
                    break

    def _paint_steps(self, active: int) -> None:
        for i, lab in enumerate(self._step_labels):
            if i < active:
                lab.configure(bg=Theme.ACCENT_SOFT, fg=Theme.ACCENT_FG)
            elif i == active:
                lab.configure(bg=Theme.ACCENT, fg="#0c0c0c")
            else:
                lab.configure(bg=Theme.CHIP, fg=Theme.TEXT_MUTED)

    def _set_text(self, widget: tk.Text, value: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state=tk.DISABLED)

    def _set_feed(self, lines: list[tuple[str, str]]) -> None:
        self.feed.configure(state=tk.NORMAL)
        self.feed.delete("1.0", tk.END)
        if not lines:
            self.feed.insert(tk.END, "进入房间后，原话、共享稿与事件将显示于此。\n", "sys")
        for tag, text in lines:
            self.feed.insert(tk.END, text + "\n", tag)
        self.feed.configure(state=tk.DISABLED)
        self.feed.see(tk.END)

    # ---- 命令（与原先相同，只调 RoomService）----
    def _create(self) -> None:
        title = simpledialog.askstring(
            "新房间",
            "房间标题：",
            initialvalue=self.title_var.get() or "演示房间",
            parent=self,
        )
        if not title:
            return
        self.title_var.set(title)
        room = self.service.create_room(title)
        self.refresh()
        messagebox.showinfo("房间", f"已创建 {room.room_id}")

    def _enter_from_list(self) -> None:
        sel = self.room_list.curselection()
        if not sel:
            return
        display = self.room_list.get(sel[0])
        room_id = self._room_items.get(display)
        if not room_id:
            return
        self.service.enter_room(room_id)
        self.refresh()

    def _invite(self) -> None:
        cur = self.service.current_room()
        if not cur:
            messagebox.showwarning("房间", "请先进入房间")
            return
        label = self.agent_combo.get()
        if not label:
            messagebox.showwarning("邀请", "无就绪 Agent")
            return
        agent_id = label.split("|", 1)[0].strip()
        try:
            self.service.invite_ready_agent(cur.room_id, agent_id)
            self.refresh()
        except (KeyError, ValueError) as exc:
            messagebox.showerror("邀请", str(exc))

    def _ask(self) -> None:
        cur = self.service.current_room()
        if not cur:
            messagebox.showwarning("提问", "请先进入房间")
            return
        try:
            self.service.ask_question(cur.room_id, self.q_var.get())
            self.q_var.set("")
            self.refresh()
        except ValueError as exc:
            messagebox.showerror("提问", str(exc))

    def _demo_to_review(self) -> None:
        cur = self.service.current_room()
        if not cur:
            return
        if not cur.pinned_question:
            messagebox.showwarning("演示", "请先提问")
            return
        if not cur.invited_agent_ids:
            messagebox.showwarning("演示", "无成员")
            return
        try:
            agent_id = cur.invited_agent_ids[0]
            self.agents.user_assign_roles(
                cur.room_id,
                first_answerer_agent_id=agent_id,
                judge_is_user=True,
            )
            self.service.submit_first_answer(
                cur.room_id, agent_id, "【壳层演示首答】共享稿区块内容"
            )
            self.refresh()
        except (KeyError, ValueError) as exc:
            messagebox.showerror("演示", str(exc))

    def _choose_workspace(self) -> None:
        cur = self.service.current_room()
        if not cur:
            messagebox.showwarning("工作区", "请先进入房间")
            return
        chosen = filedialog.askdirectory(
            initialdir=cur.workspace_path or "", mustexist=False
        )
        if not chosen:
            return
        try:
            self.service.set_workspace(cur.room_id, chosen)
            self.refresh()
            messagebox.showinfo("工作区", f"已绑定：\n{chosen}")
        except OSError as exc:
            messagebox.showerror("工作区", str(exc))

    def _click_deliver(self) -> None:
        """T1：用户点「交付」触发正式落盘。"""
        cur = self.service.current_room()
        if not cur:
            messagebox.showwarning("交付", "请先进入房间")
            return
        try:
            result = self.service.click_deliver(
                cur.room_id,
                summary="用户点击交付生成的总结。",
            )
            self.refresh()
            if result.ok:
                messagebox.showinfo(
                    "交付",
                    f"已写入工作区\n{result.manifest_rel}\n产物 {len(result.items)} 个",
                )
            else:
                messagebox.showerror("交付", f"{result.code}: {result.message}")
        except (KeyError, ValueError, OSError) as exc:
            messagebox.showerror("交付", str(exc))

    def _judge(self, cmd: str) -> None:
        cur = self.service.current_room()
        if not cur:
            return
        try:
            self.service.judge_command(cur.room_id, cmd)
            self.refresh()
        except ValueError as exc:
            messagebox.showerror("评判台", str(exc))

    def _interrupt(self) -> None:
        cur = self.service.current_room()
        if not cur:
            return
        self.service.interrupt(cur.room_id)
        self.refresh()

    def _resume(self) -> None:
        cur = self.service.current_room()
        if not cur:
            return
        self.service.resume(cur.room_id)
        self.refresh()

    def _sim_clarify(self) -> None:
        cur = self.service.current_room()
        if not cur:
            return
        q = simpledialog.askstring("澄清", "Agent 向用户的问题：", parent=self)
        if not q:
            return
        agent = cur.invited_agent_ids[0] if cur.invited_agent_ids else ""
        self.service.ask_clarify(cur.room_id, q, from_agent=agent)
        self.refresh()

    def _answer_clarify(self) -> None:
        cur = self.service.current_room()
        if not cur or not cur.pending_clarify:
            messagebox.showinfo("澄清", "当前无待答")
            return
        a = simpledialog.askstring("回答澄清", cur.pending_clarify, parent=self)
        if a is None:
            return
        self.service.answer_clarify(cur.room_id, a)
        self.refresh()
