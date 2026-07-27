/**
 * Web UI client — talks only to /api/* (Python Model/Agent/Room services).
 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  view: "room",
  rooms: [],
  currentId: null,
  selectedModelId: null,
  judgeCommands: [],
  agentsById: {},
  attachments: [],
};

const FLOW = ["创建", "竞选", "审阅", "评判", "终稿"];
const PHASE_STEP = {
  Idle: 0,
  Campaign: 1,
  AwaitingFirstAnswer: 1,
  ReviewOpen: 2,
  Frozen: 2,
  AwaitingUserClarify: 2,
  ConfirmOpen: 3,
  AwaitingJudge: 3,
  AwaitingUserEscalation: 3,
  Final: 4,
};

const TITLES = {
  room: ["CHAT REVIEW", "聊天室"],
  models: ["MODELS", "模型配置"],
  agents: ["AGENTS", "Agent 成员"],
};

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || data.message || data.ui_text || `HTTP ${res.status}`);
  }
  return data;
}

function toast(msg, kind = "") {
  const el = $("#toast");
  el.hidden = false;
  el.textContent = msg;
  el.className = "toast " + kind;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.hidden = true;
  }, 3200);
}

function setView(name) {
  state.view = name;
  $$(".act").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("show", v.dataset.view === name));
  const [eye, title] = TITLES[name] || ["APP", name];
  $("#chromeEyebrow").textContent = eye;
  $("#chromeTitle").textContent = title;
  $("#statusHint").textContent = name;
  if (name === "room") refreshRooms();
  if (name === "models") refreshModels();
  if (name === "agents") refreshAgents();
}

function paintSteps(phase) {
  const active = PHASE_STEP[phase] ?? 0;
  $("#flowSteps").innerHTML = FLOW.map((label, i) => {
    const cls = i < active ? "done" : i === active ? "on" : "";
    return `<span class="step ${cls}">${label}</span>`;
  }).join("");
}

async function refreshRooms() {
  try {
    const data = await api("/api/rooms");
    state.rooms = data.items || [];
    state.judgeCommands = data.judge_commands || [];
    if (!state.currentId && data.current_id) state.currentId = data.current_id;
    // 有房间却未选中时，自动进入第一个，避免左侧有房、右侧仍是空态
    if (!state.currentId && state.rooms.length) {
      state.currentId = state.rooms[0].room_id;
    }
    renderRoomList();
    renderJudge();
    if (state.currentId) {
      try {
        await api(`/api/rooms/${state.currentId}/enter`, { method: "POST", body: "{}" });
      } catch {
        /* enter 失败仍尝试拉快照 */
      }
      await loadSnapshot(state.currentId);
    } else {
      paintEmptyRoom();
      await fillInviteAgents([]);
    }
  } catch (e) {
    toast(String(e.message || e), "err");
  }
}

function paintEmptyRoom() {
  $("#roomTitle").textContent = "选择或创建房间";
  $("#phaseBadge").textContent = "Idle";
  paintSteps("Idle");
  $("#agendaText").textContent = "（未进入房间）";
  $("#lockText").textContent = "尚未首答";
  $("#finalText").textContent = "未通过";
  $("#statusMode").textContent = "当前：空闲";
  $("#agentStatus").textContent = "（无成员）";
  $("#memberList").textContent = "（尚未进入房间）";
  $("#feed").innerHTML = `<div class="chat-empty">发送问题后，对话会出现在这里</div>`;
  const mon = $("#eventMonitor");
  if (mon) {
    mon.innerHTML = `<div class="muted">系统事件会出现在这里，不占用聊天区。</div>`;
  }
  const ec = $("#eventCount");
  if (ec) ec.textContent = "0";
}

