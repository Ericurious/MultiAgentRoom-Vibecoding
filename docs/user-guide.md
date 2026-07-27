# MultiAgentRoom 使用指导书（v0.1）

| 项 | 内容 |
|----|------|
| 产品 | 多 Agent 聊天室（Chat Review v2）本机宿主 |
| 版本 | 0.1.0 |
| 平台 | Windows 10/11，Python ≥ 3.11（打包后终端用户可不装 Python） |
| UI | 自带 tkinter 桌面界面（非 Web；无单独后端进程） |
| 数据根 | `%AppData%\MultiAgentRoom\` |

---

## 1. 你想要的启动方式：双击即可

本应用是**单进程桌面程序**（GUI + 业务同进程），**不需要**先开 PowerShell、也不需要另起「后端服务」。

### 1.1 日常使用（推荐）

| 操作 | 文件 |
|------|------|
| 双击启动（无黑框） | 项目根目录 `启动 MultiAgentRoom.vbs` |
| 双击启动 | `启动 MultiAgentRoom.bat` |
| 桌面图标 | 运行一次 `scripts\create_desktop_shortcut.ps1` |

逻辑：

1. 若已存在 `dist\MultiAgentRoom\MultiAgentRoom.exe` → **直接启动 exe**  
2. 否则用本机 `pythonw` 静默启动源码 GUI（仍无需你手敲命令）

### 1.2 打包成独立 exe（给自己或分发）

在任意 PowerShell 中执行**一次**：

```powershell
cd D:\CursorProject
.\scripts\build_exe.ps1 -CreateDesktopShortcut
```

| 产物 | 路径 |
|------|------|
| 主程序 | `dist\MultiAgentRoom\MultiAgentRoom.exe` |
| 构建依赖 | `requirements-build.txt`（仅打包机需要 PyInstaller） |

打包后：

- 双击 **exe** 或桌面「多 Agent 聊天室」快捷方式即可  
- 可将整个 `dist\MultiAgentRoom\` 文件夹拷到别的 Windows 机器使用（目标机一般**不必装 Python**）  
- 数据仍写在用户 `%AppData%\MultiAgentRoom\`，与是否打包无关  

> 说明：当前 UI 即产品界面（暖色三栏）。后续若换成 WinUI/WebView 等，仍可用同一「单 exe 双击」分发模型。

---

## 2. 其它启动接口（开发 / 验收）

### 2.1 带控制台调试

```powershell
cd D:\CursorProject
$env:PYTHONPATH = "D:\CursorProject\src"
python -m multi_agent_room
```

### 2.2 无界面冒烟

```powershell
python -m multi_agent_room --smoke
```

### 2.3 测试

```powershell
python -m unittest discover -s tests -q
```

### 2.4 编程入口

| 接口 | 位置 |
|------|------|
| `main()` | `multi_agent_room.app` |
| `python -m multi_agent_room` | `__main__.py` |
| `RoomService` | 房间业务门面 |
| `is_frozen()` | `runtime.py`（判断是否 exe） |

---

## 3. 首次使用（GUI 五步）

1. **模型配置** → Base URL / Key → 探活至 ready  
2. **Agent 成员** → 绑定就绪模型（建议工人 + 评议）  
3. **工作区** → 选择本机目录（点「交付」前需要）  
4. **聊天室** → 新房间 → 邀请 → **提问并钉选**  
5. 审阅 / 评判台 → `JudgeApprove` → **交付**

正常路径：`提问 → 首答 → 审阅/补丁 → 评判 →（确认轮）→ JudgeApprove → 最终回复 → 交付`

---

## 4. 界面地图

```
顶栏：聊天室 | 模型配置 | Agent 成员 | 工作区
左：房间列表 / 邀请
中：议程 · 共享稿 · 提问 · 评判台 · 交付
右：phase / 成员状态
```

---

## 5. 故障排查

| 现象 | 处理 |
|------|------|
| 双击无反应 | 看 `%AppData%\MultiAgentRoom\logs\`；或改用 `python -m multi_agent_room` 看报错 |
| 提示找不到 Python | 安装 Python 3.11+ **勾选 Add to PATH**，或先 `build_exe.ps1` |
| 打包失败 | `python -m pip install -U pyinstaller` 后重跑构建脚本 |
| 杀毒误报 | PyInstaller 常见；可加白名单或改用 onedir 分发整个文件夹 |
| 点交付失败 | 先评议通过并写入最终回复，或 AuthorizeDeliver |

---

## 6. 快速对照卡

```text
日常：双击「启动 MultiAgentRoom.vbs」或桌面快捷方式
打包：.\scripts\build_exe.ps1 -CreateDesktopShortcut
调试：$env:PYTHONPATH=...\src ; python -m multi_agent_room
冒烟：python -m multi_agent_room --smoke
```
