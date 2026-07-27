# 任务清单与模块详细设计（tasks.md）

| 项 | 内容 |
|----|------|
| 文档位置 | `docs/tasks.md` |
| 依据 | `docs/spec.md`（Chat Review v2） |
| 范围 | 本轮只出清单与设计，**不写代码** |
| 模块顺序 | **正文=实现序**（与 spec 目录序可不一致）；总表与章节号一致 |
| 高风险修复 | 已对齐 spec：路径·静默通过/补丁确认、评议通过分层、工人静默检查、白板隔离、ChangedSet；详见 `docs/review-findings.md` §1 |
| P0 功能缺失 | 已补：T-AGENT/PROTO/BUS 细设计、冲突不兼容、全文重写、净变更、GATE E2E 勾选；详见 `docs/review-findings.md` §2 |
| P1/细则/结构 | 已补：MCP 宿主、钩子表、授权落盘状态、ENV-07a、各模块细则、M8a/b、SEC=M1 Key、P0 须自动 Agent；详见 findings §2–§4 |

---

## 0. 总览

### 0.1 任务总表（按**实现序**＝正文顺序）

| 序号 | 模块 | 期次 | 任务包 ID | 状态 |
|------|------|------|-----------|------|
| 0 | ENV 运行与部署骨架（含 P0 最小日志） | P0 | T-ENV | **已完成（P0）** |
| 1 | M1 模型配置与纳管 | P0 | T-M1 | **已完成（P0）** |
| 2 | M3 Agent 身份 / 隔离 / 职责 | P0 | T-M3 | **已完成（P0）** |
| 3 | M2 共享聊天室壳层 | P0 | T-M2 | **已完成（P0）** |
| 4a | **M8a** phase 状态机骨架 | P0 | T-M8a | **已完成（P0）** |
| 4b | 事件总线 | P0 | T-BUS | **已完成（P0）** |
| 4c | 协议归一化 | P0 | T-PROTO | **已完成（P0）** |
| 4d | 工人 Agent 运行时 | P0 | T-AGENT | **已完成（P0）** |
| 5 | M4 首答与区块化共享稿 | P0 | T-M4 | **已完成（P0）** |
| 6 | M5 审阅窗 / 补丁 / 琐碎过滤 | P0 | T-M5 | **已完成（P0）** |
| 7 | M6 评判合入 / 打回 / 微调 | P0 | T-M6 | **已完成（P0）** |
| 8 | M7 确认轮与最终回复 | P0 | T-M7 | **已完成（P0）** |
| 8b | **M8b** 审计回放 / 恢复 | P0 | T-M8b | **已完成（P0）** |
| 9 | GATE 门禁联调（含 GWT 脚本） | P0 | T-GATE | **已完成（P0）** |
| 10 | M9 共享/私有记忆（基础） | P1a | T-M9 | **已完成（P1a）** |
| 11 | M10 Skills / MCP / 本机工具 | P1a | T-M10 | **已完成（P1a）** |
| 12 | M12 正式落盘 | P1a | T-M12 | **已完成（P1a）** |
| 13 | M11 执行补充（轻量） | P1b | T-M11 | **已完成（P1b）** |
| 14 | SEC 安全需求（Key 存储唯一实现） | P0/P1 | T-SEC | **已完成（P0/P1a/P1b）** |
| 15 | P2 标题项 | P2 | T-P2 | 仅登记 |

> **结构约定：**  
> 1. **正文已按实现序排章**（见上表与下方 ## 编号）：M3 在 M2 前；M8a 早挂；BUS/PROTO/AGENT 紧随；M8b 在 M7 后。**勿按 spec 目录序（M2 在 M3 前）开工。**  
> 2. **P0 必须自动 Agent（T-AGENT）**；GATE 不得靠「人工扮演工人」冒充通过。人工代发已读/PATCH **仅调试开关**，不计入 P0 Done。  
> 3. **Key 存储**：SEC-01 **实现一次**；T-M1-03 与 T-SEC-01 **两处引用同一 SecretStore**，禁止第二套实现。

### 0.2 建议验收顺序（总序）

详见文末 **§19**。原则：ENV → M1 → M3 → M2 → M8a → BUS/PROTO → AGENT/M4… → M8b → GATE。

### 0.3 「Pass / 通过」命令对照（防糊）

| 口语/旧称 | 权威事件或命令 | 角色 | 是否终局门禁 |
|-----------|----------------|------|--------------|
| 静默检查通过 | `SilentCheckPass` | 工人 | 否 |
| 已读 / 沉默同意 | `Read` + 无有效补丁 | 审阅者 | 否 |
| 合入 | `AcceptPatch` | 评议 | 否 |
| 确认轮沉默 | 确认窗关闭条件 | 审阅者 | 否 |
| **评议通过** / 旧称 FinalPass | **`JudgeApprove`** | 仅评议或降级 A 用户 | **是**（唯一出口） |
| 裸写「通过」「Pass」 | **禁止** | — | 解析失败或拒收 |

---

## 1. ENV — 运行与部署骨架


### 1.1 任务清单

| ID | 任务 | 期次 | 完成标准摘要 | 状态 |
|----|------|------|--------------|------|
| T-ENV-01 | Windows 应用/服务进程骨架 | P0 | 可启动本机进程 + 主窗口或托盘入口 | **完成** |
| T-ENV-02 | 本地配置目录约定 | P0 | 固定本机配置/数据路径；重启可加载 | **完成** |
| T-ENV-03 | 工作区路径抽象 | P0 | 支持盘符路径；房间可绑定工作区 | **完成** |
| T-ENV-04a | 最小运行日志 ENV-07a | P0 | phase/探活码/拒收/合入/打回/JudgeApprove/致命异常；无 Key | **完成** |
| T-ENV-04 | 日志轮转与诊断包 ENV-07 | P1 | UTF-8 轮转；脱敏导出 | 待做 |
| T-ENV-05 | 资源配额（标题） | P2 | 仅登记 | 仅登记 |

### 1.2 详细设计


**实现落点（2026-07-23）：** `src/multi_agent_room/`；启动 `python -m multi_agent_room`；验收 `python -m unittest tests.test_env -v`。

**目标：** 提供 Windows 本地宿主，不依赖云部署。

**进程形态（二选一或并存）：**
- 桌面 UI 进程（主）  
- 可选后台服务进程（同机 IPC）

**目录约定（建议）：**
- `%AppData%/<App>/config`：模型配置密文、房间策略  
- `%AppData%/<App>/data`：房间状态、审计、记忆库  
- `%AppData%/<App>/logs`：日志  
- 用户工作区：用户自选，写入房间元数据  

**网络：** 仅模型 API / 可选 MCP 出网；平台核心逻辑本地。

**非目标：** 多租户、云托管安装包分发策略（可后补）。

### 1.3 验收方式

