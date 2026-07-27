# MultiAgentRoom

**MultiAgentRoom**（多 Agent 聊天室）是一款运行于 Windows 本机的多智能体协作宿主，实现 **Chat Review v2** 协议：多个由大模型驱动的 Agent 在共享草稿上协同审阅与修订，明确区分「工人输出」与「裁决」，仅在通过门禁后，才允许向本地文件系统进行受控交付。

| | |
|---|---|
| **产品** | MultiAgentRoom（多 Agent 聊天室） |
| **版本** | 0.1.0 |
| **运行环境** | Windows 10/11 · Python ≥ 3.11 |
| **部署形态** | 单机宿主；模型 API 可走远端；**数据与进程留在本机** |
| **协议范围** | 见 `docs/tasks.md`：P0 MVP + P1a/P1b；P2 仅登记标题 |

产品需求与验收标准以 [`docs/spec.md`](docs/spec.md) 为准。  
实现顺序与任务状态见 [`docs/tasks.md`](docs/tasks.md)。

> English README: [`README.en.md`](README.en.md)

---

## 1. 产品目标

为开源与闭源大模型提供共用「聊天室」：

1. 用户提出问题或任务。
2. 一个或多个 **Agent**（各自绑定独立模型会话）在 **共享草稿** 上协作：审阅、局部补丁、合并或驳回。
3. **裁决模型**（单模型降级时由用户裁决）发出 **Judge Approve（裁决通过）**。
4. 仅在该门禁之后，系统才可生成 **最终回复**，并可选调用本机工具（读写文件、执行已授权命令、交付产物）。

### 1.1 主交互模型（Chat Review v2）

```
用户提问（房间在提问前保持空闲）
  → 角色拆分：裁决者 vs 工人（多模型可用时）
  → 工人：首答 → 静默自检
  → 有实质问题：补丁 → 裁决者合并 → 确认（仅 ChangedSet）
  → 收敛干净 → Judge Approve → 最终回复 / 可执行路径
  → （可选）用户触发交付 / 执行（须已通过裁决）
```

### 1.2 协议约束

| 约束 | 说明 |
|------|------|
| 会话隔离 | Agent 不得共享厂商侧缓存；私有思维存储对其他 Agent 不可读 |
| 白板通道 | Agent 间交换限于共享草稿与房间事件总线 |
| 首答 ≠ 裁决者 | 按 **模型配置 ID** 比较；仅一个 ready 模型时走 **降级 A**（用户裁决） |
| 批准分层 | 静默检查 / 静默同意 / 合并 ≠ **Judge Approve**；仅后者打开最终回复与执行门禁 |
| 原话钉住 | 裁决上下文须保留用户钉住的原问题，抑制多轮漂移 |

本阶段非目标（云端多租户 SaaS、以远端服务器改文件为主模式、完整 A2A 死信系统等）见 `docs/spec.md` §1.2.1。

---

## 2. 能力路线图

| 阶段 | 目标 | 状态（对照 `docs/tasks.md`） |
|------|------|------------------------------|
| **P0** | 多模型 Chat Review 直至最终回复（M1–M8、ENV、GATE、SEC 等） | 已完成 |
| **P1a** | 本机工具、基础记忆、授权交付（M9/M10/M12） | 已完成 |
| **P1b** | 轻量执行（M11） | 已完成 |
| **P2** | 浏览器加固、快照运维、Excel / 游戏等 | 仅标题登记 |

---

## 3. 运行时架构

默认界面为 **本机 Web 前端**，由进程内 HTTP API 提供服务（监听 `127.0.0.1`，猫咖风格软 UI）。业务逻辑位于 Python 服务层 `src/multi_agent_room/`。

| 层级 | 职责 |
|------|------|
| Web 静态界面 | 房间、模型、Agent、对话、工具活动 |
| 本机 API | `/api/*`，封装 Model / Agent / Room 服务 |
| 工具循环 | 绑定工作区后，提供类 Cursor 的 `dir_list` / `glob_search` / `file_read` / `file_write` / `search_replace` / `file_delete`（仅真实文件；**拒绝符号链接**） |
| 可选 tk 壳 | 通过 `--tk` 启用旧版桌面 UI |

