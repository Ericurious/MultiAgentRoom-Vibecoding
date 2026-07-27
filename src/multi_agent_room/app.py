"""主入口：默认启动 Cat Café 风格 Web UI；`--tk` 保留旧桌面壳。"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox

from multi_agent_room import APP_DISPLAY_NAME, __version__
from multi_agent_room.config import load_config, save_config
from multi_agent_room.logging_setup import (
    latest_log_file,
    log_event,
    log_fatal,
    log_phase_change,
    setup_logging,
)
from multi_agent_room.paths import get_appdata_root
from multi_agent_room.runtime import is_frozen


def _run_web(argv: list[str]) -> int:
    from multi_agent_room.web_server import serve_forever

    port = 8765
    host = "127.0.0.1"
    open_browser = "--no-browser" not in argv
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
        if a.startswith("--port="):
            port = int(a.split("=", 1)[1])
    serve_forever(host=host, port=port, open_browser=open_browser)
    return 0


def _run_tk() -> int:
    from multi_agent_room.shell import AppShell
    from multi_agent_room.theme import Theme, apply_theme, page_header

    class MainWindow:
        def __init__(self) -> None:
            self.config = load_config()
            log_event("startup", f"{APP_DISPLAY_NAME} v{__version__} 启动 (tk)")
            log_phase_change("None", "Idle")

            self.root = tk.Tk()
            self.root.title(f"{APP_DISPLAY_NAME}")
            self.root.geometry(
                f"{max(self.config.window_width, 1280)}x{max(self.config.window_height, 800)}"
            )
            self.root.minsize(1080, 680)
            apply_theme(self.root)

            self.shell = AppShell(self.root, config=self.config)
            self.shell.pack(fill=tk.BOTH, expand=True)

            self._mount_panels()
            self.shell.show("room")
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        def _mount_panels(self) -> None:
            from multi_agent_room.agents_panel import AgentsPanel
            from multi_agent_room.models_panel import ModelsPanel
            from multi_agent_room.room_panel import RoomPanel

            models_wrap = tk.Frame(self.shell.models_host, bg=Theme.BG)
            models_wrap.pack(fill=tk.BOTH, expand=True, padx=24, pady=20)
            page_header(
                models_wrap,
                "模型配置",
                "只需 API 地址与 Key；探活成功后即可绑定为房间大脑。",
            )
            self.models_panel = ModelsPanel(models_wrap)
            self.models_panel.pack(fill=tk.BOTH, expand=True)

            model_svc = self.models_panel.service

            agents_wrap = tk.Frame(self.shell.agents_host, bg=Theme.BG)
            agents_wrap.pack(fill=tk.BOTH, expand=True, padx=24, pady=20)
            page_header(
                agents_wrap,
                "Agent 成员",
                "持久身份、竞选与资格锁 — 仅绑定已就绪模型。",
            )
            self.agents_panel = AgentsPanel(agents_wrap, models=model_svc)
            self.agents_panel.pack(fill=tk.BOTH, expand=True)

            self.room_panel = RoomPanel(
                self.shell.room_host,
                agents=self.agents_panel.service,
                models=model_svc,
            )
            self.room_panel.pack(fill=tk.BOTH, expand=True)

        def _on_close(self) -> None:
            self.config.window_width = self.root.winfo_width()
            self.config.window_height = self.root.winfo_height()
            save_config(self.config)
            log_event("shutdown", "主窗口关闭")
            self.root.destroy()

        def run(self) -> None:
            self.root.mainloop()

    MainWindow().run()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    setup_logging()

    if "--smoke" in argv:
        cfg = load_config()
        save_config(cfg)
        log_event("smoke", "ENV smoke ok")
        log_phase_change("None", "Idle")
        from multi_agent_room.model_service import ModelService

        svc = ModelService()
        print("SMOKE_OK")
        print(f"APPDATA={get_appdata_root()}")
        print(f"LOG={latest_log_file()}")
        print(f"MODELS={len(svc.list_models())} BINDABLE={len(svc.list_bindable())}")
        return 0

    try:
        if "--tk" in argv:
            return _run_tk()
        log_event("startup", f"{APP_DISPLAY_NAME} v{__version__} 启动 (web)")
        log_phase_change("None", "Idle")
        return _run_web(argv)
    except Exception as exc:  # noqa: BLE001
        log_fatal(exc)
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                APP_DISPLAY_NAME,
                f"启动失败：\n{exc}\n\n详情见日志：\n{latest_log_file() or '(无)'}",
            )
            root.destroy()
        except Exception:  # noqa: BLE001
            if not is_frozen():
                print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
