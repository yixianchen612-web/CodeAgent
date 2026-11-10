"""第 7 步：生成结果检查。

检查项（轻量、确定性，不做深度静态分析）:
1. 计划文件齐全且非空
2. 依赖声明文件存在（requirements.txt / package.json）
3. README.md 存在
4. tests/ 下至少一个测试文件
5. Python 语法检查（内置 compile，无外部依赖）
6. JSON 文件可解析
7. Node 文件语法检查（node --check，若本机有 node；没有则跳过）
8. package.json test 脚本规范（Node >=24 目录参数坑）
9. SQL DDL 建表覆盖代码中的表引用（真实数据库可用性）
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .models import CheckFailure, PlannedFile, ProjectPlan


def _check_python_syntax(path: Path) -> str | None:
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as e:
        return f"Python 语法错误: {e.msg} (行 {e.lineno})"
    return None


def _check_json(path: Path) -> str | None:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return f"JSON 解析失败: {e.msg} (行 {e.lineno})"
    return None


def _check_test_script(path: Path, plan: ProjectPlan) -> list[CheckFailure]:
    """package.json 的 test 脚本规范检查（仅 Node.js 项目）。

    实测: Node >=24 下 `node --test <目录>`（无论是否带尾斜杠）会把目录
    当作模块加载而报 MODULE_NOT_FOUND；必须用默认发现 `node --test` 或
    引号 glob 形式。逐 token 检查可容忍中间夹带的 Node flags。
    """
    if plan.language and plan.language.lower() != "nodejs":
        return []  # 非 Node 项目的 package.json（如前端附属文件）不做此规则
    pkg_path = path / "package.json"
    if not pkg_path.is_file():
        return []
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []  # JSON 语法错误由通用检查报告
    if not isinstance(pkg, dict):
        return [CheckFailure("package.json", "package.json 顶层必须是对象",
                             "test-script")]
    scripts = pkg.get("scripts")
    if not isinstance(scripts, dict):
        return [CheckFailure("package.json", "package.json 的 scripts 必须是对象",
                             "test-script")]
    script = str(scripts.get("test") or "").strip()
    if not script:
        return [CheckFailure("package.json", "缺少 test 脚本（scripts.test）",
                             "test-script")]
    m = re.match(r"^node\s+(.+)$", script)
    if m:
        for tok in m.group(1).split():
            if tok.startswith("--"):
                continue  # 跳过 --test 及中间夹带的 flags（如 --test-concurrency=1）
            arg = tok.strip('"').strip("'")
            if not arg or any(c in arg for c in "*?["):
                continue  # glob 形式合法
            if (path / arg).is_dir():
                return [CheckFailure(
                    "package.json",
                    f"test 脚本 {script!r} 把目录 {arg!r} 作为 --test 参数，"
                    f"Node >=24 下会报 MODULE_NOT_FOUND；请改用 node --test"
                    f"（默认发现 tests/ 下的 *.test.js）或引号 glob 形式 "
                    f"node --test \"tests/*.test.js\"",
                    "test-script")]
    return []


def _check_node_syntax(path: Path) -> str | None:
    if shutil.which("node") is None:
        return None  # 本机无 node → 跳过，不判失败
    proc = subprocess.run(["node", "--check", str(path)],
                          capture_output=True, text=True, timeout=60,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return f"node --check 失败: {(proc.stderr or '').strip()[:300]}"
    return None


# ---- SQL 表引用检测（真实数据库可用性） -----------------------------------

# 标识符：裸名 / 双引号 / 反引号 / SQL Server 方括号（含空格、保留字等）
_IDENT = r'(?:[A-Za-z_][A-Za-z0-9_]*|"[^"]*"|`[^`]*`|\[[^\]]*\])'
_IDENT_RE = re.compile(_IDENT, re.I)
# 限定名：schema.table / mydb.public.users / "public"."users" / `db`.`t`
_QUAL = _IDENT + r'(?:\s*\.\s*' + _IDENT + r')*'

# 表引用上下文：
#   (from|into|update|join) <qualified>   —— 排除 from x import ...、
#     ES "import ... from './x'"、Docker "FROM python:3.12"
#   (truncate|alter) table <qualified>
#   copy <qualified> (from|to)
_TABLE_REF_RE = re.compile(
    r"\b(?:from|into|update|join)\s+(?P<q1>" + _QUAL + r")(?!\s+import)(?![\w:])"
    r"|\b(?:truncate|alter)\s+table\s+(?P<q2>" + _QUAL + r")(?![\w:])"
    r"|\bcopy\s+(?P<q3>" + _QUAL + r")\s+(?:from|to)\b",
    re.I,
)

# CREATE TABLE：兼容 TEMP/TEMPORARY/UNLOGGED、IF NOT EXISTS、schema/引号限定名。
_CREATE_TABLE_RE = re.compile(
    r"\bcreate\s+(?:(?:temp|temporary|unlogged)\s+)?table\s+"
    r"(?:if\s+not\s+exists\s+)?(?P<name>" + _QUAL + r")",
    re.I,
)

# 字符串字面量（单/双引号、反引号模板串、Python 三引号）。SQL 查询就藏在里面。
_STRING_LITERAL_RE = re.compile(
    r"'''[\s\S]*?'''"
    r'|"""[\s\S]*?"""'
    r"'(?:[^'\\\n]|\\.)*'"
    r'"(?:[^"\\\n]|\\.)*"'
    r"`(?:[^`\\]|\\.)*`",
)

# SQL 语句标记：字符串/代码行里出现这些词才当作“含 SQL”去提取表引用，
# 从而避免把普通英文散文（"Failed to load questions from the database"）里的
# from/into/update/join 误判成表引用。
_SQL_MARKERS = (
    "select", "insert", "delete", "update", "merge", "truncate", "alter",
    "copy", "create", "drop", "where", "values", "returning", "group by",
    "order by", "having", "limit", "offset", "union", "on conflict",
)

# 系统 schema：这些 schema 下的视图（information_schema.tables、pg_catalog.pg_type）
# 不是应用自己的表，引用它们不视为缺失建表。
_SYSTEM_SCHEMAS = {"information_schema", "pg_catalog"}

# 这些 SQL 函数接受 "X FROM y" / "X PLACING y FROM z" 形式的参数，FROM 后面是
# 列/表达式而非表名——扫描前把函数实参整体挖掉，避免误判成表引用。
_SQL_FUNC_ARG_RE = re.compile(
    r"\b(?:extract|trim|substring|position|overlay)\s*\(([^()]*)\)", re.I)


def _blank_sql_func_args(sql_text: str) -> str:
    """挖掉 EXTRACT/TRIM/SUBSTRING/POSITION/OVERLAY 的实参（其中的 FROM 不是表引用）。"""
    def _repl(m):
        inner = m.group(1)
        return m.group(0).replace(inner, " " * len(inner))
    return _SQL_FUNC_ARG_RE.sub(_repl, sql_text)

# 非表名单词：协议/IO、英语虚词、SQL 关键字与函数、常见散文名词。
_NON_TABLE_WORDS = {
    "http", "https", "stdin", "stdout", "stderr",
    "the", "a", "an", "this", "that", "these", "those", "it", "its",
    "is", "be", "are", "was", "were", "of", "to", "in", "on", "at",
    "by", "for", "with", "as", "or", "and", "not", "no", "nor", "only",
    "lateral", "both", "each", "every", "other", "such", "same", "own",
    "all", "any", "some", "none", "null", "exc", "items", "item",
    "select", "where", "values", "set", "when", "then", "else", "if",
    "case", "end", "begin", "returning", "limit", "offset", "group",
    "order", "having", "union", "extract", "trim", "generate_series",
    "unnest", "information_schema", "now", "current_date", "current_timestamp",
    "database", "server", "cache", "file", "files", "error", "errors",
    "exception", "exceptions", "message", "messages", "result", "results",
    "record", "records", "data", "api", "import", "crashing", "crash",
}


def _strip_comment_state(text: str, line_markers, block: bool) -> str:
    """字符串感知的注释剥离：整行/行尾注释 + 跨行块注释，保留字符串内容。

    逐个字符推进并跟踪引号状态，保证 "http://..." 或 SQL 字符串里的
    // / -- 不会被误删；仅删除注释里的英文，避免其被误判为表引用。
    """
    out: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None  # '、` 或 "
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:  # 转义符，连下一字符一起保留
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            out.append(c)
            i += 1
            continue
        if block and c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        marker = next((mk for mk in line_markers if text.startswith(mk, i)), None)
        if marker is not None:
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _strip_comments(text: str, suffix: str) -> str:
    """按文件类型剥离注释（字符串感知）。"""
    if suffix == ".py":
        return _strip_comment_state(text, line_markers=("#",), block=True)
    if suffix == ".sql":
        return _strip_comment_state(text, line_markers=("--",), block=True)
    return _strip_comment_state(text, line_markers=("//",), block=True)


def _norm_ident(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"`[]":
        raw = raw[1:-1]
    return raw.lower()


def _last_ident(qualified: str) -> str | None:
    """限定名取最后一段作为表名（schema.table → table）。

    若限定名带系统 schema（information_schema.tables / pg_catalog.pg_type），
    返回 None——这些是系统视图，不是应用自己的表。
    """
    idents = _IDENT_RE.findall(qualified)
    if not idents:
        return None
    if len(idents) >= 2 and _norm_ident(idents[0]) in _SYSTEM_SCHEMAS:
        return None
    return _norm_ident(idents[-1])


def _refs_in_sql(sql_text: str) -> set[str]:
    """在已确认的 SQL 文本里提取被引用的表名集合。"""
    out: set[str] = set()
    for m in _TABLE_REF_RE.finditer(_blank_sql_func_args(sql_text)):
        q = m.group("q1") or m.group("q2") or m.group("q3")
        if not q:
            continue
        name = _last_ident(q)
        if name:
            out.add(name)
    return out


def _unquote(lit: str) -> str:
    if len(lit) >= 6 and lit[:3] == lit[-3:] and lit[:3] in ("'''", '"""'):
        return lit[3:-3]
    if len(lit) >= 2 and lit[0] == lit[-1] and lit[0] in "\"'`":
        return lit[1:-1]
    return lit