| 方式 | 步骤 | 通过条件 | 结果（2026-07-23） |
|------|------|----------|-------------------|
| 自动 | `python -m multi_agent_room --smoke` | 打印 SMOKE_OK；创建 AppData 目录 | **通过** |
| 自动 | `python -m unittest tests.test_env -v` | 路径/配置/日志/脱敏 4 用例 | **通过** |
| 自动 | 创建 MainWindow 后 destroy | 无崩溃 | **通过** |
| 手工 | 启动主窗口、选工作区、保存 | 配置与日志可读中文 | 可用 `python -m multi_agent_room` 自测 |

---


## 2. M1 — 模型配置与纳管


### 2.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M1-01 | 模型配置 CRUD UI/存储 | P0 | **完成** |
| T-M1-02 | 字段含 providerId、超时、启用 | P0 | **完成** |
| T-M1-03 | Key：只写 apiKeyRef；**引用** SEC-01 SecretStore（实现在 SEC，此处不实现） | P0 | **完成** |
| T-M1-04 | 探活契约 + 错误映射 UI（超时/鉴权/网络） | P0 | **完成** |
| T-M1-04b | ProviderAdapter 插拔（OpenAI 默认） | P0 | **完成** |
| T-M1-05 | 就绪/失败状态机 | P0 | **完成** |
| T-M1-06 | 多 baseURL 并存 | P0 | **完成** |
| T-M1-07 | 「未就绪不可绑定」门禁 | P0 | **完成** |
| T-M1-08 | 熔断与备用（标题） | P2 | 仅登记 |
| T-M1-09 | 用量日志（标题） | P2 | 仅登记 |

### 2.2 详细设计

**实体：ModelConfig**
- `configId`, `providerId`, `displayName`, `apiKeyRef`, `baseUrl`, `modelId`, `timeoutMs`, `enabled`  
- `status`: `unknown | probing | ready | failed`  
- `lastProbeAt`, `lastError`, `lastErrorCode`（`timeout|auth|network|adapter`）  

**探活（对齐 spec）：** OpenAI 兼容最小 `chat/completions`；超时/401/网络映射 UI 文案；详情截断写 ENV-07a。

**Key：** `SecretStore.put/get` 仅 SEC 模块实现；M1 CRUD 只碰 ref。

**适配器：** `ProviderAdapter { id, probe, chat }` 注册表。

### 2.3 验收方式

| 用例 | 步骤 | 通过 | 结果（2026-07-24） |
|------|------|------|-------------------|
| M1-A | 添加 2 个不同 baseURL | 均保存成功 | **通过** |
| M1-B | 错误 baseURL 探活 | 标红 failed；无法加入房间大脑 | **通过** |
| M1-C | 正确探活 | ready；可绑定 | **通过**（mock 成功探活） |
| M1-D | 重启应用 | 配置仍在；Key 不以明文出现在共享稿 | **通过**（DPAPI vault） |
| M1-E | 禁用模型 | 房间选择器不可选 | **通过**（can_bind 拒绝） |
| SEC-D | M1 与 SEC 同一 SecretStore | 单例双检 | **通过** |

**实现落点：** `secret_store.py`（SEC-01）· `model_config.py` · `adapters.py` · `model_service.py` · `models_panel.py`；测试 `tests/test_m1.py`。

---


## 3. M3 — Agent 身份、会话隔离与职责认领


### 4.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M3-01 | Agent 持久身份存储 | P0 | **完成** |
| T-M3-02 | 绑定 ModelConfig（主/备字段预留） | P0 | **完成** |
| T-M3-03 | 独立 SessionHandle | P0 | **完成** |
| T-M3-04 | 竞选窗状态机（§5.6） | P0 | **完成** |
| T-M3-05 | 用户指定职责 | P0 | **完成** |
| T-M3-06 | 资格锁（首答 configId ≠ 评判 configId） | P0 | **完成** |
| T-M3-07 | 降级 A | P0 | **完成** |
| T-M3-08 | 消息身份签名/令牌 | P0 | **完成** |
| T-M3-09 | 首答可自审权限 | P0 | **完成** |
| T-M3-10 | 能力标签 | P1a | 待做（字段已预留） |
| T-M3-11 | 信誉画像（标题） | P2 | 仅登记 |

### 4.2 详细设计

**实体：AgentProfile**
- `agentId`, `displayName`, `persona`（可选）  
- `modelConfigId`, `backupConfigIds[]`  
- `capabilityTags[]`（P1a）  
- `toolAllowlist` / `pathAllowlist`（为 M10 预留）  

**SessionHandle：**
- 每 Agent 每次房间回合持有独立句柄  
- 调用模型时禁止合并历史；禁止共享 cache key  
- 审计字段：`requestId`, `sessionId`  

**职责 RoleAssignment（每轮 Round）：**
- `firstAnswererAgentId` + `firstAnswererConfigId`  
- `reviewerAgentIds[]`  
- `judgeAgentId` 或 `judge=User`  
- `frozen=true` 后不可自行改职骗评判  

**竞选窗：**
- 时长默认 60s  
- 用户指定 > 竞选  
- 无人首答 → `AwaitingFirstAnswer` 停住  
- 无人评判 → 多模型提示指定；单模型降级 A  
- 无人审阅 → 回退「除评判外全体（含首答）」  

**竞选与首答并发：** 职责冻结前禁止正式写 M4；生成中只进候选缓冲。见 spec M3 段。

**备用模型：** P0 只存 `backupConfigIds`；P2 才自动切换。

**工人 / 评议（P0 双模型默认）：**
- 非评议模型 = **工人**：首答 → 静默检查 → 补丁/确认轮响应  
- 评议模型：合入 / R1·R2·R3 / `JudgeApprove`  
- 工人可已读 + 实质 PATCH；**禁止** `JudgeApprove`  

**私有思考区：** 每 Agent 独立；他模不可见；交流只经白板。

**降级 A：** 单 ready 模型 → `judge=User`；拦截该模型 `JudgeApprove`。

### 4.3 验收方式

| 用例 | 通过条件 | 结果（2026-07-24） |
|------|----------|-------------------|
| M3-A | 新房可选同一 agentId | **通过** |
| M3-B | 两 Agent 连续调用 session/request 标识不同 | **通过** |
| M3-C | 首答模型认领评判被拒并提示 | **通过** |
| M3-D | 单模型房间强制用户评判台 | **通过** |
| M3-E | 伪造他 Agent PATCH 失败 | **通过** |
| M3-F | 工人可已读+发实质 PATCH；发 JudgeApprove 失败 | **通过** |
| M3-G | 用户指定职责覆盖竞选 | **通过** |
| M3-H | 他模上下文不含本模私有思考区内容 | **通过** |
| M3-I | 未提问时房间不启动首答/竞选 | **通过** |

**实现落点：** `agent_profile.py` · `session.py` · `identity.py` · `private_thought.py` · `roles.py` · `agent_service.py` · `agents_panel.py`；测试 `tests/test_m3.py`。

---


## 4. M2 — 共享聊天室壳层