function renderRoomList() {
  const q = ($("#roomSearch").value || "").toLowerCase();
  const box = $("#roomList");
  box.innerHTML = state.rooms
    .filter((r) => `${r.title} ${r.room_id} ${r.phase}`.toLowerCase().includes(q))
    .map(
      (r) => `<button type="button" class="room-item ${
        r.room_id === state.currentId ? "active" : ""
      }" data-id="${esc(r.room_id)}">
        <div class="t">${esc(r.title || r.room_id)}</div>
        <div class="s">${esc(r.room_id)} · ${esc(r.phase)} · 成员 ${(r.invited_agent_ids || []).length}</div>
      </button>`
    )
    .join("");
  box.querySelectorAll(".room-item").forEach((btn) => {
    btn.onclick = async () => {
      try {
        state.currentId = btn.dataset.id;
        renderRoomList();
        await api(`/api/rooms/${state.currentId}/enter`, { method: "POST", body: "{}" });
        await loadSnapshot(state.currentId);
      } catch (e) {
        toast(String(e.message || e), "err");
      }
    };
  });
}

function renderJudge() {
  const row = $("#judgeRow");
  row.innerHTML = (state.judgeCommands || [])
    .map((c) => `<button type="button" class="btn" data-cmd="${esc(c)}">${esc(c)}</button>`)
    .join("");
  row.querySelectorAll("button").forEach((b) => {
    b.onclick = async () => {
      if (!state.currentId) return toast("请先进入房间", "err");
      try {
        await api(`/api/rooms/${state.currentId}/judge`, {
          method: "POST",
          body: JSON.stringify({ command: b.dataset.cmd }),
        });
        await loadSnapshot(state.currentId);
        toast("已执行 " + b.dataset.cmd, "ok");
      } catch (e) {
        toast(String(e.message || e), "err");
      }
    };
  });
}

async function loadSnapshot(rid) {
  const data = await api(`/api/rooms/${rid}/snapshot`);
  const room = data.room;
  applyRoomPanel(room, data);
  await fillInviteAgents(room.invited_agent_ids || []);
}

function applyRoomPanel(room, data = {}) {
  $("#roomTitle").textContent = room.title || room.room_id;
  $("#phaseBadge").textContent = room.phase;
  paintSteps(room.phase);
  if (data.agenda != null) $("#agendaText").textContent = data.agenda || "（无）";
  else if (typeof room.agenda_text === "string") $("#agendaText").textContent = room.agenda_text;
  if (data.lock != null) $("#lockText").textContent = data.lock || "尚未首答";
  if (data.final != null) $("#finalText").textContent = data.final || "未通过";
  $("#statusMode").textContent = `当前：${room.phase}`;
  paintMembers(room.invited_agent_ids || []);

  if (data.feed) {
    paintChatAndEvents(data.feed);
  }
  paintTools(data.tools, room);

  // 同步房间列表里的成员数
  const idx = state.rooms.findIndex((r) => r.room_id === room.room_id);
  if (idx >= 0) {
    state.rooms[idx] = { ...state.rooms[idx], ...room };
    renderRoomList();
  }
}

function paintTools(tools, room) {
  const el = $("#toolsStatus");
  const wsInput = $("#workspacePath");
  const hint = $("#workspaceHint");
  if (!el) return;
  const ws = (tools && tools.workspace_path) || room?.workspace_path || "";
  if (wsInput && document.activeElement !== wsInput) wsInput.value = ws || "";
  const skills = (tools && tools.skills) || [];
  const lines = [
    ws ? `工作区: ${ws}` : "工作区: （未绑定）",
    `已加载技能: ${skills.length}`,
    ...skills.map((s) => {
      const auth = s.default_room_auth ? "默认授权" : "需授权/写阶段";
      return `· ${s.name} (${s.skill_id}) [${s.risk}] ${s.enabled ? "开" : "关"} · ${auth}`;
    }),
    "",
    (tools && tools.note) || "",
  ];
  el.textContent = lines.filter(Boolean).join("\n");
  if (hint) {
    hint.textContent = ws
      ? "已绑定。回复将自动走工具循环（读目录/写真实文件，禁软链接）；附件进 _mar_inbox/。"
      : "未绑定工作区时无法自动读写文件，也无法上传附件落盘。";
  }
}

