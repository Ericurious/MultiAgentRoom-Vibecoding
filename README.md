# MultiAgentRoom

> **Vibecoding 建出来的多模型聊天室。**  
> 不是又一个「单模型对话框套壳」，而是让多个大脑在同一间房里审稿、打补丁、做裁决——然后才允许碰你的磁盘。

**MultiAgentRoom**（多 Agent 聊天室）是 Windows 本机多智能体协作宿主。当前仓库有两条主线，**我们先把第一条做扎实**：

| 重点 | 是什么 | 状态 |
|------|--------|------|
| **① Vibecoding 建平台** | 用对话驱动把规格写成可运行宿主（Chat Review、门禁、工具、Web UI） | **进行中 · 本仓库主线** |
| **② 平台上的业务编排** | 在稳定宿主上叠具体业务流水线与技能 | 后续展开 |

英文版：[README.en.md](README.en.md) · Vibecoding 手记：[docs/vibecoding.md](docs/vibecoding.md)

| | |
|---|---|
| **产品** | MultiAgentRoom |
| **版本** | 0.1.0 |
| **环境** | Windows 10/11 · Python ≥ 3.11 |
| **形态** | 单机宿主；模型 API 可远端；**数据与进程留在本机** |
| **协议** | Chat Review v2（P0 + P1a/P1b）；P2 仅登记 |

---

## 先读这三份（导航）

| 文档 | 一句话 | 适合谁 |
|------|--------|--------|
| [**操作流程手册**](docs/workflow-ops.md) | 从装模型、建房到 Judge Approve / 工具落盘的逐步操作 | 使用者、演示、验收 |
| [**提示词目录**](docs/prompts-catalog.md) | W1–W4、J1、工具循环、Criti、纠错回灌、协作提示词全文 | 改协议、调模型行为 |
| [**Vibecoding 建台手记**](docs/vibecoding.md) | 本阶段怎么用对话继续完善平台、提示词骨架与优先级 | 继续写代码的协作者 |

规格与任务：[docs/spec.md](docs/spec.md) · [docs/tasks.md](docs/tasks.md)  
能力与功能清单：[多Agent聊天室能力项.md](多Agent聊天室能力项.md) · [多模型协作Agent功能清单.md](多模型协作Agent功能清单.md)

---

## 1. 平台在做什么

为开源与闭源大模型提供共用「聊天室」：

1. 你提出问题或任务（**原话钉选**，防多轮跑题）。  
2. 一个或多个 **Agent**（独立模型会话）在 **共享草稿** 上协作：审阅、局部补丁、合并或驳回。  
3. **裁决模型**（仅一个 ready 模型时 → 你来当裁判）发出 **Judge Approve**。  
4. 门禁打开后，才生成 **最终回复**，并可选调用本机工具做真实读写/交付。

```
提问 → 首答稿 → 审阅/补丁 → 评议合入或打回 → 确认轮 → Judge Approve → 最终回复
                              └─ 需要落盘时：工具循环 / 执行补充（步骤零→四）
```

风味一句话：**先把稿子辩清楚，再碰文件系统。**

---

## 2. Vibecoding 这一条线（当前重点）

本仓库大部分代码与文档，是按「愿景 → 规格 → 任务 → 实现 → 回写验收」长出来的。  
若你要继续完善平台，请从这里进：

