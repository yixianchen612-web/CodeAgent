"""Opt-in acceptance runner for the packaged GUI; never makes API requests."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import time
import traceback


def _capture_window(app, target: Path) -> bool:
    """Capture this application's window only, using Windows GDI (no extra packages)."""
    if sys.platform != "win32":
        return False
    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowDC.argtypes = [wintypes.HWND]
    user.GetWindowDC.restype = wintypes.HDC
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HANDLE
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HANDLE, wintypes.UINT,
                            wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.UINT]
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    hwnd = user.GetAncestor(app.winfo_id(), 2)
    rect = wintypes.RECT()
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    width, height = rect.right - rect.left, rect.bottom - rect.top
    dc = user.GetWindowDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not user.PrintWindow(hwnd, memory, 0):
            return False
        gdi.SelectObject(memory, previous)
        pixels = ctypes.create_string_buffer(width * height * 4)
        header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0,
                             len(pixels), 0, 0, 0, 0)
        info = ctypes.create_string_buffer(header)
        if not gdi.GetDIBits(memory, bitmap, 0, height, pixels, info, 0):
            return False
        file_header = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
        target.write_bytes(file_header + header + pixels.raw)
        return True
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, dc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an offline packaged-GUI acceptance check")
    parser.add_argument("--offline-self-test", action="store_true", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--views", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args(argv)
    evidence = Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    output = Path(args.out).resolve()
    # Test profile is deliberately local to the evidence directory.
    os.environ["APPDATA"] = str(evidence / "profile")
    os.environ.pop("DEEPSEEK_API_KEY", None)
    from . import config as config_module
    from .config import AppConfig
    from .gui import CodeAgentGUI, messagebox

    config_module.CONFIG_PATH = evidence / "profile" / "CodeAgent" / "config.json"
    cfg = AppConfig(doc_path=str(Path(args.doc).resolve()),
                    views_path=str(Path(args.views).resolve()), output_dir=str(output))
    app = CodeAgentGUI(cfg)
    app.mock_var.set(True)
    app.verify_var.set(True)
    app.api_key_var.set("")
    app.title("CodeAgent — 离线自检")
    dialogs: list[dict] = []
    messagebox.askyesno = lambda title, text, **kw: dialogs.append(
        {"type": "completion", "title": title, "text": text}) or False
    messagebox.showerror = lambda title, text, **kw: dialogs.append(
        {"type": "error", "title": title, "text": text})
    messagebox.showwarning = lambda title, text, **kw: dialogs.append(
        {"type": "warning", "title": title, "text": text})
    started = time.monotonic()
    result: dict = {
        "mode": "offline_mock_gui", "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        "path": os.environ.get("PATH", ""),
        "external_tools": {name: shutil.which(name) for name in ("python", "py", "node", "npm")},
        "api_used": False, "status": "running", "dialogs": dialogs,
        "verification_scope": "Current Windows host with restricted process PATH; not a clean OS test.",
    }

    def finish(error: str = "") -> None:
        try:
            if error:
                result["status"] = "failed"
                result["error"] = error
            else:
                report = app.report
                result["report"] = report.to_dict()
                root = Path(report.output_dir)
                files = sorted(p.relative_to(root).as_posix()
                               for p in root.rglob("*") if p.is_file())
                result["output_files"] = files
                result["client_class"] = type(app.agent.llm).__name__
                checks = {
                    "packaged_executable": result["frozen"],
                    "mock_client": result["client_class"] == "MockLLMClient",
                    "task_completed": report.stop_reason.value == "completed",
                    "no_reported_failures": not report.failures,
                    "readme": (root / "README.md").is_file(),
                    "dependencies": any((root / p).is_file() for p in ("requirements.txt", "package.json")),
                    "tests": any(p.startswith("tests/") for p in files),
                    "source": any(p.startswith("src/") for p in files),
                    "plan_and_report": all((root / p).is_file() for p in ("project_plan.json", "run_report.json")),
                    "verification_truthful": report.verification.get("status") in ("skipped", "not_run"),
                    "not_using_external_tools": all(v is None for v in result["external_tools"].values()),
                }
                result["checks"] = checks
                result["status"] = "passed" if all(checks.values()) else "failed"
            app.update_idletasks()
            (evidence / "gui.log").write_text(app.log.get("1.0", "end"), encoding="utf-8")
            result["screenshot_saved"] = _capture_window(app, evidence / "completed.bmp")
        except Exception:
            result["status"] = "failed"
            result["error"] = traceback.format_exc()
        finally:
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            (evidence / "acceptance_result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            app.destroy()

    def poll() -> None:
        if app.report is not None:
            app.after(250, finish)
        elif time.monotonic() - started > 90:
            if app.agent:
                app.agent.cancel()
            finish("The offline GUI task did not finish within 90 seconds")
        elif not app.running and dialogs:
            finish("GUI validation rejected the input")
        else:
            app.after(100, poll)

    def begin() -> None:
        try:
            app.btn_start.invoke()
            app.after(100, poll)
        except Exception:
            finish(traceback.format_exc())

    app.after(200, begin)
    app.mainloop()
    return 0 if result["status"] == "passed" else 1
