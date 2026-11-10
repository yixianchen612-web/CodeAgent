"""应用配置：GUI 可编辑，持久化到 %APPDATA%\\CodeAgent\\config.json。

安全约定：API Key 只保存在内存中（或从环境变量 DEEPSEEK_API_KEY 读取），
永远不写入磁盘配置。
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, fields
from pathlib import Path


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "CodeAgent"


CONFIG_PATH = _config_dir() / "config.json"


@dataclass
class AppConfig:
    # API（key 仅内存）
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    # 生成参数
    temperature: float = 0.2
    max_tokens: int = 16384
    # Agent 运行上限
    max_steps: int = 50
    repair_rounds: int = 2
    request_timeout: int = 300
    # 运行时验证闸门：生成后真实执行 npm install + npm test（仅 Node.js 项目）
    verify_runtime: bool = True
    # 输入输出
    doc_path: str = ""      # Architecture_Documentation.md
    views_path: str = ""    # Architecture_View.md
    output_dir: str = ""    # 生成项目输出目录

    def effective_api_key(self) -> str:
        return self.api_key or os.environ.get("DEEPSEEK_API_KEY", "")

    def save(self) -> None:
        """保存配置（剔除 api_key）。"""
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = dataclasses.asdict(self)
        data.pop("api_key", None)
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_PATH.exists():
            return cls()
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        data.pop("api_key", None)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
