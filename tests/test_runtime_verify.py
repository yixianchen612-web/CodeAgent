"""运行时验证闸门单元测试（不触发真实 npm 安装）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codeagent.config import AppConfig  # noqa: E402
from codeagent.llm import MockLLMClient  # noqa: E402
from codeagent.models import CheckFailure, StopReason  # noqa: E402
from codeagent.orchestrator import (CodeAgent, _final_stop,  # noqa: E402
                                    _sanitize_model_note,
                                    _summarize_test_output, _zero_tests)


class TestSummarizeTestOutput(unittest.TestCase):
    def test_parses_failures_and_assertions(self):
        out = """\
ℹ tests 10
ℹ pass 7
ℹ fail 3
test at tests\\game.test.js:24:1
✖ POST /games with token returns 201 (189.5064ms)
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:
    actual: 500,
    expected: 201,
✖ failing tests:

✖ POST /games with token returns 201 (189.5064ms)
"""
        s = _summarize_test_output(out)
        self.assertIn("npm test 失败", s)
        self.assertIn("POST /games with token returns 201", s)
        self.assertIn("actual: 500", s)
        self.assertIn("expected: 201", s)

    def test_parses_jest(self):
        out = """\
FAIL tests/game.test.js
  ● Game endpoints › POST /play/:gameId/answer evaluates an answer

    expect(received).toBe(expected) // Object.is equality

    Expected: 200
    Received: 500

      80 |       .send({ questionId: 1, answer: '3/4' });
      81 |
    > 82 |     expect(res.status).toBe(200);

      at Object.<anonymous> (tests/game.test.js:82:22)

Test Suites: 1 failed, 2 passed, 3 total
Tests:       9 passed, 1 failed, 10 total
"""
        s = _summarize_test_output(out)
        self.assertIn("Tests:       9 passed, 1 failed", s)
        self.assertIn("Game endpoints › POST /play/:gameId/answer evaluates an answer", s)
        self.assertIn("Expected: 200", s)
        self.assertIn("Received: 500", s)

    def test_parses_jest_stack_without_node_modules(self):
        out = """\
Tests: 1 failed, 2 total
  ● Console

    console.error
      [ERROR] Cannot read properties of undefined (reading 'rows') {"stack":"TypeError: Cannot read properties of undefined (reading 'rows')\\n    at Object.rows [as random] (E:\\\\x\\\\src\\\\models\\\\question.js:91:19)"}

      at error (src/utils/logger.js:17:13)
      at error (src/middleware/errorHandler.js:10:12)
      at Layer.handle_error (node_modules/express/lib/router/layer.js:71:5)
"""
        s = _summarize_test_output(out)
        self.assertIn("TypeError", s)
        self.assertIn("question.js:91", s)
        self.assertNotIn("node_modules", s)
        self.assertNotIn("Console", s.split("失败用例")[-1])

    def test_module_not_found(self):
        s = _summarize_test_output(
            "Error: Cannot find module 'E:\\x\\tests'\n    at Module._resolveFilename")
        self.assertIn("Cannot find module", s)

    def test_pairs_after_failing_marker(self):
        # node:test 把断言行放在 "✖ failing tests:" 标记之后（实测）→ 必须扫全文
        out = """\
ℹ tests 3
ℹ fail 1
✖ POST /games with token returns 201 (189.5ms)
✖ failing tests:

test at tests\\game.test.js:24:1
✖ POST /games with token returns 201 (189.5ms)
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:
  actual: 500,
  expected: 201,
