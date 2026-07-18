#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校园网自动登录 —— 图形界面版(可打包为 EXE)
==========================================
功能:
  * 启动即自动连接(默认「移动无线 @yd」, 可在界面修改)
  * 后台周期检测连通性, 断连自动重连(失败退避, 不狂刷)
  * 手动 连接 / 断开 / 立即检测
  * 开机自启 = 任务计划(用户登录时触发, 当前用户运行, 免提权)
  * 配置持久化到同目录 config.json
  * --minimized 启动后最小化到任务栏; --headless 无界面服务模式

依赖: 仅 Python 标准库 + srun_login.py (同目录)。打包用 PyInstaller。
"""
import os
import sys
import json
import time
import queue
import threading
import subprocess
import ctypes

import tkinter as tk
from tkinter import ttk, messagebox

# 同目录导入 srun_login (PyInstaller 打包后会一并打入)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from srun_login import SrunClient, is_online

# --------------------------------------------------------------------------- #
#  常量 / 配置
# --------------------------------------------------------------------------- #
# 线路下拉: (显示名, 域名后缀)  —— 首项为默认「移动无线」
DOMAINS = [
    ("移动无线", "@yd"),
    ("移动有线", "@ydyx"),
    ("电信",     "@dxwx"),
    ("学生",     "@stu"),
    ("教师",     "@tch"),
]
DOMAIN_BY_NAME = {n: c for n, c in DOMAINS}
DEFAULT_CONFIG = {
    "server": "172.16.245.50",
    "ac_id": "1",
    "username": "",
    "password": "",
    "domain": "@yd",
    "check_interval": 20,
    "auto_start": False,
    "start_minimized": False,
}
APP_REG_NAME = "SrunAutoLogin"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TASK_NAME = "SrunAutoLogin"   # 任务计划名: 用户登录时触发


def app_dir() -> str:
    """配置文件目录: 打包后取 EXE 所在目录, 开发时取脚本目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path() -> str:
    return os.path.join(app_dir(), "config.json")


def exe_path() -> str:
    """开机自启要指向的可执行文件。"""
    return sys.executable if getattr(sys, "frozen", False) else \
        os.path.abspath(sys.argv[0])


def load_config_file() -> dict:
    """从 config.json 读取并合并默认值(供 GUI 与 headless 共用)。"""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


# --------------------------------------------------------------------------- #
#  开机自启 = 任务计划(用户登录时 /SC ONLOGON, 以当前用户运行 GUI 到托盘)
#  - 登录时触发, 桌面会话已就绪, 直接跑 GUI(--minimized 缩到托盘), 无需无界面服务
#  - 注意: 本机的任务计划根目录需管理员权限才能创建/删除任务, 故弹一次 UAC
#          (这是机器安全配置决定的, 与触发类型无关; 普通用户机通常免 UAC)
# --------------------------------------------------------------------------- #
def _run_schtasks(args) -> int:
    """非提权运行 schtasks(用于 Query), 不弹控制台; 返回 returncode。"""
    return subprocess.run(["schtasks"] + args, capture_output=True,
                          creationflags=0x08000000).returncode


