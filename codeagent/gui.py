"""第 8 步：Tkinter 桌面界面。

功能（对应任务书交付要求）:
* 配置 DeepSeek API Key（仅内存，不落盘）与模型名
* 选择两份输入文件（架构文档 / 架构视图）
* 指定输出目录
* 启动 Agent 任务、停止任务
* 实时进度日志 + 结束汇总（调用次数 / tokens / 检查结果）
* Mock 模式（离线联调）
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .config import AppConfig
from .llm import DeepSeekClient, LLMError, MockLLMClient
from .models import RunReport, StopReason
from .orchestrator import CodeAgent

STAGE_COLORS = {
    "preflight": "#8a6d3b",
    "preprocess": "#31708f",
    "plan": "#7d3c98",
    "generate": "#1d6f42",
    "check": "#c0392b",
    "repair": "#c0392b",
    "finalize": "#000000",
}


class CodeAgentGUI(tk.Tk):
    def __init__(self, config: AppConfig | None = None):
        super().__init__()
        self.config = config or AppConfig.load()
        self.queue: queue.Queue = queue.Queue()
        self.agent: CodeAgent | None = None
        self.worker: threading.Thread | None = None
        self.running = False
        self.report: RunReport | None = None

        self.title("CodeAgent — 简易代码智能体")
        self.geometry("860x640")
        self.minsize(720, 520)
        self._build_widgets()
        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------ UI 构建

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        # -- API 设置 --
        api_frame = ttk.LabelFrame(main, text="DeepSeek API 设置", padding=8)
        api_frame.pack(fill="x", **pad)
        api_frame.columnconfigure(1, weight=1)

        ttk.Label(api_frame, text="API Key:").grid(row=0, column=0, sticky="w")
        self.api_key_var = tk.StringVar(value=self.config.api_key)
        self.api_key_entry = ttk.Entry(api_frame, textvariable=self.api_key_var, show="•")
        self.api_key_entry.grid(row=0, column=1, sticky="ew")
        self.btn_test = ttk.Button(api_frame, text="测试连接", command=self._test_connection)
        self.btn_test.grid(row=0, column=2, **pad)

        ttk.Label(api_frame, text="模型:").grid(row=1, column=0, sticky="w")
        self.model_var = tk.StringVar(value=self.config.model)
        ttk.Entry(api_frame, textvariable=self.model_var).grid(row=1, column=1, sticky="ew")

        ttk.Label(api_frame, text="API 地址:").grid(row=2, column=0, sticky="w")
        self.base_url_var = tk.StringVar(value=self.config.base_url)
        ttk.Entry(api_frame, textvariable=self.base_url_var).grid(row=2, column=1, sticky="ew")

        # -- 输入输出 --
        io_frame = ttk.LabelFrame(main, text="输入 / 输出", padding=8)
        io_frame.pack(fill="x", **pad)
        io_frame.columnconfigure(1, weight=1)

        ttk.Label(io_frame, text="架构文档:").grid(row=0, column=0, sticky="w")
        self.doc_var = tk.StringVar(value=self.config.doc_path)
        ttk.Entry(io_frame, textvariable=self.doc_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(io_frame, text="浏览…", command=lambda: self._pick_file(
            self.doc_var, [("Markdown", "*.md"), ("所有文件", "*.*")],
            "选择 Architecture_Documentation.md")).grid(row=0, column=2, **pad)

        ttk.Label(io_frame, text="架构视图:").grid(row=1, column=0, sticky="w")
        self.views_var = tk.StringVar(value=self.config.views_path)
        ttk.Entry(io_frame, textvariable=self.views_var).grid(row=1, column=1, sticky="ew")
        ttk.Button(io_frame, text="浏览…", command=lambda: self._pick_file(
            self.views_var, [("Markdown", "*.md"), ("所有文件", "*.*")],
            "选择 Architecture_View.md")).grid(row=1, column=2, **pad)

        ttk.Label(io_frame, text="输出目录:").grid(row=2, column=0, sticky="w")
        self.out_var = tk.StringVar(value=self.config.output_dir)
        ttk.Label(io_frame, text="（每次任务在其下创建 generated_ 子目录）",
                  foreground="#666").grid(row=3, column=1, sticky="w")
        ttk.Entry(io_frame, textvariable=self.out_var).grid(row=2, column=1, sticky="ew")
        ttk.Button(io_frame, text="浏览…", command=lambda: self._pick_dir(
            self.out_var)).grid(row=2, column=2, **pad)

        # -- 高级与操作 --
        adv_frame = ttk.Frame(main)
        adv_frame.pack(fill="x", **pad)
        ttk.Label(adv_frame, text="最大步数:").pack(side="left")
        self.max_steps_var = tk.StringVar(value=str(self.config.max_steps))
        ttk.Entry(adv_frame, textvariable=self.max_steps_var, width=6).pack(side="left", padx=4)
        ttk.Label(adv_frame, text="修复轮数:").pack(side="left", padx=(12, 0))
        self.repair_var = tk.StringVar(value=str(self.config.repair_rounds))
        ttk.Entry(adv_frame, textvariable=self.repair_var, width=6).pack(side="left", padx=4)
        self.mock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(adv_frame, text="Mock 模式（离线联调）",
                        variable=self.mock_var).pack(side="left", padx=12)
        self.verify_var = tk.BooleanVar(value=self.config.verify_runtime)
        ttk.Checkbutton(adv_frame, text="运行时验证（npm test）",
                        variable=self.verify_var).pack(side="left", padx=12)

        self.btn_start = ttk.Button(adv_frame, text="启动 Agent", command=self._start)
        self.btn_start.pack(side="right")
        self.btn_stop = ttk.Button(adv_frame, text="停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="right", padx=6)

        # -- 日志 --
        log_frame = ttk.LabelFrame(main, text="运行日志", padding=4)
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = scrolledtext.ScrolledText(log_frame, height=16, state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        for stage, color in STAGE_COLORS.items():
            self.log.tag_config(stage, foreground=color)

        # -- 状态栏 --
        self.status_var = tk.StringVar(value="就绪。请配置 API Key 与输入文件后启动。")
        ttk.Label(main, textvariable=self.status_var, relief="sunken",
                  anchor="w").pack(fill="x", pady=(4, 0))

    # ------------------------------------------------------------------ 交互

    def _pick_file(self, var: tk.StringVar, types: list, title: str) -> None:
        path = filedialog.askopenfilename(title=title, filetypes=types)
        if path:
            var.set(path)

    def _pick_dir(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            var.set(path)

    def _log(self, stage: str, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stage}] {message}\n", stage)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _collect_config(self) -> AppConfig | None:
        doc, views = self.doc_var.get().strip(), self.views_var.get().strip()
        out = self.out_var.get().strip()
        if not doc or not Path(doc).is_file():
            messagebox.showerror("输入错误", "请选择有效的架构文档文件")
            return None
        if not views or not Path(views).is_file():
            messagebox.showerror("输入错误", "请选择有效的架构视图文件")
            return None
        if not out:
            messagebox.showerror("输入错误", "请指定输出目录")
            return None
        try:
            max_steps = int(self.max_steps_var.get())
            repair = int(self.repair_var.get())
        except ValueError:
            messagebox.showerror("输入错误", "最大步数 / 修复轮数必须是整数")
            return None
        cfg = AppConfig(
            api_key=self.api_key_var.get().strip(),
            base_url=self.base_url_var.get().strip() or "https://api.deepseek.com",
            model=self.model_var.get().strip() or "deepseek-v4-pro",
            doc_path=doc, views_path=views, output_dir=out,
            max_steps=max(1, max_steps), repair_rounds=max(0, repair),
            verify_runtime=self.verify_var.get(),
        )
        cfg.save()  # 保存路径等设置（不含 Key）
        return cfg

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_test.configure(state="disabled" if running else "normal")

    def _test_connection(self) -> None:
        key = self.api_key_var.get().strip()
        if not key:
            messagebox.showwarning("缺少 Key", "请先填写 API Key")
            return
        self.status_var.set("正在测试连接…")

        def worker():
            try:
                client = DeepSeekClient(key, base_url=self.base_url_var.get().strip(),
                                        model=self.model_var.get().strip() or "deepseek-v4-pro")
                models = client.list_models()
                self.queue.put({"stage": "preflight",
                                "message": f"连接成功：{len(models)} 个可用模型"
                                           f"（含 {self.model_var.get()}? "
                                           f"{'是' if self.model_var.get() in models else '否'}）"})
            except LLMError as e:
                self.queue.put({"stage": "check", "message": f"连接失败: {e}"})
        threading.Thread(target=worker, daemon=True).start()

    def _start(self) -> None:
        if self.running:
            return
        cfg = self._collect_config()
        if cfg is None:
            return
        if not cfg.effective_api_key() and not self.mock_var.get():
            messagebox.showwarning("缺少 Key", "请先填写 API Key（或勾选 Mock 模式）")
            return
        self.config = cfg
        self._set_running(True)
        self.report = None
        self._log("finalize", f"任务启动 → 输出目录: {cfg.output_dir}")

        def worker():
            try:
                if self.mock_var.get():
                    llm = MockLLMClient()
                else:
                    llm = DeepSeekClient(cfg.effective_api_key(), base_url=cfg.base_url,
                                         model=cfg.model, timeout=cfg.request_timeout)
                agent = CodeAgent(cfg, llm, emit=self.queue.put)
                self.agent = agent
                report = agent.run()
                self.queue.put({"stage": "__done__", "message": "运行结束",
                                "data": {"report": report}})
            except Exception as e:  # 线程内兜底，保证 UI 不崩溃
                self.queue.put({"stage": "__error__", "message": f"异常: {e}"})
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        if self.agent:
            self.agent.cancel()
            self.status_var.set("正在停止…（等待当前调用返回）")

    def _poll_queue(self) -> None:
        try:
            while True:
                ev = self.queue.get_nowait()
                self._handle_event(ev)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _handle_event(self, ev: dict) -> None:
        stage = ev.get("stage", "generate")
        message = ev.get("message", "")
        if stage == "__done__":
            self._set_running(False)
            report: RunReport = ev["data"]["report"]
            self.report = report
            self._log("finalize", f"结果: {report.stop_reason.value} — {report.message}")
            self._log("finalize", f"模型调用 {report.model_calls} 次 | "
                                  f"tokens: 输入 {report.tokens.get('prompt', 0)} / "
                                  f"输出 {report.tokens.get('completion', 0)}")
            if report.failures:
                for f in report.failures:
                    self._log("check", f"遗留问题: {f.path} — {f.message}")
            v = report.verification or {}
            vstatus, vdetail = v.get("status", "not_run"), v.get("detail", "")
            vlabel = {"passed": "运行时验证通过", "failed": "运行时验证失败",
                      "skipped": "运行时验证未执行",
                      "not_run": "运行时验证未执行"}.get(vstatus, "运行时验证未执行")
            if vdetail:
                vlabel += f"（{vdetail}）"
            self._log("finalize", f"运行时验证: {vlabel}")
            self.status_var.set(f"完成：{report.stop_reason.value}。输出目录: {report.output_dir}")
            if report.stop_reason == StopReason.COMPLETED:
                if messagebox.askyesno("完成", f"项目生成成功！\n\n{report.message}\n\n"
                                              f"{vlabel}\n\n是否打开输出目录？"):
                    self._open_dir(report.output_dir)
        elif stage == "__error__":
            self._set_running(False)
            self._log("check", message)
            self.status_var.set(f"错误: {message}")
        else:
            self._log(stage, message)
            self.status_var.set(message)

    @staticmethod
    def _open_dir(path: str) -> None:
        import os
        os.startfile(path)  # Windows 专用


def main() -> None:
    gui = CodeAgentGUI()
    gui.mainloop()


if __name__ == "__main__":
    main()
