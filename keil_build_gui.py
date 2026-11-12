# -*- coding: utf-8 -*-
"""
Keil 编译助手  —  不打开 uVision 也能编译 Keil 工程
原理：调用 uVision 命令行 UV4.exe -j0 -b <工程> -o <日志>
      并强制把工作目录切到 .uvproj 所在目录（uVision 用工作目录解析相对路径）

运行：python keil_build_gui.py     （需要带 tkinter 的 Python 3.8+）
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

APP_TITLE = "Keil 编译助手"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "KeilBuildHelper")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# ---------------- 配色（深色） ----------------
BG       = "#1e1e1e"
PANEL    = "#252526"
PANEL_2  = "#2d2d30"
BORDER   = "#3c3c3c"
ENTRY_BG = "#333337"
FG       = "#d4d4d4"
MUTED    = "#8b8b8b"
ACCENT   = "#0e639c"
ACCENT_H = "#1177bb"
OK_C     = "#89d185"
WARN_C   = "#dcdcaa"
ERR_C    = "#f48771"
INFO_C   = "#75beff"
BTN_BG   = "#3a3a3d"
BTN_H    = "#4a4a4d"

PROJECT_FILETYPES = [
    ("Keil 工程", "*.uvproj *.uvprojx *.uvmpw"),
    ("uVision 项目 (*.uvproj)", "*.uvproj"),
    ("MDK-ARM 项目 (*.uvprojx)", "*.uvprojx"),
    ("多工程工作区 (*.uvmpw)", "*.uvmpw"),
    ("所有文件", "*.*"),
]

RE_ERROR   = re.compile(r"\berror\b", re.I)
RE_ERRCNT  = re.compile(r"(\d+)\s+error\(s\)", re.I)
RE_WARNCNT = re.compile(r"(\d+)\s+warning\(s\)", re.I)
RE_SIZE    = re.compile(r"program size:\s*(.+)", re.I)
RE_HEX     = re.compile(r'creating hex file from\s+"?([^"]+)"?', re.I)


# ============================================================
#  环境探测
# ============================================================
def find_keil_root():
    """返回 Keil 安装根目录（包含 UV4\\UV4.exe 的那一层）"""
    cands = []
    for drive in "CDEFGH":
        for sub in ("Keil_v5", "Keil", "Keil_v4", "Keil_C251", "Keil_C51",
                    r"Program Files\Keil_v5", r"Program Files (x86)\Keil_v5"):
            cands.append("%s:\\%s" % (drive, sub))
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Keil\Products\MDK"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Keil\Products\MDK"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Keil\Products\MDK")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    i = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(k, i)
                            i += 1
                        except OSError:
                            break
                        if isinstance(value, str) and value.lower().endswith(("keil_v5", "keil")) and "\\" in value:
                            cands.append(value)
            except OSError:
                pass
    except Exception:
        pass
    for p in cands:
        if os.path.isfile(os.path.join(p, "UV4", "UV4.exe")):
            return os.path.normpath(p)
    return ""


def read_project_info(proj):
    """从 uvproj 里读 target 列表 / 输出目录 / 输出名"""
    info = {"targets": [], "outdir": "", "outname": ""}
    try:
        raw = open(proj, "rb").read()
    except OSError:
        return info
    txt = None
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not txt:
        return info
    info["targets"] = re.findall(r"<TargetName>(.*?)</TargetName>", txt)
    m = re.search(r"<OutputDirectory>(.*?)</OutputDirectory>", txt)
    if m:
        info["outdir"] = m.group(1).strip().replace("\\", os.sep)
    m = re.search(r"<OutputName>(.*?)</OutputName>", txt)
    if m:
        info["outname"] = m.group(1).strip()
    return info


def resolve_outdir(proj, info):
    base = os.path.dirname(proj)
    d = info.get("outdir") or r".\Objects"
    if not os.path.isabs(d):
        d = os.path.normpath(os.path.join(base, d))
    return d


# ============================================================
#  编译线程
# ============================================================
class BuildWorker(threading.Thread):
    """跑 UV4，同时 tail 日志文件，把增量输出塞进队列"""

    def __init__(self, uv4, project, mode, target, logfile, out_q):
        super().__init__(daemon=True)
        self.uv4 = uv4
        self.project = project
        self.mode = mode            # -b / -r / -c
        self.target = target or ""
        self.logfile = logfile
        self.q = out_q
        self.proc = None
        self._stop = False

    def stop(self):
        self._stop = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
            except Exception:
                pass

    def run(self):
        pdir = os.path.dirname(self.project)
        pname = os.path.basename(self.project)

        try:
            os.makedirs(os.path.dirname(self.logfile), exist_ok=True)
            if os.path.exists(self.logfile):
                os.remove(self.logfile)
        except OSError:
            pass

        args = [self.uv4, "-j0", self.mode]
        if self.target:
            args += ["-t", self.target]
        args += [pname, "-o", self.logfile]

        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE

        try:
            self.proc = subprocess.Popen(
                args, cwd=pdir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            self.q.put(("error", "无法启动 UV4.exe: %s" % e))
            return

        pos = 0
        idle_after_exit = 0
        while True:
            if os.path.exists(self.logfile):
                try:
                    with open(self.logfile, "rb") as f:
                        f.seek(pos)
                        chunk = f.read()
                    if chunk:
                        pos += len(chunk)
                        self.q.put(("log", chunk.decode("gbk", "replace")))
                        idle_after_exit = 0
                except OSError:
                    pass

            if self.proc.poll() is not None:
                idle_after_exit += 1
                if idle_after_exit > 4:      # 进程退出后再多读几轮，避免丢尾
                    break
            if self._stop:
                break
            time.sleep(0.12)

        # 收尾：把进程退出后残留的日志读完
        if os.path.exists(self.logfile):
            try:
                with open(self.logfile, "rb") as f:
                    f.seek(pos)
                    rest = f.read()
                if rest:
                    self.q.put(("log", rest.decode("gbk", "replace")))
            except OSError:
                pass

        rc = self.proc.returncode if self.proc else -1
        self.q.put(("done", rc))


# ============================================================
#  主窗口
# ============================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=BG)
        self.minsize(860, 560)

        self.cfg = self._load_cfg()
        geo = self.cfg.get("geometry") or "1020x700"
        self.geometry(geo)
        self.update_idletasks()
        try:
            x = (self.winfo_screenwidth() - self.winfo_width()) // 2
            y = max(0, (self.winfo_screenheight() - self.winfo_height()) // 2 - 40)
            if not self.cfg.get("geometry"):
                self.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

        self.keil_root = self.cfg.get("keil_root") or find_keil_root()
        self.queue = queue.Queue()
        self.worker = None
        self.t0 = 0.0
        self.last_meta = {}
        self.running = False

        self._build_ui()
        self._apply_cfg()
        self._update_keil_label()
        self.after(120, self._pump)

    # ---------- 配置 ----------
    def _load_cfg(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cfg(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            self.cfg["keil_root"] = self.keil_root
            self.cfg["project"] = self.var_proj.get().strip()
            self.cfg["geometry"] = self.geometry()
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dark.TCombobox",
                        fieldbackground=ENTRY_BG, background=ENTRY_BG, foreground=FG,
                        arrowcolor=FG, bordercolor=BORDER, lightcolor=ENTRY_BG,
                        darkcolor=ENTRY_BG, selectbackground=ACCENT, selectforeground="#ffffff",
                        padding=4)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", ENTRY_BG), ("disabled", PANEL)],
                  foreground=[("readonly", FG), ("disabled", MUTED)])
        style.configure("Dark.Vertical.TScrollbar",
                        background=PANEL_2, troughcolor=BG, bordercolor=BG,
                        arrowcolor=MUTED, darkcolor=PANEL_2, lightcolor=PANEL_2,
                        gripcount=0, width=12)
        style.map("Dark.Vertical.TScrollbar", background=[("active", BTN_H)])
        self.option_add("*TCombobox*Listbox.background", PANEL_2)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        # ---- 顶部标题栏 ----
        head = tk.Frame(self, bg=PANEL, height=52)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="⚙", bg=PANEL, fg=INFO_C,
                 font=("Segoe UI", 16)).pack(side="left", padx=(14, 6))
        tk.Label(head, text=APP_TITLE, bg=PANEL, fg=FG,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        self.lbl_keil = tk.Label(head, text="", bg=PANEL, fg=MUTED,
                                 font=("Microsoft YaHei UI", 9), cursor="hand2")
        self.lbl_keil.pack(side="right", padx=14)
        self.lbl_keil.bind("<Button-1>", lambda e: self.choose_keil())

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ---- 工程行 ----
        body = tk.Frame(self, bg=BG)
        body.pack(fill="x", padx=14, pady=(12, 0))

        row1 = tk.Frame(body, bg=BG)
        row1.pack(fill="x")
        tk.Label(row1, text="工程", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 8))

        self.var_proj = tk.StringVar()
        self.ent_proj = tk.Entry(row1, textvariable=self.var_proj, bg=ENTRY_BG, fg=FG,
                                 insertbackground=FG, relief="flat", bd=0,
                                 font=("Consolas", 10), highlightthickness=1,
                                 highlightbackground=BORDER, highlightcolor=ACCENT)
        self.ent_proj.pack(side="left", fill="x", expand=True, ipady=5)
        self.ent_proj.bind("<Return>", lambda e: self.load_project(self.var_proj.get()))
        self.ent_proj.bind("<FocusOut>", lambda e: self.load_project(self.var_proj.get()))

        self._btn(row1, "浏览", self.browse_project, width=6).pack(side="left", padx=(8, 0))

        row2 = tk.Frame(body, bg=BG)
        row2.pack(fill="x", pady=(8, 0))
        tk.Label(row2, text="目标", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 8))
        self.var_target = tk.StringVar()
        self.cmb_target = ttk.Combobox(row2, textvariable=self.var_target, values=[],
                                       state="readonly", style="Dark.TCombobox", width=28,
                                       font=("Consolas", 9))
        self.cmb_target.pack(side="left", ipady=1)
        self.lbl_hint = tk.Label(row2, text="", bg=BG, fg=MUTED,
                                 font=("Microsoft YaHei UI", 8))
        self.lbl_hint.pack(side="left", padx=10)

        # ---- 工具栏 ----
        bar = tk.Frame(self, bg=PANEL, height=46)
        bar.pack(fill="x", pady=(12, 0))
        bar.pack_propagate(False)
        inner = tk.Frame(bar, bg=PANEL)
        inner.pack(side="left", padx=12, pady=8)

        self.b_build   = self._btn(inner, "▶  编译 (F7)",  lambda: self.start_build("-b"), accent=True, width=11)
        self.b_rebuild = self._btn(inner, "⟳  重编译",      lambda: self.start_build("-r"), width=9)
        self.b_clean   = self._btn(inner, "🧹  清理",       lambda: self.start_build("-c"), width=8)
        self.b_stop    = self._btn(inner, "⏹  停止",        self.stop_build, width=8)
        for b in (self.b_build, self.b_rebuild, self.b_clean, self.b_stop):
            b.pack(side="left", padx=(0, 8))
        self.b_stop.configure(state="disabled")

        right = tk.Frame(bar, bg=PANEL)
        right.pack(side="right", padx=12, pady=8)
        self.b_open = self._btn(right, "📂  打开输出目录", self.open_outdir, width=14)
        self.b_open.pack(side="right")
        self.b_clear = self._btn(right, "清空日志", self.clear_log, width=8)
        self.b_clear.pack(side="right", padx=(0, 8))

        # ---- 日志区 ----
        logwrap = tk.Frame(self, bg=BORDER, bd=0)
        logwrap.pack(fill="both", expand=True, padx=14, pady=10)
        self.txt = tk.Text(logwrap, bg=BG, fg=FG, insertbackground=FG, relief="flat",
                           bd=0, wrap="none", font=("Consolas", 9), padx=10, pady=8,
                           state="disabled", selectbackground="#264f78")
        vsb = ttk.Scrollbar(logwrap, orient="vertical", command=self.txt.yview,
                            style="Dark.Vertical.TScrollbar")
        self.txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.tag_configure("err", foreground=ERR_C)
        self.txt.tag_configure("warn", foreground=WARN_C)
        self.txt.tag_configure("good", foreground=OK_C)
        self.txt.tag_configure("info", foreground=INFO_C)
        self.txt.tag_configure("dim", foreground=MUTED)

        # ---- 状态栏 ----
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        sb = tk.Frame(self, bg=PANEL, height=26)
        sb.pack(fill="x")
        sb.pack_propagate(False)
        self.lbl_status = tk.Label(sb, text="● 就绪", bg=PANEL, fg=MUTED,
                                   font=("Microsoft YaHei UI", 9))
        self.lbl_status.pack(side="left", padx=12)
        self.lbl_meta = tk.Label(sb, text="", bg=PANEL, fg=MUTED,
                                 font=("Consolas", 9))
        self.lbl_meta.pack(side="right", padx=12)

        self.bind_all("<F7>", lambda e: self.start_build("-b"))
        self.bind_all("<Shift-F7>", lambda e: self.start_build("-r"))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _btn(self, parent, text, cmd, accent=False, width=None):
        bg = ACCENT if accent else BTN_BG
        hov = ACCENT_H if accent else BTN_H
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg="#ffffff" if accent else FG,
                      activebackground=hov, activeforeground="#ffffff",
                      relief="flat", bd=0, cursor="hand2", width=width,
                      font=("Microsoft YaHei UI", 9), padx=6, pady=5,
                      disabledforeground="#6a6a6a")
        b.bind("<Enter>", lambda e, w=b, c=hov: w.configure(bg=c) if str(w["state"]) != "disabled" else None)
        b.bind("<Leave>", lambda e, w=b, c=bg: w.configure(bg=c) if str(w["state"]) != "disabled" else None)
        return b

    # ---------- 配置回填 ----------
    def _apply_cfg(self):
        proj = self.cfg.get("project") or ""
        if proj and os.path.isfile(proj):
            self.var_proj.set(proj)
            self.load_project(proj)
        else:
            default = self._guess_project()
            if default:
                self.var_proj.set(default)
                self.load_project(default)
            else:
                self._log("提示：点“浏览”选择 .uvproj / .uvprojx 工程文件。\n", "dim")

        self._log("就绪。F7 编译   Shift+F7 重编译\n", "dim")
        self._log("uVision 命令行：UV4.exe -j0 -b <工程> -o <日志>（工作目录 = 工程所在目录）\n", "dim")
        if self._uv4():
            self._log("Keil 路径：%s\n" % self._uv4(), "dim")
        self._log("─" * 68 + "\n", "dim")

    def _guess_project(self):
        # 工作区里如果只有一个工程就自动填上
        for root, dirs, files in os.walk(os.getcwd()):
            depth = root[len(os.getcwd()):].count(os.sep)
            if depth > 3:
                dirs[:] = []
                continue
            for f in files:
                if f.lower().endswith((".uvproj", ".uvprojx")):
                    return os.path.join(root, f)
        return ""

    # ---------- 事件 ----------
    def browse_project(self):
        init = os.path.dirname(self.var_proj.get()) or os.path.expanduser("~")
        p = filedialog.askopenfilename(title="选择 Keil 工程", initialdir=init,
                                       filetypes=PROJECT_FILETYPES)
        if p:
            self.var_proj.set(os.path.normpath(p))
            self.load_project(p)

    def choose_keil(self):
        p = filedialog.askdirectory(title="选择 Keil 安装目录（含 UV4 子目录）",
                                    initialdir=self.keil_root or "C:\\")
        if not p:
            return
        if not os.path.isfile(os.path.join(p, "UV4", "UV4.exe")):
            if not os.path.isfile(os.path.join(p, "UV4.exe")):
                self._msg("这个目录里没有 UV4\\UV4.exe，请选 Keil 安装根目录（例如 D:\\Keil_v5）。")
                return
            self.keil_root = os.path.dirname(p)
        else:
            self.keil_root = p
        self._update_keil_label()
        self._save_cfg()

    def load_project(self, path):
        path = (path or "").strip().strip('"')
        if not path or not os.path.isfile(path):
            return
        info = read_project_info(path)
        self.project_info = info
        targets = info.get("targets") or []
        self.cmb_target["values"] = targets
        if targets:
            if self.var_target.get() not in targets:
                self.var_target.set(targets[0])
        outdir = resolve_outdir(path, info)
        hexname = (info.get("outname") or "").strip()
        hint = ""
        if hexname:
            hint = "输出：%s\\%s.hex" % (outdir, hexname)
        if len(targets) > 1:
            hint = "共 %d 个目标   " % len(targets) + hint
        self.lbl_hint.configure(text=hint)
        self.title("%s — %s" % (APP_TITLE, os.path.basename(path)))

    def _uv4(self):
        if not self.keil_root:
            return ""
        p = os.path.join(self.keil_root, "UV4", "UV4.exe")
        if os.path.isfile(p):
            return p
        p2 = os.path.join(self.keil_root, "UV4.exe")
        return p2 if os.path.isfile(p2) else ""

    def _update_keil_label(self):
        uv4 = self._uv4()
        if uv4:
            self.lbl_keil.configure(text="Keil: %s   (点击更换)" % self.keil_root, fg=MUTED)
        else:
            self.lbl_keil.configure(text="未找到 Keil，点此指定安装目录", fg=ERR_C)

    # ---------- 编译 ----------
    def start_build(self, mode):
        if self.running:
            return
        proj = self.var_proj.get().strip().strip('"')
        if not os.path.isfile(proj):
            self._msg("请先选择一个存在的工程文件（.uvproj / .uvprojx）。")
            return
        uv4 = self._uv4()
        if not uv4:
            self._msg("没找到 UV4.exe，请点击右上角指定 Keil 安装目录。")
            return

        names = {"-b": "编译", "-r": "重编译", "-c": "清理"}
        self.running = True
        for b in (self.b_build, self.b_rebuild, self.b_clean):
            b.configure(state="disabled")
        self.b_stop.configure(state="normal")
        self.lbl_status.configure(text="● %s中…" % names.get(mode, "编译"), fg=INFO_C)
        self.lbl_meta.configure(text="")

        logfile = os.path.join(os.environ.get("TEMP", "."), "keil_build_%d.log" % int(time.time() * 1000))
        self.t0 = time.monotonic()
        self.last_meta = {}

        if mode != "-c":
            self._log("\n" + "─" * 68 + "\n", "dim")
            self._log("▶ %s  %s\n" % (names[mode], os.path.basename(proj)), "info")
        self._save_cfg()

        self.worker = BuildWorker(uv4, proj, mode, self.var_target.get(), logfile, self.queue)
        self.worker.start()
        self.after(200, self._tick)

    def stop_build(self):
        if self.worker:
            self.worker.stop()
            self._log("\n[已中止]\n", "err")
            self._finish(True)

    def _tick(self):
        if self.running:
            self.lbl_meta.configure(text="%.1fs" % (time.monotonic() - self.t0))
            self.after(200, self._tick)

    def _pump(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._append(payload)
                elif kind == "done":
                    self._finish(False, payload)
                elif kind == "error":
                    self._log(payload + "\n", "err")
                    self._finish(False, -1)
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def _finish(self, aborted, rc=None):
        self.running = False
        for b in (self.b_build, self.b_rebuild, self.b_clean):
            b.configure(state="normal")
        self.b_stop.configure(state="disabled")

        elapsed = time.monotonic() - self.t0 if self.t0 else 0
        errs = self.last_meta.get("errors")
        warns = self.last_meta.get("warnings")

        if aborted:
            self.lbl_status.configure(text="● 已中止", fg=WARN_C)
            self.lbl_meta.configure(text="%.1fs" % elapsed)
            return

        ok = (errs == 0)
        if ok:
            txt = "● 编译成功"
            if warns:
                txt += "（%d 警告）" % warns
            self.lbl_status.configure(text=txt, fg=OK_C)
        else:
            self.lbl_status.configure(text="● 编译失败（%s 个错误）" % (errs if errs is not None else "?"), fg=ERR_C)

        parts = []
        if errs is not None:
            parts.append("errors=%d" % errs)
        if warns is not None:
            parts.append("warnings=%d" % warns)
        if self.last_meta.get("size"):
            parts.append(self.last_meta["size"])
        parts.append("%.1fs" % elapsed)
        self.lbl_meta.configure(text="   ".join(parts))

        if ok and self.last_meta.get("hex"):
            self._log("✔ 已生成：%s\n" % self.last_meta["hex"], "good")

    # ---------- 日志渲染 ----------
    def _append(self, text):
        for line in text.splitlines(keepends=True):
            tag = self._classify(line)
            self._log(line, tag)

            low = line.lower()
            m = RE_ERRCNT.search(low)
            if m:
                self.last_meta["errors"] = int(m.group(1))
            m = RE_WARNCNT.search(low)
            if m:
                self.last_meta["warnings"] = int(m.group(1))
            m = RE_SIZE.search(line)
            if m:
                self.last_meta["size"] = m.group(1).strip()
            m = RE_HEX.search(line)
            if m:
                self.last_meta["hex"] = m.group(1).strip()

        if "C168" in text:
            self._log("提示：C251 编译器中断号上限为 31。\n"
                      "      需要给 C251.EXE 打中断号扩展补丁（本机已备份为 C251.EXE.bak）。\n", "warn")

    @staticmethod
    def _classify(line):
        low = line.lower()
        if "target not created" in low or "fatal" in low:
            return "err"
        m = RE_ERRCNT.search(low)
        if m:
            return "err" if int(m.group(1)) else "good"
        if RE_ERROR.search(low):
            return "err"
        if "warning" in low:
            return "warn"
        if "program size" in low or "creating hex" in low:
            return "good"
        stripped = low.lstrip()
        for kw in ("build target", "rebuild target", "compiling", "assembling",
                   "linking", "cleaning", "build time", "load"):
            if stripped.startswith(kw):
                return "info" if kw not in ("build time",) else "dim"
        return None

    def _log(self, text, tag=None):
        self.txt.configure(state="normal")
        if tag:
            self.txt.insert("end", text, tag)
        else:
            self.txt.insert("end", text)
        # 限制行数，避免长日志拖慢
        if int(self.txt.index("end-1c").split(".")[0]) > 6000:
            self.txt.delete("1.0", "800.0")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    # ---------- 其它 ----------
    def open_outdir(self):
        proj = self.var_proj.get().strip()
        if not os.path.isfile(proj):
            return
        info = getattr(self, "project_info", None) or read_project_info(proj)
        d = resolve_outdir(proj, info)
        if not os.path.isdir(d):
            self._msg("输出目录还不存在：\n%s\n\n先编译一次就会生成。" % d)
            return
        os.startfile(d)

    def _msg(self, text):
        from tkinter import messagebox
        messagebox.showwarning(APP_TITLE, text, parent=self)

    def on_close(self):
        if self.running and self.worker:
            self.worker.stop()
        self._save_cfg()
        self.destroy()


if __name__ == "__main__":
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()