### 3.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M2-01 | 房间创建/列表/进入 | P0 | **完成** |
| T-M2-02 | 邀请就绪 Agent | P0 | **完成** |
| T-M2-03 | 用户提问 + 原话钉选 | P0 | **完成** |
| T-M2-04 | 共享稿视图（区块 ID + 版本） | P0 | **完成**（M4 前 stub） |
| T-M2-05 | 事件时间线（可折叠） | P0 | **完成** |
| T-M2-06 | 最终回复槽 | P0 | **完成** |
| T-M2-07 | 评判操作台控件（绑定 M6 命令） | P0 | **完成**（命令登记；行为待 M6） |
| T-M2-08 | 工作区选择对话框 | P0 | **完成** |
| T-M2-09 | 议程板 | P0 | **完成** |
| T-M2-10 | 资格锁展示 | P0 | **完成** |
| T-M2-11 | 打断/恢复 | P0 | **完成** |
| T-M2-12 | 澄清问答 UI | P0 | **完成** |
| T-M2-13 | 系统通知 | P1a | 待做 |

### 3.2 详细设计

**房间实体 Room**
- `roomId`, `title`, `workspacePath`, `createdAt`, `phase`  
- `phase` 枚举建议：`Idle | Campaign | AwaitingFirstAnswer | ReviewOpen | AwaitingJudge | ConfirmOpen | Final | Frozen | AwaitingUserClarify`  

**UI 分区：**
1. **原话钉选区**（只读，始终可见给评判台）  
2. **议程板**：当前 phase + 下一步提示  
3. **成员与资格锁**：首答模型、评判、不可评判原因  
4. **共享稿区**：按区块渲染，显示 `blockId`、版本 `Vn`  
5. **事件流**：已读、拒收、PATCH、MERGE、R1/R2/R3、工具回执  
6. **最终回复槽**：仅 `Final` 写入；否则「未通过」  
7. **评判操作台**：合入、合并、R1、R2、R3、通过、授权落盘（P1a）  
8. **打断按钮**：Frozen ↔ 恢复  

**澄清问答：**
- Agent 发 `ClarifyQuestion` → phase=`AwaitingUserClarify`  
- 审阅窗计时暂停；不得因超时假沉默通过  
- 用户回答后恢复  

**工作区：**
- 仅路径绑定与越界校验；正式写文件在 M12  

### 3.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M2-A 建房邀人 | ≥1 就绪 Agent 入房 | **通过** |
| M2-B 原话钉选 | 提问后钉选区文本=用户输入 | **通过** |
| M2-C 议程板 | phase 随流程变化（至少演示到 ReviewOpen） | **通过** |
| M2-D 资格锁展示 | 首答后该模型标「不可评判」 | **通过** |
| M2-E 打断 | Frozen 期间审阅窗不关闭；恢复后续跑 | **通过** |
| M2-F 最终回复槽 | 门禁前不可出现终稿正文 | **通过** |
| M2-G 澄清 | 待答时不因超时进入「全员沉默通过」 | **通过** |

**实现落点：** `room.py` · `shared_doc.py` · `room_events.py` · `room_service.py` · `room_panel.py`；测试 `tests/test_m2.py`。

---


## 5. M8a — phase 状态机骨架（早挂）

### 5.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M8a-01 | phase 全表驱动 + 非法迁移拒绝 | P0 | **完成** |
| T-M8a-02 | 预算：补丁数 / R2 / confirmIndex | P0 | **完成** |
| T-M8a-03 | `room.frozen` 单一真相（M2 只发 Interrupt/Resume） | P0 | **完成** |
| T-M8a-04 | 升用户钩子（预算/打回/确认封顶） | P0 | **完成** |

### 5.2 详细设计

**时机：** Review 主链之前即可挂上；只负责 phase/预算/Frozen。完整审计事件在 **§12 M8b**。

**Orchestrator：** 订阅总线；推进 phase（spec M8 全表）；处理 Frozen/ClarifyHold；**唯一**写 `frozen`。

**Budget：** `maxPatchesPerRound`, `maxR2`, `maxConfirmChurn`

### 5.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M8a-A | 新房默认议程含审阅→评判→确认 | **通过** |
| M8a-B | 非法 phase 迁移被拒且记 ENV-07a | **通过** |
| M8a-C | M2 Frozen 与编排器同一字段 | **通过** |
| M8a-D | 超补丁预算停止并提示 | **通过** |

**实现落点：** `phase_machine.py` · `budget.py` · `orchestrator.py`；`RoomService` 经编排器写 phase/frozen；测试 `tests/test_m8a.py`。

---


## 6. T-BUS / T-PROTO / T-AGENT


### T-BUS 事件总线

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-BUS-01 | 房间内发布/订阅 API | P0 | **完成** |
| T-BUS-02 | 事件类型全集（对齐 spec §5.9） | P0 | **完成** |
| T-BUS-03 | 持久化与回放对接 M8 | P0 | **完成** |
| T-BUS-04 | ToolReceipt 进总线（MCP-06 预留） | P0 | **完成** |
| T-BUS-05 | RoomIdle / RoomAwake 信号 | P0 | **完成** |

**设计：**  
- 白板变更的唯一广播通道；UI、M5–M8、Worker 只订阅总线。  
- 禁止 Agent 实现「点对点私信」旁路（NG-05）。  
- 订阅者按 `roomId` 隔离。

**验收：** 合入后必出现 `DocVersion`+`QueueUpdated`；回放能还原顺序。 → **通过**（`tests/test_bus.py`）

**实现落点：** `event_types.py` · `event_bus.py`；`RoomService.bus`；持久化 `%AppData%/…/data/bus/*.jsonl`。

### T-PROTO 协议归一化

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-PROTO-01 | 工人输出 → Read / Abstain / Patch / SilentCheckPass | P0 | **完成** |
| T-PROTO-02 | 评议输出 → Accept / Merge / R1 / R2 / R3 / JudgeApprove | P0 | **完成** |
| T-PROTO-03 | 非法/闲聊仅进私有思考区，不进白板 | P0 | **完成** |
| T-PROTO-04 | 结构失败有限次重试提示（默认 2） | P0 | **完成** |
| T-PROTO-05 | 与 B-08 / DIS-11 字段校验对齐后再交 M5 | P0 | **完成** |

**设计：** 自然语言草稿 ≠ 白板消息；归一化是唯一上白板闸门。

**验收：** 纯闲聊不进队列；缺 claim 的「像补丁」文本不进公共流。 → **通过**（`tests/test_proto.py`）

**实现落点：** `protocol.py`；`RoomService.ingest_worker_output` / `ingest_judge_output` / `pending_patches`。

### T-AGENT 工人运行时 / Prompt 编排

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-AGENT-01 | 用户提问唤醒；未提问空闲 | P0 | **完成** |
| T-AGENT-02 | W1 Prompt：首答 → M4 入库 | P0 | **完成**（入库经壳层 stub，切块待 M4） |
| T-AGENT-03 | W2 Prompt：首答回喂静默检查 | P0 | **完成** |
| T-AGENT-04 | SilentCheckPass → 等待评议（路径·静默通过） | P0 | **完成** |
| T-AGENT-05 | 有问题 → PROTO → 实质 PATCH | P0 | **完成** |
| T-AGENT-06 | W3/W4：审阅与确认轮 Prompt（含 ChangedSet 约束） | P0 | **完成** |
| T-AGENT-07 | J1 评议 Prompt 组装（无闲聊） | P0 | **完成** |
| T-AGENT-08 | 私有思考区读写；禁止他模访问 | P0 | **完成** |
| T-AGENT-09 | P0 单工人；多工人审阅非必须 | P0 | **完成** |

