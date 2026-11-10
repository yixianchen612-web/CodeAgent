"""检查器单元测试。"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codeagent.checker import _check_table_coverage, check_project  # noqa: E402
from codeagent.models import PlannedFile, ProjectPlan  # noqa: E402


def _plan(files: list[tuple[str, str]], language: str = "python") -> ProjectPlan:
    return ProjectPlan(language=language, summary="t",
                       files=[PlannedFile(path=p, purpose=pur) for p, pur in files])


class TestChecker(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, path: str, content: str) -> None:
        p = self.ws / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def test_all_green(self):
        self._write("README.md", "# hi")
        self._write("requirements.txt", "")
        self._write("src/app.py", "print('hi')\n")
        self._write("tests/test_app.py", "import unittest\n")
        plan = _plan([("README.md", "readme"), ("requirements.txt", "deps"),
                      ("src/app.py", "app"), ("tests/test_app.py", "test")])
        self.assertEqual(check_project(self.ws, plan), [])

    def test_missing_planned_file(self):
        self._write("README.md", "# hi")
        self._write("requirements.txt", "")
        self._write("tests/test_x.py", "import unittest\n")
        plan = _plan([("README.md", "r"), ("requirements.txt", "d"),
                      ("src/missing.py", "m"), ("tests/test_x.py", "t")])
        failures = check_project(self.ws, plan)
        kinds = {f.kind for f in failures}
        self.assertIn("missing", kinds)

    def test_syntax_error(self):
        self._write("README.md", "# hi")
        self._write("requirements.txt", "")
        self._write("src/bad.py", "def broken(:\n")
        self._write("tests/test_x.py", "import unittest\n")
        plan = _plan([("README.md", "r"), ("requirements.txt", "d"),
                      ("src/bad.py", "b"), ("tests/test_x.py", "t")])
        failures = check_project(self.ws, plan)
        self.assertTrue(any(f.kind == "syntax" and "bad.py" in f.path for f in failures))

    def test_bad_json(self):
        self._write("README.md", "# hi")
        self._write("requirements.txt", "")
        self._write("package.json", "{not json")
        self._write("tests/t.js", "x")
        plan = _plan([("README.md", "r"), ("requirements.txt", "d"),
                      ("package.json", "p"), ("tests/t.js", "t")])
        failures = check_project(self.ws, plan)
        self.assertTrue(any("package.json" in f.path for f in failures))

    def test_missing_readme_and_deps_and_tests(self):
        plan = _plan([("src/a.py", "a")])
        failures = check_project(self.ws, plan)
        kinds = {f.kind for f in failures}
        self.assertTrue({"dependency", "missing", "test"} <= kinds)


class TestTestScriptRule(unittest.TestCase):
    """package.json 的 test 脚本规范（Node >=24 目录参数失效，实测）。"""

    def _check(self, script=None, extra: dict | None = None,
               language: str = "nodejs") -> list:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "README.md").write_text("# hi", encoding="utf-8")
            (ws / "tests").mkdir()
            (ws / "tests" / "t.test.js").write_text("// t\n", encoding="utf-8")
            pkg = {"name": "t", "version": "1.0.0", "scripts": {}}
            if script is not None:
                pkg["scripts"]["test"] = script
            if extra:
                pkg.update(extra)
            (ws / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
            plan = _plan([("README.md", "r"), ("package.json", "p"),
                          ("tests/t.test.js", "t")], language=language)
            return check_project(ws, plan)

    def _kinds(self, failures: list) -> set:
        return {f.kind for f in failures}

    def test_missing_script(self):
        self.assertIn("test-script", self._kinds(self._check()))

    def test_reject_dir_with_slash(self):
        failures = self._check("node --test tests/")
        self.assertTrue(any(f.kind == "test-script" for f in failures))

    def test_reject_bare_dir(self):
        failures = self._check("node --test tests")
        self.assertTrue(any(f.kind == "test-script" for f in failures))

    def test_reject_dir_after_flags(self):
        failures = self._check("node --test --test-concurrency=1 tests/")
        self.assertTrue(any(f.kind == "test-script" for f in failures))

    def test_reject_non_dict_pkg(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "README.md").write_text("# hi", encoding="utf-8")
            (ws / "tests").mkdir()
            (ws / "tests" / "t.test.js").write_text("// t\n", encoding="utf-8")
            (ws / "package.json").write_text("[]", encoding="utf-8")
            plan = _plan([("README.md", "r"), ("package.json", "p"),
                          ("tests/t.test.js", "t")], language="nodejs")
            failures = check_project(ws, plan)
        self.assertTrue(any(f.kind == "test-script" for f in failures))

    def test_reject_non_dict_scripts(self):
        failures = self._check(extra={"scripts": "node --test tests/"})
        self.assertTrue(any(f.kind == "test-script" for f in failures))

    def test_skip_for_non_nodejs_plan(self):
        # Python 项目带一个附属 package.json → 不做 test 脚本规则
        self.assertNotIn("test-script",
                         self._kinds(self._check(language="python")))

    def test_accept_default_discovery(self):
        self.assertNotIn("test-script", self._kinds(self._check("node --test")))

    def test_accept_glob(self):
        self.assertNotIn("test-script",
                         self._kinds(self._check('node --test "tests/*.test.js"')))

    def test_accept_file_arg(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "README.md").write_text("# hi", encoding="utf-8")
            (ws / "tests").mkdir()
            (ws / "tests" / "t.test.js").write_text("// t\n", encoding="utf-8")
            (ws / "test").mkdir()
            (ws / "test" / "single.test.js").write_text("// s\n", encoding="utf-8")
            pkg = {"name": "t", "version": "1.0.0",
                   "scripts": {"test": "node --test test/single.test.js"}}
            (ws / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
            plan = _plan([("README.md", "r"), ("package.json", "p"),
                          ("tests/t.test.js", "t")], language="nodejs")
            failures = check_project(ws, plan)
        self.assertNotIn("test-script", {f.kind for f in failures})

    def test_accept_jest(self):
        self.assertNotIn("test-script", self._kinds(self._check("jest")))


class TestTableCoverage(unittest.TestCase):
    """SQL DDL 建表必须覆盖代码引用的表（真实数据库可用性，实测缺陷）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, path: str, content: str) -> None:
        p = self.ws / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def test_missing_table_detected(self):
        # 样例缺陷复现：user.js 用 users 表，DDL 只有 games/questions
        self._write("sql/001_schema.sql",
                    "CREATE TABLE games (id INT);\nCREATE TABLE questions (id INT);\n")
        self._write("src/user.js",
                    "pool.query('SELECT * FROM users WHERE name = $1')\n")
        failures = _check_table_coverage(self.ws)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].kind, "ddl-coverage")
        self.assertIn("users", failures[0].message)

    def test_full_coverage_ok(self):
        self._write("sql/001_schema.sql",
                    "CREATE TABLE users (id INT);\nCREATE TABLE games (id INT);\n")
        self._write("src/user.js",
                    "INSERT INTO users (name) VALUES ('a');\n"
                    "SELECT g.id FROM games g JOIN users u ON u.id = g.uid;\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_no_ddl_no_rule(self):
        self._write("src/user.js", "SELECT * FROM users\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_python_from_import_not_a_table(self):
        self._write("sql/001_schema.sql", "CREATE TABLE users (id INT);\n")
        self._write("src/app.py", "from pathlib import Path\nimport json\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_case_insensitive_match(self):
        # 拼写一致、仅大小写不同（PG 未加引号标识符折叠为小写）→ 视为已覆盖
        self._write("sql/001_schema.sql", "CREATE TABLE Users (id INT);\n")
        self._write("src/user.js", "SELECT * FROM USERS\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_spelling_mismatch_flagged(self):
        # UserScores 与 user_scores 是不同表名（真实数据库查询会失败）
        self._write("sql/001_schema.sql", "CREATE TABLE UserScores (id INT);\n")
        self._write("src/score.js", "SELECT * FROM user_scores\n")
        failures = _check_table_coverage(self.ws)
        self.assertEqual(len(failures), 1)

    def test_ddl_files_themselves_not_scanned(self):
        # DDL 里 SELECT/INSERT 填充数据不参与引用统计
        self._write("sql/001_schema.sql",
                    "CREATE TABLE games (id INT);\n"
                    "INSERT INTO games VALUES (1);\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_comment_prose_not_a_table(self):
        # 实测误报："from crashing" 出现在注释英文里（整行 // 注释）
        self._write("sql/001_schema.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.js",
                    "// Prevent the process from crashing on idle client errors.\n"
                    "const q = 'SELECT * FROM games';\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_python_hash_comment_not_a_table(self):
        self._write("sql/001_schema.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.py",
                    "# data comes from games table\n"
                    "def q():\n    return 'SELECT id FROM games'\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_schema_qualified_ref(self):
        self._write("sql/001_schema.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.js", "const q = 'SELECT * FROM public.games';\n")
        self.assertEqual(_check_table_coverage(self.ws), [])


class TestTableCoverageAdvanced(unittest.TestCase):
    """对抗式审查确认的 checker 表覆盖规则缺陷（#16-22）的回归测试。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, path: str, content: str) -> None:
        p = self.ws / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def _missing(self, failures: list) -> set:
        # 从失败消息里抠出被判缺失的表名集合
        names: set = set()
        for f in failures:
            m = re.search(r"\[([^\]]*)\]", f.message)
            if m:
                names |= {x.strip().strip("'\"") for x in m.group(1).split(",")}
        return names

    # ---- #16: CREATE TABLE 解析（引号/限定名/TEMP/UNLOGGED/IF NOT EXISTS/注释） ----

    def test_quoted_create_covered(self):
        self._write("sql/s.sql", 'CREATE TABLE "users" (id INT);\n'
                                  'CREATE TABLE "games" (id INT);\n')
        self._write("src/app.js", "pool.query('SELECT * FROM users')\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_if_not_exists_quoted_covered(self):
        self._write("sql/s.sql", 'CREATE TABLE IF NOT EXISTS "users" (id INT);\n')
        self._write("src/app.js", "pool.query('SELECT * FROM users')\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_schema_qualified_create_covered(self):
        self._write("sql/s.sql", "CREATE TABLE public.users (id INT);\n")
        self._write("src/app.js", "pool.query('SELECT * FROM users')\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_unlogged_create_covered(self):
        self._write("sql/s.sql", "CREATE UNLOGGED TABLE users (id INT);\n")
        self._write("src/app.js", "pool.query('SELECT * FROM users')\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_commented_create_is_not_real(self):
        # 被注释掉的 CREATE 不算建表，引用该表仍应报缺
        self._write("sql/s.sql", "-- CREATE TABLE users (id INT);\n"
                                 "CREATE TABLE games (id INT);\n")
        self._write("src/app.js", "pool.query('SELECT * FROM users')\n")
        failures = _check_table_coverage(self.ws)
        self.assertEqual(len(failures), 1)
        self.assertEqual(self._missing(failures), {"users"})

    # ---- #17: 引用侧引号/反引号/三段名/TRUNCATE/ALTER/COPY ----

    def test_backtick_usage_detected(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.js", 'pool.query("INSERT INTO `users` (id) VALUES (1)")\n')
        self.assertEqual(self._missing(_check_table_coverage(self.ws)), {"users"})

    def test_double_quote_usage_detected(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.js", "pool.query('DELETE FROM \"users\" WHERE id = 1')\n")
        self.assertEqual(self._missing(_check_table_coverage(self.ws)), {"users"})

    def test_three_part_name_detected(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.js", "pool.query('SELECT * FROM mydb.public.users')\n")
        self.assertEqual(self._missing(_check_table_coverage(self.ws)), {"users"})

    def test_truncate_detected(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.js", "pool.query('TRUNCATE TABLE user_scores')\n")
        self.assertEqual(self._missing(_check_table_coverage(self.ws)), {"user_scores"})

    def test_alter_table_detected(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.js", "pool.query('ALTER TABLE user_scores ADD COLUMN x INT')\n")
        self.assertEqual(self._missing(_check_table_coverage(self.ws)), {"user_scores"})

    def test_copy_table_not_stdin(self):
        self._write("sql/schema.sql", "CREATE TABLE games (id INT);\n")
        self._write("sql/load.sql", "COPY user_scores FROM stdin;\n")
        failures = _check_table_coverage(self.ws)
        self.assertEqual(self._missing(failures), {"user_scores"})

    # ---- #18: 注释/文档字符串/raise...from 泄漏 ----

    def test_trailing_comment_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.js", "const x = 1; // seeded from users\n"
                                  "const q = 'SELECT * FROM games';\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_multiline_block_comment_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.js", "/*\n seeded from users\n*/\n"
                                  "const q = 'SELECT * FROM games';\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_python_docstring_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.py", '"""Fetch a record from the database."""\n'
                                   'def q():\n    return "SELECT id FROM games"\n')
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_raise_from_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.py", 'def f(exc):\n    raise RuntimeError("x") from exc\n')
        self.assertEqual(_check_table_coverage(self.ws), [])

    # ---- #19: SQL 函数/关键字语法 ----

    def test_extract_arg_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE employees (id INT, hire_date DATE);\n")
        self._write("src/app.js",
                    "pool.query('SELECT EXTRACT(YEAR FROM hire_date) FROM employees')\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_from_only_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE users (id INT);\n")
        self._write("src/app.js", "pool.query('SELECT * FROM ONLY users')\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_information_schema_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE users (id INT);\n")
        self._write("src/app.js",
                    "pool.query('SELECT table_name FROM information_schema.tables')\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_generate_series_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE users (id INT);\n")
        self._write("src/app.js", "pool.query('SELECT * FROM generate_series(1, 10)')\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    # ---- #20: DDL 发现范围与 .sql/.mjs 扫描 ----

    def test_ddl_in_subdir_discovered(self):
        self._write("db/schema.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.js", "pool.query('SELECT * FROM users')\n")
        self.assertEqual(self._missing(_check_table_coverage(self.ws)), {"users"})

    def test_sql_queries_file_scanned(self):
        self._write("sql/schema.sql", "CREATE TABLE games (id INT);\n")
        self._write("sql/queries.sql", "INSERT INTO users (id) VALUES (1);\n")
        self.assertEqual(self._missing(_check_table_coverage(self.ws)), {"users"})

    def test_mjs_file_scanned(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/app.mjs", "pool.query('SELECT * FROM users')\n")
        self.assertEqual(self._missing(_check_table_coverage(self.ws)), {"users"})

    # ---- #22: 字符串/文档里的英文散文 ----

    def test_prose_string_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/db.py", 'PY_MSG = "Failed to load questions from the database"\n')
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_join_the_game_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/ui.js", "const t = 'Join the game';\n")
        self.assertEqual(_check_table_coverage(self.ws), [])

    def test_update_the_score_not_a_table(self):
        self._write("sql/s.sql", "CREATE TABLE games (id INT);\n")
        self._write("src/ui.js", "const t = 'Could not update the score';\n")
        self.assertEqual(_check_table_coverage(self.ws), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