def _extract_used(text: str, suffix: str) -> set[str]:
    """从单个源文件里提取被引用的表名。

    .sql 文件整体即 SQL；其它文件里的 SQL 藏在字符串字面量中（现实场景），
    也可能以裸 SQL 单独成行（测试夹具/内联迁移）。两种都只在“含 SQL 标记”
    的文本里提取，避免英文散文被误判。
    """
    text = _strip_comments(text, suffix)
    if suffix == ".sql":
        return _refs_in_sql(text)
    used: set[str] = set()
    for lit in _STRING_LITERAL_RE.findall(text):
        body = _unquote(lit)
        low = body.lower()
        if any(mk in low for mk in _SQL_MARKERS):
            used |= _refs_in_sql(body)
    code_only = _STRING_LITERAL_RE.sub(" ", text)
    for line in code_only.splitlines():
        low = line.lower()
        if any(mk in low for mk in _SQL_MARKERS):
            used |= _refs_in_sql(line)
    return used


def _check_table_coverage(path: Path) -> list[CheckFailure]:
    """SQL DDL 建表必须覆盖代码引用的所有表（否则真实数据库初始化后功能必挂）。

    实测缺陷：样例 user.js 查询/写入 users 表，但交付 SQL 只有 games/questions
    建表，22 个测试用 mock 全部通过——真实数据库下注册/登录必失败。
    """
    sql_files = sorted(
        p for p in path.rglob("*.sql")
        if p.is_file() and ".codeagent_backup" not in p.parts
        and "node_modules" not in p.parts
    )
    if not sql_files:
        return []
    created: set[str] = set()
    for f in sql_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _strip_comments(text, ".sql")
        for m in _CREATE_TABLE_RE.finditer(text):
            name = _last_ident(m.group("name"))
            if name:
                created.add(name)
    if not created:
        return []
    used: set[str] = set()
    for src in sorted(path.rglob("*")):
        if not src.is_file() or ".codeagent_backup" in src.parts \
                or "node_modules" in src.parts:
            continue
        suffix = src.suffix.lower()
        if suffix not in (".js", ".ts", ".mjs", ".cjs", ".py", ".sql"):
            continue
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        used |= _extract_used(text, suffix)
    missing = sorted({t for t in used - created
                      if t not in _NON_TABLE_WORDS and len(t) > 2})
    if not missing:
        return []
    return [CheckFailure(
        "sql DDL",
        f"代码引用的表 {missing} 没有对应的 CREATE TABLE（真实数据库初始化后"
        f"查询会失败；若为误报请确认命名一致或补充建表语句）",
        "ddl-coverage")]


