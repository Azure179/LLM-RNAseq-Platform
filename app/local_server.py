"""Own the local Streamlit process for a double-click launcher."""

from http.client import HTTPConnection
from pathlib import Path
import socket
import subprocess
from typing import IO
from uuid import uuid4


class LocalServer:
    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()
        self.process: subprocess.Popen | None = None
        self.log_handle: IO[bytes] | None = None
        self.log_path: Path | None = None
        self.port: int | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        python = self.root / ".venv" / "Scripts" / "python.exe"
        entry = self.root / "app" / "main.py"
        if not python.is_file() or not entry.is_file():
            raise RuntimeError("找不到项目 Python 环境或界面文件，请保持启动文件位于项目目录。")
        # A port owned by another process is left alone. Never kill/reuse it.
        for port in range(8501, 8511):
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    continue
                self.port = port
                break
        if self.port is None:
            raise RuntimeError("本地端口 8501–8510 已占用，请关闭不使用的旧实例。")
        directory = self.root / "logs" / "local_app"
        directory.mkdir(parents=True, exist_ok=True)
        self.log_path = directory / f"server-{uuid4().hex}.log"
        self.log_handle = self.log_path.open("ab", buffering=0)
        try:
            self.process = subprocess.Popen(
                [str(python), "-m", "streamlit", "run", str(entry),
                 "--server.address=127.0.0.1", f"--server.port={self.port}", "--server.headless=true"],
                cwd=self.root, stdin=subprocess.DEVNULL, stdout=self.log_handle,
                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            self.log_handle.close()
            raise RuntimeError("本地程序启动失败，请检查 Python 环境和目录权限。") from None

    def ready(self) -> bool:
        if self.process is None or self.process.poll() is not None:
            return False
        connection = HTTPConnection("127.0.0.1", self.port, timeout=0.4)
        try:
            connection.request("GET", "/_stcore/health")
            response = connection.getresponse()
            return response.status == 200 and response.read(64).strip() == b"ok"
        except (OSError, TimeoutError):
            return False
        finally:
            connection.close()

    def stop(self) -> None:
        # Only the child process created by this launcher is stopped.
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log_handle is not None:
            self.log_handle.close()