绑定工作区目录后，对话回复可进入 **Agent 工具循环**，使目录读取与文件写入落到磁盘，而非仅口头描述。

---

## 4. 快速开始

### 4.1 环境要求

- Windows 10 或 11  
- 已加入 `PATH` 的 Python 3.11 或更高版本  
- 可访问 OpenAI 兼容的模型接口（如 DeepSeek）

### 4.2 启动（推荐源码方式）

```powershell
cd D:\CursorProject
$env:PYTHONPATH = "D:\CursorProject\src"
python -m multi_agent_room
```

浏览器打开 `http://127.0.0.1:8765/`。

便捷启动器（优先源码）：

- `启动 MultiAgentRoom.vbs`
- `启动 MultiAgentRoom.bat`

| 模式 | 命令 |
|------|------|
| Web UI（默认） | `python -m multi_agent_room` |
| 旧版 tk UI | `python -m multi_agent_room --tk` |
| 冒烟测试（无界面） | `python -m multi_agent_room --smoke` |

> 若存在打包产物 `dist\...\MultiAgentRoom.exe`，其反映的是**某次构建快照**，可能落后于当前源码界面。开发请优先用源码启动；仅在需要时用 `scripts\build_exe.ps1` 重新打包。

### 4.3 首次使用检查清单

1. **模型** — 添加 API Base URL 与 API Key（OpenAI 兼容）；发现模型并探测，直至状态为 `ready`。  
2. **Agent** — 仅绑定「已启用且 ready」的模型创建 Agent。  
3. **工作区** — 绑定本机目录（工具与交付的写入边界）。  
4. **房间** — 创建房间，邀请 ready Agent，提出问题（钉住）。  
5. **Chat Review** — 审阅 / 合并 / 确认 → **Judge Approve** → 最终回复；需要时再交付。

探测失败会保留完整错误面板便于排查。非 `ready` 的模型不能作为房间大脑绑定。

---

## 5. 数据、密钥与安全

| 项目 | 约定 |
|------|------|
| 数据根目录 | `%AppData%\MultiAgentRoom\`（配置 / 数据 / 日志） |
| API 密钥 | Windows DPAPI `SecretStore`；配置中仅存 `apiKeyRef`（**文件中不存明文密钥**） |
| 工作区 | 按房间绑定本机路径；写入须经工作区绑定与授权 / write token 门禁 |
| 符号链接 | 文件工具拒绝符号链接路径；仅写入真实文件 |

---

## 6. 文档索引

| 文档 | 作用 |
|------|------|
| [`docs/spec.md`](docs/spec.md) | 愿景、模块、验收、非目标、术语表 |
| [`docs/tasks.md`](docs/tasks.md) | 实现顺序与任务清单 |
| [`docs/user-guide.md`](docs/user-guide.md) | 使用与打包说明（可能略滞后于默认 Web UI） |
| [`docs/user-inputs-reserve.md`](docs/user-inputs-reserve.md) | 设计讨论中归档的产品方向输入 |

---

## 7. 开发

```powershell
cd D:\CursorProject
$env:PYTHONPATH = "D:\CursorProject\src"
python -m unittest discover -s tests -q
```

- 包入口：`python -m multi_agent_room` → `multi_agent_room.app:main`  
- 主代码：`src/multi_agent_room/`  
- Web 资源：`src/multi_agent_room/web_static/`  
- 项目元数据：`pyproject.toml`（运行时以标准库为主；可选 PyInstaller 依赖见 `[project.optional-dependencies].build`）

---

## 8. 许可证

本仓库若已在 GitHub 创建时选择许可证（例如 Apache-2.0），以仓库根目录 `LICENSE` 文件为准。若尚无 `LICENSE`，公开分发前请按组织要求补全。

---

## 9. 一句话总结

**MultiAgentRoom 是 Windows 本机多模型聊天室：Agent 在 Chat Review v2 下围绕共享草稿协作，只有 Judge Approve 之后才会解锁最终回复与受控本机交付。**
