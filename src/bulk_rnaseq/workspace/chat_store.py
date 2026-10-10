"""Project-isolated conversation history and at-most-once submission records."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID, uuid4

from bulk_rnaseq.llm.json_utils import unique_object, reject_constant
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workspace import ProjectError, ProjectManager


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    def __init__(self, manager: ProjectManager, project_key: str) -> None:
        self.manager, self.key = manager, project_key

    def path(self, relative: str) -> Path:
        root = self.manager.open_project(self.key).root
        runtime = self.manager._safe_path(root / "runtime")
        path = self.manager._safe_path(runtime / relative).resolve()
        if not path.is_relative_to(runtime):
            raise ProjectError("CHAT_PATH_INVALID", "聊天记录必须位于当前项目 runtime 目录。")
        return path

    def read(self, path: Path, *, limit: int = 8 * 1024 * 1024) -> dict[str, Any]:
        try:
            with path.open("rb") as handle:
                data = handle.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Oversize history")
            result = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
            if not isinstance(result, dict):
                raise ValueError("Expected object")
            return result
        except (OSError, ValueError, TypeError):
            raise ProjectError("CHAT_RECORD_INVALID", "聊天记录无法读取；现有文件未被覆盖。") from None

    def history(self) -> list[dict[str, Any]]:
        path = self.path("chat_history.json")
        if not path.exists():
            return []
        data = self.read(path)
        rows = data.get("messages")
        if data.get("version") != 1 or not isinstance(rows, list) or any(
            not isinstance(row, dict) or row.get("role") not in {"user", "assistant"}
            or not isinstance(row.get("content"), str) or not isinstance(row.get("timestamp"), str)
            for row in rows
        ):
            raise ProjectError("CHAT_RECORD_INVALID", "聊天记录格式异常，请先检查记录。")
        for row in rows:
            result = row.get("result", {})
            if (not isinstance(row.get("request_id"), str) or not isinstance(row.get("status"), str)
                    or not isinstance(result, dict) or not isinstance(result.get("artifacts", []), list)
                    or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                           or not isinstance(item.get("path"), str) for item in result.get("artifacts", []))):
                raise ProjectError("CHAT_RECORD_INVALID", "聊天结果记录格式异常，请先检查记录。")
        return rows

    def append(self, messages: list[dict[str, Any]]) -> None:
        rows = self.history()
        known = {(row.get("request_id"), row["role"]) for row in rows}
        rows.extend(row for row in messages if (row["request_id"], row["role"]) not in known)
        payload = {"version": 1, "messages": rows}
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 7 * 1024 * 1024:
            raise ProjectError("CHAT_HISTORY_FULL", "聊天历史已达本地上限；请先清空聊天，不要重复运行已有分析。")
        write_json(self.path("chat_history.json"), payload)

    @contextmanager
    def lock(self) -> Iterator[None]:
        path = self.path("chat.lock")
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(now())
        except FileExistsError:
            raise ProjectError("CHAT_BUSY", "Analysis in progress；若程序曾中断，请先检查运行状态，不要直接重试。") from None
        try:
            yield
        finally:
            path.unlink(missing_ok=True)

    def save_assistant(self, message: dict[str, Any]) -> None:
        """Update a paused source-choice response, not append a duplicate conversation turn."""
        rows = self.history()
        for index, row in enumerate(rows):
            if row["role"] == "assistant" and row["request_id"] == message["request_id"]:
                rows[index] = message
                payload = {"version": 1, "messages": rows}
                if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 7 * 1024 * 1024:
                    raise ProjectError("CHAT_HISTORY_FULL", "聊天历史已达本地上限。")
                write_json(self.path("chat_history.json"), payload)
                return
        self.append([message])

    def request_path(self, request_id: str) -> Path:
        try:
            if str(UUID(request_id)) != request_id:
                raise ValueError("Noncanonical UUID")
        except (ValueError, TypeError, AttributeError):
            raise ProjectError("INVALID_REQUEST_ID", "请求编号无效。") from None
        directory = self.path("chat_requests")
        directory.mkdir(exist_ok=True)
        return self.manager._safe_path(directory / (request_id + ".json"))

    def previous(self, request_id: str) -> dict[str, Any] | None:
        path = self.request_path(request_id)
        return self.read(path) if path.exists() else None

    def save_request(self, request_id: str, value: dict[str, Any]) -> None:
        write_json(self.request_path(request_id), value)

    def clear(self, *, confirm: bool = False) -> None:
        if confirm is not True:
            raise ProjectError("CLEAR_CHAT_CONFIRMATION_REQUIRED", "请确认仅清空聊天历史。")
        with self.lock():
            source = self.path("chat_history.json")
            if source.exists():
                self.history()  # Refuse to discard a corrupt record implicitly.
                archive = self.path("chat_archive")
                archive.mkdir(exist_ok=True)
                source.rename(self.manager._safe_path(archive / (str(uuid4()) + ".json")))
            write_json(source, {"version": 1, "messages": []})
