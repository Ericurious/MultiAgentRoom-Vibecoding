# Vibecoding 建台手记

> 本仓库当前阶段的**第一个重点**：用 **Vibecoding**（对话驱动、规格先行、快速落地）把「多模型共享聊天室」从愿景写成可运行的 Windows 本机平台。  
> 第二个重点（平台上的业务/内容工作流）会在宿主稳定后展开；现在先把房间、Chat Review、工具与门禁做扎实。

---

## 1. 什么是本项目里的 Vibecoding

不是「随便聊两句就交作业」，而是一条可复盘的回路：

```
愿景与约束（指导书 / 能力项）
  → 写成可验收规格（docs/spec.md）
  → 拆成任务清单（docs/tasks.md）
  → 对话实现 + 本地跑通
  → 对照验收回写规格/任务状态
```

本仓库里能直接看到的 Vibecoding 产物包括：

| 产物 | 路径 | 说明 |
|------|------|------|
| 原始指导与愿景补丁 | [`指导书.txt`](../指导书.txt) | 早期口述需求、执行五段、Chat Review 边界 |
| 能力项清单 | [`多Agent聊天室能力项.md`](../多Agent聊天室能力项.md) | 聊天室形态 + Chat Review + 执行补充 |
| 功能清单 | [`多模型协作Agent功能清单.md`](../多模型协作Agent功能清单.md) | 实现要点级功能表 |
| 正式规格 | [`docs/spec.md`](spec.md) | 模块、验收、非目标 |
| 任务与状态 | [`docs/tasks.md`](tasks.md) | P0 / P1 落地顺序 |
| 提示词落点 | [`docs/prompts-catalog.md`](prompts-catalog.md) · `src/multi_agent_room/prompts.py` | 工人 / 评议 / 工具循环 |
| 操作流程 | [`docs/workflow-ops.md`](workflow-ops.md) | 从装模型到 Judge Approve / 工具落盘 |

---

## 2. 建议的对话节奏（可复用提示词骨架）

下列提示词用于**继续完善本平台**时的人机协作，不是房间内 Agent 的运行时提示词。运行时提示词见 [`prompts-catalog.md`](prompts-catalog.md)。

### 2.1 对齐范围（开场）

```text
你在维护 D:\CursorProject 的 MultiAgentRoom。
当前阶段重点：完善 Windows 本机多 Agent 聊天室宿主（Chat Review v2 + 本机工具），
不要把重心切到无关业务产品。
先读：docs/spec.md、docs/tasks.md、README.md；改动前说明会影响哪条验收项。
```

### 2.2 按任务落地

```text
对照 docs/tasks.md 中的任务 <编号>：
1) 用三句话说明现状与缺口；
2) 给出最小改动方案（文件列表）；
3) 实现并补充/更新测试；
4) 回写 tasks 状态，并注明未做的边界。
不要扩大到 P2 标题项，除非我显式要求。
```

### 2.3 修协议 / 门禁

```text
变更必须保持：会话隔离、首答模型 ≠ 评判模型（多模型时）、
Judge Approve 才开最终回复/执行门禁、原话钉选不可静默改写。
若触及提示词，同步更新 docs/prompts-catalog.md 与 src/multi_agent_room/prompts.py。
```

### 2.4 修工具落盘

```text
工作区绑定后的回复必须走工具循环；禁止口头声称已写入。
拒绝符号链接；表格用 .csv。对照 tool_loop.py 与 prompts-catalog 中的 Agent 工具系统提示。
```

---

## 3. 当前完善优先级（平台侧）

1. **Web 默认体验**：房间 / 模型 / Agent / 提问 / 事件监控可闭环。  
2. **Chat Review 主路径**：首答 → 审阅/补丁 → 评判 → 确认轮 → Judge Approve。  
3. **工作区工具循环**：真实读写、反幻觉、附件 inbox。  
4. **规格与任务诚实**：完成项写「通过」，缺口留在 `docs/tasks.md`，不粉饰。  
5. **文档可导航**：README 链到流程与提示词，方便后来者继续 Vibecoding。

---

## 4. 与「第二个重点」的关系

| 重点 | 状态 | 说明 |
|------|------|------|
| **① Vibecoding 建平台** | **进行中（本仓库主线）** | 把聊天室宿主做完整、可演示、可审计 |
| **② 平台上的内容/业务编排** | 后续 | 在稳定宿主上叠具体业务流水线、更多技能包等 |

先把 ① 做实，② 才有可靠底座。
