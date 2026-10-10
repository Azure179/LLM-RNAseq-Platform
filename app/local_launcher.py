"""Windows local launcher: own the server, show status, open the browser when ready."""

import argparse
import logging
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.local_server import LocalServer


class Launcher:
    def __init__(self, *, open_browser: bool = True) -> None:
        self.window = tk.Tk()
        self.window.title("RNA-seq 本地程序")
        self.window.geometry("520x260")
        self.window.resizable(False, False)
        self.server = LocalServer(ROOT)
        self.open_browser = open_browser
        self.started = time.monotonic()
        self.was_ready = False
        panel = ttk.Frame(self.window, padding=24)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="RNA-seq Workspace", font=("Microsoft YaHei UI", 16)).pack(anchor="w")
        self.status = tk.StringVar(value="正在启动本地程序…")
        ttk.Label(panel, textvariable=self.status, wraplength=465).pack(anchor="w", pady=(16, 12))
        ttk.Label(panel, text="分析期间请保持此窗口打开。\n关闭浏览器后，可以点击下方按钮重新打开界面。",
                  wraplength=465).pack(anchor="w")
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x", pady=(24, 0))
        self.open_button = ttk.Button(buttons, text="打开分析界面", command=self.open_page, state="disabled")
        self.open_button.pack(side="left")
        ttk.Button(buttons, text="退出本地程序", command=self.close).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

    def open_page(self) -> None:
        if not webbrowser.open(self.server.url):
            messagebox.showinfo("打开分析界面", f"请在浏览器中打开：\n{self.server.url}", parent=self.window)

    def poll(self) -> None:
        process = self.server.process
        if process is None or process.poll() is not None:
            code = process.returncode if process is not None else "未启动"
            self.status.set(f"程序已停止（退出码 {code}）。日志：{self.server.log_path}")
            self.open_button.configure(state="disabled")
            return
        if not self.was_ready:
            if self.server.ready():
                self.was_ready = True
                self.status.set(f"正在运行：{self.server.url}")
                self.open_button.configure(state="normal")
                if self.open_browser:
                    self.open_page()
            elif time.monotonic() - self.started > 45:
                self.server.stop()
                self.status.set(f"启动超时，请查看日志：{self.server.log_path}")
                return
        self.window.after(500, self.poll)

    def close(self) -> None:
        workspace = Path(os.environ.get("RNASEQ_WORKSPACE_ROOT", str(ROOT / "workspace")))
        try:
            busy = any(workspace.glob("*/runtime/chat.lock"))
        except OSError:
            messagebox.showerror("无法检查分析状态", "请确认分析已结束，再关闭程序。", parent=self.window)
            return
        if busy:
            messagebox.showinfo("分析尚未结束", "检测到正在运行或中断后遗留的聊天任务。请先检查分析状态和项目日志，再退出。",
                                parent=self.window)
            return
        self.server.stop()
        self.window.destroy()

    def run(self) -> None:
        try:
            self.server.start()
        except (RuntimeError, OSError) as error:
            self.status.set(str(error))
        else:
            self.window.after(100, self.poll)
        self.window.mainloop()


def check_startup() -> int:
    """Explicit smoke check: starts/stops only Streamlit; no browser, R or API."""
    server = LocalServer(ROOT)
    try:
        server.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if server.ready():
                logging.info("Local startup verified: %s; log=%s", server.url, server.log_path)
                return 0
            if server.process.poll() is not None:
                break
            time.sleep(0.25)
        logging.error("Startup failed: exit=%s; log=%s", server.process.poll(), server.log_path)
        return 1
    finally:
        server.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="RNA-seq local application launcher")
    parser.add_argument("--check", action="store_true", help="Verify startup and stop, without opening a window or browser")
    parser.add_argument("--no-browser", action="store_true", help="Show the control window without automatically opening the browser")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if options.check:
        return check_startup()
    Launcher(open_browser=not options.no_browser).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