**设计：** 对齐 spec §5.7；编排器按 phase 选 Prompt 模板，不把工人/评议上下文混装。

**验收：** E2E-09、12、15；他模上下文不含工人私有思考；未提问无 W1 调用。 → **通过**（`tests/test_agent.py` 骨架；完整 GATE 仍待 T-GATE）

**实现落点：** `prompts.py` · `worker_runtime.py`（`AgentRuntime`）；输出经 PROTO；`JudgeApprove` 受职责锁约束。

---


## 7. M4 — 首答与区块化共享稿


### 5.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M4-01 | 共享稿数据模型（版本+区块） | P0 | **完成** |
| T-M4-02 | 切块：MD 规则 + 纯文本空行/窗口切块 | P0 | **完成** |
| T-M4-02b | 合入后 blockId 稳定性（保留/分裂/tombstone） | P0 | **完成** |
| T-M4-03 | 首答入库 → 发布 V0 | P0 | **完成**（入库 V1 active + DocVersion） |
| T-M4-04 | 并行首答候选与选定（§5.5） | P0 | **完成** |
| T-M4-05 | 版本递增规则 | P0 | **完成** |
| T-M4-06 | R2 后作废与新首答 | P0 | **完成** |

### 5.2 详细设计

**SharedDoc**
- `docId`, `roomId`, `version`（单调递增整数）  
- `status`: `active | voided`  
- `blocks[]`: `{ blockId, type, text, order }`  
- `baseFrom`: `firstAnswer | merge | tweak | r1Fix`  

**切块规则（P0）：**
- Markdown 优先：`#` 标题、空行分段、fenced code、列表项  
- 纯文本：空行分段；过长再按句号/换行；再不行固定窗口（见 spec M4）  
- 每块稳定 `blockId`；合入保留目标 ID；分裂记 `splitFrom`；删除进 tombstone  

**写入口（唯一，对接 DEP-04）：**
- M6 ApplyMerge / ApplyTweak / ApplyPatchAccept  
- M4 CreateFromFirstAnswer（含 R2 后新首答）  
- 禁止 M5/M9/M10 直接改 blocks  

**并行首答：**
- 多份 `CandidateDoc`  
- 选定权：用户 > 评判 > 先完成且基础校验通过  
- 未采用保留可追溯，不进入 active 链  

**已读绑定版本：**
- `ReadReceipt(agentId, version)`  
- Vn 的已读不能自动当作 Vn+1 同意  

### 5.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M4-A | 首答后存在 ≥1 blockId | **通过** |
| M4-B | 无 target 的补丁在 M5 被拒（联调） | **通过**（`can_patch_target` 门禁） |
| M4-C | 并行两候选，选定后仅一版 active | **通过** |
| M4-D | 合入后 version+1；旧已读不继承 | **通过** |
| M4-E | R2 后旧 doc voided；必须新首答才可再审 | **通过** |

**实现落点：** `chunker.py` · `shared_doc.py`（`DocService`/`SharedDoc`）；`RoomService.docs`；测试 `tests/test_m4.py`。

---


## 8. M5 — 审阅窗、已读、补丁、琐碎过滤


### 6.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M5-01 | 审阅窗状态机（开/关，§5.4.1） | P0 | **完成** |
| T-M5-02 | 已读登记 | P0 | **完成** |
| T-M5-03 | 沉默同意计算 | P0 | **完成** |
| T-M5-04 | 弃权 | P0 | **完成** |
| T-M5-05 | 有效审阅者解析（含自审回退） | P0 | **完成** |
| T-M5-06 | PATCH 协议校验 | P0 | **完成** |
| T-M5-07 | 琐碎规则引擎 + 净变更计量 + 金样例夹具自动化 | P0 | **完成** |
| T-M5-07b | MarkTrivial UI/权限（仅评议） | P0 | **完成**（命令+权限；UI 按钮可后补） |
| T-M5-07c | 静默期/打断/澄清计时状态机 | P0 | **完成** |
| T-M5-08 | 待合入队列 | P0 | **完成** |
| T-M5-09 | 超范围拒收 | P0 | **完成** |
| T-M5-09b | 全文重写检测（§6.1.2） | P0 | **完成** |
| T-M5-10 | 评议六维系统提示 | P0 | **完成** |
| T-M5-11 | 补丁静默期 / 窗超时配置 | P0 | **完成** |
| T-M5-12 | implies（标题） | P1b | 待做 |

### 6.2 详细设计

**ReviewWindow**
- `version`, `openedAt`, `closesAt`, `quietPeriodMs=15000`, `timeoutMs=120000`  
- `frozen`（打断时）  
- `clarifyHold`（澄清待答时暂停计时）  

**关闭条件（最早）：**
1. 全体有效审阅者已读 ∧ 距最后有效补丁 ≥ quietPeriod  
2. 超时 ∧ ≥1 已读  
3. 强制提交评判  

**Patch 结构：**
```
target: blockId
category: scheme|api|logic|fact|acceptance|other
claim: string
replace: string | diff
implies: blockId[] (optional, P1b)
```

**过滤器流水线：**
1. 缺字段 → Reject(invalid)  
2. multi-target → Reject  
3. 全文重写检测（spec §6.1.2 RW-1～3）→ Reject(full_rewrite)（除非 REWRITE 令牌或 R2 新首答入口）  
4. 琐碎启发式：  
   - 计算净变更量（spec §6.1.1：中文按字 / 英文按词 / 代码按非空行+token）  
   - TRIV-5：净变更&lt;100 且无实质 category 且 claim 低价值 → Reject(trivial)  
   - 禁发模式（纯形容词/标点润色等）→ Reject(trivial)  
5. 通过 → 入 Queue；发贴者记已读  

**评判第二道：** `MarkTrivial(patchId)` — 仅评议/用户；工人失败；UI 在评判台。

**计时：** 对齐 spec §6.1.0；Frozen/ClarifyHold 暂停；新有效补丁重置 quietPeriod。

**金样例：** `fixtures/patches/TRIV-*.json` / `SUB-*.json` 自动化跑过滤器。

**有效审阅者算法：**
1. 显式 reviewer 列表且未弃权  
2. 否则：房间内除 judge 外全部 Agent（含 firstAnswerer）  
3. 若空：确认轮改用户已读  

### 6.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M5-A | 未已读不算同意 | **通过** |
| M5-B | 超时未表态 ≠ 同意 | **通过** |
| M5-C | TRIV-1～5 全拒收（含中/英/代码净变更样例各至少 1） | **通过** |
| M5-D | SUB-1～4 可入队 | **通过** |
| M5-E | 弃权不进分母 | **通过** |
| M5-F | 窗关闭后队列交给 M6，无自动最终回复 | **通过** |
| M5-G | 六维提示存在于审阅上下文 | **通过** |
| M5-H | RW-1/2/3 样例 → Reject(full_rewrite) | **通过** |
| M5-I | 持 REWRITE 令牌或 R2 新首答 → 不误杀 | **通过** |