"""
        s = _summarize_test_output(out)
        self.assertIn("actual: 500", s)
        self.assertIn("expected: 201", s)

    def test_empty_ok(self):
        self.assertIn("npm test 失败", _summarize_test_output(""))


class TestZeroTests(unittest.TestCase):
    def test_node_test_runner_zero(self):
        self.assertTrue(_zero_tests("ℹ tests 0\nℹ pass 0\nℹ fail 0"))

    def test_jest_pass_with_no_tests(self):
        self.assertTrue(_zero_tests("Test Suites: 0 skipped, 0 total\nTests:       0 total"))

    def test_normal_run_not_zero(self):
        self.assertFalse(_zero_tests("ℹ tests 12\nℹ pass 12\nℹ fail 0"))
        self.assertFalse(_zero_tests("Tests:       9 passed, 1 failed, 10 total"))


class TestFinalStop(unittest.TestCase):
    def test_completed_clean(self):
        s, m = _final_stop(StopReason.COMPLETED, "ok", [])
        self.assertEqual(s, StopReason.COMPLETED)

    def test_max_steps_clean_is_completed(self):
        s, m = _final_stop(StopReason.MAX_STEPS, "达到上限", [])
        self.assertEqual(s, StopReason.COMPLETED)
        self.assertIn("检查全部通过", m)
        self.assertIn("运行时验证未执行", m)

    def test_max_steps_clean_verified_message(self):
        s, m = _final_stop(StopReason.MAX_STEPS, "达到上限", [],
                           verify_status="passed")
        self.assertEqual(s, StopReason.COMPLETED)
        self.assertIn("运行时验证通过", m)

    def test_max_steps_clean_skipped_message(self):
        s, m = _final_stop(StopReason.MAX_STEPS, "达到上限", [],
                           verify_status="skipped", verify_detail="本机无 node/npm")
        self.assertEqual(s, StopReason.COMPLETED)
        self.assertIn("运行时验证未执行", m)
        self.assertIn("本机无 node/npm", m)

    def test_max_steps_with_failures(self):
        f = [CheckFailure("a.py", "缺失", "missing")]
        s, m = _final_stop(StopReason.MAX_STEPS, "达到上限", f)
        self.assertEqual(s, StopReason.MAX_STEPS)

    def test_completed_with_failures_is_error(self):
        f = [CheckFailure("a.py", "缺失", "missing")]
        s, m = _final_stop(StopReason.COMPLETED, "ok", f)
        self.assertEqual(s, StopReason.ERROR)

    def test_completed_passed_only_then_claims_tests_passed(self):
        # 只有 verify_status == "passed"（真实 npm test 通过）才允许出现“运行时验证通过”
        s, m = _final_stop(StopReason.COMPLETED, "", [], verify_status="passed")
        self.assertEqual(s, StopReason.COMPLETED)
        self.assertIn("运行时验证通过", m)
        s, m = _final_stop(StopReason.COMPLETED, "", [], verify_status="not_run")
        self.assertNotIn("运行时验证通过", m)
        self.assertIn("运行时验证未执行", m)

    def test_probe_model_claims_tests_passed_but_verify_skipped(self):
        # 实测探针：模型摘要写 “All tests passed”，而运行时验证实际未执行
        # （本机无 node/npm）→ 结论不得同时声称“全部通过”与“未执行”。
        note = "All tests passed. Generated 39 files for the game."
        s, m = _final_stop(StopReason.COMPLETED, note, [],
                           verify_status="skipped", verify_detail="本机无 node/npm")
        self.assertEqual(s, StopReason.COMPLETED)
        self.assertNotIn("tests passed", m.lower())
        self.assertNotIn("All tests", m)
        self.assertIn("运行时验证未执行", m)
        self.assertIn("本机无 node/npm", m)
        # 摘要中“生成了哪些内容”的描述保留
        self.assertIn("Generated 39 files", m)

    def test_probe_chinese_claims_stripped_content_kept(self):
        note = ("所有测试全部通过。生成了 39 个文件，"
                "含 tests/ 下 27 个用例与四屏前端页面。")
        s, m = _final_stop(StopReason.COMPLETED, note, [],
                           verify_status="skipped", verify_detail="本机无 node/npm")
        self.assertNotIn("全部通过", m.replace("静态检查全部通过", ""))
        self.assertIn("运行时验证未执行", m)
        self.assertIn("39 个文件", m)
        self.assertIn("四屏前端", m)

    def test_model_claims_stripped_on_failure_branch(self):
        # 失败分支同样不得保留模型“测试通过”的自述
        f = [CheckFailure("a.py", "缺失", "missing")]
        s, m = _final_stop(StopReason.COMPLETED, "All tests passed!", f)
        self.assertEqual(s, StopReason.ERROR)
        self.assertNotIn("passed", m)
        self.assertIn("检查未全部通过", m)

    def test_max_steps_note_is_program_text_not_sanitized_away(self):
        # MAX_STEPS 分支的 note 是程序文本（步数上限），原文保留
        s, m = _final_stop(StopReason.MAX_STEPS, "达到最大步数上限 30", [])
        self.assertIn("达到最大步数上限 30", m)

    def test_max_steps_message_no_content_duplication(self):
        # 对抗审查 finding：程序文本曾被标成“生成内容”重复出现
        s, m = _final_stop(StopReason.MAX_STEPS, "达到最大步数上限 30", [],
                           verify_status="skipped", verify_detail="本机无 node/npm")
        self.assertNotIn("生成内容", m)
        self.assertEqual(m.count("达到最大步数上限 30"), 1)

    def test_empty_model_note_pure_program_conclusion(self):
        # 模型无工具调用且 content 为空 → 纯程序结论，无“生成内容”附注
        s, m = _final_stop(StopReason.COMPLETED, "", [],
                           verify_status="skipped", verify_detail="本机无 node/npm")
        self.assertNotIn("生成内容", m)
        self.assertIn("运行时验证未执行", m)

    def test_probe_confirmed_contradictions_fixed(self):
        # 对抗审查逐条实测确认的矛盾文案（note 结论词 + “运行时验证未执行”同句）
        cases = [
            ("27/27 passed. Generated 39 files.", "passed", "Generated 39 files"),
            ("全部通过。生成了 39 个文件。", "全部通过", "39 个文件"),
            ("所有测试成功。生成了 39 个文件。", "成功", "39 个文件"),
            ("zero failures. Generated 39 files.", "failures", "Generated 39 files"),
            ("验证完成，全部通过。生成了 39 个文件。", "全部通过", "39 个文件"),
            ("All 27 tests are green. Generated 39 files.", "green",
             "Generated 39 files"),
        ]
        for note, banned, kept in cases:
            s, m = _final_stop(StopReason.COMPLETED, note, [],
                               verify_status="skipped", verify_detail="本机无 node/npm")
            self.assertNotIn(banned, m.replace("静态检查全部通过", ""),
                             f"结论词泄漏: {note!r} -> {m!r}")
            self.assertIn(kept, m, note)
            self.assertIn("运行时验证未执行", m, note)


class TestSanitizeModelNote(unittest.TestCase):
    def test_keeps_content_only(self):
        note = "Generated a web UI with 4 screens and a public/ folder."
        self.assertEqual(_sanitize_model_note(note),
                         "Generated a web UI with 4 screens and a public/ folder.")

    def test_strips_english_claims(self):
        for bad in ["All tests passed", "27/27 tests passed", "tests pass",
                    "npm test succeeded", "0 failures", "Jest passed all suites",
                    "unittest ran clean", "assertions all good"]:
            self.assertEqual(_sanitize_model_note(bad), "", bad)

    def test_strips_chinese_claims(self):
        for bad in ["所有测试通过", "测试全部通过", "用例均通过", "检查通过",
                    "全部检查通过", "断言通过", "测试未执行", "用例失败",
                    "零失败", "运行时验证通过", "本机无 node/npm"]:
            self.assertEqual(_sanitize_model_note(bad), "", bad)

    def test_claim_sentence_dropped_content_sentence_kept(self):
        note = "All tests passed. Generated 39 files including a tests/ dir with 27 cases."
        clean = _sanitize_model_note(note)
        self.assertNotIn("passed", clean)
        self.assertIn("39 files", clean)
        self.assertIn("tests/ dir", clean)  # 目录描述不是通过/失败结论，保留

    def test_chinese_sentence_split(self):
        note = "所有测试通过；生成了四屏前端页面。README 已包含数据库初始化步骤。"
        clean = _sanitize_model_note(note)
        self.assertNotIn("通过", clean)
        self.assertIn("四屏前端", clean)
        self.assertIn("数据库初始化", clean)

    def test_version_numbers_not_mis_split(self):
        # 句点后跟数字不应造成误删（v1.0.0、Node 24. 等）
        note = "使用 Node.js v24. Express 5.1 与 PostgreSQL。"
        clean = _sanitize_model_note(note)
        self.assertIn("Express", clean)

    def test_empty_and_short_notes(self):
        self.assertEqual(_sanitize_model_note(""), "")
        # “ok” 本身是结论词（短句规则剥离）；短注文的附注与否由 _final_stop 决定
        self.assertEqual(_sanitize_model_note("ok"), "")

    # —— 对抗审查确认的绕过路径回归（10 项发现的 sanitizer 部分）——

    def test_standalone_pass_claims_stripped(self):
        # 无测试词的独立结论句：“27/27 passed”“全部通过”“all passed”
        # “验收通过”“验证完成，全部通过”等也必须剥离
        for bad in ["27/27 passed", "全部通过", "all passed", "all pass!",
                    "验收通过", "验证完成，全部通过", "已验证", "验证完成",
                    "PASS全部", "全部PASS", "测试pass", "测试OK"]:
            self.assertEqual(_sanitize_model_note(bad), "", bad)

    def test_success_synonym_claims_stripped(self):
        # success/green/OK/成功 等同义词与测试词共现时剥离
        for bad in ["所有测试成功", "已生成 27 个文件，所有测试成功",
                    "All 27 tests successful", "测试全部OK", "所有用例OK",
                    "All tests green", "The test suite is green",
                    "Tests are all good", "27/27 green", "所有用例全绿",
                    "npm-test all green", "All test cases are successful."]:
            self.assertEqual(_sanitize_model_note(bad), "", bad)

    def test_zero_fail_word_forms_stripped(self):
        # zero/no/none 等英文词形与中文“没有失败/没有问题”
        for bad in ["zero failures", "no failures", "Zero failures, zero errors",
                    "no errors", "No failures at all", "没有失败", "没有问题"]:
            self.assertEqual(_sanitize_model_note(bad), "", bad)

    def test_fullwidth_and_homoglyph_claims_stripped(self):
        # 全角字母（NFKC 折叠）与常见同形异体字母（西里尔 е/р/а）
        for bad in ["ｔｅｓｔｓ ｐａｓｓｅｄ", "ＴＥＳＴＳ ＰＡＳＳＥＤ",
                    "all tеsts passed", "tests раssed"]:
            self.assertEqual(_sanitize_model_note(bad), "", bad)

    def test_vague_everything_works_stripped(self):
        self.assertEqual(_sanitize_model_note("Everything works."), "")
        self.assertEqual(_sanitize_model_note("Everything is fine."), "")

    def test_generation_statements_kept(self):
        # 弱结论词（成功）描述“生成了什么”时保留，不误伤内容描述
        self.assertEqual(_sanitize_model_note("成功生成了 39 个文件。"),
                         "成功生成了 39 个文件。")
        self.assertIn("39 个文件",
                      _sanitize_model_note("成功生成了 39 个文件，含四屏前端页面。"))

    def test_password_and_network_not_false_positive(self):
        # pass/fail/work 等词带词边界：password/network 等普通名词不误伤
        clean = _sanitize_model_note("README 记录 password 哈希与 network 调用方式。")
        self.assertIn("password", clean)
        self.assertIn("network", clean)


class TestRuntimeVerifySkip(unittest.TestCase):
    def _agent(self, out_dir: str, verify: bool = True) -> CodeAgent:
        config = AppConfig(doc_path="a.md", views_path="b.md",
                           output_dir=out_dir, verify_runtime=verify)
        return CodeAgent(config, MockLLMClient())

    def test_skip_without_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "requirements.txt").write_text("", encoding="utf-8")
            agent = self._agent(tmp)
            agent.workspace = ws
            events = []
            agent.emit = events.append
            self.assertEqual(agent._runtime_verify(None), [])
            self.assertTrue(any("跳过" in e["message"] for e in events))
            self.assertEqual(agent._verify_status, "skipped")
            self.assertIn("无 package.json", agent._verify_detail)

    def test_skip_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "package.json").write_text("{}", encoding="utf-8")
            agent = self._agent(tmp, verify=False)
            agent.workspace = ws
            events = []
            agent.emit = events.append
            self.assertEqual(agent._runtime_verify(None), [])
            self.assertEqual(events, [])
            self.assertEqual(agent._verify_status, "skipped")
            self.assertIn("关闭", agent._verify_detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
