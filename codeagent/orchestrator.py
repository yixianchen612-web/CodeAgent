"""第 5-6 步：Agent 编排器 —— 状态机与运行循环。

状态机:
    Preflight → Preprocess → Plan → GenerateLoop → CheckGate
    → RepairLoop(≤ repair_rounds 轮) → CheckGate → Finalize

设计要点（调研结论）:
* 循环与传输/执行解耦（LLMClient / ToolExecutor 两个最小接口），可换 Mock。
* 迭代上限 max_steps；每个工具结果作为 role=tool 消息回填。
* Plan 阶段: 无工具 + JSON 输出模式 + thinking 开启 + reasoning_effort=high；
  Generate/Repair 阶段: 有工具 + thinking 显式关闭 + temperature 生效。
* Plan 校验失败 → 重试 1 次 → 确定性兜底计划。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import preprocessor
from .checker import check_project, failures_text
from .config import AppConfig
from .llm.base import LLMClient
from .llm.errors import LLMError, OutputParseError
from .models import CheckFailure, PlannedFile, ProjectPlan, RunReport, StopReason
from .prompts import (GENERATE_SYSTEM_PROMPT, GENERATE_USER_TEMPLATE,
                      PLAN_SYSTEM_PROMPT, PLAN_USER_TEMPLATE, REPAIR_APPENDIX,
                      REPAIR_USER_TEMPLATE)
from .spec_digest import build_digest
from .tools import TOOL_SCHEMAS, AgentError, ToolExecutor


class PlanValidationError(Exception):
    """计划 JSON 校验失败。"""


# ---------------------------------------------------------------------------
# 计划校验与兜底
# ---------------------------------------------------------------------------

def _strict_json(text: str) -> dict:
    """从模型输出中严格提取 JSON 对象（拒绝 markdown 围栏、拒绝非对象）。"""
    t = str(text).strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end < start:
        raise OutputParseError("响应中找不到 JSON 对象")
    try:
        obj = json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        raise OutputParseError(f"JSON 解析失败: {e}") from e
    if not isinstance(obj, dict):
        raise OutputParseError("JSON 顶层必须是对象")
    return obj


def validate_plan(data: dict) -> ProjectPlan:
    if not isinstance(data.get("files"), list):
        raise PlanValidationError("缺少 files 数组")
    files_raw = data["files"]
    if not 3 <= len(files_raw) <= 50:
        raise PlanValidationError(f"files 数量需在 3-50 之间，实际 {len(files_raw)}")

    planned: list[PlannedFile] = []
    seen: set[str] = set()
    for f in files_raw:
        if not isinstance(f, dict):
            raise PlanValidationError(f"files 项必须是对象: {f!r}")
        path = str(f.get("path", "")).strip().replace("\\", "/")
        if not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path) \
                or ".." in path.split("/"):
            raise PlanValidationError(f"非法路径: {path!r}")
        if path in seen:
            raise PlanValidationError(f"重复路径: {path}")
        seen.add(path)
        try:
            est = int(f.get("est_lines") or 0)
        except (TypeError, ValueError):
            est = 0
        planned.append(PlannedFile(path=path, purpose=str(f.get("purpose", "")).strip(),
                                   est_lines=max(0, min(est, 400))))

    names = seen
    if "README.md" not in names:
        raise PlanValidationError("计划缺少 README.md")
    if not ({"requirements.txt", "package.json"} & names):
        raise PlanValidationError("计划缺少依赖声明（requirements.txt 或 package.json）")
    if not any(p.startswith("tests/") for p in names):
        raise PlanValidationError("计划缺少 tests/ 下的测试文件")

    return ProjectPlan(
        language=str(data.get("language", "")).strip(),
        summary=str(data.get("summary", "")).strip(),
        directory_tree=list(data.get("directory_tree") or []),
        files=planned,
        build_command=str(data.get("build_command", "") or "").strip(),
        run_command=str(data.get("run_command", "") or "").strip(),
        test_command=str(data.get("test_command", "") or "").strip(),
        notes=[str(n) for n in (data.get("notes") or [])],
    )


def _check_deliverable_coverage(plan: ProjectPlan, spec: dict) -> None:
    """交付物清单中的每个文件名必须出现在计划文件路径中。"""
    missing = []
    for d in spec.get("documentation", {}).get("deliverables", []):
        if not any(f.path == d or f.path.endswith("/" + d) for f in plan.files):
            missing.append(d)
    if missing:
        raise PlanValidationError(f"计划未覆盖交付物: {missing}")


_UI_FILE_EXTS = (".html", ".htm", ".jsx", ".tsx", ".vue", ".css", ".scss")
_UI_DIR_PREFIXES = ("public/", "static/", "frontend/", "client/", "ui/",
                    "templates/")


def _check_ui_coverage(plan: ProjectPlan, spec: dict) -> None:
    """架构文档描述浏览器/用户界面功能时，计划必须包含前端文件。

    实测缺陷：架构文档描述分数学习游戏（介绍/主菜单/答题/结束反馈界面），
    模型却只生成后端 API。文档检测到 UI 需求后，计划校验强制前端覆盖。
    """
    if not spec.get("analysis", {}).get("has_ui_requirement"):
        return
    paths = [f.path.lower() for f in plan.files]
    has_ui = any(x.endswith(_UI_FILE_EXTS) or x.startswith(_UI_DIR_PREFIXES)
                 for x in paths)
    if not has_ui:
        raise PlanValidationError(
            "架构文档描述了浏览器/用户界面功能（介绍、主菜单、答题、结束反馈等界面），"
            "计划必须包含用户界面文件（html/jsx/vue/css 或 public/static/frontend 目录），"
            "不能只生成后端 API")


def fallback_plan(spec: dict) -> ProjectPlan:
    """确定性兜底计划：交付物 + 架构组件映射为文件（Node.js 栈，贴合 spec）。"""
    files = [
        ("README.md", "项目说明与运行方式", 40),
        ("package.json", "依赖声明（express）", 25),
        ("src/server.js", "Express 服务，/play 接口（OpenAPI 契约）", 60),
        ("src/game.js", "游戏状态机 Playing/Paused/GameOver", 90),
        ("src/questions.js", "题目数据与判分", 70),
        ("src/score.js", "计分视图（View Score 用例）", 40),
        ("tests/game.test.js", "状态机与判分单元测试", 80),
        ("openapi.yaml", "API 契约（来自架构文档）", 60),
        ("internal.proto", "内部 RPC 契约（来自架构文档）", 30),
        ("k8s/spacefractions-deployment.yaml", "K8s 部署清单", 30),
        ("sql/game_ddl.sql", "数据库 DDL", 25),
        ("traceability_matrix.csv", "需求追溯矩阵", 10),
        ("architecture.md", "架构说明", 50),
        ("Dockerfile", "容器化（选做）", 10),
    ]
    if spec.get("analysis", {}).get("has_ui_requirement"):
        # 文档描述浏览器界面功能 → 兜底计划同样必须覆盖前端（介绍/菜单/答题/反馈）
        files += [
            ("public/index.html", "浏览器界面骨架（介绍/主菜单/答题/结束反馈）", 60),
            ("public/app.js", "界面交互与答题流程", 90),
            ("public/style.css", "界面样式", 40),
        ]
    planned = [PlannedFile(path=p, purpose=pur, est_lines=est) for p, pur, est in files]
    return ProjectPlan(
        language="nodejs",
        summary="Space Fractions 分数学习游戏（架构文档驱动生成；兜底计划）",
        directory_tree=[p.path for p in planned],
        files=planned,
        run_command="node src/server.js",
        test_command="node --test",
        notes=["fallback: LLM 计划输出无效，使用确定性兜底计划"],
    )


# ---------------------------------------------------------------------------
# 运行时验证闸门（第 7 步增强）
# ---------------------------------------------------------------------------

_NETWORK_ERR = ("ECONNREFUSED", "ECONNRESET", "ENOTFOUND", "ETIMEDOUT",
                "EAI_AGAIN", "GETADDRINFO", "NETWORK_IS_UNREACHABLE",
                "ENETUNREACH", "EHOSTUNREACH")

_OUTPUT_TAIL_BYTES = 200_000  # 子进程输出只回读尾部，防内存失控


def _run_capped(cmd: list, cwd: Path, timeout: int) -> tuple[int, str, bool, str]:
    """运行命令，输出写入临时文件（只回读尾部），返回 (退出码, 输出, 超时?, 启动错误)。

    超时时用 taskkill /T /F 强制终止整个进程树（npm.cmd → cmd.exe → node 链，
    只杀包装进程会留下孤儿 node）；启动失败（OSError，如 PATHEXT 异常/杀软拦截）
    不抛异常，由调用方决定跳过。
    """
    try:
        buf = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=buf,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
    except OSError as e:
        return -1, "", False, str(e)
    try:
        proc.wait(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    buf.seek(0, 2)
    end = buf.tell()
    buf.seek(max(0, end - _OUTPUT_TAIL_BYTES))
    out = buf.read()
    buf.close()
    return proc.returncode, out, timed_out, ""


def _summarize_test_output(out: str) -> str:
    """把 npm test 输出压缩成修复提示（兼容 node:test 与 Jest 两种格式）。"""
    lines = [ln.strip() for ln in out.splitlines()]
    section = "\n".join(lines[:next((i for i, ln in enumerate(lines)
                                    if ln.startswith("✖ failing tests:")),
                                   len(lines))])
    parts = []

    # 1. 统计行
    stats = next((ln for ln in lines
                  if ln.startswith("ℹ tests") or ln.startswith("Tests:")), "")
    parts.append(f"npm test 失败（{stats}）" if stats else "npm test 失败")

    # 2. 失败用例名（node:test 的 ✖ 与 Jest 的 ✕ / ● 三种标记）
    names = re.findall(r"^✖ (.+?) \(\d+(?:\.\d+)?ms\)", section, flags=re.M)
    names += re.findall(r"^✕ (.+?) \(\d+(?:\.\d+)?ms\)", section, flags=re.M)
    head = "\n".join(lines[:next((i for i, ln in enumerate(lines)
                                 if ln.startswith("Tests:")), len(lines))])
    names += re.findall(r"^● (.+?)$", head, flags=re.M)
    seen, failed = set(), []
    for n in names:
        n = n.strip()
        if n and n not in seen and n != "Console":
            seen.add(n)
            failed.append(n)
    if failed:
        parts.append("失败用例: " + "; ".join(failed[:10]))

    # 3. 断言对比（node:test: actual/expected；Jest: Expected/Received）
    # 注意：node:test 的断言行出现在 "✖ failing tests:" 标记之后，
    # 必须在完整输出上扫描，不能只扫标记前的片段。
    pairs = []
    exp = [ln for ln in lines if ln.startswith(("expected:", "Expected:"))]
    act = [ln for ln in lines if ln.startswith(("actual:", "Received:"))]
    for e, a in zip(exp[:8], act[:8]):
        pairs.append(f"{e}  vs  {a}")
    parts += pairs

    # 4. 错误签名 + 指向生成代码的栈帧（排除 node_modules 噪音）
    err_line = next((ln for ln in lines if re.search(
        r"(TypeError|ReferenceError|SyntaxError|AssertionError|Error):", ln)), "")
    if err_line:
        parts.append(err_line[:300])
    frames = [ln for ln in lines if ln.startswith("at ") and "node_modules" not in ln]
    for f in frames[:8]:
        parts.append(f[:200])

    if len(parts) == 1:  # 什么都没解析到 → 给原始片段兜底
        parts.append("\n".join(lines[:20])[:800])
    return "\n".join(parts)[:1500]


def _zero_tests(out: str) -> bool:
    """npm test 退出码 0 但没有任何测试实际执行。

    覆盖：0 个测试（文件名与发现模式不匹配）、全部跳过（ℹ skipped N 且
    pass 0 / fail 0）、Jest 全 skipped（Tests: 0 passed 且带 skipped 标记）。
    """
    if re.search(r"ℹ tests 0\b", out) or re.search(r"Tests:\s+0\s+total", out):
        return True
    m = re.search(r"ℹ pass (\d+).*?ℹ fail (\d+)", out, re.S)
    if m and m.group(1) == "0" and m.group(2) == "0":
        # pass/fail 均为 0 但有测试/跳过计数 → 全部 skipped 或 todo，无断言执行
        if re.search(r"ℹ (?:tests|skipped) [1-9]\d*\b", out):
            return True
    m2 = re.search(r"Tests:\s+(\d+)\s+passed,\s+(\d+)\s+failed", out)
    if m2 and m2.group(1) == "0" and m2.group(2) == "0" \
            and re.search(r"skipped", out, re.I):
        return True
    return False


_VERIFY_LABEL = {
    "passed": "运行时验证通过",
    "failed": "运行时验证失败",
    "skipped": "运行时验证未执行",
    "not_run": "运行时验证未执行",
}

# 模型摘要中不得由模型自己陈述的“验证结论类”语句（通过/失败/未执行等）。
# 结论必须由程序根据真实验证状态生成，否则会出现实测探针中的矛盾：
# 模型写 “All tests passed”，而程序附注 “运行时验证未执行” —— 同一句话
# 既声称全部通过又声称未执行。剥离后模型摘要只用于描述“生成了哪些内容”。
#
# 判定层次（匹配前先把全角字母/数字 NFKC 折叠为 ASCII）：
# ① 命中验证工具名 / 验证词 / 零失败式结论的句子直接剥离；
# ② 同一句中“测试类词”与“结论类词”共现（含 success/green/OK/成功 等
#    同义词，“所有测试成功”“测试全部OK”“Tests are all good”等组合）剥离；
# ③ “N/N”比例 + 测试词或结论词（“27/27 passed”“27/27 green”）剥离；
# ④ 短句且命中强结论词（“全部通过。”“all passed”“验收通过。”等
#    独立结论句）剥离。弱结论词（成功/正常/works/fine 也常用于描述
#    “成功生成了 N 个文件”）只在与测试词/比例共现时剥离。
# 宁可多剥离内容描述，绝不放过一句结论自述；残余风险：刻意用
# 同形异体字（如西里尔 е）伪装关键词的对抗性输入不做穷举。
_VERIFY_TOOL_RE = re.compile(
    r"npm[\s-]+test|unittest|\bjest\b|\bpytest\b|\bassert(?:ions?)?\b|断言"
    r"|(?:runtime\s+)?verification|verif(?:y|ied|ication)|验证"
    r"|(?:本机\s*)?无\s*node|no\s+node"
    r"|everything\s+(?:works|is\s+(?:ok|fine|good|green))",
    re.I,
)
_ZERO_FAIL_RE = re.compile(
    r"\b(?:0|zero|no|none|nothing|without)\s+(?:failures?|errors?|problems?)\b"
    r"|(?:零|没有|未见|无)\s*(?:失败|错误|问题)",
    re.I,
)
_TEST_WORD_RE = re.compile(
    r"\btests?\b|t[еe]sts?|\bcases?\b|\bsuites?\b|测试|用例|检查|\bchecks?\b",
    re.I,
)
_STRONG_CLAIM_RE = re.compile(
    r"\b(?:pass|[рp][аa]ss)(?:ed|ing)?\b|\bfail(?:ed|ing|ures?)?\b"
    r"|\bsucceed(?:ed)?\b|\bsuccess(?:ful(?:ly)?)?\b|\bok(?:ay)?\b|\bgood\b"
    r"|\bgreen\b|\bclean\b|通过|失败|未执行|跳过|全绿|跑通|跑过|验收|skip(?:ped)?",
    re.I,
)
_CLAIM_WORD_RE = re.compile(
    _STRONG_CLAIM_RE.pattern + r"|\bwork(?:ed|s)?\b|\bfine\b|成功|正常",
    re.I,
)
_RATIO_RE = re.compile(r"\d+\s*/\s*\d+")
_SENT_SPLIT_RE = re.compile(r"(?<=[。；;!?！？])|\n+|(?<=[.!?])\s+")
_SHORT_SENT_LIMIT = 20  # 短句 + 强结论词 → 独立结论句（“全部通过。”）
# 中文字符是 Unicode 词字符，"测试OK"/"PASS全部" 等中英紧邻处没有
# \b 词边界，会漏掉 \bok\b/\bpass\b —— 匹配前在中英紧邻处插空格。
_CJK_LATIN_GAP_RE = re.compile(r"(?<=[一-龥])(?=[A-Za-z])|(?<=[A-Za-z])(?=[一-龥])")


def _is_verify_claim(sent: str) -> bool:
    """句子是否属于模型自述的验证结论（应剥离）。"""
    folded = unicodedata.normalize("NFKC", sent)
    spaced = _CJK_LATIN_GAP_RE.sub(" ", folded)
    if _VERIFY_TOOL_RE.search(spaced) or _ZERO_FAIL_RE.search(spaced):
        return True
    has_test = bool(_TEST_WORD_RE.search(spaced))
    has_claim = bool(_CLAIM_WORD_RE.search(spaced))
    if has_test and has_claim:
        return True
    if _RATIO_RE.search(spaced) and (has_test or has_claim):
        return True
    if _STRONG_CLAIM_RE.search(spaced) and len(folded) <= _SHORT_SENT_LIMIT:
        return True
    return False


def _sanitize_model_note(note: str) -> str:
    """剥离模型摘要中的验证结论类语句，仅保留“生成了哪些内容”。"""
    parts = []
    for sent in _SENT_SPLIT_RE.split(note or ""):
        sent = " ".join(sent.split())
        if sent and not _is_verify_claim(sent):
            parts.append(sent)
    return "；".join(parts)[:300]


def _final_stop(reason: StopReason, note: str, failures: list,
                verify_status: str = "not_run", verify_detail: str = ""):
    """把 (终止原因, 备注, 检查结果) 归约为最终报告状态。

    结论一律由程序根据真实验证状态生成，模型摘要（note）经
    `_sanitize_model_note` 剥离验证类语句后仅作为“生成内容”附注，
    绝不参与通过/失败结论（修复实测探针：“All tests passed”与
    “运行时验证未执行”同句矛盾的缺陷）。
    MAX_STEPS 分支的 note 是程序文本（步数上限说明），不作为生成内容。
    """
    def verify_suffix() -> str:
        label = _VERIFY_LABEL.get(verify_status, "运行时验证未执行")
        if verify_status == "skipped" and verify_detail:
            label += f"（{verify_detail}）"
        return label

    if reason == StopReason.MAX_STEPS:
        if not failures:
            return StopReason.COMPLETED, \
                f"{note}；静态检查全部通过；{verify_suffix()}"
        return StopReason.MAX_STEPS, f"{note}；检查未全部通过（{len(failures)} 个问题）"

    # 仅 COMPLETED 分支的 note 是模型摘要：剥离验证结论后作为内容附注
    summary = _sanitize_model_note(note) if note else ""
    content = f"。生成内容：{summary}" if len(summary) > 4 else ""
    if not failures:
        # 完成结论成立，但只有 verify_status == "passed"（真实执行 npm test
        # 且全部通过）才允许出现“运行时验证通过”字样；未执行/跳过必须如实
        # 说明，不得留下“测试全部通过”的误导。
        return StopReason.COMPLETED, \
            f"项目生成完成，静态检查全部通过；{verify_suffix()}" + content
    return StopReason.ERROR, f"项目生成失败：检查未全部通过（{len(failures)} 个问题）" + content


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------

@dataclass
class _Budget:
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, usage: dict) -> None:
        self.model_calls += 1
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)


class CodeAgent:
    def __init__(self, config: AppConfig, llm: LLMClient, emit=None):
        self.config = config
        self.llm = llm
        self.emit = emit or (lambda ev: None)
        self.cancelled = False
        self.budget = _Budget()
        self.workspace: Path | None = None
        self.executor: ToolExecutor | None = None
        self._active_plan: ProjectPlan | None = None
        self._total_steps = 0
        self._write_count = 0          # 累计写入次数（修复轮“有动作”判定）
        # 运行时验证状态：passed | failed | skipped | not_run（如实记录，见 run()）
        self._verify_status = "not_run"
        self._verify_detail = ""

    def _mark_verify(self, status: str, detail: str = "") -> None:
        self._verify_status = status
        self._verify_detail = detail

    # -- 工具方法 -----------------------------------------------------------

    def _emit(self, stage: str, message: str, **data) -> None:
        self.emit({"stage": stage, "message": message, "data": data})

    def cancel(self) -> None:
        self.cancelled = True

    def _check_cancel(self) -> None:
        if self.cancelled:
            raise AgentError("任务已被用户取消")

    def _digest_text(self, spec: dict) -> str:
        return json.dumps(build_digest(spec), ensure_ascii=False)

    @staticmethod
    def _assistant_message(resp) -> dict:
        msg: dict = {"role": "assistant", "content": resp.content or ""}
        if resp.tool_calls:
            msg["tool_calls"] = [
                {"id": tc["id"] or f"call_{i}", "type": "function",
                 "function": {"name": tc["name"],
                              "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                for i, tc in enumerate(resp.tool_calls)
            ]
        return msg

    def _complete(self, messages: list, tools=None, temperature=0.2,
                  thinking=None, reasoning_effort=None, response_format=None):
        self._check_cancel()
        resp = self.llm.complete(
            messages, tools=tools, temperature=temperature,
            max_tokens=self.config.max_tokens, thinking=thinking,
            reasoning_effort=reasoning_effort, response_format=response_format)
        self.budget.add(resp.usage)
        return resp

    # -- 各阶段 -------------------------------------------------------------

    def _run_plan(self, spec: dict) -> ProjectPlan:
        self._emit("plan", "生成项目计划…")
        digest = self._digest_text(spec)
        analysis = json.dumps(spec.get("analysis", {}), ensure_ascii=False)
        messages = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": PLAN_USER_TEMPLATE.format(
                spec=digest, analysis=analysis)},
        ]
        for attempt in range(2):
            try:
                resp = self._complete(messages, response_format={"type": "json_object"},
                                      thinking={"type": "enabled"}, reasoning_effort="high")
                plan = validate_plan(_strict_json(resp.content))
                _check_deliverable_coverage(plan, spec)
                _check_ui_coverage(plan, spec)
                self._emit("plan", f"计划校验通过：{len(plan.files)} 个文件，"
                                   f"语言 {plan.language or '未指定'}")
                return plan
            except (OutputParseError, PlanValidationError) as e:
                self._emit("plan", f"计划输出无效（第 {attempt + 1} 次尝试）: {e}")
                messages.append(self._assistant_message(resp))
                messages.append({"role": "user",
                                 "content": f"Invalid plan: {e}. "
                                            f"Return ONLY a valid JSON object per the rules."})
        self._emit("plan", "计划生成失败，使用确定性兜底计划")
        return fallback_plan(spec)

    def _run_tool_loop(self, messages: list) -> tuple[StopReason, str]:
        """带工具的执行循环；返回 (终止原因, 备注)。"""
        steps = 0
        while steps < self.config.max_steps:
            resp = self._complete(messages, tools=TOOL_SCHEMAS, temperature=0.2,
                                  thinking={"type": "disabled"})
            messages.append(self._assistant_message(resp))
            if not resp.tool_calls:
                # 模型未用工具即结束 → 视为完成。
                # 注意：这里返回的文本会成为 _final_stop 的 note（模型摘要），
                # 空内容时返回空串，让 _final_stop 输出纯程序结论，
                # 不要把程序默认文案误标成“生成内容”。
                return StopReason.COMPLETED, (resp.content or "").strip()
            for tc in resp.tool_calls:
                self._check_cancel()
                result, marker = self.executor.execute(tc["name"], tc["arguments"])
                messages.append({"role": "tool", "tool_call_id": tc["id"] or f"call_{steps}",
                                 "content": json.dumps(result, ensure_ascii=False)})
                self._emit("generate", f"工具 {tc['name']} 完成",
                           tool=tc["name"], result=result)
                if marker == "complete":
                    return StopReason.COMPLETED, str(result.get("summary", ""))
                if marker == "fail":
                    raise AgentError(f"模型宣告失败: {result.get('reason', '')}")
            steps += 1
            self._total_steps += 1
        return StopReason.MAX_STEPS, f"达到最大步数上限 {self.config.max_steps}"

    def _run_generate(self, plan: ProjectPlan, spec: dict) -> tuple[StopReason, str]:
        self._emit("generate", f"开始生成 {len(plan.files)} 个文件…")
        messages = [
            {"role": "system", "content": GENERATE_SYSTEM_PROMPT},
            {"role": "user", "content": GENERATE_USER_TEMPLATE.format(
                spec=self._digest_text(spec),
                plan=json.dumps(plan.to_dict(), ensure_ascii=False, indent=1),
                manifest=json.dumps(plan.manifest(), ensure_ascii=False, indent=1))},
        ]
        return self._run_tool_loop(messages)

    def _run_repair(self, failures, plan: ProjectPlan, spec: dict):
        """单轮修复：返回 (是否有修复动作发生, 修复后的检查结果)。

        “修复动作”以实际写入文件为准，而不是失败数量下降：运行时闸门把
        所有 npm test 失败聚合成 1 条，修复后仍失败时数量不降，但文件确实
        被改写了，应允许下一轮继续（否则 repair_rounds=2 永远只跑 1 轮）。
        """
        before = len(failures)
        writes_before = self._write_count
        self._emit("repair", f"检查发现 {before} 个问题，进入修复轮…")
        messages = [
            {"role": "system", "content": GENERATE_SYSTEM_PROMPT + REPAIR_APPENDIX},
            {"role": "user", "content": REPAIR_USER_TEMPLATE.format(
                failures=failures_text(failures),
                manifest=json.dumps(plan.manifest(), ensure_ascii=False, indent=1))},
        ]
        reason, note = self._run_tool_loop(messages)
        new_failures = self._check_gate(plan)
        changed = self._write_count > writes_before
        return changed, new_failures

    # -- 检查闸门 -----------------------------------------------------------

    def _check_gate(self, plan: ProjectPlan) -> list:
        """静态检查 + 运行时验证（静态通过才跑运行时，避免浪费）。"""
        failures = check_project(self.workspace, plan)
        if not failures:
            failures = self._runtime_verify(plan)
        return failures

    def _runtime_verify(self, plan: ProjectPlan) -> list:
        """真实执行 npm install + npm test，把失败反馈进修复循环。

        仅适用于 Node.js 项目且本机有 node/npm；依赖安装遇到网络错误时
        跳过（不判失败，离线环境仍可完成生成）。install 加 --ignore-scripts，
        避免执行生成内容中可能存在的生命周期脚本。
        """
        self._check_cancel()
        if not self.config.verify_runtime:
            self._mark_verify("skipped", "配置关闭运行时验证")
            return []
        ws = self.workspace
        if not (ws / "package.json").is_file():
            self._mark_verify("skipped", "非 Node.js 项目（无 package.json）")
            self._emit("check", "运行时验证跳过：非 Node.js 项目（无 package.json）")
            return []
        npm = shutil.which("npm")
        if npm is None or shutil.which("node") is None:
            self._mark_verify("skipped", "本机无 node/npm")
            self._emit("check", "运行时验证跳过：本机无 node/npm")
            return []

        self._emit("check", "运行时验证：npm install …")
        rc, out, timed_out, spawn_err = _run_capped(
            [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=ws, timeout=300)
        if spawn_err:
            self._mark_verify("skipped", f"无法启动 npm（{spawn_err}）")
            self._emit("check", f"运行时验证跳过：无法启动 npm（{spawn_err}）")
            return []
        if timed_out:
            self._mark_verify("failed", "npm install 超时（>300 秒）")
            return [CheckFailure("npm install",
                                 f"npm install 超时（>300 秒）: {out[-400:]}", "runtime")]
        if rc != 0:
            if any(k in out.upper() for k in _NETWORK_ERR):
                self._mark_verify("skipped", "依赖安装失败（网络不可用）")
                self._emit("check", "运行时验证跳过：依赖安装失败（网络不可用）")
                return []
            self._mark_verify("failed", "npm install 失败")
            return [CheckFailure("npm install", f"npm install 失败: {out[-400:]}",
                                 "runtime")]

        self._check_cancel()
        self._emit("check", "运行时验证：npm test …")
        rc, out, timed_out, spawn_err = _run_capped([npm, "test"], cwd=ws, timeout=180)
        if spawn_err:
            self._mark_verify("skipped", f"无法启动 npm（{spawn_err}）")
            self._emit("check", f"运行时验证跳过：无法启动 npm（{spawn_err}）")
            return []
        if timed_out:
            self._mark_verify("failed", "npm test 超时（>180 秒）")
            return [CheckFailure("npm test", "npm test 超时（>180 秒）:\n"
                                 + _summarize_test_output(out), "runtime")]
        if rc != 0:
            self._mark_verify("failed", "npm test 失败")
            return [CheckFailure("npm test", _summarize_test_output(out), "runtime")]
        if _zero_tests(out):
            self._mark_verify("failed", "npm test 未执行任何用例")
            return [CheckFailure(
                "npm test",
                "npm test 退出码 0 但 0 个测试被执行：测试文件名与发现模式不匹配，"
                "或 test 脚本未运行任何用例（请确认 tests/ 下存在 *.test.js）",
                "runtime")]
        self._mark_verify("passed", "npm install + npm test 全部通过")
        self._emit("check", "运行时验证：npm test 全部通过")
        return []

    # -- 主入口 -------------------------------------------------------------

    def run(self) -> RunReport:
        report = RunReport(output_dir=str(self.config.output_dir))
        try:
            # Preflight：在用户所选目录下创建本次任务的独立子目录，
            # 绝不移动/删除用户已有文件（输入文件可能就在所选目录内）
            self._emit("preflight", "预检：准备输出目录…")
            base = Path(self.config.output_dir).resolve()
            base.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            out = base / f"generated_{stamp}"
            n = 2
            while out.exists():
                out = base / f"generated_{stamp}_{n}"
                n += 1
            # 防御：输入文件不得位于本次工作区内（工作区是新目录，正常不可能，
            # 但输出目录与输入目录重叠的配置一旦出现必须明确报错而不是读失败）
            for label, p in (("架构文档", self.config.doc_path),
                             ("架构视图", self.config.views_path)):
                if p and Path(p).resolve().is_relative_to(out):
                    raise AgentError(f"{label}位于输出工作区 {out} 内；"
                                     f"请为输出选择独立的任务目录")
            if (self.config.doc_path and self.config.views_path
                    and Path(self.config.doc_path).resolve()
                    == Path(self.config.views_path).resolve()):
                raise AgentError("架构文档与架构视图不能是同一个文件")
            out.mkdir(parents=True, exist_ok=True)
            self.workspace = out
            report.output_dir = str(out)
            self.executor = ToolExecutor(out, on_write=self._on_write)
            self._emit("preflight", f"本次任务工作目录：{out}")

            # 模型预检（仅真实客户端）
            if hasattr(self.llm, "list_models"):
                try:
                    models = self.llm.list_models()
                    self._emit("preflight", f"模型预检通过：{len(models)} 个可用模型")
                except LLMError as e:
                    raise AgentError(f"预检失败: {e}")

            # Preprocess
            self._emit("preprocess", "解析输入架构文档…")
            spec = preprocessor.preprocess(self.config.doc_path, self.config.views_path)
            stats = spec["analysis"]["stats"]
            self._emit("preprocess",
                       f"解析完成：{stats['diagrams']} 张图 / {stats['requirements']} 条需求 / "
                       f"{len(spec['analysis']['gaps'])} 个文档问题")

            # Plan
            plan = self._run_plan(spec)
            report.plan = plan
            self._active_plan = plan

            # Generate
            reason, note = self._run_generate(plan, spec)
            report.steps = self._total_steps

            # CheckGate → RepairLoop
            failures = self._check_gate(plan)
            self._emit("check", f"首轮检查：{len(failures)} 个问题")
            for round_no in range(self.config.repair_rounds):
                if not failures:
                    break
                changed, failures = self._run_repair(failures, plan, spec)
                self._emit("check", f"修复轮 {round_no + 1} 后：{len(failures)} 个问题")
                if not changed:
                    break

            report.failures = failures
            report.stop_reason, report.message = _final_stop(
                reason, note, failures,
                verify_status=self._verify_status,
                verify_detail=self._verify_detail)
        except AgentError as e:
            report.stop_reason = StopReason.CANCELLED if self.cancelled else StopReason.ERROR
            report.message = str(e)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
            report.stop_reason = StopReason.ERROR
            report.message = f"{type(e).__name__}: {e}"
        except LLMError as e:
            report.stop_reason = StopReason.ERROR
            report.message = str(e)

        # 验证执行情况在所有路径（含异常路径）都如实记录进报告
        report.verification = {"status": self._verify_status,
                               "detail": self._verify_detail}
        report.model_calls = self.budget.model_calls
        report.tokens = {"prompt": self.budget.prompt_tokens,
                         "completion": self.budget.completion_tokens}
        self._finalize(report)
        return report

    # -- 收尾 ---------------------------------------------------------------

    def _on_write(self, path: str) -> None:
        self._write_count += 1
        if self._active_plan:
            f = self._active_plan.find(path)
            if f:
                f.status = "done"

    def _finalize(self, report: RunReport) -> None:
        self._emit("finalize", f"结束：{report.stop_reason.value} — {report.message}")
        if self.workspace:
            if report.plan:
                (self.workspace / "project_plan.json").write_text(
                    json.dumps(report.plan.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
            (self.workspace / "run_report.json").write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            self._emit("finalize", f"运行报告已写入 {self.workspace}",
                       output_dir=str(self.workspace))