- 读：[docs/vibecoding.md](docs/vibecoding.md)  
- 复制开场提示词：[docs/prompts-catalog.md#vibecoding-协作提示词](docs/prompts-catalog.md#vibecoding-协作提示词)  
- 按任务改：[docs/tasks.md](docs/tasks.md)

原始口述材料仍保留在 [`指导书.txt`](指导书.txt)，方便对照「当初为什么这样设计」。

---

## 3. 操作流程（摘要 + 深链）

完整逐步说明见 **[操作流程手册](docs/workflow-ops.md)**。下面是地图：

| 环节 | 你要做什么 | 深链 |
|------|------------|------|
| 启动 | 源码拉起 Web UI | [启动与环境](docs/workflow-ops.md#1-启动与环境) |
| 配模型 / Agent / 房 | 五步走到能提问 | [首次配置五步](docs/workflow-ops.md#2-首次配置五步) |
| Chat Review | 主范式：审稿与裁决 | [Chat Review 主流程](docs/workflow-ops.md#3-chat-review-主流程) |
| 绑工作区 | 真实写文件、反幻觉 | [工作区与工具落盘](docs/workflow-ops.md#4-工作区与工具落盘) |
| 执行补充 | 环境踩点 → 骨架分段 → 沙盒 → Criti | [步骤零→四](docs/workflow-ops.md#5-执行阶段补充步骤零四) |
| 门禁 / 降级 | 单模型用户裁判等 | [降级与门禁](docs/workflow-ops.md#6-降级与门禁) |

### 30 秒启动

```powershell
cd D:\CursorProject
$env:PYTHONPATH = "D:\CursorProject\src"
python -m multi_agent_room
```

打开 `http://127.0.0.1:8765/`。也可用 `启动 MultiAgentRoom.vbs` / `.bat`。

---

## 4. 提示词（摘要 + 深链）

运行时提示词以源码为准，目录页有可复制全文：

| 提示词 | 角色 | 深链 |
|--------|------|------|
| W1 | 工人首答 | [W1](docs/prompts-catalog.md#w1-首答) |
| W2 | 静默自检 | [W2](docs/prompts-catalog.md#w2-静默自检) |
| W3 | 审阅 Read/Patch | [W3](docs/prompts-catalog.md#w3-审阅响应) |
| W4 | 确认轮（仅 ChangedSet） | [W4](docs/prompts-catalog.md#w4-确认轮) |
| J1 | 评议 / JudgeApprove | [J1](docs/prompts-catalog.md#j1-评议) |
| 工具循环 | 绑工作区后的 Cursor 式落盘 | [工具系统提示](docs/prompts-catalog.md#agent-工具循环系统提示) |
| Criti | 业务审查（能跑 ≠ 正确） | [Criti](docs/prompts-catalog.md#criti-业务审查模板) |
| 纠错回灌 | 沙盒报错扔回 Agent | [纠错模板](docs/prompts-catalog.md#纠错回灌模板) |

实现文件：`src/multi_agent_room/prompts.py` · `tool_loop.py`

---

## 5. 运行时架构（极简）

| 层 | 职责 |
|----|------|
| Web 静态 UI | 房间、模型、Agent、对话、事件监控 |
| 本机 API | `/api/*` → Model / Agent / Room |
| 工具循环 | 真实文件工具；拒绝符号链接；表格用 `.csv` |
| 可选 tk | `--tk` 旧桌面壳 |

业务代码：`src/multi_agent_room/`

---

## 6. 数据与安全

| 项 | 约定 |
|----|------|
| 数据根 | `%AppData%\MultiAgentRoom\` |
| API Key | DPAPI；配置只存引用；**禁止进提示词明文** |
| 写入 | 须工作区绑定 + 授权 / write token |
| 符号链接 | 工具拒绝 |

---

## 7. 文档地图

| 文档 | 角色 |
|------|------|
| [docs/vibecoding.md](docs/vibecoding.md) | 建台哲学与协作提示词骨架 |
| [docs/workflow-ops.md](docs/workflow-ops.md) | 操作流程（逐步） |
| [docs/prompts-catalog.md](docs/prompts-catalog.md) | 提示词全文目录 |
| [docs/spec.md](docs/spec.md) | 正式规格与验收 |
| [docs/tasks.md](docs/tasks.md) | 任务状态 |
| [docs/user-guide.md](docs/user-guide.md) | 使用与打包（部分内容可能略滞后于 Web 默认） |
| [多Agent聊天室能力项.md](多Agent聊天室能力项.md) | 能力项总表 |
| [多模型协作Agent功能清单.md](多模型协作Agent功能清单.md) | 功能实现要点 |

---

## 8. 开发速查

```powershell
cd D:\CursorProject
$env:PYTHONPATH = "D:\CursorProject\src"
python -m unittest discover -s tests -q
```

---

## 9. 许可证

以仓库根目录 `LICENSE` 为准（若创建仓库时已选择）。

---

## 一句话

**先用 Vibecoding 把聊天室宿主磨利；让多个模型在共享稿上吵清楚，Judge Approve 之后，再允许它们碰你的文件。**