def _run_elevated(cmd_list) -> bool:
    """以管理员权限异步执行(schtasks 创建/删除任务需提权)。返回是否已发起。"""
    if sys.platform != "win32":
        return False
    exe, params = cmd_list[0], subprocess.list2cmdline(cmd_list[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 0)
    return int(rc) > 32


def _clear_legacy_run_key():
    """迁移: 清理旧版注册表 Run 自启项。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, APP_REG_NAME)
            except FileNotFoundError:
                pass
    except OSError:
        pass


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    return _run_schtasks(["/Query", "/TN", TASK_NAME]) == 0


def set_autostart(enable: bool):
    """开启: 创建登录时任务(运行 --minimized 到托盘, 当前用户); 关闭: 删除。"""
    if sys.platform != "win32":
        return
    _clear_legacy_run_key()
    _run_elevated(["schtasks", "/Delete", "/TN", "SrunAutoLogin-Boot", "/F"])
    if enable:
        cmd = ["schtasks", "/Create", "/TN", TASK_NAME,
               "/TR", '"%s" --minimized' % exe_path(),
               "/SC", "ONLOGON",   # 用户登录时(桌面已就绪, 跑 GUI)
               "/F"]
    else:
        cmd = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    _run_elevated(cmd)


# --------------------------------------------------------------------------- #
#  后台工作线程: 命令驱动 + 周期断连重连
#  与 UI 解耦: 通过 ui_q 推 (type, ...) 事件, 由主线程 after() 消费
# --------------------------------------------------------------------------- #
class Worker(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.cmd_q = queue.Queue()        # UI -> Worker 命令
        self._stop = threading.Event()
        self.fail = 0                     # 连续失败计数(退避用)
        self.last_online = None           # 上次在线状态(翻转时才记日志)

    def stop(self):
        self._stop.set()

    def send(self, cmd):
        self.cmd_q.put(cmd)

    # ---- 工具 ---- #
    def _cfg(self):
        return self.app.current_config

    def _client(self):
        c = self._cfg()
        return SrunClient(c["server"], c["ac_id"], c["username"],
                          c["password"], c["domain"])

    def _state(self, kind):
        """kind: 'online' | 'offline' | 'busy'"""
        self.app.post(("state", kind))

    def _log(self, msg):
        self.app.post(("log", time.strftime("%H:%M:%S") + "  " + msg))

    def _do_login(self) -> bool:
        c = self._cfg()
        if not c.get("username") or not c.get("password"):
            self._log("⚠ 尚未填写账号或密码, 请在界面设置后保存")
            return False
        try:
            resp = self._client().login()
            ok = resp.get("error") == "ok"
            if ok:
                self._log("✓ 登录成功  ({})".format(c["domain"]))
            else:
                self._log("✗ 登录失败: {}".format(
                    resp.get("error_msg") or resp.get("error")))
            return ok
        except Exception as e:
            self._log("✗ 登录异常: {}".format(e))
            return False

    def _do_logout(self):
        try:
            resp = self._client().logout()
            self._log("已注销  ({})".format(resp.get("error")))
        except Exception as e:
            self._log("注销异常: {}".format(e))

    def _probe(self) -> bool:
        online = is_online()
        self.app.post(("online", online))
        return online

    # ---- 单次检测+重连(周期任务) ---- #
    def _periodic(self):
        online = self._probe()
        if online:
            if self.last_online is not True:        # 状态翻转才记日志(避免刷屏)
                self._log("✓ 已连接")
            self.last_online, self.fail = True, 0
            self._state("online")
            return
        if self.last_online is not False:
            self._log("⚠ 检测到断线, 尝试重连…")
        self.last_online = False
        self._state("busy")
        ok = self._do_login()
        online = self._probe() if ok else False
        self._state("online" if online else "offline")
        if online:
            self.last_online, self.fail = True, 0
        else:
            self.fail += 1

    # ---- 命令分发 ---- #
    # 注意: 不能命名为 _handle —— threading.Thread.start() 会把 OS 线程句柄
    # 写入实例属性 self._handle, 从而覆盖同名方法, 导致线程首次处理命令即崩溃。
    def _process_cmd(self, cmd):
        if cmd == "login":
            self._state("busy")
            ok = self._do_login()
            online = self._probe() if ok else False
            self._state("online" if online else "offline")
            self.fail = 0 if online else self.fail + 1
        elif cmd == "logout":
            self._do_logout()
            self._probe()
            self._state("offline")
        elif cmd == "check":
            online = self._probe()
            self._state("online" if online else "offline")
            self._log("当前: {}".format("在线" if online else "离线"))

    # ---- 主循环 ---- #
    def run(self):
        self._log("后台监控已启动")
        next_check = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            # 1) 先消费 UI 命令(立即响应)
            try:
                cmd = self.cmd_q.get(
                    timeout=max(0.3, next_check - now))
                self._process_cmd(cmd)
                continue
            except queue.Empty:
                pass
            # 2) 到点做一次周期断连重连检测
            if time.monotonic() >= next_check:
                iv = max(5, int(self._cfg().get("check_interval", 20)))
                backoff = min(180, iv * (self.fail + 1)) if self.fail else iv
                next_check = time.monotonic() + backoff
                self._periodic()


# --------------------------------------------------------------------------- #
#  系统托盘(pystray + PIL; 缺库时 self.icon=None, 程序降级为普通窗口)
# --------------------------------------------------------------------------- #
class TrayController:
    def __init__(self, app):
        self.app = app
        self.icon = None

    def start(self):
        try:
            import pystray
        except Exception as e:
            print("[托盘] 未安装 pystray/Pillow, 托盘功能禁用:", e)
            return
        self.icon = pystray.Icon(
            "SrunAutoLogin",
            self._make_icon("#888888"),
            "校园网登录 · 启动中",
            menu=pystray.Menu(
                pystray.MenuItem("显示窗口", self._on_show, default=True),
                pystray.MenuItem("退出", self._on_quit),
            ),
        )
        threading.Thread(target=self.icon.run, daemon=True).start()

    @staticmethod
    def _make_icon(hex_color):
        from PIL import Image, ImageDraw
        r = (int(hex_color[1:3], 16), int(hex_color[3:5], 16),
             int(hex_color[5:7], 16), 255)
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=r)
        return img

    def update(self, kind):
        """随连接状态刷新托盘图标颜色与提示。"""
        if not self.icon:
            return
        tbl = {"online": ("#2e8b57", "已连接"),
               "offline": ("#c0392b", "未连接"),
               "busy": ("#e08a00", "连接中…")}
        color, text = tbl.get(kind, ("#888888", "校园网登录"))
        try:
            self.icon.icon = self._make_icon(color)
            self.icon.title = "校园网登录 · " + text
        except Exception:
            pass

    def _on_show(self, icon=None, item=None):
        self.app.post(("tray_show", None))   # 交给主线程操作 Tk

    def _on_quit(self, icon=None, item=None):
        self.app.post(("tray_quit", None))

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()             # 移除托盘图标
            except Exception:
                pass


# --------------------------------------------------------------------------- #
#  主窗口
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("校园网自动登录")
        self.geometry("430x470")
        self.resizable(False, False)
        self.current_config = dict(DEFAULT_CONFIG)

        self.ui_q = queue.Queue()          # Worker/Tray -> UI 事件
        self.worker = Worker(self)
        self.tray = TrayController(self)
        self.tray.start()                  # 无 pystray 时自动降级(self.icon=None)

        self._build_vars()
        self._load_config()
        self._build_ui()
        self._sync_config()                # 同步 current_config 供 worker 用

        self.protocol("WM_DELETE_WINDOW", self._on_close)   # X 按钮 = 退出程序
        self.bind("<Unmap>", self._on_unmap)                # 最小化按钮 = 缩回托盘
        self.after(120, self._poll)

        # 启动后台 + 立即检测一次
        self.worker.start()
        self.after(400, lambda: self.worker.send("check"))

        # 静默启动: 直接进托盘(无托盘则普通最小化到任务栏)
        if "--minimized" in sys.argv or self.var_start_min.get():
            self.after(300, self._minimize_to_tray)

    # ---------- 配置变量 ---------- #
    def _build_vars(self):
        self.var_user = tk.StringVar()
        self.var_pwd = tk.StringVar()
        self.var_server = tk.StringVar()
        self.var_domain = tk.StringVar()
        self.var_interval = tk.StringVar()
        self.var_autostart = tk.BooleanVar()
        self.var_start_min = tk.BooleanVar()
        # 任意字段变化 -> 重新同步 current_config (主线程, 安全)
        for v in (self.var_user, self.var_pwd, self.var_server,
                  self.var_domain, self.var_interval):
            v.trace_add("write", lambda *_: self._sync_config())

    def _sync_config(self):
        name = self.var_domain.get()
        domain = DOMAIN_BY_NAME.get(name, self.var_domain.get())
        try:
            iv = int(self.var_interval.get())
        except ValueError:
            iv = DEFAULT_CONFIG["check_interval"]
        self.current_config.update({
            "server": self.var_server.get().strip() or DEFAULT_CONFIG["server"],
            "ac_id": "1",
            "username": self.var_user.get().strip(),
            "password": self.var_pwd.get(),
            "domain": domain,
            "check_interval": iv,
        })

    def _load_config(self):
        cfg = dict(DEFAULT_CONFIG)
        try:
            with open(config_path(), "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
        self.var_user.set(cfg.get("username", ""))
        self.var_pwd.set(cfg.get("password", ""))
        self.var_server.set(cfg.get("server", DEFAULT_CONFIG["server"]))
        # 反查显示名
        name = next((n for n, c in DOMAINS if c == cfg.get("domain")), "移动无线")
        self.var_domain.set(name)
        self.var_interval.set(str(cfg.get("check_interval",
                                          DEFAULT_CONFIG["check_interval"])))
        self.var_start_min.set(bool(cfg.get("start_minimized", False)))
        # 注册表实际状态优先(开机自启以系统为准)
        self.var_autostart.set(autostart_enabled())

    def _save_config(self):
        self._sync_config()
        data = dict(self.current_config)
        want_autostart = self.var_autostart.get()
        data["auto_start"] = want_autostart
        data["start_minimized"] = self.var_start_min.get()
        try:
            with open(config_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        # 开机自启 = 任务计划(登录时, 当前用户跑 GUI 到托盘), 本机需一次 UAC
        if want_autostart and not getattr(sys, "frozen", False):
            messagebox.showwarning("开机自启",
                "开机自启需使用打包后的 EXE。\n"
                "请先运行 build_exe.bat 生成 SrunAutoLogin.exe, 用 EXE 打开本程序后再开启此选项。")
            self.var_autostart.set(False)
        else:
            set_autostart(want_autostart)
            messagebox.showinfo("开机自启",
                "已发起" + ("创建「登录时」自启任务" if want_autostart else "移除自启任务")
                + "。\n如弹出 UAC, 请点【是】。")
        self.after(2000, self._refresh_autostart)

    def _refresh_autostart(self):
        self.var_autostart.set(autostart_enabled())

    # ---------- 界面 ---------- #
    def _build_ui(self):
        pad = {"padx": 8, "pady": 5}
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)

        # 状态栏
        bar = ttk.Frame(f)
        bar.pack(fill="x", **pad)
        self.lbl_dot = tk.Label(bar, text="●", fg="#888", font=("Segoe UI", 18))
        self.lbl_dot.pack(side="left")
        self.lbl_state = ttk.Label(bar, text="检测中…", font=("Segoe UI", 12, "bold"))
        self.lbl_state.pack(side="left", padx=8)
        ttk.Button(bar, text="立即检测", width=8,
                   command=lambda: self.worker.send("check")).pack(side="right")
        ttk.Button(bar, text="断开", width=6,
                   command=lambda: self.worker.send("logout")).pack(side="right", padx=4)
        ttk.Button(bar, text="连接", width=6,
                   command=lambda: self.worker.send("login")).pack(side="right")

        ttk.Separator(f).pack(fill="x", pady=8)

        # 表单
        form = ttk.Frame(f)
        form.pack(fill="x", **pad)
        ttk.Label(form, text="账号").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self.var_user).grid(
            row=0, column=1, columnspan=2, sticky="we", pady=4)
        ttk.Label(form, text="密码").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self.var_pwd, show="•").grid(
            row=1, column=1, columnspan=2, sticky="we", pady=4)
        ttk.Label(form, text="线路").grid(row=2, column=0, sticky="w", pady=4)
        cb = ttk.Combobox(form, textvariable=self.var_domain, state="readonly",
                          values=[n for n, _ in DOMAINS], width=10)
        cb.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(form, text="服务器").grid(row=2, column=2, sticky="e", padx=(10, 0))
        ttk.Entry(form, textvariable=self.var_server, width=16).grid(
            row=2, column=3, sticky="we", pady=4, padx=(4, 0))
        ttk.Label(form, text="检测间隔(秒)").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self.var_interval, width=6).grid(
            row=3, column=1, sticky="w", pady=4)
        form.columnconfigure(1, weight=1)

        opts = ttk.Frame(f)
        opts.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Checkbutton(opts, text="开机自启(登录时)",
                        variable=self.var_autostart).pack(side="left")
        ttk.Checkbutton(opts, text="启动后最小化到托盘",
                        variable=self.var_start_min).pack(side="left", padx=12)
        ttk.Button(opts, text="保存设置", command=self._save_config).pack(side="right")

        # 日志
        ttk.Label(f, text="日志").pack(anchor="w", padx=8, pady=(10, 2))
        self.log_txt = tk.Text(f, height=9, wrap="word", state="disabled",
                               bg="#1e1e1e", fg="#d4d4d4",
                               font=("Consolas", 9), relief="flat")
        self.log_txt.pack(fill="both", expand=True, padx=8)

    # ---------- UI 事件消费(主线程) ---------- #
    def post(self, event):
        self.ui_q.put(event)

    def _poll(self):
        try:
            while True:
                self._apply(self.ui_q.get_nowait())
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _apply(self, event):
        kind = event[0]
        if kind == "state":
            self._set_state(event[1])
        elif kind == "log":
            self._append_log(event[1])
        elif kind == "tray_show":
            self._show_window()
        elif kind == "tray_quit":
            self._on_close()

    def _set_state(self, kind):
        mapping = {
            "online":  ("已连接",   "#2e8b57"),
            "offline": ("未连接",   "#c0392b"),
            "busy":    ("连接中…", "#e08a00"),
        }
        text, color = mapping.get(kind, ("检测中…", "#888"))
        self.lbl_state.config(text=text)
        self.lbl_dot.config(fg=color)
        if self.tray:
            self.tray.update(kind)            # 同步刷新托盘图标/提示

    def _append_log(self, line):
        self.log_txt.config(state="normal")
        self.log_txt.insert("end", line + "\n")
        self.log_txt.see("end")
        self.log_txt.config(state="disabled")

    # ---------- 窗口/托盘控制 ---------- #
    def _on_unmap(self, event):
        # 拦截标题栏"最小化"按钮: 缩回托盘
        if event.widget is self and self.state() == "iconic":
            self.after(10, self._minimize_to_tray)

    def _minimize_to_tray(self):
        if self.tray and self.tray.icon:     # 有托盘 -> 窗口+任务栏全部隐藏
            self.withdraw()
        # 无托盘时维持普通最小化到任务栏(iconic)

    def _show_window(self):
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(60, lambda: self.attributes("-topmost", False))
        self.focus_force()

    # ---------- 关闭(X 按钮 = 退出) ---------- #
    def _on_close(self):
        self.worker.stop()
        self.tray.stop()
        self.destroy()


# --------------------------------------------------------------------------- #
#  无界面服务模式(--headless): 仅运行 Worker 连网+断连重连, 无 Tk/托盘。
#  用于"系统启动时"任务计划(会话0, SYSTEM 账户, 无桌面)。日志写 srun_service.log。
# --------------------------------------------------------------------------- #
class _HeadlessApp:
    def __init__(self):
        try:
            self._logf = open(os.path.join(app_dir(), "srun_service.log"),
                              "a", encoding="utf-8")
        except OSError:
            self._logf = None

    @property
    def current_config(self):
        return load_config_file()          # 每次读取, 自动感知 GUI 改动

    def post(self, event):
        if event[0] == "log":
            line = time.strftime("%Y-%m-%d ") + event[1]   # 日期 + (worker 已含 时:分:秒)
            print(line, flush=True)
            if self._logf:
                try:
                    self._logf.write(line + "\n"); self._logf.flush()
                except OSError:
                    pass


def run_headless():
    print("[headless] 无界面服务模式启动")
    cfg = load_config_file()
    if not cfg.get("username") or not cfg.get("password"):
        print("[headless] config.json 未配置账号/密码, 退出")
        return
    app = _HeadlessApp()
    worker = Worker(app)
    worker.start()
    try:
        while True:
            time.sleep(3600)               # 主线程常驻, Worker 是 daemon
    except KeyboardInterrupt:
        worker.stop()


def main():
    if "--headless" in sys.argv:           # 任务计划/系统服务入口
        run_headless()
        return
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