/** 聊天区只放 user/agent；sys 事件进右侧监控。 */
function paintChatAndEvents(feedRows) {
  const rows = feedRows || [];
  const chat = [];
  const events = [];
  for (const row of rows) {
    if (row.tag === "sys") events.push(row);
    else chat.push(row);
  }

  // 合并连续 agent 行（仅当来自旧 doc_lines 回退）；chat_turns 每轮已是整段
  const merged = [];
  for (const row of chat) {
    const prev = merged[merged.length - 1];
    const looksLikeDocLine = row.tag === "agent" && /^(\(空稿|DocVersion=|\[B\d+)/.test(row.text || "");
    if (looksLikeDocLine && prev && prev.tag === "agent" && prev._doc) {
      prev.text = `${prev.text}\n${row.text}`;
    } else {
      merged.push({ ...row, _doc: looksLikeDocLine });
    }
  }

  const feed = $("#feed");
  if (!merged.length) {
    feed.innerHTML = `<div class="chat-empty">发送问题后，对话会出现在这里</div>`;
  } else {
    feed.innerHTML = merged
      .map((row) => {
        const label = row.tag === "user" ? "你 · 原话" : "共享稿 / Agent";
        return `<div class="bubble ${row.tag}"><span class="bubble-label">${label}</span>${esc(row.text)}</div>`;
      })
      .join("");
    feed.scrollTop = feed.scrollHeight;
  }

  const mon = $("#eventMonitor");
  const ec = $("#eventCount");
  if (ec) ec.textContent = String(events.length);
  if (mon) {
    if (!events.length) {
      mon.innerHTML = `<div class="muted">暂无系统事件。</div>`;
    } else {
      mon.innerHTML = events
        .map((e) => `<div class="event-item">${esc(e.text)}</div>`)
        .join("");
      mon.scrollTop = mon.scrollHeight;
    }
  }
}

function paintMembers(ids) {
  const lines = (ids || []).map((id) => {
    const a = state.agentsById[id];
    const name = a?.display_name || id;
    const health = a?.health ? ` · ${a.health}` : "";
    return `● ${name}${health}\n  ${id}`;
  });
  const text = lines.join("\n") || "（无成员）\n点左侧「邀请入房」加入 ready Agent";
  $("#agentStatus").textContent = text;
  const ml = $("#memberList");
  if (ml) ml.textContent = lines.join("\n") || "（无）— 先选 ready Agent，再点「邀请入房」";
}

/** 填充「邀请」下拉：仅 health=ready 且尚未入房。 */
async function fillInviteAgents(invitedIds = []) {
  const invited = new Set(invitedIds || []);
  let all = [];
  try {
    const data = await api("/api/agents");
    all = data.items || [];
    state.agentsById = Object.fromEntries(all.map((a) => [a.agent_id, a]));
  } catch {
    all = [];
  }
  // 成员缓存更新后，重绘右侧（若已有 invited）
  if (invited.size) paintMembers([...invited]);

  const ready = all.filter((a) => a.health === "ready");
  const selectable = ready.filter((a) => !invited.has(a.agent_id));
  const sel = $("#inviteAgent");
  const hint = $("#inviteHint");

  if (!selectable.length) {
    const inRoom = ready.filter((a) => invited.has(a.agent_id)).length;
    sel.innerHTML = `<option value="">（无可邀请）</option>`;
    if (!all.length) {
      hint.textContent = "还没有 Agent。请到「成员」页创建，并绑定已探活模型。";
    } else if (!ready.length) {
      hint.textContent = `已有 ${all.length} 个 Agent，但绑定模型未 ready。请到「模型」探活成功后再邀请。`;
    } else if (inRoom === ready.length) {
      hint.textContent = "可用 ready Agent 都已在本房间（见右侧 AGENT）。";
    } else {
      hint.textContent = "暂无可邀请的 ready Agent。";
    }
    return;
  }

  sel.innerHTML = selectable
    .map(
      (a) =>
        `<option value="${esc(a.agent_id)}">${esc(a.display_name)} · ${esc(a.agent_id)} · ready</option>`
    )
    .join("");
  const skipped = all.length - ready.length;
  hint.textContent = skipped
    ? `候选 ${selectable.length} 个；点「邀请入房」才会入房。另有 ${skipped} 个未就绪。`
    : `候选 ${selectable.length} 个 ready — 必须点「邀请入房」才会出现在右侧。`;
}

function showModelError(m) {
  if (!m) {
    $("#modelError").textContent = "未选中模型。";
    return;
  }
  $("#modelError").textContent = [
    `ID: ${m.config_id}`,
    `名称: ${m.display_name}`,
    `API: ${m.base_url}`,
    `模型: ${m.model_id}`,
    `状态: ${m.status}`,
    `错误码: ${m.last_error_code || "（无）"}`,
    "",
    "—— 完整错误 ——",
    m.last_error || "（无）",
  ].join("\n");
}

async function refreshModels() {
  const data = await api("/api/models");
  const items = data.items || [];
  const readyN = items.filter((m) => m.status === "ready" && m.enabled).length;
  const failedN = items.filter((m) => m.status === "failed").length;
  const live = $("#healthLive");
  const liveTop = $("#livePill");
  if (readyN) {
    live.textContent = `up ${readyN}/${items.length}`;
    live.className = "pill ok";
    liveTop.textContent = "Live · up";
    liveTop.className = "pill ok";
  } else if (failedN) {
    live.textContent = `down ${failedN}`;
    live.className = "pill bad";
    liveTop.textContent = "Live · down";
    liveTop.className = "pill bad";
  } else {
    live.textContent = "未探活";
    live.className = "pill soft";
    liveTop.textContent = "Live";
    liveTop.className = "pill";
  }

  const box = $("#modelTable");
  box.innerHTML = items
    .map((m) => {
      const active = m.config_id === state.selectedModelId ? "active" : "";
      const st =
        m.status === "ready" ? "ok" : m.status === "failed" ? "bad" : "soft";
      return `<div class="row ${active}" data-id="${esc(m.config_id)}">
        <div><strong>${esc(m.display_name)}</strong><div class="muted">${esc(m.config_id)}</div></div>
        <div>${esc(m.model_id)}</div>
        <div class="pre mono" style="margin:0">${esc(m.base_url)}</div>
        <div><span class="pill ${st}">${esc(m.status)}</span></div>
        <div>${m.enabled ? "启用" : "禁用"}</div>
        <div class="ops">
          <button type="button" class="btn btn-tiny" data-act="probe">探活</button>
          <button type="button" class="btn btn-tiny" data-act="toggle">${m.enabled ? "禁用" : "启用"}</button>
          <button type="button" class="btn btn-tiny btn-danger" data-act="del">删</button>
        </div>
      </div>`;
    })
    .join("");

  box.querySelectorAll(".row").forEach((row) => {
    row.onclick = (e) => {
      if (e.target.closest("button")) return;
      state.selectedModelId = row.dataset.id;
      showModelError(items.find((x) => x.config_id === row.dataset.id));
      refreshModels();
    };
    row.querySelectorAll("button").forEach((btn) => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        const id = row.dataset.id;
        try {
          if (btn.dataset.act === "probe") {
            const r = await api(`/api/models/${id}/probe`, { method: "POST", body: "{}" });
            toast(r.probe?.ui_text || (r.ok ? "探活成功" : "探活失败"), r.ok ? "ok" : "err");
            state.selectedModelId = id;
            showModelError(r.model);
          } else if (btn.dataset.act === "toggle") {
            const m = items.find((x) => x.config_id === id);
            await api(`/api/models/${id}/enabled`, {
              method: "POST",
              body: JSON.stringify({ enabled: !m.enabled }),
            });
          } else if (btn.dataset.act === "del") {
            if (!confirm("确认删除 " + id + "？")) return;
            await api(`/api/models/${id}`, { method: "DELETE" });
            state.selectedModelId = null;
          }
          await refreshModels();
        } catch (err) {
          toast(String(err.message || err), "err");
        }
      };
    });
  });
}