def check_project(workspace: Path, plan: ProjectPlan) -> list[CheckFailure]:
    failures: list[CheckFailure] = []
    workspace = Path(workspace)

    # 1. 计划文件齐全性（空文件豁免：requirements.txt / __init__.py 为合法常态）
    for f in plan.files:
        p = workspace / f.path
        if not p.is_file():
            failures.append(CheckFailure(f.path, "计划文件缺失", "missing"))
            f.status = "failed"
        elif p.stat().st_size == 0 and f.path != "requirements.txt" \
                and not f.path.endswith("__init__.py"):
            failures.append(CheckFailure(f.path, "文件为空", "empty"))
            f.status = "failed"

    # 2. 依赖声明
    if not ((workspace / "requirements.txt").is_file()
            or (workspace / "package.json").is_file()):
        failures.append(CheckFailure("requirements.txt|package.json",
                                     "缺少依赖声明文件", "dependency"))

    # 3. README
    if not (workspace / "README.md").is_file():
        failures.append(CheckFailure("README.md", "缺少项目 README", "missing"))

    # 4. 测试目录
    test_files = [p for p in workspace.glob("tests/**/*") if p.is_file()]
    if not test_files:
        failures.append(CheckFailure("tests/", "缺少测试用例", "test"))

    # 5-7. 语法与格式检查
    for p in sorted(workspace.rglob("*")):
        if not p.is_file() or ".codeagent_backup" in p.parts \
                or "node_modules" in p.parts:
            continue
        err = None
        if p.suffix == ".py":
            err = _check_python_syntax(p)
        elif p.suffix == ".json":
            err = _check_json(p)
        elif p.suffix == ".js":
            err = _check_node_syntax(p)
        if err:
            failures.append(CheckFailure(p.relative_to(workspace).as_posix(), err, "syntax"))

    # 8. test 脚本规范（package.json）
    failures += _check_test_script(workspace, plan)

    # 9. SQL DDL 建表覆盖代码表引用（真实数据库可用性）
    failures += _check_table_coverage(workspace)

    return failures


def failures_text(failures: list[CheckFailure]) -> str:
    if not failures:
        return "[]"
    return json.dumps([f.to_dict() for f in failures], ensure_ascii=False, indent=1)
