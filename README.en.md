# MultiAgentRoom

> **A multi-model chat room grown by Vibecoding.**  
> Not a single-model chat wrapper—agents review a shared draft, patch locally, adjudicate, and only then may touch your disk.

**MultiAgentRoom** is a Windows-local multi-agent collaboration host. This repository has two tracks; **we are finishing the first first**:

| Focus | What | Status |
|-------|------|--------|
| **① Vibecoding the platform** | Spec-driven host: Chat Review, gates, tools, Web UI | **In progress · main line** |
| **② Workloads on the platform** | Business pipelines / skills on a stable host | Later |

Chinese README (canonical for this phase): [`README.md`](README.md)

| | |
|---|---|
| **Product** | MultiAgentRoom |
| **Version** | 0.1.0 |
| **Runtime** | Windows 10/11 · Python ≥ 3.11 |
| **Deployment** | On-device host; model APIs may be remote |
| **Protocol** | Chat Review v2 (P0 + P1a/P1b); P2 titles only |

---

## Start here (navigation)

| Doc | One-liner |
|-----|-----------|
| [**Vibecoding notes**](docs/vibecoding.md) | How this platform was / should be built via dialogue |
| [**Operations workflow**](docs/workflow-ops.md) | Step-by-step: models → room → Judge Approve → tools *(Chinese)* |
| [**Prompts catalog**](docs/prompts-catalog.md) | W1–W4, J1, tool-loop, Criti, fix-up templates *(Chinese)* |
| [`docs/spec.md`](docs/spec.md) | Formal requirements |
| [`docs/tasks.md`](docs/tasks.md) | Implementation checklist |

Deep capability lists (Chinese): [`多Agent聊天室能力项.md`](多Agent聊天室能力项.md) · [`多模型协作Agent功能清单.md`](多模型协作Agent功能清单.md)

---

## 1. What the platform does

1. You ask a question (pinned utterance).  
2. Agents (isolated model sessions) collaborate on a **shared draft**.  
3. An adjudicator (or **you**, under single-model Degradation A) issues **Judge Approve**.  
4. Only then: final reply and optional gated local tools.

```
Ask → first answer → review/patches → verdict → confirm → Judge Approve → final
                         └─ if landing artifacts: tool loop / exec steps 0–4
```

Flavor: **argue the draft clear before touching the filesystem.**

---

## 2. Vibecoding track (current focus)

Most of this repo grew as vision → spec → tasks → implement → re-verify.  
To keep improving the **host**, start at [`docs/vibecoding.md`](docs/vibecoding.md) and the collaboration prompts in [`docs/prompts-catalog.md`](docs/prompts-catalog.md).

---

## 3. Operations (summary)

Full steps: [`docs/workflow-ops.md`](docs/workflow-ops.md)

```powershell
cd D:\CursorProject
$env:PYTHONPATH = "D:\CursorProject\src"
python -m multi_agent_room
```

Open `http://127.0.0.1:8765/`.

| Step | Link |
|------|------|
| Launch | [§1](docs/workflow-ops.md#1-启动与环境) |
| First-run five steps | [§2](docs/workflow-ops.md#2-首次配置五步) |
| Chat Review | [§3](docs/workflow-ops.md#3-chat-review-主流程) |
| Workspace / tools | [§4](docs/workflow-ops.md#4-工作区与工具落盘) |
| Exec supplement 0–4 | [§5](docs/workflow-ops.md#5-执行阶段补充步骤零四) |

---

## 4. Prompts (summary)

Full text: [`docs/prompts-catalog.md`](docs/prompts-catalog.md)

| Prompt | Role |
|--------|------|
| W1–W4 | Worker: answer / silent check / review / confirm |
| J1 | Adjudicator (incl. JudgeApprove) |
| Tool-loop system | Cursor-style real writes when workspace bound |
| Criti | Business review (runs ≠ correct) |

Code: `src/multi_agent_room/prompts.py`, `tool_loop.py`

---

## 5. Architecture / safety / license

See Chinese [`README.md`](README.md) §§5–8 for tables (data root, DPAPI keys, no symlink writes, `LICENSE`).

Tests:

```powershell
$env:PYTHONPATH = "D:\CursorProject\src"
python -m unittest discover -s tests -q
```

---

## One-liner

**Vibecode the chat-room host sharp first; let models fight on a shared draft, and only after Judge Approve may they touch your files.**