async function refreshAgents() {
  const bindable = await api("/api/models/bindable");
  const bindItems = bindable.items || [];
  $("#aModel").innerHTML = bindItems.length
    ? bindItems
        .map(
          (m) =>
            `<option value="${esc(m.config_id)}">${esc(m.display_name)} · ${esc(m.model_id)} · ready</option>`
        )
        .join("")
    : `<option value="">（无 ready 模型，请先到「模型」探活）</option>`;

  const data = await api("/api/agents");
  const box = $("#agentTable");
  const items = data.items || [];
  if (!items.length) {
    box.innerHTML = `<div class="muted">暂无 Agent。创建后可在房间内邀请（需模型 ready）。</div>`;
    return;
  }
  box.innerHTML = items
    .map(
      (a) => `<div class="row agent-row" data-id="${esc(a.agent_id)}" style="cursor:default">
      <div><strong>${esc(a.display_name)}</strong><div class="muted">${esc(a.agent_id)}</div></div>
      <div class="muted">${esc(a.model_config_id)}</div>
      <div><span class="pill ${a.health === "ready" ? "ok" : "soft"}">${esc(a.health)}</span></div>
      <div class="ops">
        <button type="button" class="btn btn-tiny btn-danger" data-act="del">删除</button>
      </div>
    </div>`
    )
    .join("");
  box.querySelectorAll("button[data-act=del]").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.closest(".agent-row")?.dataset.id;
      if (!id || !confirm("确认删除 " + id + "？")) return;
      try {
        await api(`/api/agents/${id}`, { method: "DELETE" });
        toast("已删除", "ok");
        await refreshAgents();
        if (state.view === "room") await refreshRooms();
      } catch (e) {
        toast(String(e.message || e), "err");
      }
    };
  });
}

