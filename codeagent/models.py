"""核心数据模型：计划、清单、检查结果、运行报告。"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum


class StopReason(str, Enum):
    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class PlannedFile:
    path: str                   # 相对工作区路径（"/" 分隔）
    purpose: str
    est_lines: int = 0
    content: str = ""           # 生成后回填
    status: str = "pending"     # pending / done / failed

    def to_dict(self) -> dict:
        return {"path": self.path, "purpose": self.purpose,
                "est_lines": self.est_lines, "status": self.status}


@dataclass
class ProjectPlan:
    language: str
    summary: str
    directory_tree: list = field(default_factory=list)
    files: list = field(default_factory=list)       # list[PlannedFile]
    build_command: str = ""
    run_command: str = ""
    test_command: str = ""
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "summary": self.summary,
            "directory_tree": self.directory_tree,
            "files": [f.to_dict() for f in self.files],
            "build_command": self.build_command,
            "run_command": self.run_command,
            "test_command": self.test_command,
            "notes": self.notes,
        }

    def manifest(self) -> list[dict]:
        """供 LLM 使用的清单（含状态）。"""
        return self.to_dict()["files"]

    def find(self, path: str) -> PlannedFile | None:
        for f in self.files:
            if f.path == path:
                return f
        return None


@dataclass
class CheckFailure:
    path: str
    message: str
    kind: str = ""              # missing / empty / syntax / dependency / test / json

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class RunReport:
    stop_reason: StopReason = StopReason.ERROR
    steps: int = 0
    model_calls: int = 0
    tokens: dict = field(default_factory=dict)      # {prompt, completion}
    failures: list = field(default_factory=list)    # list[CheckFailure]
    plan: ProjectPlan | None = None
    output_dir: str = ""
    message: str = ""
    # 运行时验证执行情况：{"status": passed|failed|skipped|not_run, "detail": str}
    # 与 stop_reason 独立——生成完成不掩盖“检查未执行”
    verification: dict = field(default_factory=lambda: {"status": "not_run", "detail": ""})

    def to_dict(self) -> dict:
        return {
            "stop_reason": self.stop_reason.value,
            "steps": self.steps,
            "model_calls": self.model_calls,
            "tokens": self.tokens,
            "failures": [f.to_dict() for f in self.failures],
            "plan": self.plan.to_dict() if self.plan else None,
            "output_dir": self.output_dir,
            "message": self.message,
            "verification": dict(self.verification),
        }
