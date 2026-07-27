# MultiAgentRoom

**MultiAgentRoom** is a Windows-local multi-agent collaboration host that implements the **Chat Review v2** protocol: multiple model-backed agents collaborate on a shared working document, with a clear separation between worker output and adjudication, before any gated delivery to the local filesystem.

| | |
|---|---|
| **Product** | MultiAgentRoom (多 Agent 聊天室) |
| **Version** | 0.1.0 |
| **Runtime** | Windows 10/11 · Python ≥ 3.11 |
| **Deployment** | Single-machine host; model APIs may be remote; **data and process remain on-device** |
| **Protocol scope** | P0 MVP + P1a/P1b as tracked in `docs/tasks.md`; P2 items are registered only |

Authoritative product requirements and acceptance criteria: [`docs/spec.md`](docs/spec.md).  
Implementation order and task status: [`docs/tasks.md`](docs/tasks.md).

> Chinese README: [`README.md`](README.md)

---

## 1. Purpose

MultiAgentRoom provides a shared “chat room” for open- and closed-source large language models:

1. The user states a question or task.
2. One or more **Agents** (each bound to an independent model session) collaborate on a **shared draft**: review, localized patches, merge, or reject.
3. An **adjudication model** (or the user, under single-model degradation) issues **Judge Approve**.
4. Only after that gate may the system produce a **final reply** and optionally invoke local tools (read/write files, run approved commands, deliver artifacts).

### 1.1 Primary interaction model (Chat Review v2)

```
User question (room idle until asked)
  → Role split: adjudicator vs worker (when multiple models are available)
  → Worker: first answer → silent self-check
  → Material issues: patches → adjudicator merge → confirmation (ChangedSet only)
  → Clean convergence → Judge Approve → final reply / executable path
  → (Optional) user-triggered deliver / execute (requires prior approval)
```

### 1.2 Protocol constraints

| Constraint | Description |
|------------|-------------|
| Session isolation | Agents must not share vendor-side caches; private thought stores are not readable by peers |
| Whiteboard channel | Inter-agent exchange is limited to the shared draft and room event bus |
| First answer ≠ adjudicator | Compared by **model config ID**; single ready model uses **Degradation A** (user adjudicates) |
| Approval layering | Silent check / silent agree / merge ≠ **Judge Approve**; only Judge Approve opens Final / execution gates |
| Original utterance pin | Adjudication context must retain the user’s pinned question to limit multi-turn drift |

Non-goals for this phase (cloud multi-tenant SaaS, remote server file mutation as primary mode, full A2A dead-letter systems, etc.) are listed in `docs/spec.md` §1.2.1.

---

## 2. Capability roadmap

| Phase | Objective | Status (vs `docs/tasks.md`) |
|-------|-----------|-------------------------------|
| **P0** | Multi-model Chat Review through final reply (M1–M8, ENV, GATE, SEC keys) | Complete |
| **P1a** | Local tools, memory basics, authorized delivery (M9/M10/M12) | Complete |
| **P1b** | Lightweight execution (M11) | Complete |
| **P2** | Browser hardening, snapshot ops, Excel / games, etc. | Title-level registration only |

---

## 3. Architecture (runtime)

Default UI is a **local Web front end** served with an in-process HTTP API on `127.0.0.1` (Cat Café–style soft UI). Business logic remains in the Python service layer under `src/multi_agent_room/`.

| Layer | Responsibility |
|-------|----------------|
| Web static UI | Rooms, models, agents, chat, tool activity |
| Local API | `/api/*` wrapping Model / Agent / Room services |
| Tool loop | Cursor-style `dir_list` / `glob_search` / `file_read` / `file_write` / `search_replace` / `file_delete` when a workspace is bound (real files only; **symlinks rejected**) |
| Optional tk shell | Legacy desktop UI via `--tk` |

When a workspace directory is bound, chat replies may enter an **agent tool loop** so directory reads and file writes land on disk instead of being narrated only.

---

## 4. Quick start

### 4.1 Prerequisites

- Windows 10 or 11  
- Python 3.11 or newer on `PATH`  
- Network access to your OpenAI-compatible model endpoint (e.g. DeepSeek)

### 4.2 Launch (source, recommended)

```powershell
cd D:\CursorProject
$env:PYTHONPATH = "D:\CursorProject\src"
python -m multi_agent_room
```

Open `http://127.0.0.1:8765/` in a browser.

Convenience launchers (source-first):

- `启动 MultiAgentRoom.vbs`
- `启动 MultiAgentRoom.bat`

| Mode | Command |
|------|---------|
| Web UI (default) | `python -m multi_agent_room` |
| Legacy tk UI | `python -m multi_agent_room --tk` |
| Smoke (no UI) | `python -m multi_agent_room --smoke` |

> Packaged `dist\...\MultiAgentRoom.exe`, if present, reflects a **build snapshot** and may lag the current source UI. Prefer source launch for development. Rebuild with `scripts\build_exe.ps1` only when needed.

### 4.3 First-run checklist

1. **Models** — Add API base URL and API key (OpenAI-compatible); discover models and probe until status is `ready`.  
2. **Agents** — Create an agent bound only to an **enabled + ready** model.  
3. **Workspace** — Bind a local directory (write boundary for tools and delivery).  
4. **Room** — Create a room, invite a ready agent, ask a question (pinned).  
5. **Chat Review** — Review / merge / confirm → **Judge Approve** → final reply; deliver when required.

Probe failures retain a full error panel for diagnosis. Models that are not `ready` cannot be bound as room brains.

---

## 5. Data, secrets, and safety

| Item | Convention |
|------|------------|
| Data root | `%AppData%\MultiAgentRoom\` (config / data / logs) |
| API keys | Windows DPAPI `SecretStore`; config stores `apiKeyRef` only (**no plaintext keys in files**) |
| Workspace | Per-room local path; writes require workspace binding and authorization / write token gates |
| Symlinks | File tools refuse symlink paths; writes use real files only |

---

## 6. Documentation map

| Document | Role |
|----------|------|
| [`docs/spec.md`](docs/spec.md) | Vision, modules, acceptance, non-goals, glossary |
| [`docs/tasks.md`](docs/tasks.md) | Implementation order and task checklist |
| [`docs/user-guide.md`](docs/user-guide.md) | Usage and packaging notes (may lag Web-default UI) |
| [`docs/user-inputs-reserve.md`](docs/user-inputs-reserve.md) | Archived product-direction inputs from design chats |

---

## 7. Development

```powershell
cd D:\CursorProject
$env:PYTHONPATH = "D:\CursorProject\src"
python -m unittest discover -s tests -q
```

- Package entry: `python -m multi_agent_room` → `multi_agent_room.app:main`  
- Primary code: `src/multi_agent_room/`  
- Web assets: `src/multi_agent_room/web_static/`  
- Project metadata: `pyproject.toml` (stdlib runtime; optional PyInstaller extras under `[project.optional-dependencies].build`)

---

## 8. License

If a license was selected when creating the GitHub repository (e.g. Apache-2.0), the repository root `LICENSE` file is authoritative. Add or update `LICENSE` before public redistribution if required by your organization.

---

## 9. Summary

**MultiAgentRoom is a Windows-local multi-model chat room: agents collaborate on a shared draft under Chat Review v2, and only Judge Approve unlocks final reply and gated local delivery.**
