"""CLI 入口：python -m codeagent [--mock] --doc A.md --views B.md --out DIR。

无 --mock 时使用 DeepSeek 官方 API（Key 取 DEEPSEEK_API_KEY 环境变量或 --api-key）。
"""

from __future__ import annotations

import argparse
import sys

from .config import AppConfig
from .llm import DeepSeekClient, MockLLMClient
from .orchestrator import CodeAgent


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CodeAgent —— 简易代码智能体（CLI）")
    p.add_argument("--doc", help="Architecture_Documentation.md 路径")
    p.add_argument("--views", help="Architecture_View.md 路径")
    p.add_argument("--out", default="./generated_project",
                   help="生成项目输出目录（每次任务在其下创建独立子目录 generated_<时间戳>/）")
    p.add_argument("--mock", action="store_true", help="使用 Mock LLM（离线联调，无需 API Key）")
    p.add_argument("--api-key", default="", help="DeepSeek API Key（优先取环境变量 DEEPSEEK_API_KEY）")
    p.add_argument("--model", default="", help="模型名（默认 deepseek-v4-pro）")
    p.add_argument("--base-url", default="", help="API 基础地址（默认 https://api.deepseek.com）")
    p.add_argument("--max-steps", type=int, default=0, help="Agent 最大步数")
    p.add_argument("--no-verify", action="store_true",
                   help="跳过生成后的运行时验证（npm install + npm test）")
    return p


def main(argv: list[str] | None = None) -> int:
    # GBK 控制台下输出 ✓/ℹ 等符号会 UnicodeEncodeError → 以替换字符降级
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    args = _build_parser().parse_args(argv)
    config = AppConfig.load()
    if args.api_key:
        config.api_key = args.api_key
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.max_steps:
        config.max_steps = args.max_steps
    if args.no_verify:
        config.verify_runtime = False
    config.doc_path = args.doc or config.doc_path
    config.views_path = args.views or config.views_path
    config.output_dir = args.out

    if not config.doc_path or not config.views_path:
        print("错误：需要 --doc 与 --views 两个输入文件", file=sys.stderr)
        return 2

    if args.mock:
        llm = MockLLMClient()
        print("[mock] 使用 Mock LLM（离线模式）")
    else:
        key = config.effective_api_key()
        if not key:
            print("错误：未配置 API Key（--api-key 或环境变量 DEEPSEEK_API_KEY）",
                  file=sys.stderr)
            return 2
        llm = DeepSeekClient(key, base_url=config.base_url, model=config.model,
                             timeout=config.request_timeout)

    def emit(ev: dict) -> None:
        print(f"[{ev['stage']}] {ev['message']}")

    agent = CodeAgent(config, llm, emit=emit)
    report = agent.run()
    print()
    print(f"结果: {report.stop_reason.value} | 模型调用 {report.model_calls} 次 | "
          f"tokens: {report.tokens}")
    print(f"说明: {report.message}")
    v = report.verification or {}
    vlabel = {"passed": "运行时验证通过", "failed": "运行时验证失败",
              "skipped": "运行时验证未执行",
              "not_run": "运行时验证未执行"}.get(v.get("status"), "运行时验证未执行")
    if v.get("detail"):
        vlabel += f"（{v['detail']}）"
    print(f"运行时验证: {vlabel}")
    if report.failures:
        print("遗留问题:")
        for f in report.failures:
            print(f"  - {f.path}: {f.message}")
    return 0 if report.stop_reason.value == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