**实现落点：** `review_window.py` · `review_service.py` · `patch_filter.py` · `net_change.py`；夹具 `fixtures/patches/`；`RoomService.review`；测试 `tests/test_m5.py`。

---


## 9. M6 — 评判：合入、合并、打回、微调


### 7.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M6-01 | 评判上下文组装（原话+稿+队列） | P0 | **完成** |
| T-M6-02 | 裁定消息类型限制 | P0 | **完成**（PROTO JUDGE_KINDS） |
| T-M6-03 | 点合入（含单补丁） | P0 | **完成** |
| T-M6-04 | 冲突检测（§6.3）与一次合并 | P0 | **完成** |
| T-M6-04b | MergeConflict 策略枚举 chooseA/B/concat/rewrite + UI | P0 | **完成**（命令；UI 可后补） |
| T-M6-05 | R1 局部打回 + 关闭语义 | P0 | **完成** |
| T-M6-05b | R1 重交责任人：默认原作者，不可用则竞选/指定 | P0 | **完成**（默认原作者） |
| T-M6-06 | R2(A) + 说明书质检 | P0 | **完成** |
| T-M6-07 | R3 微调边界与软限额 | P0 | **完成** |
| T-M6-08 | R1+R3 原子裁定 | P0 | **完成**（可同轮先后下发） |
| T-M6-09 | 同块打回上限 | P0 | **完成** |
| T-M6-10 | JudgeApprove 权限检查（仅评议/降级A用户） | P0 | **完成** |
| T-M6-11 | 合并说明可追溯存储 | P0 | **完成** |
| T-M6-12 | critical 升用户（标题） | P1a | 待做 |

### 7.2 详细设计

**JudgeContext（禁止含闲聊）：**
- userAnchor  
- sharedDoc snapshot  
- pendingPatches  
- openRejects  
- rules summary  

**命令（须拆清，禁止混用）：**
- `AcceptPatch(patchId)` — 合入  
- `MergeConflict(...)` — 冲突合并  
- `R1` / `R2` / `R3`  
- **`JudgeApprove`（评议通过）** — 仅当路径已干净时：  
  - 路径·静默通过：静默检查无问题后可点  
  - 路径·补丁确认：确认轮已读齐且无有效补丁且无未决 R1 后可点  
  - 成功后由 M7 `CommitFinalReply` 写最终槽并允许执行  
- **禁止**：工人用任何别名（含旧称 FinalPass）绕过 `JudgeApprove`  

**不是「整平台通过」：** 静默检查通过、确认轮沉默等只是轮次信号；出口门禁只有 `JudgeApprove`。

**冲突（spec §6.3）：**
- 同 `target` ≥2 有效补丁 → 自动分类：兼容可叠 / 不兼容 / 待裁定  
- **禁止**自动互辩  
- 不兼容或待裁定 → 打开合并台；`MergeConflict` 必填 `reason`  
- 产出新块文本 + `MergeRecord`（mode=stack|choose|rewrite）  

**R1：**
- 未点名块锁定  
- `OpenReject` 存在则禁止 Final  
- 重交 → Accept → **R1 关闭** → 触发 +1  

**R2：**
- 字段不全或润色理由 → 拒收命令  
- 成功：active doc → voided；keep 仅参考；phase 回新首答  
- **ChangedSet 例外：** R2 全篇打回后不再受上一确认轮变动块约束  

**R3：**
- 允许/禁止见 spec §6.2  
- 限额：块≤3 或 diff 行≤40（可配）  
- 写回后必 +1  

### 7.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M6-A | 单补丁未点合入稿不变 | **通过** |
| M6-B | 点合入后 version+1 且进确认轮 | **通过** |
| M6-C | 两冲突补丁一次合并+说明可查 | **通过** |
| M6-C2 | 两不兼容 replace → 合并台；无 reason 失败 | **通过** |
| M6-C3 | 兼容可叠 diff → 可一键叠合但仍须点合入 | **通过** |
| M6-D | R1 未关闭无最终回复；关闭后 +1 | **通过** |
| M6-E | 缺 user_goal_ref 的 R2 失败 | **通过** |
| M6-F | R2 成功旧稿作废 | **通过** |
| M6-G | 越界 R3 被拒 | **通过** |
| M6-H | 非评议账号 JudgeApprove 失败 | **通过** |
| M6-I | 评议上下文无过程附和消息 | **通过** |
| M6-J | 工人发 JudgeApprove 失败 | **通过** |

**实现落点：** `judge_service.py` · `conflict.py`；`RoomService.judge` / `judge_context`；测试 `tests/test_m6.py`。

---


## 10. M7 — 确认轮与最终回复门禁


### 8.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M7-01 | 确认轮强制触发器（§5.4.2） | P0 | **完成** |
| T-M7-02 | 路径·静默通过 | P0 | **完成** |
| T-M7-03 | 路径·补丁确认与确认轮封顶 | P0 | **完成** |
| T-M7-04 | JudgeApprove 前置条件校验（轮次通过≠评议通过） | P0 | **完成** |
| T-M7-04b | ChangedSet 计算与确认轮范围校验 | P0 | **完成** |
| T-M7-05 | 写入最终回复槽 | P0 | **完成** |
| T-M7-06 | R2 重置确认状态 | P0 | **完成** |
| T-M7-07 | 禁止跳过开关（硬编码禁跳） | P0 | **完成** |
| T-M7-08 | 确认轮封顶升用户：AwaitingUserEscalation 四选项交互 | P0 | **完成** |

### 8.2 详细设计

**废止「路径 A/B」叫法。** 与 spec §5.4.3 对齐：

**路径·静默通过：**  
工人首答 → 静默检查无问题 → **不开确认轮** → `JudgeApprove` → `CommitFinalReply`（可执行）

**路径·补丁确认：**  
发现问题 → PATCH → 合入 → 确认轮（**仅 ChangedSet**）→（循环）→ 确认轮干净 → `JudgeApprove` → `CommitFinalReply`

**ConfirmRound.ChangedSet（原「只改变动块」展开）：**
- 来源：触发本确认轮的写稿动作触及的全部 `blockId`（合入 target / 合并块 / R3 块 / R1 重交合入块）  
- 允许：`target ∈ ChangedSet` 的实质 PATCH；拒绝集合外（明确错误码）  
- 确认轮合入新补丁 → 新 `ChangedSet` → 再开确认轮（封顶）  
- 确认轮干净 = 已读齐且无有效补丁 → **等待** `JudgeApprove`，不是自动 Final  
- **例外：** 评议/用户 **R2 全篇打回** → 旧稿作废、新首答；不受上一 ChangedSet 约束（不可在确认轮内借机改未变动块冒充全篇重写）  

**CommitFinalReply 前置：** 必须已有本轮成功的 `JudgeApprove`；不能仅凭静默检查通过或确认轮沉默自动 Final。

