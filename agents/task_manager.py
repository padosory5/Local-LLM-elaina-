from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core.paths import RUNTIME_ROOT


@dataclass
class AgentTask:
    id: str
    agent_id: str
    request: str
    status: str
    created_at: str
    updated_at: str
    message: str = ""


class AgentTaskManager:
    """Track agent work without allowing detached, invisible operations."""

    VALID_STATES = {
        "working",
        "input_required",
        "waiting_approval",
        "completed",
        "failed",
        "cancelled",
    }

    def __init__(self, audit_path: Path | None = None) -> None:
        self.audit_path = (
            audit_path
            or RUNTIME_ROOT / "audit" / "agent_tasks.jsonl"
        )
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, AgentTask] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def start(self, agent_id: str, request: str) -> AgentTask:
        now = self._now()
        task = AgentTask(
            id=uuid4().hex,
            agent_id=agent_id,
            request=request,
            status="working",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._tasks[task.id] = task
            self._append(task)
        return task

    def update(
        self,
        task_id: str,
        status: str,
        message: str = "",
    ) -> AgentTask:
        if status not in self.VALID_STATES:
            raise ValueError(f"Invalid task state: {status}")

        with self._lock:
            task = self._tasks[task_id]
            task.status = status
            task.message = str(message)
            task.updated_at = self._now()
            self._append(task)
            return task

    def get(self, task_id: str) -> AgentTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def _append(self, task: AgentTask) -> None:
        with self.audit_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(task), ensure_ascii=False) + "\n")