function bind() {
  $$(".act").forEach((b) => (b.onclick = () => setView(b.dataset.view)));

  $("#btnNewRoom").onclick = async () => {
    const title = prompt("房间标题", "演示房间");
    if (!title) return;
    try {
      const r = await api("/api/rooms", { method: "POST", body: JSON.stringify({ title }) });
      state.currentId = r.room.room_id;
      await api(`/api/rooms/${state.currentId}/enter`, { method: "POST", body: "{}" });
      toast("已创建房间", "ok");
      await refreshRooms();
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  };

  $("#roomSearch").oninput = renderRoomList;
  $("#btnRefreshRoom").onclick = () => state.currentId && loadSnapshot(state.currentId);

  $("#btnAsk").onclick = async () => {
    if (!state.currentId) return toast("请先进入房间", "err");
    const q = $("#askInput").value.trim();
    if (!q && !state.attachments.length) return;
    const btn = $("#btnAsk");
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "生成中…";
    const hasWs = !!($("#workspacePath")?.value || "").trim();
    toast(
      hasWs
        ? "Agent 工具循环运行中（可读目录/写文件）…"
        : "正在生成回复（未绑定工作区则无文件工具）…",
      ""
    );
    try {
      const r = await api(`/api/rooms/${state.currentId}/ask`, {
        method: "POST",
        body: JSON.stringify({
          question: q || "（见附件）",
          attachments: state.attachments.map((a) => ({
            name: a.name,
            content: a.content,
            encoding: a.encoding || "utf-8",
          })),
        }),
      });
      $("#askInput").value = "";
      state.attachments = [];
      renderAttachChips();
      await loadSnapshot(state.currentId);
      if (r.tool_steps?.length) {
        const mon = $("#eventMonitor");
        if (mon) {
          const extra = r.tool_steps
            .map(
              (s) =>
                `<div class="event-item">Tool ${esc(s.name)} → ${s.ok ? "ok" : "fail"}: ${esc(s.message)}</div>`
            )
            .join("");
          mon.insertAdjacentHTML("beforeend", extra);
          mon.scrollTop = mon.scrollHeight;
        }
      }
      if (r.w1_ok) {
        toast(r.ui_text || "已生成回复", "ok");
      } else if (r.w1_error) {
        toast(r.ui_text || r.w1_error, "err");
      } else {
        toast(r.ui_text || "已提问", "ok");
      }
    } catch (e) {
      toast(String(e.message || e), "err");
    } finally {
      btn.disabled = false;
      btn.textContent = prev || "发送";
      $("#askInput").focus();
    }
  };

  // Cursor 风格：Enter 发送，Shift+Enter 换行
  $("#askInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!$("#btnAsk").disabled) $("#btnAsk").click();
    }
  });

  function renderAttachChips() {
    const box = $("#attachChips");
    if (!box) return;
    if (!state.attachments.length) {
      box.innerHTML = "";
      return;
    }
    box.innerHTML = state.attachments
      .map(
        (a, i) =>
          `<span class="attach-chip" data-i="${i}"><span title="${esc(a.name)}">${esc(a.name)}</span>` +
          `<button type="button" data-rm="${i}" aria-label="移除">×</button></span>`
      )
      .join("");
    box.querySelectorAll("button[data-rm]").forEach((b) => {
      b.onclick = () => {
        state.attachments.splice(Number(b.dataset.rm), 1);
        renderAttachChips();
      };
    });
  }

  $("#fileAttach")?.addEventListener("change", async (e) => {
    const files = [...(e.target.files || [])];
    e.target.value = "";
    for (const f of files) {
      if (f.size > 1_500_000) {
        toast(`${f.name} 超过 1.5MB，已跳过`, "err");
        continue;
      }
      try {
        const buf = await f.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let isText = true;
        for (let i = 0; i < Math.min(bytes.length, 800); i++) {
          if (bytes[i] === 0) {
            isText = false;
            break;
          }
        }
        if (isText) {
          const text = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
          state.attachments.push({ name: f.name, content: text, encoding: "utf-8" });
        } else {
          let bin = "";
          bytes.forEach((c) => {
            bin += String.fromCharCode(c);
          });
          state.attachments.push({
            name: f.name,
            content: btoa(bin),
            encoding: "base64",
          });
        }
      } catch (err) {
        toast(`读取失败 ${f.name}: ${err}`, "err");
      }
    }
    renderAttachChips();
  });

  $("#btnWorkspace")?.addEventListener("click", async () => {
    if (!state.currentId) return toast("请先进入房间", "err");
    const path = ($("#workspacePath").value || "").trim();
    if (!path) return toast("请填写工作区路径", "err");
    try {
      const r = await api(`/api/rooms/${state.currentId}/workspace`, {
        method: "POST",
        body: JSON.stringify({ path }),
      });
      toast("工作区已绑定", "ok");
      await loadSnapshot(state.currentId);
      if (r.workspace_path) $("#workspacePath").value = r.workspace_path;
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  });

  $("#btnInvite").onclick = async () => {
    try {
      if (!state.currentId) {
        if (state.rooms.length) state.currentId = state.rooms[0].room_id;
        else return toast("请先新建或选择房间", "err");
      }
      const agent_id = $("#inviteAgent").value;
      if (!agent_id) return toast("没有可邀请的 ready Agent（需绑定已探活模型）", "err");
      await api(`/api/rooms/${state.currentId}/enter`, { method: "POST", body: "{}" });
      const r = await api(`/api/rooms/${state.currentId}/invite`, {
        method: "POST",
        body: JSON.stringify({ agent_id }),
      });
      // 立刻用接口返回刷新成员，避免只看到左侧候选、右侧仍空
      applyRoomPanel(r.room);
      await fillInviteAgents(r.room.invited_agent_ids || []);
      await loadSnapshot(state.currentId);
      toast("已邀请入房", "ok");
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  };

  $("#btnDemo").onclick = async () => {
    if (!state.currentId) return toast("请先进入房间", "err");
    try {
      await api(`/api/rooms/${state.currentId}/demo_review`, { method: "POST", body: "{}" });
      await loadSnapshot(state.currentId);
      toast("已推进演示审阅", "ok");
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  };

  $("#btnInterrupt").onclick = async () => {
    if (!state.currentId) return;
    await api(`/api/rooms/${state.currentId}/interrupt`, { method: "POST", body: "{}" });
    await loadSnapshot(state.currentId);
  };

  $("#btnResume").onclick = async () => {
    if (!state.currentId) return;
    await api(`/api/rooms/${state.currentId}/resume`, { method: "POST", body: "{}" });
    await loadSnapshot(state.currentId);
  };

  $("#btnDiscover").onclick = async () => {
    try {
      const r = await api("/api/models/discover", {
        method: "POST",
        body: JSON.stringify({ base_url: $("#mBase").value, api_key: $("#mKey").value }),
      });
      $("#modelList").innerHTML = (r.model_ids || [])
        .map((id) => `<option value="${esc(id)}"></option>`)
        .join("");
      if (r.model_ids?.length && !$("#mModel").value) $("#mModel").value = r.model_ids[0];
      $("#modelError").textContent = `拉取成功：\n${(r.model_ids || []).join("\n")}`;
      toast(r.ui_text || "已拉取模型", "ok");
    } catch (e) {
      toast(String(e.message || e), "err");
      $("#modelError").textContent = String(e.message || e);
    }
  };

  $("#btnAddModel").onclick = async () => {
    try {
      const r = await api("/api/models/add", {
        method: "POST",
        body: JSON.stringify({
          base_url: $("#mBase").value,
          api_key: $("#mKey").value,
          display_name: $("#mName").value,
          model_id: $("#mModel").value,
        }),
      });
      $("#mKey").value = "";
      state.selectedModelId = r.model.config_id;
      showModelError(r.model);
      await refreshModels();
      toast(r.probe?.ok ? "添加并探活成功" : "已保存但探活失败", r.probe?.ok ? "ok" : "err");
    } catch (e) {
      toast(String(e.message || e), "err");
      $("#modelError").textContent = String(e.message || e);
    }
  };

  $("#btnChat").onclick = async () => {
    if (!state.selectedModelId) return toast("请先选中模型", "err");
    try {
      const r = await api(`/api/models/${state.selectedModelId}/chat_test`, {
        method: "POST",
        body: JSON.stringify({ prompt: $("#chatPrompt").value }),
      });
      $("#chatOut").textContent = r.ok ? `[成功]\n${r.reply || ""}` : `[失败]\n${r.ui_text}\n${r.message}`;
      showModelError(r.model);
      await refreshModels();
      toast(r.ui_text || (r.ok ? "成功" : "失败"), r.ok ? "ok" : "err");
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  };

  $("#btnCreateAgent").onclick = async () => {
    const mid = $("#aModel").value;
    if (!mid) return toast("没有可绑定的 ready 模型", "err");
    try {
      await api("/api/agents", {
        method: "POST",
        body: JSON.stringify({
          display_name: $("#aName").value,
          model_config_id: mid,
        }),
      });
      toast("已创建 Agent", "ok");
      await refreshAgents();
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  };
}

/** Cursor 风格：拖拽分隔条调整左/右栏宽度，并持久化。 */
function bindSashes() {
  const tri = $("#roomTri");
  if (!tri || tri.dataset.sashBound) return;
  tri.dataset.sashBound = "1";

  const KEY = "mar.pane.widths";
  const MIN_L = 180;
  const MIN_R = 200;
  const MIN_M = 280;

  function apply(left, right) {
    tri.style.setProperty("--pane-left", `${left}px`);
    tri.style.setProperty("--pane-right", `${right}px`);
  }

  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "null");
    if (saved?.left && saved?.right) apply(saved.left, saved.right);
  } catch {
    /* ignore */
  }

  function startDrag(which, ev) {
    ev.preventDefault();
    const sash = ev.currentTarget;
    sash.classList.add("dragging");
    document.body.classList.add("sash-dragging");
    const startX = ev.clientX;
    const rect = tri.getBoundingClientRect();
    const startLeft = parseFloat(getComputedStyle(tri).getPropertyValue("--pane-left")) || 260;
    const startRight = parseFloat(getComputedStyle(tri).getPropertyValue("--pane-right")) || 300;

    const onMove = (e) => {
      const dx = e.clientX - startX;
      let left = startLeft;
      let right = startRight;
      if (which === "left") left = startLeft + dx;
      else right = startRight - dx;

      const maxLeft = Math.max(MIN_L, rect.width - right - MIN_M - 20);
      const maxRight = Math.max(MIN_R, rect.width - left - MIN_M - 20);
      left = Math.min(Math.max(left, MIN_L), maxLeft);
      right = Math.min(Math.max(right, MIN_R), maxRight);
      apply(left, right);
    };

    const onUp = () => {
      sash.classList.remove("dragging");
      document.body.classList.remove("sash-dragging");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      const left = parseFloat(getComputedStyle(tri).getPropertyValue("--pane-left")) || 260;
      const right = parseFloat(getComputedStyle(tri).getPropertyValue("--pane-right")) || 300;
      try {
        localStorage.setItem(KEY, JSON.stringify({ left: Math.round(left), right: Math.round(right) }));
      } catch {
        /* ignore */
      }
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  $("#sashLeft")?.addEventListener("pointerdown", (e) => startDrag("left", e));
  $("#sashRight")?.addEventListener("pointerdown", (e) => startDrag("right", e));
}

bind();
bindSashes();
setView("room");
