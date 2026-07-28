# 操作流程手册

面向使用者与继续 Vibecoding 的协作者。默认 UI 为 **本机 Web**（`http://127.0.0.1:8765/`）；旧版 tk 见文末。

相关文档：[`vibecoding.md`](vibecoding.md) · [`prompts-catalog.md`](prompts-catalog.md) · [`spec.md`](spec.md) · [`user-guide.md`](user-guide.md)

---

## 目录

1. [启动与环境](#1-启动与环境)
2. [首次配置五步](#2-首次配置五步)
3. [Chat Review 主流程](#3-chat-review-主流程)
4. [工作区与工具落盘](#4-工作区与工具落盘)
5. [执行阶段补充（步骤零→四）](#5-执行阶段补充步骤零四)
6. [降级与门禁](#6-降级与门禁)
7. [故障排查速查](#7-故障排查速查)

---

## 1. 启动与环境

### 1.1 推荐：源码启动

```powershell
cd D:\CursorProject
$env:PYTHONPATH = "D:\CursorProject\src"
python -m multi_agent_room
```

浏览器打开：`http://127.0.0.1:8765/`

便捷启动器（优先源码）：`启动 MultiAgentRoom.vbs` / `启动 MultiAgentRoom.bat`

| 模式 | 命令 |
|------|------|
| Web（默认） | `python -m multi_agent_room` |
| 旧 tk UI | `python -m multi_agent_room --tk` |
| 冒烟 | `python -m multi_agent_room --smoke` |

数据根目录：`%AppData%\MultiAgentRoom\`（配置 / 日志 / 密钥引用）。

---

## 2. 首次配置五步

```
① 模型  →  ② Agent  →  ③ 工作区（可选但推荐）  →  ④ 房间与邀请  →  ⑤ 提问进入 Chat Review
```

### 步骤 ① 添加模型

1. 打开「模型」页。  
2. 填写显示名、**Base URL**、**API Key**、模型 ID（OpenAI 兼容）。  
3. 保存 → 发现/探活，直到状态为 **`ready`**。  
4. 非 `ready` 的模型不能作为房间大脑。

> 对应执行补充「步骤零」。密钥经 DPAPI 保管，配置中只存引用，不进提示词明文。

### 步骤 ② 创建 Agent

1. 打开「Agent」页。  
2. 创建成员并绑定一个 **已启用且 ready** 的模型。  
3. 多模型场景建议至少：工人向 + 评议向（不同模型配置 ID）。

### 步骤 ③ 绑定工作区

1. 在房间设置中绑定本机目录。  
2. 此目录是工具读写与交付的边界。  
3. 绑定后，提问回复可走 **工具循环**（真实落盘），而不仅是口头描述。

### 步骤 ④ 建房并邀请

1. 新建房间。  
2. 从 ready Agent 列表邀请进房。  
3. 进入房间；可在右侧「事件监控」观察协议事件。

### 步骤 ⑤ 提问（钉选）

1. 在中间栏输入问题；**Enter 发送**，Shift+Enter 换行。  
2. 用户原话会被 **钉选**，作为后续审阅/评议的锚点。  
3. 可附带文件；附件落入工作区 `_mar_inbox/`。

---

## 3. Chat Review 主流程

这是房间的**主范式**（平台首先是沟通与评审场所）。

```
用户提问（原话钉选）
  → 角色/职责：工人 vs 评议（多模型时首答模型 ≠ 评判模型）
  → 工人首答（共享稿）
  → 静默自检 / 审阅：已读；无实质问题则沉默；有问题则局部 PATCH
  → 评议：合入 / 合并 / R1·R2·R3 / …
  → （合入后）确认轮：仅可动 ChangedSet
  → 收敛 → Judge Approve
  → 最终回复；（可选）交付 / 执行
```

| 阶段 | 谁在做事 | 用户可见要点 |
|------|----------|--------------|
| 提问 | 用户 | 原话钉在上下文中 |
| 首答 | 工人 Agent | 共享稿出现在对话/稿区 |
| 自检/审阅 | 工人 | 事件中可见 Read / Patch / SilentCheck |
| 评议 | 评议 Agent 或用户（降级 A） | Accept / Merge / R1–R3 / JudgeApprove |
| 确认轮 | 审阅工人 | 仅 ChangedSet 可改 |
| 通过 | 门禁 | 最终回复槽位打开 |

详细协议与能力项：[`多Agent聊天室能力项.md`](../多Agent聊天室能力项.md) §1.1 · [`spec.md`](spec.md)

运行时提示词：[`prompts-catalog.md`](prompts-catalog.md)（W1–W4 / J1）

---

## 4. 工作区与工具落盘

当房间**已绑定工作区**且走自动回复路径时：

1. 系统授予短时 `write_token`（聊天写权限）。  
2. 进入 **Agent 工具循环**（对齐 Cursor）：`dir_list` / `glob_search` / `file_read` / `file_write` / `search_replace` / `file_delete`。  
3. 必须真实调用工具；口头「已写入」而无成功 `file_write` 会被反幻觉逻辑拦截/纠正。  
4. **拒绝符号链接**；表格请用 **`.csv`**，不要用 `.xlsx`。  
5. 写完应用 `dir_list` / `file_read` 核实，再向用户汇报。

提示词全文：[`prompts-catalog.md#agent-工具循环系统提示`](prompts-catalog.md#agent-工具循环系统提示)

界面提示：右侧事件监控中可看到 `ToolCall` 等；完成后常有「工具循环完成：调用 N 次（其中写入 M 次）」类提示。

---

## 5. 执行阶段补充（步骤零→四）

> **定位**：需要「可验证产物落地」时的实施补充，**不取代** Chat Review。  
> 爬取仅为示例；小游戏、Excel/CSV、读文档总结等同样适用。

| 步骤 | 名称 | 做什么 |
|------|------|--------|
| **零** | 模型纳管 | Base URL / Key / 探活 → ready |
| **一** | 环境检查与调方案 | 踩点、依赖、权限；发现障碍则改技术方案 |
| **二** | 解构与骨架分段 | 目标 / 清单 / 约束 → 自顶向下，禁止一次甩千行 |
| **三** | 静态检查与沙盒自测 | 能跑关；失败把报错回灌给 Agent 修改 |
| **四** | 业务审查（Criti） | 能跑 ≠ 正确；对照「目标 vs 结果」；通过才交付，否则 re-plan |

Criti 审查提示词模板见 [`prompts-catalog.md#criti-业务审查模板`](prompts-catalog.md#criti-业务审查模板)。

关系图：

```
【主】Chat Review v2 → … → Judge Approve → 最终回复
                 │
                 └─ 需落地时 →【补】步骤零→四 → 再回到评判/确认门禁
```

完整能力表：[`多Agent聊天室能力项.md`](../多Agent聊天室能力项.md) §13

---

## 6. 降级与门禁

| 规则 | 行为 |
|------|------|
| 多模型 | 本轮产生过首答的模型配置 **不得** 任评判 |
| 单 ready 模型 | **降级 A**：用户当评判；禁止该模型自己宣布通过 |
| 静默检查 / 合并 | ≠ Judge Approve |
| 仅 Judge Approve | 打开最终回复与（受控）执行/交付门禁 |
| 密钥 | 不进提示词明文；红线见测试与 `redact` |

---

## 7. 故障排查速查

| 现象 | 处理 |
|------|------|
| 页面打不开 | 确认进程已启动；访问 `127.0.0.1:8765`；看 `%AppData%\MultiAgentRoom\logs\` |
| 模型不是 ready | 检查 Base URL / Key / 网络；看探活错误面板 |
| 邀请列表空 | 先有 ready Agent；刷新 Agent 列表 |
| 声称写了文件但磁盘空 | 确认已绑定工作区且走了工具循环；**重启源码进程**后再试；看事件里是否有 ToolCall |
| 想写 xlsx | 改用 csv；工具侧会拒绝 xlsx |
| 交付失败 | 先完成 Judge Approve / 授权交付范围 |

更完整的打包与双击启动说明（部分内容可能略滞后于 Web 默认）：[`user-guide.md`](user-guide.md)

---

## 附录：旧版 tk UI

```powershell
python -m multi_agent_room --tk
```

顶栏：聊天室 | 模型配置 | Agent 成员 | 工作区。主路径仍是：提问钉选 → 审阅/评判台 → JudgeApprove → 交付。
