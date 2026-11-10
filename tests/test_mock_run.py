"""端到端离线联调：Mock LLM + 真实输入文档 + 完整状态机。

验证: Plan → Generate → Check → Finalize 全流程，无需 API Key。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codeagent.config import AppConfig  # noqa: E402
from codeagent.llm import MockLLMClient  # noqa: E402
from codeagent.models import StopReason  # noqa: E402
from codeagent.orchestrator import (CodeAgent, _check_ui_coverage,  # noqa: E402
                                    fallback_plan, validate_plan)

TASK_DIR = ROOT / "input"
if not TASK_DIR.is_dir():
    TASK_DIR = ROOT.parent / "Task (1)"  # compatibility with the original checkout
DOC_PATH = TASK_DIR / "Architecture_Documentation.md"
VIEWS_PATH = TASK_DIR / "Architecture_View.md"


class TestPlanValidation(unittest.TestCase):
    def test_valid_plan(self):
        plan = validate_plan({
            "language": "python", "summary": "s",
            "files": [
                {"path": "README.md", "purpose": "r", "est_lines": 10},
                {"path": "requirements.txt", "purpose": "d", "est_lines": 1},
                {"path": "tests/test_x.py", "purpose": "t", "est_lines": 10},
            ],
        })
        self.assertEqual(len(plan.files), 3)

    def test_reject_bad_paths(self):
        for bad in ["../x.py", "C:/x.py", "/x.py"]:
            with self.assertRaises(Exception):
                validate_plan({
                    "language": "py",
                    "files": [
                        {"path": "README.md", "purpose": ""},
                        {"path": "requirements.txt", "purpose": ""},
                        {"path": "tests/t.py", "purpose": ""},
                        {"path": bad, "purpose": ""},
                    ],
                })

    def test_reject_missing_required(self):
        with self.assertRaises(Exception):
            validate_plan({"language": "py",
                           "files": [{"path": "a.py", "purpose": ""}]})

    def test_fallback_plan_covers_deliverables(self):
        spec = {"documentation": {"deliverables": ["openapi.yaml", "sql/game_ddl.sql"]}}
        plan = fallback_plan(spec)
        paths = {f.path for f in plan.files}
        self.assertIn("openapi.yaml", paths)
        self.assertIn("sql/game_ddl.sql", paths)
        self.assertIn("README.md", paths)
        self.assertTrue(any(p.startswith("tests/") for p in paths))

    def _backend_plan(self):
        return validate_plan({
            "language": "nodejs", "summary": "s",
            "files": [
                {"path": "README.md", "purpose": "r", "est_lines": 10},
                {"path": "package.json", "purpose": "d", "est_lines": 10},
                {"path": "src/server.js", "purpose": "a", "est_lines": 40},
                {"path": "tests/test_server.js", "purpose": "t", "est_lines": 20},
            ],
        })

    def test_ui_coverage_required_when_spec_has_ui(self):
        """架构描述浏览器界面 → 只有后端 API 的计划必须被拒绝。"""
        with self.assertRaises(Exception):
            _check_ui_coverage(self._backend_plan(),
                               {"analysis": {"has_ui_requirement": True}})

    def test_ui_coverage_pass_with_frontend(self):
        plan = validate_plan({
            "language": "nodejs", "summary": "s",
            "files": [
                {"path": "README.md", "purpose": "r", "est_lines": 10},
                {"path": "package.json", "purpose": "d", "est_lines": 10},
                {"path": "src/server.js", "purpose": "a", "est_lines": 40},
                {"path": "public/index.html", "purpose": "ui", "est_lines": 30},
                {"path": "tests/test_server.js", "purpose": "t", "est_lines": 20},
            ],
        })
        _check_ui_coverage(plan, {"analysis": {"has_ui_requirement": True}})  # 不抛

    def test_ui_coverage_skipped_without_ui_requirement(self):
        _check_ui_coverage(self._backend_plan(),
                           {"analysis": {"has_ui_requirement": False}})  # 不抛

    def test_fallback_plan_adds_ui_files_when_needed(self):
        spec = {"analysis": {"has_ui_requirement": True},
                "documentation": {"deliverables": ["openapi.yaml"]}}
        plan = fallback_plan(spec)
        paths = {f.path for f in plan.files}
        self.assertIn("public/index.html", paths)
        self.assertIn("public/app.js", paths)
        self.assertIn("public/style.css", paths)


@unittest.skipUnless(DOC_PATH.exists() and VIEWS_PATH.exists(),
                     "未找到任务输入文档，跳过")
class TestMockEndToEnd(unittest.TestCase):
    def test_full_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(
                doc_path=str(DOC_PATH), views_path=str(VIEWS_PATH),
                output_dir=str(Path(tmp) / "out"), max_steps=30, repair_rounds=2)
            events = []
            agent = CodeAgent(config, MockLLMClient(), emit=events.append)
            report = agent.run()

            self.assertEqual(report.stop_reason, StopReason.COMPLETED,
                             f"报告: {report.message}")
            self.assertEqual(report.failures, [])
            self.assertGreater(report.model_calls, 3)
            self.assertGreater(report.steps, 0)

            # 输出在所选目录下的独立子目录（不直接占用用户目录）
            out = Path(report.output_dir)
            self.assertTrue(out.name.startswith("generated_"))
            # Mock 生成的是 Python 项目（无 package.json）→ 运行时验证如实记为未执行
            self.assertEqual(report.verification["status"], "skipped")
            for expected in ["README.md", "requirements.txt", "src/game.py",
                             "tests/test_game.py", "Dockerfile",
                             "project_plan.json", "run_report.json"]:
                self.assertTrue((out / expected).is_file(),
                                f"缺少生成文件: {expected}")

            # 生成的项目测试可真实运行（Mock 内容有效）
            report_json = json.loads((out / "run_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report_json["stop_reason"], "completed")

            # 生成的单元测试本身可跑（python -m unittest）
            import subprocess
            proc = subprocess.run([sys.executable, "-m", "unittest"],
                                  cwd=str(out), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=120)
            self.assertEqual(proc.returncode, 0, f"生成项目测试失败:\n{proc.stdout}\n{proc.stderr}")

    def test_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(doc_path=str(DOC_PATH), views_path=str(VIEWS_PATH),
                               output_dir=str(Path(tmp) / "out"))
            agent = CodeAgent(config, MockLLMClient())
            agent.cancel()
            report = agent.run()
            self.assertEqual(report.stop_reason, StopReason.CANCELLED)


@unittest.skipUnless(DOC_PATH.exists() and VIEWS_PATH.exists(),
                     "未找到任务输入文档，跳过")
class TestPreflightWorkspace(unittest.TestCase):
    """输出目录处理：每次任务建独立子目录，绝不移动用户已有文件。"""

    def test_keeps_existing_files_and_creates_subdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "out"
            base.mkdir()
            keep = base / "keep.txt"
            keep.write_text("keep", encoding="utf-8")
            config = AppConfig(doc_path=str(DOC_PATH), views_path=str(VIEWS_PATH),
                               output_dir=str(base))
            agent = CodeAgent(config, MockLLMClient())
            report = agent.run()
            self.assertEqual(report.stop_reason, StopReason.COMPLETED)
            self.assertTrue(keep.is_file(), "用户已有文件不得被移动")
            self.assertFalse((base / ".codeagent_backup").exists())
            ws = Path(report.output_dir)
            self.assertTrue(ws.name.startswith("generated_"))
            self.assertEqual(ws.parent, base)

    def test_input_files_inside_output_dir_survive(self):
        """输入文件位于所选输出目录内：先移动后读取失败的历史缺陷。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "out"
            base.mkdir()
            doc = base / "my_doc.md"
            doc.write_text("# minimal", encoding="utf-8")
            views = base / "my_views.md"
            views.write_text("# minimal", encoding="utf-8")
            config = AppConfig(doc_path=str(doc), views_path=str(views),
                               output_dir=str(base))
            agent = CodeAgent(config, MockLLMClient())
            report = agent.run()
            # 输入文件必须原样保留在输出目录内（无论生成是否成功）
            self.assertTrue(doc.is_file(), "输入文档被移动了")
            self.assertTrue(views.is_file(), "输入视图被移动了")
            self.assertFalse((base / ".codeagent_backup").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
