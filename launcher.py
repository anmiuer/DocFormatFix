"""DocFormatFix 无控制台启动器。

- 双击 VBS/BAT 后以 pythonw 启动，不创建可操作终端
- 启动前扫描 Python 版本、依赖和项目文件
- 环境缺失时弹出 GUI 提示，可自动安装、选择本地 Python 环境或离线安装包
- GUI 关闭后启动器进程自然退出
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
LAUNCHER = Path(__file__).resolve()
CONFIG_PATH = BASE / "config" / "launcher.json"
LOG_PATH = BASE / "logs" / "launcher.log"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

PACKAGE_SPECS = {
    "docx": "python-docx>=1.1.0",
    "tkinterdnd2": "tkinterdnd2>=0.4.2",
}
CRITICAL_MODULES = ("tkinter", "docx")
OPTIONAL_MODULES = ("tkinterdnd2",)
PROJECT_ITEMS = (
    "gui.py",
    "format_editor.py",
    "format_requirements_dialog.py",
    "template_manager.py",
    "launcher.py",
    "src/__init__.py",
    "src/file_handler.py",
    "src/pre_scanner.py",
    "src/structure_detector.py",
    "src/format_engine.py",
    "src/format_requirements.py",
    "src/markdown_handler.py",
    "src/reporter.py",
    "src/pipeline.py",
    "src/win_drop.py",
    "templates",
)


def log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except Exception:
        pass


def load_launcher_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_launcher_config(data: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log("保存 launcher.json 失败：\n" + traceback.format_exc())


def _same_path(left, right) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except Exception:
        return str(left) == str(right)


def console_python(python_exe) -> str:
    path = Path(python_exe)
    if path.name.lower() == "pythonw.exe":
        sibling = path.with_name("python.exe")
        if sibling.exists():
            return str(sibling)
    return str(path)


def pythonw_for(python_exe) -> str:
    path = Path(python_exe)
    if path.name.lower() == "pythonw.exe" and path.exists():
        return str(path)
    sibling = path.with_name("pythonw.exe")
    if sibling.exists():
        return str(sibling)
    return str(path)


def _current_missing() -> tuple:
    version = list(sys.version_info[:3])
    missing = []
    for name in ("tkinter", "docx", "tkinterdnd2"):
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    return version, missing, None


def _remote_missing(python_exe) -> tuple:
    console = console_python(python_exe)
    code = (
        "import importlib, json, sys\n"
        "missing = []\n"
        "for name in ('tkinter', 'docx', 'tkinterdnd2'):\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception:\n"
        "        missing.append(name)\n"
        "print(json.dumps({'version': list(sys.version_info[:3]), 'missing': missing}))\n"
    )
    try:
        result = subprocess.run(
            [console, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return None, [], (result.stderr or result.stdout or "检测失败").strip()
        lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
        data = json.loads(lines[-1])
        return data.get("version"), data.get("missing", []), None
    except Exception as exc:
        return None, [], str(exc)


def _project_issues() -> tuple:
    critical = []
    warnings = []
    for item in PROJECT_ITEMS:
        if not (BASE / item).exists():
            critical.append(f"缺少项目文件/目录：{item}")
    template_dir = BASE / "templates"
    if template_dir.exists() and not list(template_dir.glob("*.json")):
        critical.append("templates 目录中没有 JSON 模板")
    if not (BASE / "config" / "settings.json").exists():
        warnings.append("config/settings.json 不存在，将使用默认设置")

    settings = {}
    try:
        settings_path = BASE / "config" / "settings.json"
        if settings_path.exists():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                settings = data
    except Exception as exc:
        warnings.append(f"读取 config/settings.json 失败：{exc}")

    for key, default in (("backup_dir", "backup"), ("output_dir", "output")):
        value = settings.get(key) or default
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = BASE / path
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            warnings.append(f"{key} 不可写：{path}（{exc}）")
    return critical, warnings


def _build_scan(version, missing, error, python_exe) -> dict:
    critical = []
    optional = []
    warnings = []
    if error:
        critical.append(f"环境检测失败：{error}")
    if version and tuple(version[:2]) < (3, 9):
        critical.append(f"Python 版本过低：{'.'.join(map(str, version))}（需要 3.9+）")
    for name in missing:
        if name == "tkinterdnd2":
            optional.append("tkinterdnd2（OLE 拖放增强，缺少时仍可使用原生拖放）")
        elif name == "docx":
            critical.append("python-docx（Word 文档处理）")
        elif name == "tkinter":
            critical.append("tkinter（Python 图形界面组件）")
        else:
            critical.append(name)
    project_critical, project_warnings = _project_issues()
    critical.extend(project_critical)
    warnings.extend(project_warnings)
    return {
        "python": str(python_exe),
        "version": version,
        "missing": list(missing),
        "critical": critical,
        "optional": optional,
        "warnings": warnings,
        "ok": not critical,
    }


def scan_current() -> dict:
    version, missing, error = _current_missing()
    return _build_scan(version, missing, error, sys.executable)


def scan_for_python(python_exe) -> dict:
    version, missing, error = _remote_missing(python_exe)
    return _build_scan(version, missing, error, python_exe)


def fallback_message(title: str, message: str, error: bool = False) -> None:
    try:
        import ctypes

        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(0, message, title, flags)
    except Exception:
        pass


def show_message(parent, title: str, message: str, error: bool = False) -> None:
    try:
        from tkinter import messagebox

        text = message if len(message) <= 4000 else message[-4000:]
        if error:
            messagebox.showerror(title, text, parent=parent)
        else:
            messagebox.showinfo(title, text, parent=parent)
    except Exception:
        fallback_message(title, message, error=error)


def _dialog_text(scan: dict) -> str:
    parts = []
    if scan["critical"]:
        parts.append("必要项缺失：\n" + "\n".join(f"  - {item}" for item in scan["critical"]))
    if scan["optional"]:
        parts.append("可选项缺失：\n" + "\n".join(f"  - {item}" for item in scan["optional"]))
    if scan["warnings"]:
        parts.append("警告：\n" + "\n".join(f"  - {item}" for item in scan["warnings"]))
    parts.append(
        f"当前 Python：{scan['python']}\n"
        f"版本：{'.'.join(map(str, scan['version'] or [])) or '未知'}"
    )
    return "\n\n".join(parts)


def show_environment_dialog(root, scan: dict) -> str:
    import tkinter as tk
    from tkinter import ttk

    dialog = tk.Toplevel(root)
    dialog.title("DocFormatFix 环境检查")
    dialog.geometry("720x440")
    dialog.minsize(620, 380)
    dialog.transient(root)
    dialog.grab_set()

    action = {"value": "exit"}

    def choose(value: str) -> None:
        action["value"] = value
        dialog.destroy()

    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill="both", expand=True)

    heading = "检测到运行环境不完整或存在警告。"
    ttk.Label(
        frame,
        text=heading,
        font=("Microsoft YaHei UI", 12, "bold"),
    ).pack(anchor="w")

    ttk.Label(
        frame,
        text=(
            "自动安装会使用当前 Python 的 pip 下载依赖；"
            "也可以选择本地 Python 环境或离线安装包。"
        ),
        wraplength=660,
    ).pack(anchor="w", pady=(4, 8))

    text_frame = ttk.Frame(frame)
    text_frame.pack(fill="both", expand=True)
    content = tk.Text(
        text_frame,
        wrap="word",
        height=12,
        relief="flat",
        background="#fbfbfc",
        font=("Microsoft YaHei UI", 10),
        padx=8,
        pady=8,
    )
    content.pack(side="left", fill="both", expand=True)
    scroll = ttk.Scrollbar(text_frame, orient="vertical", command=content.yview)
    scroll.pack(side="right", fill="y")
    content.configure(yscrollcommand=scroll.set)
    content.insert("1.0", _dialog_text(scan))
    content.configure(state="disabled")

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))

    ttk.Button(
        buttons,
        text="自动下载安装",
        command=lambda: choose("auto"),
    ).pack(side="left")
    ttk.Button(
        buttons,
        text="选择本地 Python 环境",
        command=lambda: choose("local"),
    ).pack(side="left", padx=(8, 0))
    ttk.Button(
        buttons,
        text="选择离线安装文件",
        command=lambda: choose("file"),
    ).pack(side="left", padx=(8, 0))

    ttk.Button(buttons, text="退出", command=lambda: choose("exit")).pack(side="right")
    launch_button = ttk.Button(
        buttons,
        text="仍然启动",
        command=lambda: choose("launch"),
    )
    launch_button.pack(side="right", padx=(0, 8))
    if not scan["ok"]:
        launch_button.configure(state="disabled")

    dialog.protocol("WM_DELETE_WINDOW", lambda: choose("exit"))
    root.wait_window(dialog)
    return action["value"]


def run_pip(root, python_exe, args, title: str) -> tuple:
    import tkinter as tk
    from tkinter import ttk

    progress = tk.Toplevel(root)
    progress.title("安装依赖")
    progress.geometry("520x150")
    progress.transient(root)
    progress.grab_set()
    progress.protocol("WM_DELETE_WINDOW", lambda: None)

    frame = ttk.Frame(progress, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=title, wraplength=470).pack(anchor="w")
    bar = ttk.Progressbar(frame, mode="indeterminate")
    bar.pack(fill="x", pady=(12, 0))
    bar.start(12)

    result_queue = queue.Queue()
    console = console_python(python_exe)

    def worker() -> None:
        command = [
            console,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--disable-pip-version-check",
            *args,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                creationflags=CREATE_NO_WINDOW,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            if completed.returncode != 0 and "No module named pip" in output:
                subprocess.run(
                    [console, "-m", "ensurepip", "--upgrade"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                    creationflags=CREATE_NO_WINDOW,
                )
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=900,
                    creationflags=CREATE_NO_WINDOW,
                )
                output = (completed.stdout or "") + (completed.stderr or "")
            result_queue.put((completed.returncode == 0, output))
        except Exception as exc:
            result_queue.put((False, str(exc)))

    result = {"ok": False, "output": ""}

    def poll() -> None:
        try:
            ok, output = result_queue.get_nowait()
        except queue.Empty:
            root.after(150, poll)
            return
        result["ok"] = ok
        result["output"] = output
        bar.stop()
        progress.destroy()

    threading.Thread(target=worker, daemon=True).start()
    root.after(150, poll)
    root.wait_window(progress)
    return result["ok"], result["output"]


def choose_local_environment(root):
    import tkinter as tk
    from tkinter import filedialog, ttk

    dialog = tk.Toplevel(root)
    dialog.title("选择本地 Python 环境")
    dialog.geometry("520x180")
    dialog.transient(root)
    dialog.grab_set()
    result = {"path": None}

    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text="选择包含 python.exe 的环境文件夹，或直接选择 python.exe / pythonw.exe。",
        wraplength=470,
    ).pack(anchor="w", pady=(0, 12))

    def choose_file() -> None:
        path = filedialog.askopenfilename(
            parent=dialog,
            title="选择 Python 可执行文件",
            filetypes=[("Python 可执行文件", "*.exe"), ("所有文件", "*.*")],
        )
        if path:
            result["path"] = path
            dialog.destroy()

    def choose_folder() -> None:
        folder = filedialog.askdirectory(parent=dialog, title="选择 Python 环境文件夹")
        if not folder:
            return
        base = Path(folder)
        candidates = [
            base / "Scripts" / "python.exe",
            base / "python.exe",
            base / "pythonw.exe",
            base / "Scripts" / "pythonw.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                result["path"] = str(candidate)
                dialog.destroy()
                return
        from tkinter import messagebox

        messagebox.showerror(
            "环境无效",
            "所选文件夹中未找到 python.exe 或 pythonw.exe。",
            parent=dialog,
        )

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", side="bottom")
    ttk.Button(buttons, text="选择 python.exe", command=choose_file).pack(side="left")
    ttk.Button(buttons, text="选择环境文件夹", command=choose_folder).pack(
        side="left", padx=(8, 0)
    )
    ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    root.wait_window(dialog)
    return result["path"]


def choose_install_file(root):
    from tkinter import filedialog

    return filedialog.askopenfilename(
        parent=root,
        title="选择离线安装文件或 Python 环境",
        filetypes=[
            ("安装包 / 环境", "*.whl *.tar.gz *.zip *.txt *.exe"),
            ("所有文件", "*.*"),
        ],
    )


def relaunch_with(python_exe, save: bool = True):
    if save:
        config = load_launcher_config()
        config["python_path"] = str(python_exe)
        save_launcher_config(config)
    executable = pythonw_for(python_exe)
    try:
        subprocess.Popen(
            [executable, str(LAUNCHER), "--skip-check"],
            cwd=str(BASE),
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
    except Exception:
        log("重启启动器失败：\n" + traceback.format_exc())
        fallback_message(
            "DocFormatFix",
            "无法启动新的 GUI 进程，请查看 logs/launcher.log。",
            error=True,
        )
        raise SystemExit(1)
    raise SystemExit(0)


def relaunch_current():
    relaunch_with(sys.executable, save=False)


def use_local_environment(root, python_exe) -> None:
    from tkinter import messagebox

    scan = scan_for_python(python_exe)
    version = scan.get("version") or []
    if version and tuple(version[:2]) < (3, 9):
        show_message(
            root,
            "环境不兼容",
            f"所选 Python 版本为 {'.'.join(map(str, version))}，需要 3.9+。",
            error=True,
        )
        return
    if "tkinter" in scan.get("missing", []):
        show_message(
            root,
            "环境不完整",
            "所选 Python 缺少 tkinter，无法启动图形界面。请选择带 Tcl/Tk 的 Python。",
            error=True,
        )
        return

    if "docx" in scan.get("missing", []):
        if messagebox.askyesno(
            "缺少 python-docx",
            "所选环境缺少 python-docx，是否自动安装到该环境？",
            parent=root,
        ):
            ok, output = run_pip(
                root,
                python_exe,
                [PACKAGE_SPECS["docx"]],
                "正在向所选环境安装 python-docx…",
            )
            if not ok:
                show_message(root, "安装失败", output[-2500:], error=True)
                return
            scan = scan_for_python(python_exe)

    if "tkinterdnd2" in scan.get("missing", []):
        if messagebox.askyesno(
            "缺少 tkinterdnd2",
            "所选环境缺少 tkinterdnd2（OLE 拖放增强）。\n"
            "缺少时仍可使用原生拖放。是否现在安装？",
            parent=root,
        ):
            ok, output = run_pip(
                root,
                python_exe,
                [PACKAGE_SPECS["tkinterdnd2"]],
                "正在安装 tkinterdnd2…",
            )
            if not ok:
                show_message(root, "安装失败", output[-2500:], error=True)
            else:
                scan = scan_for_python(python_exe)

    if not scan.get("ok"):
        show_message(
            root,
            "环境仍不可用",
            "\n".join(scan.get("critical", []) or ["未知环境问题"]),
            error=True,
        )
        return

    root.destroy()
    relaunch_with(python_exe)


def install_offline_file(root, path: str) -> None:
    suffix = Path(path).suffix.lower()
    if suffix == ".exe":
        use_local_environment(root, path)
        return

    if suffix == ".txt":
        args = ["-r", path]
    else:
        args = [path]
    ok, output = run_pip(
        root,
        sys.executable,
        args,
        f"正在从本地文件安装：{Path(path).name}",
    )
    if not ok:
        show_message(root, "安装失败", output[-2500:], error=True)
        return
    root.destroy()
    relaunch_current()


def launch_gui() -> None:
    os.chdir(BASE)
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    try:
        import gui

        gui.main()
    except Exception:
        log("GUI 启动失败：\n" + traceback.format_exc())
        fallback_message(
            "DocFormatFix 启动失败",
            f"程序启动失败，详细信息见：\n{LOG_PATH}",
            error=True,
        )
        raise SystemExit(1)


def main() -> None:
    os.chdir(BASE)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log("launcher start")

    if "--check" in sys.argv:
        print(json.dumps(scan_current(), ensure_ascii=False, indent=2))
        return

    if "--skip-check" in sys.argv:
        launch_gui()
        return

    saved = load_launcher_config().get("python_path")
    if saved and Path(saved).exists() and not _same_path(saved, sys.executable):
        saved_scan = scan_for_python(saved)
        if saved_scan.get("ok"):
            log(f"use saved local environment: {saved}")
            relaunch_with(saved, save=False)
            return

    scan = scan_current()
    log(json.dumps(scan, ensure_ascii=False))
    if not scan["critical"] and not scan["warnings"]:
        if scan["optional"]:
            log(
                "可选依赖缺失，使用原生拖放回退："
                + "；".join(scan["optional"])
            )
        launch_gui()
        return

    if "tkinter" in scan.get("missing", []):
        fallback_message(
            "DocFormatFix 环境缺失",
            "当前 Python 缺少 tkinter，无法显示图形界面。\n"
            "请重新安装带 Tcl/Tk 的 Python 3.9+，或选择本地 Python 环境。",
            error=True,
        )
        return

    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    action = show_environment_dialog(root, scan)

    if action == "exit":
        root.destroy()
        return

    if action == "launch":
        root.destroy()
        relaunch_current()

    elif action == "auto":
        packages = []
        if "docx" in scan.get("missing", []):
            packages.append(PACKAGE_SPECS["docx"])
        if "tkinterdnd2" in scan.get("missing", []):
            packages.append(PACKAGE_SPECS["tkinterdnd2"])
        if not packages:
            show_message(
                root,
                "自动安装",
                "没有可自动安装的缺失依赖；请恢复项目文件或选择本地环境。",
                error=True,
            )
            root.destroy()
            return
        ok, output = run_pip(
            root,
            sys.executable,
            packages,
            "正在下载并安装缺失依赖…",
        )
        if not ok:
            show_message(root, "安装失败", output[-3000:], error=True)
            root.destroy()
            return
        new_scan = scan_current()
        if new_scan["critical"]:
            show_message(
                root,
                "安装后仍有问题",
                "\n".join(new_scan["critical"]),
                error=True,
            )
            root.destroy()
            return
        root.destroy()
        relaunch_current()

    elif action == "local":
        path = choose_local_environment(root)
        if path:
            use_local_environment(root, path)
            try:
                root.destroy()
            except Exception:
                pass
        else:
            root.destroy()

    elif action == "file":
        path = choose_install_file(root)
        if path:
            install_offline_file(root, path)
            try:
                root.destroy()
            except Exception:
                pass
        else:
            root.destroy()

    else:
        root.destroy()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log("launcher 未捕获异常：\n" + traceback.format_exc())
        fallback_message(
            "DocFormatFix 启动失败",
            f"启动器发生异常，详细信息见：\n{LOG_PATH}",
            error=True,
        )
        raise SystemExit(1)