### 8.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M7-A | 有合入却无确认轮 → 不能 JudgeApprove 成功 | **通过** |
| M7-B | 静默通过：无确认轮，一次 JudgeApprove → Final | **通过** |
| M7-C | 补丁确认：确认轮打非 ChangedSet → 拒收 | **通过** |
| M7-D | 确认轮干净但未 JudgeApprove → 无 Final | **通过** |
| M7-E | 确认轮循环封顶升用户或停止自动 +1 | **通过** |
| M7-F | R2 后确认状态清零 | **通过** |

**实现落点：** `confirm_service.py`（`ALLOW_SKIP_CONFIRM=False`）；`RoomService.confirm` / `commit_final_reply` / `mark_confirm_clean` / `apply_user_escalation`；测试 `tests/test_m7.py`。

---


## 11. M8b — 审计回放与会话恢复

**时机：** M4–M7 事件齐全后完善；依赖总线已有合入/打回等事件。

### 11.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M8b-01 | AuditEvent 持久化 + 回放 UI | P0 | **完成**（JSONL + `replay_audit`/`summarize`；桌面回放面板可后补） |
| T-M8b-02 | RoomState schema 恢复 | P0 | **完成** |
| T-M8-06 | 房间归档 | P1a | **完成**（最小 index 归档） |
| T-M8-07 | 导出包 | P1a | 待做 |
| T-M8-08 | 监控/快照/故障隔离（标题） | P2 | 仅登记 |

### 12.2 详细设计

**时机：** M4–M7 事件齐全后完善；依赖总线已有合入/打回等事件。

**AuditEvent / RoomState：** schema 见 spec M8；崩溃恢复后与中断前一致。

**落盘：**
- `%AppData%/MultiAgentRoom/data/audit/<roomId>/events.jsonl`
- `%AppData%/MultiAgentRoom/data/room_state/<roomId>/state.json`
- 归档：`data/archive/index.json`

### 12.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M8b-A | 回放能看到合入与 R1 | **通过** |
| M8b-B | Kill 进程再开，版本与未决补丁恢复 | **通过** |
| M8b-C | 归档后列表可见（P1a） | **通过**（最小实现） |

**实现落点：** `audit_store.py` · `room_state.py`；`RoomService.persist_room` / `restore_room` / `replay_audit`；测试 `tests/test_m8b.py`。

---


## 12. GATE — 门禁联调任务包（跨 M4–M8 + AGENT）


| ID | 任务 | 期次 | 对应 E2E | 状态 |
|----|------|------|----------|------|
| T-GATE-01 | 双模型同房提问跑通 Review | P0 | E2E-01 | **完成** |
| T-GATE-02 | 语法润色贴拒收且不打断收敛 | P0 | **E2E-02** | **完成** |
| T-GATE-03 | 单条实质补丁：未合入不变→合入→确认→评议通过 | P0 | **E2E-03** | **完成** |
| T-GATE-04 | 同块冲突一次合并（含不兼容合并台） | P0 | E2E-04 / E2E-17 | **完成** |
| T-GATE-05 | R1 打回两块：锁定/关闭/确认/未关闭无通过 | P0 | **E2E-05** | **完成** |
| T-GATE-06 | R2(A)：说明书质检、旧稿作废、新首答 | P0 | **E2E-06** | **完成** |
| T-GATE-07 | R3 后确认轮 + ChangedSet + 评议通过 | P0 | **E2E-07** | **完成** |
| T-GATE-08 | 降级 A：用户代评议 | P0 | E2E-08 | **完成** |
| T-GATE-09 | 路径·静默通过 | P0 | **E2E-09** | **完成** |
| T-GATE-10 | 双模型工人/评议分工 | P0 | E2E-12 | **完成** |
| T-GATE-11 | 打断审阅窗 | P0 | E2E-13 | **完成** |
| T-GATE-12 | ChangedSet 拒收非变动块 | P0 | E2E-14 | **完成** |
| T-GATE-13 | 未提问空闲 | P0 | E2E-15 | **完成** |
| T-GATE-14 | 无授权全文重写拒收 | P0 | E2E-16 | **完成** |

设计要点：严格按 spec §5.4；**两路径均须一次 JudgeApprove**；编排器为唯一 phase 推进者。  
**Pass 语义：** 见本文 §0.3 / spec §5.4.3b；GATE 断言里禁止使用裸「Pass」，一律写 `JudgeApprove` / `SilentCheckPass` 等权威名。  
**P0：** 必须 T-AGENT 自动工人；人工代发不算 Done。

### 15.1 E2E 勾选表 + Given/When/Then（防漏测）

> **P0 硬性：** 下列脚本须由 **自动工人 Agent（T-AGENT）** 驱动；人工代发已读/PATCH 仅调试，**不得**勾选为 P0 Done。

| E2E | GATE | Given / When / Then | ☐ |
|-----|------|---------------------|---|
| E2E-01 | GATE-01 | **G** 两就绪模型同房 **W** 用户提问 **T** 完成 Review 且两 session 标识不同 | ☑ |
| E2E-02 | GATE-02 | **G** 审阅窗开 **W** 发语法润色贴 **T** Reject(trivial)；收敛不被打断 | ☑ |
| E2E-03 | GATE-03 | **G** 单实质 PATCH 入队 **W** 未合入 / 再点合入 / 确认干净 / JudgeApprove **T** 稿仅合入后变；Final 仅最后步出现 | ☑ |
| E2E-04 | GATE-04 | **G** 同块两冲突贴 **W** 评议合并 **T** 一次合并+reason；无互辩 | ☑ |
| E2E-05 | GATE-05 | **G** R1 打回两块 **W** 重交并合入 **T** 其余锁定；关闭后开确认；未关闭无 JudgeApprove | ☑ |
| E2E-06 | GATE-06 | **G** 缺说明书 R2 **W** 再发合格 R2 **T** 前者失败；后者旧稿 voided 且须新首答 | ☑ |
| E2E-07 | GATE-07 | **G** R3 写回 **W** 确认轮打 ChangedSet 外块 **T** 拒收；干净后 JudgeApprove→Final | ☑ |
| E2E-08 | GATE-08 | **G** 仅 1 模型 **W** 模型发 JudgeApprove **T** 失败；用户台可代评议 | ☑ |
| E2E-09 | GATE-09 | **G** 工人首答+静默检查通过 **W** 评议 JudgeApprove **T** 无确认轮；Final | ☑ |
| E2E-12 | GATE-10 | **G** 工人+评议 **W** 工人 PATCH 与 JudgeApprove **T** PATCH 可；Approve 失败；评议可 Approve | ☑ |
| E2E-13 | GATE-11 | **G** ReviewOpen **W** Interrupt 再 Resume **T** 冻结期不关窗；恢复后续跑 | ☑ |
| E2E-14 | GATE-12 | **G** ConfirmOpen ChangedSet={B12} **W** PATCH B03 **T** 拒收 | ☑ |
| E2E-15 | GATE-13 | **G** 房间已建未提问 **W** 等待 **T** 无 W1/竞选自动调用 | ☑ |
| E2E-16 | GATE-14 | **G** 无 REWRITE **W** 全文重写贴 **T** Reject(full_rewrite) | ☑ |
| E2E-17 | GATE-04 | **G** 两不兼容 replace **W** MergeConflict 无 reason **T** 失败；有 reason 成功 | ☑ |

