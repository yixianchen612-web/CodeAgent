"""工具执行器单元测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codeagent.tools import TOOL_SCHEMAS, ToolExecutor  # noqa: E402


class TestToolExecutor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self.ex = ToolExecutor(self.ws)

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_and_read(self):
        result, marker = self.ex.execute("write_file",
                                         {"path": "src/a.py", "content": "x = 1\n"})
        self.assertEqual(result["ok"], True)
        self.assertIsNone(marker)
        self.assertTrue((self.ws / "src" / "a.py").is_file())
        result, _ = self.ex.execute("read_file", {"path": "src/a.py"})
        self.assertEqual(result["content"], "x = 1\n")

    def test_write_files_batch(self):
        result, _ = self.ex.execute("write_files",
                                    {"files": [{"path": "a.txt", "content": "1"},
                                               {"path": "b.txt", "content": "2"}]})
        self.assertEqual(len(result["written"]), 2)
        self.assertTrue((self.ws / "b.txt").is_file())

    def test_path_traversal_rejected(self):
        for bad in ["../evil.py", "..\\evil.py", "a/../../evil.py",
                    "C:/evil.py", "/abs/evil.py"]:
            result, _ = self.ex.execute("write_file", {"path": bad, "content": "x"})
            self.assertIn("error", result, f"应拒绝路径: {bad}")
        self.assertFalse((self.ws.parent / "evil.py").exists())

    def test_run_check_whitelist(self):
        result, _ = self.ex.execute("run_check", {"command": "rm -rf /"})
        self.assertIn("白名单", result.get("error", ""))
        # 白名单命令可以真实执行
        self.ex.execute("write_file", {"path": "m.py", "content": "print(1)\n"})
        result, _ = self.ex.execute("run_check", {"command": "python -m py_compile m.py"})
        self.assertEqual(result.get("exit_code"), 0)

    def test_run_check_injection_rejected(self):
        # 历史漏洞：前缀匹配 + shell=True 时 "& 其他命令" 会被执行
        for cmd in ["node --check a.js & echo hacked",
                    "node --check a.js; npm test",
                    "npm test && del /f a.js",
                    "python -m unittest `rm -rf /`"]:
            result, _ = self.ex.execute("run_check", {"command": cmd})
            self.assertIn("error", result, f"应拒绝注入: {cmd!r}")
            self.assertNotIn("exit_code", result)

    def test_run_check_rejects_wrong_shape(self):
        # 白名单可执行文件但调用形态不在允许列表内
        result, _ = self.ex.execute("run_check", {"command": "python -m http.server 8000"})
        self.assertIn("error", result)
        result, _ = self.ex.execute("run_check", {"command": "node -e \"require('x')\""})
        self.assertIn("error", result)
        result, _ = self.ex.execute("run_check", {"command": "npm install evil"})
        self.assertIn("error", result)

    def test_run_check_python_uses_current_interpreter(self):
        import codeagent.tools as tools_mod
        self.assertEqual(tools_mod._resolve_check_exe("python"), sys.executable)

    def test_run_check_frozen_python_requires_system_interpreter(self):
        # 打包后 sys.executable 是 CodeAgent.exe，不能当解释器（PyInstaller 行为）
        import unittest.mock as mock
        import codeagent.tools as tools_mod
        with mock.patch.object(tools_mod.sys, "frozen", True, create=True), \
             mock.patch.object(tools_mod.shutil, "which", return_value=None):
            self.assertIsNone(tools_mod._resolve_check_exe("python"))
            result, _ = self.ex.execute("run_check",
                                        {"command": "python -m py_compile m.py"})
            self.assertIn("打包版", result.get("error", ""))

    def test_run_check_node_resolves_via_path(self):
        import unittest.mock as mock
        import codeagent.tools as tools_mod
        with mock.patch.object(tools_mod.shutil, "which", return_value=None):
            result, _ = self.ex.execute("run_check", {"command": "node --check a.js"})
            self.assertIn("找不到可执行文件", result.get("error", ""))

    def test_node_check_rejects_option_flags(self):
        # 历史漏洞：node --check 承诺纯语法检查，但 --require/--import 会执行
        # 任意 JS 且退出码 0（实测确认），必须在形态校验后拒绝一切 "-" 开头参数。
        self.ex.execute("write_file", {"path": "dummy.js", "content": "const x = 1;\n"})
        self.ex.execute("write_file", {"path": "evil.js",
                                       "content": "require('fs').writeFileSync('PWNED.txt','x');\n"})
        for cmd in ["node --check --require ./evil.js dummy.js",
                    "node --check --import ./evil.js dummy.js",
                    "node --check --require=./evil.js dummy.js"]:
            result, _ = self.ex.execute("run_check", {"command": cmd})
            self.assertIn("error", result, f"应拒绝选项注入: {cmd!r}")
            self.assertNotIn("exit_code", result)
        self.assertFalse((self.ws / "PWNED.txt").exists())

    def test_run_check_quoted_path(self):
        # 历史漏洞：shlex(posix=False) 保留引号，带引号路径被当作带引号的文件名
        self.ex.execute("write_file", {"path": "m.py", "content": "print(1)\n"})
        result, _ = self.ex.execute("run_check",
                                    {"command": 'python -m py_compile "m.py"'})
        self.assertEqual(result.get("exit_code"), 0)

    def test_expand_globs(self):
        # node --check 的引号 glob 在 shell=False 下由执行器自行展开
        self.ex.execute("write_file", {"path": "tests/a.test.js", "content": "// a\n"})
        self.ex.execute("write_file", {"path": "tests/b.test.js", "content": "// b\n"})
        expanded = self.ex._expand_globs(["tests/*.test.js"])
        self.assertEqual(len(expanded), 2)
        self.assertTrue(any(p.endswith("a.test.js") for p in expanded))
        self.assertTrue(any(p.endswith("b.test.js") for p in expanded))

    def test_real_exe_rejects_store_stub(self):
        import codeagent.tools as tools_mod
        stub = "C:\\Users\\u\\AppData\\Local\\Microsoft\\WindowsApps\\python.EXE"
        self.assertFalse(tools_mod._real_exe(stub))
        self.assertFalse(tools_mod._real_exe(None))
        self.assertFalse(tools_mod._real_exe(""))

    def test_resolve_check_exe_frozen_skips_store_stub(self):
        # 打包版优先 py.exe，且跳过 0 字节的 WindowsApps 商店占位 stub
        import unittest.mock as mock
        import codeagent.tools as tools_mod
        stub = "C:\\Users\\u\\AppData\\Local\\Microsoft\\WindowsApps\\python.EXE"
        real_py = "C:\\Windows\\py.EXE"

        def fake_which(name):
            return {"py": real_py, "python": stub}.get(name)

        with mock.patch.object(tools_mod.sys, "frozen", True, create=True), \
             mock.patch.object(tools_mod.shutil, "which", side_effect=fake_which), \
             mock.patch.object(tools_mod, "_real_exe",
                               side_effect=lambda p: bool(p) and "windowsapps" not in p.lower()):
            self.assertEqual(tools_mod._resolve_check_exe("python"), real_py)
            self.assertEqual(tools_mod._resolve_check_exe("py"), real_py)

    def test_complete_and_fail_markers(self):
        _, marker = self.ex.execute("complete", {"summary": "done"})
        self.assertEqual(marker, "complete")
        _, marker = self.ex.execute("fail", {"reason": "cannot"})
        self.assertEqual(marker, "fail")

    def test_list_files(self):
        self.ex.execute("write_file", {"path": "x/y.txt", "content": "1"})
        result, _ = self.ex.execute("list_files", {})
        self.assertIn("x/", result["files"])
        self.assertIn("x/y.txt", result["files"])

    def test_all_schemas_present(self):
        names = {t["function"]["name"] for t in TOOL_SCHEMAS}
        self.assertEqual(names, {"write_file", "write_files", "read_file",
                                 "list_files", "run_check", "complete", "fail"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
