"""第 5 步：Agent 工具集（OpenAI function schema + 执行器）。

工具列表（7 个，调研综合建议）:
* write_file   写单个文件（自动建父目录）
* write_files  批量写多个小文件（省调用）
* read_file    读已有文件（修复阶段用）
* list_files   列工作区文件树
* run_check    运行白名单检查命令（语法/测试）
* complete     宣告完成 → 终止循环
* fail         宣告放弃 → 终止循环并报错

安全约束:
* 所有路径必须是工作区内的相对路径（拒绝 ".."、盘符、绝对路径）。
* 单文件 ≤ 200KB，累计写入 ≤ 3MB。
* run_check 只执行白名单检查命令：参数先按 argv 解析再校验，shell=False 直接
  exec（模型输出的整段字符串绝不进 Shell——历史漏洞：前缀匹配 + shell=True 时
  "node --check a.js & 任意命令" 可注入）。
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

MAX_FILE_BYTES = 200 * 1024
MAX_TOTAL_BYTES = 3 * 1024 * 1024
MAX_BATCH_FILES = 10
MAX_LIST_ENTRIES = 500

# run_check 白名单：argv[0]（小写 basename）→ 允许的参数形态列表。
# 校验顺序：shlex 按 argv 拆分 → argv[0] 查表 → 前 N 个参数必须匹配某形态 →
# node --check 形态额外拒绝一切 "-" 开头参数（实测 --require/--import 会执行
# 任意 JS 且退出码 0）→ 全参数拒绝 shell 元字符 → shell=False 执行。
_ALLOWED_CHECK_SHAPES = {
    "node": [("--check",)],                      # node --check <文件> [<更多文件>]，支持引号 glob
    "npm": [("test",)],                          # npm test
    "python": [("-m", "py_compile"), ("-m", "unittest"), ("-m", "pytest")],
    "py": [("-m", "py_compile"), ("-m", "unittest"), ("-m", "pytest")],
}
_SHELL_META_RE = re.compile(r"[&|;<>`$%!]")
_RUN_CHECK_TIMEOUT = 120


def _win_unquote(token: str) -> str:
    """shlex.split(posix=False) 保留引号字符 → 剥离成对的首尾引号。

    否则 "a b.js" 这类带空格路径会以 '"a' / 'b.js"' 两个残片进入 argv。
    """
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _decode_output(b: bytes) -> str:
    """子进程输出解码：优先 UTF-8（生成项目统一编码），
    大量替换字符时回退 GBK（cp936 控制台的编译/测试输出）。"""
    if not b:
        return ""
    s = b.decode("utf-8", errors="replace")
    if s.count("�") > max(4, len(s) // 8):
        try:
            s = b.decode("gbk", errors="replace")
        except UnicodeDecodeError:
            pass
    return s


def _real_exe(p: str | None) -> bool:
    """候选可执行文件可用性检查：排除 0 字节的 WindowsApps 商店占位 stub。"""
    if not p:
        return False
    if "windowsapps" in p.lower():
        return False
    try:
        return Path(p).stat().st_size > 0
    except OSError:
        return False


def _resolve_check_exe(exe: str) -> str | None:
    """把白名单内的 argv[0] 解析为真实可执行文件路径；找不到返回 None。

    源码运行：python/py 一律换成当前解释器（避免商店占位 stub）。
    打包运行（frozen）：sys.executable 指向 CodeAgent.exe，不能当解释器用
    （PyInstaller 官方说明的行为）；优先 py.exe 启动器，再找 PATH 上的
    python，并排除 WindowsApps 商店占位 stub。
    """
    if exe in ("python", "py"):
        if not getattr(sys, "frozen", False):
            return sys.executable
        for cand in (shutil.which("py"), shutil.which("python")):
            if _real_exe(cand):
                return cand
        return None
    return shutil.which(exe)  # node / npm（Windows 下可解析到 npm.cmd）


def _run_check_process(argv: list, cwd: str) -> dict:
    """执行检查命令：超时 taskkill /T 整树终止，输出尾部回读。

    Windows 下 subprocess.run(timeout=) 只杀直接子进程且 communicate 无超时，
    npm.cmd → cmd.exe → node 链的孙进程持有管道时调用会无限阻塞
    （实测 timeout=2 的命令 6 秒后才返回）——必须自己管超时与进程树。
    """
    try:
        proc = subprocess.Popen(argv, shell=False, cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        return {"error": f"命令启动失败: {e}"}
    try:
        out_b, err_b = proc.communicate(timeout=_RUN_CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        # 整树已杀，管道此时应可回收；再兜底一次防僵尸阻塞
        try:
            out_b, err_b = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out_b = proc.stdout.read() if proc.stdout else b""
            err_b = proc.stderr.read() if proc.stderr else b""
        return {"timed_out": True,
                "stdout": _decode_output(out_b)[-4000:],
                "stderr": _decode_output(err_b)[-4000:]}
    return {"exit_code": proc.returncode,
            "stdout": _decode_output(out_b)[-4000:],
            "stderr": _decode_output(err_b)[-4000:]}

_PATH_BAD_RE = re.compile(r"(^|[\\/])\.\.")  # 路径中出现 ..
DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _safe_path(workspace: Path, raw: str) -> Path:
    """校验并解析工作区内相对路径。"""
    path = str(raw).strip().replace("\\", "/")
    if not path:
        raise ValueError("空路径")
    if path.startswith("/") or DRIVE_RE.match(path) or _PATH_BAD_RE.search(path):
        raise ValueError(f"非法路径（必须为工作区内相对路径）: {raw!r}")
    resolved = (workspace / path).resolve()
    if not str(resolved).startswith(str(workspace.resolve())):
        raise ValueError(f"路径越出工作区: {raw!r}")
    return resolved


def write_file_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write one complete file to the workspace. Creates parent directories automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path, e.g. src/game.py"},
                    "content": {"type": "string", "description": "Complete UTF-8 file content"},
                },
                "required": ["path", "content"],
            },
        },
    }


def write_files_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "write_files",
            "description": "Write up to 10 files at once. Use for several small files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
                "required": ["files"],
            },
        },
    }


def read_file_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read an existing file from the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative file path"}},
                "required": ["path"],
            },
        },
    }


def list_files_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"subdir": {"type": "string", "description": "Optional subdirectory"}},
            },
        },
    }


def run_check_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "run_check",
            "description": "Run a whitelisted check command (syntax check or unit tests) in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "Allowed shapes: 'node --check <file> [<files>]' (quoted globs like \"tests/*.test.js\" supported), 'npm test', 'python -m py_compile <file>', 'python -m unittest', 'python -m pytest <dir>' (py also accepted as the python launcher). No shell metacharacters or node option flags."},
                },
                "required": ["command"],
            },
        },
    }


def complete_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "complete",
            "description": "Signal that all planned files have been written. Ends the run.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string", "description": "Short summary of WHAT was generated (file paths/counts only). Never state test or verification results — the system runs its own checks and reports those itself."}},
                "required": ["summary"],
            },
        },
    }


def fail_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "fail",
            "description": "Signal that the task cannot be completed. Ends the run with an error.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    }


TOOL_SCHEMAS = [
    write_file_schema(), write_files_schema(), read_file_schema(), list_files_schema(),
    run_check_schema(), complete_schema(), fail_schema(),
]


class AgentError(Exception):
    """Agent 运行期错误（工具 fail / 越界等）。"""


class ToolExecutor:
    """执行工具调用，返回 (结果dict, 终止标记)。

    终止标记: None | "complete" | "fail"
    """

    def __init__(self, workspace: Path, on_write=None):
        self.workspace = Path(workspace).resolve()
        self.on_write = on_write          # callback(path) 供编排器更新清单状态
        self.total_bytes = 0

    # -- 写 ----------------------------------------------------------------

    def _do_write(self, path: str, content: str) -> dict:
        target = _safe_path(self.workspace, path)
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"文件过大（>{MAX_FILE_BYTES // 1024}KB）: {path}")
        if self.total_bytes + len(data) > MAX_TOTAL_BYTES:
            raise ValueError(f"累计写入超过 {MAX_TOTAL_BYTES // (1024 * 1024)}MB 上限")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.total_bytes += len(data)
        if self.on_write:
            # 用规范化相对路径回调（模型可能写 "src\\a.py" 或 "./src/a.py"，
            # 否则清单状态匹配不上，一直停在 pending）
            self.on_write(target.relative_to(self.workspace).as_posix())
        return {"path": path, "bytes": len(data), "ok": True}

    # -- 读 / 列 / 检查 ------------------------------------------------------

    def _do_read(self, path: str) -> dict:
        target = _safe_path(self.workspace, path)
        if not target.is_file():
            return {"path": path, "error": "文件不存在"}
        return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace")}

    def _do_list(self, subdir: str = "") -> dict:
        base = self.workspace if not subdir else _safe_path(self.workspace, subdir)
        if not base.is_dir():
            return {"error": f"目录不存在: {subdir}"}
        entries = []
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(self.workspace).as_posix()
            if any(part in (".codeagent_backup", "node_modules") for part in p.parts):
                continue
            entries.append(rel + ("/" if p.is_dir() else ""))
            if len(entries) >= MAX_LIST_ENTRIES:
                break
        return {"files": entries}

    def _expand_globs(self, args: list) -> list:
        """展开 node --check 参数中的引号 glob（shell=False 下 shell 不做展开）。"""
        out = []
        for a in args:
            if any(c in a for c in "*?["):
                hits = sorted(str(p) for p in self.workspace.glob(a))
                out.extend(hits or [a])
            else:
                out.append(a)
        return out

    def _do_run_check(self, command: str) -> dict:
        cmd = str(command).strip()
        try:
            argv = shlex.split(cmd, posix=False)
        except ValueError as e:
            return {"command": cmd, "error": f"命令解析失败: {e}"}
        argv = [_win_unquote(t) for t in argv]
        if not argv or not argv[0]:
            return {"command": cmd, "error": "空命令"}
        exe = argv[0].lower()
        if exe not in _ALLOWED_CHECK_SHAPES:
            return {"command": cmd, "error": "命令不在白名单内",
                    "allowed": sorted(_ALLOWED_CHECK_SHAPES)}
        head = argv[1:]
        matched = next((s for s in _ALLOWED_CHECK_SHAPES[exe]
                        if head[:len(s)] == list(s)), None)
        if matched is None:
            return {"command": cmd,
                    "error": f"仅允许 {exe} 的以下调用形态: "
                             + " / ".join(" ".join(s) for s in _ALLOWED_CHECK_SHAPES[exe])}
        tail = head[len(matched):]
        # 双保险：即使 shell=False，参数中出现 shell 元字符也一律拒绝
        if any(_SHELL_META_RE.search(a) for a in argv):
            return {"command": cmd, "error": "参数包含 shell 元字符，已拒绝"}
        # node --check 承诺“纯语法检查”：任何 "-" 开头参数都拒绝。
        # 实测 --require/--import 会在语法检查前执行任意 JS 且退出码 0（审查确认）
        if exe == "node" and any(a.startswith("-") for a in tail):
            return {"command": cmd,
                    "error": "node --check 不接受选项参数（--require/--import 等会执行代码）"}
        if exe == "node":
            tail = self._expand_globs(tail)
        resolved = _resolve_check_exe(exe)
        if resolved is None:
            if exe in ("python", "py"):
                return {"command": cmd, "error":
                        "打包版程序未内置 Python 解释器，且系统 PATH 找不到可用的 "
                        "python/py（已排除商店占位 stub）：Python 类检查无法执行。"
                        "打包版可独立完成的检查：node --check、npm test；"
                        "Python 语法/测试检查请使用源码运行"}
            return {"command": cmd, "error": f"找不到可执行文件: {exe}"}
        argv[0] = resolved
        result = _run_check_process(argv, str(self.workspace))
        if "error" in result:
            return {"command": cmd, **result}
        if result.get("timed_out"):
            return {"command": cmd, "error": "执行超时（120s，已终止进程树）",
                    "stdout": result["stdout"], "stderr": result["stderr"]}
        return {"command": cmd, "exit_code": result["exit_code"],
                "stdout": result["stdout"], "stderr": result["stderr"]}

    # -- 总入口 --------------------------------------------------------------

    def execute(self, name: str, arguments: dict) -> tuple[dict, str | None]:
        try:
            if name == "write_file":
                return self._do_write(arguments["path"], arguments["content"]), None
            if name == "write_files":
                files = arguments.get("files") or []
                if len(files) > MAX_BATCH_FILES:
                    raise ValueError(f"单次批量最多 {MAX_BATCH_FILES} 个文件")
                results = [self._do_write(f["path"], f["content"]) for f in files]
                return {"written": results}, None
            if name == "read_file":
                return self._do_read(arguments["path"]), None
            if name == "list_files":
                return self._do_list(str(arguments.get("subdir") or "")), None
            if name == "run_check":
                return self._do_run_check(arguments["command"]), None
            if name == "complete":
                return {"summary": arguments.get("summary", ""), "ok": True}, "complete"
            if name == "fail":
                return {"reason": arguments.get("reason", ""), "ok": False}, "fail"
            return {"error": f"未知工具: {name}"}, None
        except (ValueError, KeyError, OSError) as e:
            return {"error": str(e)}, None