验收：上表全部勾选；且日志可见 ENV-07a 关键事件。  
**实现落点：** `tests/test_gate.py`（`AgentRuntime` 注入响应驱动）；R1 未点名块锁定 + 开打回重交可越过确认轮 ChangedSet。

---


## 13. M9 — 共享记忆与私有记忆


### 10.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M9-01 | 共享记忆存储 | P1a | **完成** |
| T-M9-02 | 私有记忆隔离 | P1a | **完成** |
| T-M9-03 | 检索 API（权限过滤） | P1a | **完成** |
| T-M9-04 | 禁止旁路改稿 | P1a | **完成** |
| T-M9-05 | 分层/冲突/擦除/装配（标题） | P1b/P2 | 仅登记 |

### 10.2 详细设计

**SharedMemoryItem：** 类型含 `anchor_summary | constraint | final_pointer | todo | delivery_index`  
- 过程稿不得标 `resolved`  

**PrivateMemory：** `agentId` 作用域；组装他者上下文时剥离  

**API 红线：** 任何 memory.write 不得接受 `blocks[]` 正文修改  

### 10.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M9-A | 终稿指针可检索 | **通过** |
| M9-B | A 的私有草稿不出现在 B 的上下文 | **通过** |
| M9-C | 尝试经记忆改 block 失败 | **通过** |

**实现落点：** `memory_service.py`；`RoomService.memory` / `write_*_memory` / `search_*_memory` / `agent_memory_context`；提问写 `anchor_summary`/`constraint`；`commit_final_reply` 写 `final_pointer`；测试 `tests/test_m9.py`。

---


## 14. M10 — Skills 与本机工具


### 11.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M10-01 | Skill 注册/开关/授权 | P1a | **完成** |
| T-M10-02 | 文件读写工具 | P1a | **完成** |
| T-M10-03 | 终端：白名单 / 超时杀进程 / 输出截断 | P1a | **完成** |
| T-M10-03b | MCP Client 宿主生命周期（连接/发现/策略包） | P1a | **完成**（进程内 stub） |
| T-M10-04 | schema 校验与错误映射 | P1a | **完成** |
| T-M10-05 | 默认只读 + 钩子时机（spec §5.3.2） | P1a | **完成** |
| T-M10-06 | 回执进事件总线 | P1a | **完成** |
| T-M10-07 | 高危写用户确认 | P1a | **完成** |
| T-M10-08 | Key 不进提示红线 | P1a | **完成** |
| T-M10-09 | 浏览器/远端/版本回滚（标题） | P2 | 仅登记 |

### 11.2 详细设计

**ToolHost：** 发现工具、校验参数、鉴权、执行、回执。

**MCP 生命周期：** 配置 Server → 连接 → list_tools 经策略包过滤 → 调用 → ToolReceipt → shutdown/杀进程。见 spec M10。

**终端：** 白名单可执行名；超时 taskkill；stdout/stderr 截断 32KB。

**阶段策略 / 钩子：** 对齐 spec §5.3.2（发言前只读；通过后或持令牌可写）。

### 11.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M10-A | 未授权 Skill 无法触发 | **通过** |
| M10-B | 非法参数失败且房间可读 | **通过** |
| M10-C | 审阅阶段写盘失败 | **通过** |
| M10-D | 高危未确认不执行 | **通过** |
| M10-E | 提示词/日志红线无 API Key 明文 | **通过** |

**实现落点：** `skill_registry.py` · `schema_validate.py` · `file_tools.py` · `terminal_tools.py` · `mcp_host.py` · `tool_host.py` · `redact.py`；`RoomService.tools` / `invoke_skill` / `authorize_deliver`；测试 `tests/test_m10.py`。

---


## 15. M12 — 多样化交付（本机）


### 13.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M12-01 | 正式落盘门禁（M7 或授权） | P1a | **完成** |
| T-M12-02 | md/总结写入工作区 | P1a | **完成** |
| T-M12-03 | 交付清单 | P1a | **完成** |
| T-M12-04 | 回执进房 | P1a | **完成** |
| T-M12-05 | Excel/小游戏/回滚（标题） | P2 | 仅登记 |

### 13.2 详细设计

**DeliverCommand / 授权落盘（spec §5.3.1）：**
- **点交付**：用户点「交付」→ 需 `FinalCommitted` **或** 有效 `writeToken`  
- **授权落盘**：评判台发放 `writeToken`（不自动 Final）；审计必记  
- 路径必须在 workspace 下；写入后 `DeliveryReceipt`  

**T1：** 用户点「交付」触发，不靠关键词  

### 13.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M12-A | Final 前正式落盘失败 | **通过** |
| M12-B | 点交付后文件非空且在工作区 | **通过** |
| M12-C | 越界路径失败 | **通过** |
| M12-D | 清单与磁盘一致 | **通过** |

**实现落点：** `deliver_service.py`；`RoomService.click_deliver` / `authorize_deliver` / `verify_delivery_manifest`；房间面板「交付」按钮；测试 `tests/test_m12.py`。

---


## 16. M11 — 执行阶段补充（轻量）


### 12.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-M11-01 | T0 语义：纯问答可跳过执行 | P0/P1b | **完成** |
| T-M11-02 | 环境画像（文件/依赖） | P1b | **完成** |
| T-M11-03 | 静态检查 | P1b | **完成** |
| T-M11-04 | 骨架分段：编排器强制拒收超长（默认非仅警告） | P1b | **完成** |
| T-M11-05 | 简沙盒：临时目录 + python/node/dotnet 子进程 | P1b | **完成** |
| T-M11-06 | 报错回灌模板 | P1b | **完成** |
| T-M11-07 | 结果审查四字段 | P1b | **完成** |
| T-M11-08 | 强制回门禁 | P1b | **完成** |
| T-M11-09 | 强沙盒/侦察/re-plan 细表（标题） | P2 | 仅登记 |

### 12.2 详细设计

**插入点：** 实现 T0/T1/T2（T3 浏览器 P2）  

**骨架分段：** 编排器在入库/交沙盒前按行数/字数配额强制拒收，要求拆段（spec M11）。

**沙盒：**  
- 工作目录=临时路径；按扩展名选 `python`/`node`/`dotnet`；缺失 → `runtime_missing`  
- 禁止写正式 workspace（DEP-11）  
- 失败 → 回灌 PATCH/修订上下文  

**结果审查：**
```
goal / current_result / gap / pass|fail
```
fail → 不得 Complete；回 M5/M6/M7  

### 12.3 验收方式

| 用例 | 通过条件 | 结果 |
|------|----------|------|
| M11-A | T0 不启用执行仍可 Final | **通过** |
| M11-B | 静态检查失败不进沙盒成功态 | **通过** |
| M11-C | 沙盒失败含 stack 回灌 | **通过** |
| M11-D | 空结果审查 fail | **通过** |
| M11-E | 无 M7 不能标任务完成 | **通过** |

**实现落点：** `exec_service.py` · `sandbox_runner.py` · `skeleton_gate.py`；`RoomService.run_exec_t0` / `run_exec_t2` / `mark_task_complete`；首答入库前骨架门禁；测试 `tests/test_m11.py`。

---


## 17. SEC — 安全

> **实现一次、两处引用：** `SecretStore` **仅在本包 T-SEC-01 实现**；`T-M1-03` 只读写 `apiKeyRef`。禁止 M1/SEC 各写一套加密存储。

### 17.1 任务清单

| ID | 任务 | 期次 | 状态 |
|----|------|------|------|
| T-SEC-01 | Key 本机安全存储（**唯一实现**；M1 只引用 apiKeyRef） | P0 | **完成** |
| T-SEC-02 | Key 不进共享稿/记忆 | P0 | **完成** |
| T-SEC-03 | Key 不进模型提示 | P1a | **完成** |
| T-SEC-04 | 高危写确认 | P1a | **完成** |
| T-SEC-05 | 沙盒与工作区分离 | P1b | **完成** |
| T-SEC-06 | 沙盒禁出网（标题） | P2 | 仅登记 |

### 17.2 详细设计 / 验收

- 存储：DPAPI/系统凭据或加密文件；**全应用唯一 SecretStore**；T-M1-03 不得再实现第二套  
- 红线测试：扫描即将发送的 prompt 与审计导出  

| 用例 | 通过 | 结果 |
|------|------|------|
| SEC-A | 磁盘配置无明文 Key（或仅加密blob） | **通过** |
| SEC-B | 共享稿搜索不到 Key | **通过** |
| SEC-C | 提示组装器剥离 Key | **通过** |
| SEC-D | M1 与 SEC 指向同一 SecretStore 实现（代码/文档双检） | **通过** |

**实现落点：** `secret_store.py`（唯一实现）· `sec_guard.py` · `redact.py`；共享稿/记忆写入拦截；ToolHost 高危确认；Sandbox 与 workspace 分离；测试 `tests/test_sec.py`。

---


## 18. P2 标题登记（不展开）


见 spec §12；本任务文档仅保留包 ID：

| ID | 标题簇 |
|----|--------|
| T-P2-01 | 快照 / 监控 / 故障隔离 |
| T-P2-02 | 浏览器侦察与强沙盒 |
| T-P2-03 | Excel / 小游戏全链路 |
| T-P2-04 | 盲审/红队/投票 |
| T-P2-05 | A2A 私信/死信 |
| T-P2-06 | 远端服务器工具 |
| T-P2-07 | 熔断配额 / 信誉 / 能力路由 |
| T-P2-08 | 托盘快捷键拖放 / 插件热更新 |

---


## 19. 建议验收顺序（详细）


### 17.1 层内顺序（开发完成即测）

```
① T-ENV
② T-M1
③ T-M3
④ T-BUS + T-PROTO
⑤ T-M2
⑥ T-M8a（phase 骨架）
⑦ T-M4 + T-AGENT（首答/静默检查）
⑧ T-M5
⑨ T-M6
⑩ T-M7
⑪ T-M8b（审计/恢复）
⑫ T-GATE（GWT 勾选；须自动 Agent）
⑬ T-SEC（P0；Key 唯一实现）
── P0 完成里程碑 ──
```

### 17.2 P0 建议测试日排期

| 日序 | 验收焦点 | 对应 E2E/模块用例 |
|------|----------|-------------------|
| D1 | 配置与隔离 | M1-A..E, M3-B |
| D2 | 壳层与竞选 | M2-A..G, M3-C..G, §5.6 |
| D3 | 区块稿+审阅过滤 | M4-*, M5-*（含金样例） |
| D4 | 评判 R1/R2/R3 + 冲突不兼容 | M6-*（含 C2/C3） |
| D5 | 确认轮+ChangedSet+R2例外 | M7-*, E2E-14/06 |
| D6 | GATE 勾选 E2E-01～09 | GATE-01～09 |
| D7 | E2E-12～17 + 空闲/重写/审计 | GATE-10～14, M8-*, 冒烟 |


### 17.3 P1 建议验收顺序

1. M9 隔离与禁旁路  
2. M10 只读门控与确认写  
3. M12 点交付落盘（E2E-11）  
4. M11 T2 沙盒回灌 + 回门禁  
5. SEC-02/03/04  

### 17.4 出口标准

| 里程碑 | 出口 |
|--------|------|
| P0 Done | E2E-01～09、12～17 GWT 全过；**T-AGENT 自动跑通**（人工代发不算）；DEP-01～14 抽测；ENV-07a 有关键日志 |
| P1a Done | E2E-11 + M9/M10/M12 用例全过 |
| P1b Done | M11 用例全过；无跳过门禁完成态 |
| P2 | 按标题单独立项后再定出口 |

---


## 20. 修订记录


| 日期 | 说明 |
|------|------|
| 2026-07-23 | 初版 |
| 2026-07-23 | 高风险误解修复：对齐 spec 路径重命名、JudgeApprove、T-BUS/PROTO/AGENT、ChangedSet、私有思考/白板/空闲 |
| 2026-07-23 | P0 功能缺失补全：Worker/PROTO/BUS 细设计；净变更/全文重写/冲突不兼容；GATE E2E 勾选表；R2 例外 |
| 2026-07-23 | P1+细则+结构：ENV-07a、MCP/钩子/落盘状态、M1–M11 细则、M8a/b、SEC=M1 Key、GWT、P0 须自动 Agent |
| 2026-07-23 | 结构加固：正文按实现序重排（M3→M2、M8a/M8b 分章、BUS 前移）；覆盖对照与 Pass 语义表 |
| 2026-07-23 | T-ENV P0 完成：宿主/路径/工作区/ENV-07a；unittest+smoke 通过 |
| 2026-07-24 | T-M1 P0 完成：CRUD/探活/门禁/UI；SEC-01 DPAPI SecretStore；test_m1 A–E 通过 |
| 2026-07-24 | T-M3 P0 完成：身份/会话隔离/竞选/资格锁/降级A/签名/私有思考；test_m3 A–I 通过 |
| 2026-07-24 | T-M2 P0 完成：房间壳层/钉选/议程/资格锁/打断澄清/评判台控件/工作区越界；test_m2 A–G 通过 |
| 2026-07-24 | T-M8a P0 完成：phase 全表/预算/frozen 单一真相/升用户钩子；test_m8a A–D 通过 |
| 2026-07-24 | T-BUS P0 完成：pub/sub、§5.9 类型、JSONL 回放、ToolReceipt、Idle/Awake；test_bus 通过 |
| 2026-07-24 | T-PROTO P0 完成：工人/评议归一化、闲聊进私有、缺 claim 拒收、重试 2 次；test_proto 通过 |
| 2026-07-24 | T-AGENT P0 完成：W1–W4/J1 Prompt 编排、空闲门禁、静默通过/分工骨架、私有隔离；test_agent 通过 |
| 2026-07-25 | T-M4 P0 完成：切块/候选/版本/已读绑定/R2 作废/blockId 稳定；test_m4 A–E 通过 |
