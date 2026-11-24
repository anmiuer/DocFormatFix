"""DocFormatFix 图形界面入口（Tkinter）。

GUI 只负责采集参数与展示结果，预扫描、识别、备份、格式化、报告全部复用 src 包。
"""

from __future__ import annotations

import copy
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except Exception:
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False

try:
    from src.win_drop import WinDropTarget

    WIN_DROP_AVAILABLE = True
except Exception:
    WinDropTarget = None
    WIN_DROP_AVAILABLE = False

from src import __version__  # noqa: E402
from src import markdown_handler  # noqa: E402
from src.file_handler import FileHandler  # noqa: E402
from src.format_engine import (  # noqa: E402
    file_signature,
    list_templates,
    load_template,
    templates_signature,
)
from src.pipeline import Pipeline  # noqa: E402
from src.structure_detector import (  # noqa: E402
    ALL_LABELS,
    LABEL_EMPTY,
    StructureDetector,
    preview_text,
    summarize_results,
)
from format_editor import FormatEditorDialog  # noqa: E402
from format_requirements_dialog import FormatRequirementsDialog  # noqa: E402
from template_manager import TemplateManagerDialog  # noqa: E402

TEMPLATE_NAMES = {
    "undergraduate_thesis": "本科毕业论文",
    "competition_report": "竞赛申报书",
    "common_homework": "课程作业",
}

CLEANUP_ITEMS = [
    ("merge_empty", "清理连续空段落"),
    ("revisions", "删除修订痕迹、隐藏文字"),
    ("spaces", "清除多余半角/全角空格"),
]

FORMAT_ORDER = [
    "一级标题",
    "二级标题",
    "三级标题",
    "正文",
    "参考文献",
    "表格",
    "代码块",
    "数学公式",
    "公式",
    "列表",
    "引用",
    "图注",
    "表注",
    "分隔线",
]

FORMAT_STYLES = {
    "一级标题": {"key": "h1", "bg": "#fde8e8", "fg": "#b42318"},
    "二级标题": {"key": "h2", "bg": "#fdf1dd", "fg": "#b54708"},
    "三级标题": {"key": "h3", "bg": "#fbf7d9", "fg": "#8a6100"},
    "正文": {"key": "body", "bg": "#f2f7f3", "fg": "#1e5e2d"},
    "参考文献": {"key": "ref", "bg": "#e6f7f5", "fg": "#0f766e"},
    "表格": {"key": "table", "bg": "#e3ecf7", "fg": "#24538a"},
    "代码块": {"key": "code", "bg": "#f1f3f5", "fg": "#343a40"},
    "数学公式": {"key": "math", "bg": "#f3e8ff", "fg": "#6b21a8"},
    "公式": {"key": "formula", "bg": "#eef2ff", "fg": "#4338ca"},
    "列表": {"key": "list", "bg": "#eefbf6", "fg": "#0f766e"},
    "引用": {"key": "quote", "bg": "#fdf6e3", "fg": "#8a6100"},
    "图注": {"key": "fig", "bg": "#e8f0fe", "fg": "#1d4ed8"},
    "表注": {"key": "tab", "bg": "#f1e9fd", "fg": "#6d28d9"},
    "分隔线": {"key": "hr", "bg": "#f8f9fa", "fg": "#6b7280"},
    "空段落": {"key": "empty", "bg": "#ffffff", "fg": "#9aa0a6"},
    "保护": {"key": "protect", "bg": "#eef0f3", "fg": "#5f6368"},
}

PROTECT_ROW = "__protected__"
TABLE_ROW = "__table__"
MAX_MERGE_EMPTY_GAP = 2

DEFAULT_SETTINGS = {
    "confidence_threshold": 70,
    "default_template": "competition_report",
    "html_report": False,
    "backup_dir": "backup",
    "output_dir": "output",
    "cleanup": {"merge_empty": False, "revisions": False, "spaces": False},
}


class DocFormatFixApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"DocFormatFix {__version__} - 文档格式自动校准")
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(1360, max(1000, screen_width - 100))
        height = min(900, max(700, screen_height - 120))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(1120, width), min(760, height))
        self.root.rowconfigure(2, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.ui_queue = queue.Queue()
        self._startup_warnings = []
        self.settings = self._load_settings()
        self.backup_dir_setting = self.settings.get("backup_dir") or "backup"
        self.output_dir_setting = self.settings.get("output_dir") or "output"
        try:
            self.handler = FileHandler(
                ROOT,
                backup_dir=self.backup_dir_setting,
                output_dir=self.output_dir_setting,
            )
        except Exception as exc:
            self._startup_warnings.append(
                f"备份/输出目录不可用，已回退到默认目录：{exc}"
            )
            self.backup_dir_setting = "backup"
            self.output_dir_setting = "output"
            self.handler = FileHandler(ROOT)

        self.busy = False
        self.files = []
        self.skipped = []
        self.base_dir = None
        self.previews = {}
        self.overrides = {}
        self.file_status = {}
        self.skipped_rows = {}
        self.current_file = None
        self.current_results = []
        self.current_preview = None
        self.text_ranges = {}
        self.format_rows = {}
        self.protected = {}
        self.groups = {}
        self.forced_breaks = {}
        self.line_map = {}
        self.group_label_ranges = []
        self.paragraph_spans = {}
        self._mouse_press_xy = None
        self._mouse_press_state = 0
        self._selection_anchor_index = None
        self._para_selection_snapshot = set()
        self._active_paragraph = None
        self._focus_mode = False
        self._suppress_selection_status = False
        self._selection_status_job = None
        self._drop_targets = None
        self._last_drop_time = 0.0
        self._last_drop_signature = ()
        self._win_drop = None
        self._dnd_backend = None
        self._pending_scan_job = None
        self.dnd_hint_var = tk.StringVar(value="拖拽初始化中…")
        self._format_dialog = None
        self._requirements_dialog = None
        self._template_manager = None
        self.menu_row = None
        self._adv_visible = False

        self.templates = list_templates(ROOT)
        self.template_displays = [self._display_template(name) for name in self.templates]
        self._templates_signature = templates_signature(ROOT)
        self._settings_path = ROOT / "config" / "settings.json"
        self._settings_signature = file_signature(self._settings_path)

        threshold = self._initial_threshold()
        cleanup_settings = self.settings.get("cleanup", {})
        default_stem = self.settings.get("default_template", "")
        if default_stem not in self.templates:
            default_stem = self.templates[0] if self.templates else ""
        self.template_data = {}
        self._active_template_stem = default_stem
        if default_stem:
            try:
                self.template_data = load_template(ROOT, default_stem)
            except Exception as exc:
                self._startup_warnings.append(f"读取模板 {default_stem} 失败：{exc}")

        self.mode_var = tk.StringVar(value="file")
        self.path_var = tk.StringVar()
        self.template_var = tk.StringVar(value=self._display_template(default_stem))
        self.backup_var = tk.BooleanVar(value=True)
        self.html_var = tk.BooleanVar(value=bool(self.settings.get("html_report", False)))
        self.infer_var = tk.BooleanVar(value=True)
        self.threshold_var = tk.DoubleVar(value=threshold)
        self.cleanup_vars = {
            key: tk.BooleanVar(value=bool(cleanup_settings.get(key, False)))
            for key, _ in CLEANUP_ITEMS
        }
        self.summary_var = tk.StringVar(value="尚未预扫描")
        self.template_state_var = tk.StringVar(value="")

        self._build_style()
        self._build_ui()
        self._bind_dnd()
        self._poll_queue()
        self.root.after(1200, self._poll_external_changes)

        self.log(f"DocFormatFix {__version__} 已启动。")
        self.log(f"Python：{sys.executable}")
        self.log("仅修改排版格式，不改动正文文字；每次任务强制自动备份。")
        self.log(f"输出目录：{self.handler.output_root}")
        self.log(f"备份目录：{self.handler.backup_root}")
        for warning in self._startup_warnings:
            self.log(warning)
        if not self.templates:
            self.log("错误：templates 目录没有可用模板，无法执行格式化。")
        elif not self.template_data:
            self.log("错误：当前模板数据为空，请在【格式设置】中检查模板文件。")

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def _load_settings(self) -> dict:
        settings = {
            "confidence_threshold": DEFAULT_SETTINGS["confidence_threshold"],
            "default_template": DEFAULT_SETTINGS["default_template"],
            "html_report": DEFAULT_SETTINGS["html_report"],
            "backup_dir": DEFAULT_SETTINGS["backup_dir"],
            "output_dir": DEFAULT_SETTINGS["output_dir"],
            "cleanup": dict(DEFAULT_SETTINGS["cleanup"]),
        }
        path = ROOT / "config" / "settings.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key in (
                        "confidence_threshold",
                        "default_template",
                        "html_report",
                        "backup_dir",
                        "output_dir",
                    ):
                        if key in data:
                            settings[key] = data[key]
                    if isinstance(data.get("cleanup"), dict):
                        settings["cleanup"].update(data["cleanup"])
            except (json.JSONDecodeError, OSError) as exc:
                self._startup_warnings.append(f"读取 config/settings.json 失败，使用默认设置：{exc}")
        return settings

    def _initial_threshold(self) -> int:
        try:
            value = int(self.settings.get("confidence_threshold", 70))
        except (TypeError, ValueError):
            value = 70
        return max(60, min(90, value))

    @staticmethod
    def _display_template(stem: str) -> str:
        if not stem:
            return ""
        return f"{stem}（{TEMPLATE_NAMES.get(stem, '自定义模板')}）"

    def _template_stem(self) -> str:
        return self.template_var.get().split("（")[0].strip()

    def _on_template_change(self, event=None) -> None:
        stem = self._template_stem()
        if not stem:
            return
        if self._has_custom_edits():
            if not messagebox.askyesno(
                "切换模板",
                "当前模板有未另存的自定义格式，切换后会丢失，是否继续？",
            ):
                self.template_var.set(self._display_template(self._active_template_stem))
                return
        if not self._load_template(stem):
            messagebox.showerror("模板加载失败", f"无法加载模板：{stem}.json")
            self.template_var.set(self._display_template(self._active_template_stem))
            return
        self.template_state_var.set("")
        self.log(f"已加载模板：{stem}")

    @staticmethod
    def _store_dir_path(path) -> str:
        resolved = Path(path).expanduser().resolve()
        try:
            return resolved.relative_to(ROOT).as_posix()
        except ValueError:
            return str(resolved)

    @staticmethod
    def _browse_dir(parent, variable) -> None:
        selected = filedialog.askdirectory(
            parent=parent,
            initialdir=variable.get() or str(ROOT),
        )
        if selected:
            variable.set(selected)

    def open_directory_settings(self) -> None:
        if self.busy:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("备份与输出目录")
        dialog.geometry("760x270")
        dialog.minsize(680, 250)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(1, weight=1)

        backup_var = tk.StringVar(value=str(self.handler.backup_root))
        output_var = tk.StringVar(value=str(self.handler.output_root))

        ttk.Label(dialog, text="文档备份目录：").grid(
            row=0, column=0, sticky="w", padx=(14, 6), pady=(16, 6)
        )
        backup_entry = ttk.Entry(dialog, textvariable=backup_var)
        backup_entry.grid(row=0, column=1, sticky="ew", pady=(16, 6))
        ttk.Button(
            dialog,
            text="选择…",
            command=lambda: self._browse_dir(dialog, backup_var),
        ).grid(row=0, column=2, padx=(6, 14), pady=(16, 6))

        ttk.Label(dialog, text="格式化输出目录：").grid(
            row=1, column=0, sticky="w", padx=(14, 6), pady=6
        )
        ttk.Entry(dialog, textvariable=output_var).grid(
            row=1, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            dialog,
            text="选择…",
            command=lambda: self._browse_dir(dialog, output_var),
        ).grid(row=1, column=2, padx=(6, 14), pady=6)

        ttk.Label(
            dialog,
            text="支持相对路径（基于项目目录）或绝对路径；保存后写入 config/settings.json，下次启动继续生效。",
            style="Hint.TLabel",
            wraplength=680,
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=14, pady=(4, 8))

        def reset_defaults() -> None:
            backup_var.set(str((ROOT / "backup").resolve()))
            output_var.set(str((ROOT / "output").resolve()))

        def save() -> None:
            backup_text = backup_var.get().strip()
            output_text = output_var.get().strip()
            if not backup_text or not output_text:
                messagebox.showerror(
                    "目录设置", "备份目录和输出目录不能为空。", parent=dialog
                )
                return
            backup_path = Path(backup_text).expanduser()
            output_path = Path(output_text).expanduser()
            if not backup_path.is_absolute():
                backup_path = ROOT / backup_path
            if not output_path.is_absolute():
                output_path = ROOT / output_path

            try:
                new_handler = FileHandler(
                    ROOT, str(backup_path), str(output_path)
                )
            except Exception as exc:
                messagebox.showerror("目录设置", str(exc), parent=dialog)
                return

            stored_backup = self._store_dir_path(backup_path)
            stored_output = self._store_dir_path(output_path)
            config_path = ROOT / "config" / "settings.json"
            data = {}
            if config_path.exists():
                try:
                    loaded = json.loads(config_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
                except (json.JSONDecodeError, OSError):
                    data = {}
            data["backup_dir"] = stored_backup
            data["output_dir"] = stored_output
            try:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                messagebox.showerror("保存失败", str(exc), parent=dialog)
                return

            self.backup_dir_setting = stored_backup
            self.output_dir_setting = stored_output
            self.settings["backup_dir"] = stored_backup
            self.settings["output_dir"] = stored_output
            self._settings_signature = file_signature(self._settings_path)
            self.handler = new_handler
            self.log(f"备份目录已更新：{new_handler.backup_root}")
            self.log(f"输出目录已更新：{new_handler.output_root}")
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=3, column=0, columnspan=3, sticky="e", padx=14, pady=(8, 12))
        ttk.Button(buttons, text="恢复默认", command=reset_defaults).pack(side="left")
        ttk.Button(
            buttons, text="保存", command=save, style="Accent.TButton"
        ).pack(side="right")
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(
            side="right", padx=(0, 8)
        )
        backup_entry.focus_set()

    def open_format_editor(self) -> None:
        if self.busy:
            return
        if not self.template_data:
            messagebox.showerror("格式设置", "当前模板未加载，无法编辑格式。")
            return
        if (
            self.current_preview is not None
            and getattr(self.current_preview, "source_type", "docx") == "markdown"
        ):
            messagebox.showinfo(
                "格式设置",
                "Markdown 文档使用结构规范化，字体/段落模板不适用。\n"
                "如需保留某个块的原始内容，可在原文中选中后使用【保护所选】。",
            )
            return
        counts = {}
        if self.current_preview is not None:
            allowed = {
                "一级标题",
                "二级标题",
                "三级标题",
                "正文",
                "参考文献",
                "表格",
                "图注",
                "表注",
            }
            counts = {
                label: count
                for label, count in self.current_preview.stats.get("labels", {}).items()
                if label in allowed
            }
            table_count = getattr(self.current_preview.scan, "table_count", 0)
            if table_count:
                counts["表格"] = table_count
        self._format_dialog = FormatEditorDialog(
            parent=self.root,
            template_name=self._template_stem(),
            template_data=self.template_data,
            project_root=ROOT,
            counts=counts,
            on_apply=self._on_format_applied,
            on_saved=self._on_template_saved,
            logger=self.log,
        )

    def _on_format_applied(self) -> None:
        self.template_state_var.set("已自定义（本次任务生效）")
        self.log("格式设置已更新，将在执行格式化时生效。")

    def _on_template_saved(self, name: str, data: dict) -> None:
        self._refresh_template_choices()
        self.template_var.set(self._display_template(name))
        self.template_data = data
        self._active_template_stem = name
        self.template_state_var.set(f"已保存并切换：{name}")
        self.log(f"已保存并切换为自定义模板：{name}")
        if self._dialog_open(self._template_manager):
            self._template_manager.active_stem = name
            self._template_manager.refresh(select=name)

    # ------------------------------------------------------------------
    # 模板工具与外部变化同步
    # ------------------------------------------------------------------

    def _has_custom_edits(self) -> bool:
        return self.template_state_var.get().startswith("已自定义")

    @staticmethod
    def _dialog_open(dialog) -> bool:
        if dialog is None:
            return False
        try:
            return bool(dialog.top.winfo_exists())
        except Exception:
            return False

    def _load_template(self, stem: str) -> bool:
        try:
            data = load_template(ROOT, stem)
        except Exception as exc:
            self.log(f"加载模板失败：{stem}：{exc}")
            return False
        self.template_data = data
        self._active_template_stem = stem
        self.template_var.set(self._display_template(stem))
        return True

    def _refresh_template_choices(self) -> None:
        self.templates = list_templates(ROOT)
        self.template_displays = [
            self._display_template(stem) for stem in self.templates
        ]
        self.template_combo.configure(values=self.template_displays)
        self._templates_signature = templates_signature(ROOT)

    def open_requirements_dialog(self) -> None:
        if self.busy:
            return
        if not self.template_data:
            messagebox.showerror(
                "识别格式要求", "当前模板未加载，无法应用识别结果。"
            )
            return
        if self._dialog_open(self._requirements_dialog):
            self._requirements_dialog.top.lift()
            self._requirements_dialog.top.focus_set()
            return
        self._requirements_dialog = FormatRequirementsDialog(
            parent=self.root,
            base_template=copy.deepcopy(self.template_data),
            project_root=ROOT,
            on_apply=self._on_requirements_applied,
            on_saved=self._on_template_saved,
            logger=self.log,
        )

    def _on_requirements_applied(self, template: dict) -> None:
        self.template_data = template
        self.template_state_var.set("已自定义（本次任务生效）")
        self.log("已应用智能识别的格式要求，将在执行格式化时生效。")

    def open_template_manager(self) -> None:
        if self.busy:
            return
        if self._dialog_open(self._template_manager):
            self._template_manager.top.lift()
            self._template_manager.top.focus_set()
            return
        self._template_manager = TemplateManagerDialog(
            parent=self.root,
            project_root=ROOT,
            active_stem=self._active_template_stem,
            on_activate=self._activate_template_from_manager,
            on_changed=self._on_templates_changed,
            logger=self.log,
        )

    def _activate_template_from_manager(self, stem: str) -> bool:
        parent = (
            self._template_manager.top
            if self._dialog_open(self._template_manager)
            else self.root
        )
        if self._has_custom_edits():
            if not messagebox.askyesno(
                "切换模板",
                "当前模板有未另存的自定义格式，切换后会丢失，是否继续？",
                parent=parent,
            ):
                return False
        if not self._load_template(stem):
            messagebox.showerror(
                "模板加载失败", f"无法加载模板：{stem}.json", parent=parent
            )
            return False
        self.template_state_var.set("")
        self.log(f"已加载模板：{stem}")
        return True

    def _on_templates_changed(self, select=None):
        return self._sync_templates_from_disk(select=select)

    def _sync_templates_from_disk(self, select=None) -> str:
        previous = self._active_template_stem
        custom = self._has_custom_edits()
        old_map = {
            Path(name).stem: (mtime, size)
            for name, mtime, size in self._templates_signature
        }

        self._refresh_template_choices()
        available = set(self.templates)
        new_map = {
            Path(name).stem: (mtime, size)
            for name, mtime, size in self._templates_signature
        }
        modified = (
            previous in old_map
            and previous in new_map
            and old_map[previous] != new_map[previous]
        )

        if previous in available:
            self._active_template_stem = previous
            self.template_var.set(self._display_template(previous))
            if not custom:
                if modified:
                    self.log("当前模板文件已被外部修改，正在重新加载。")
                try:
                    self.template_data = load_template(ROOT, previous)
                    self.template_state_var.set("")
                except Exception as exc:
                    self.log(f"模板 {previous} 读取失败：{exc}")
                    self.template_state_var.set("模板 JSON 读取失败")
            elif modified:
                self.template_state_var.set("已自定义·外部已修改")
                self.log("当前模板文件已被外部修改，已保留本次会话的自定义格式。")
        elif select in available:
            if custom and self.template_data:
                self._active_template_stem = select
                self.template_var.set(self._display_template(select))
                self.template_state_var.set("已自定义·模板已删除")
            elif self._load_template(select):
                self.template_state_var.set("")
                self.log(f"模板列表已变化，已切换为：{select}")
        elif custom and self.template_data:
            self.template_var.set(self._display_template(previous))
            self.template_state_var.set("模板已删除·使用内存格式")
            self.log("当前模板文件已被外部删除或重命名，保留当前会话中的格式。")
        elif self.templates:
            target = self.templates[0]
            if self._load_template(target):
                self.template_state_var.set("原模板缺失·已自动切换")
                self.log(f"当前模板已不存在，已自动切换为：{target}")
        else:
            self.template_data = {}
            self._active_template_stem = ""
            self.template_var.set("")
            self.template_state_var.set("")
            self.log("templates 目录已为空，无法执行格式化。")

        if (
            (
                self._active_template_stem not in available
                or self.template_state_var.get() == "模板 JSON 读取失败"
            )
            and not (custom and self.template_data)
            and self.templates
        ):
            for candidate in self.templates:
                if self._load_template(candidate):
                    self.template_state_var.set("原模板缺失·已自动切换")
                    self.log(f"已自动切换为可用模板：{candidate}")
                    break
            else:
                self.log("templates 目录中没有可读取的模板，无法执行格式化。")

        if self._dialog_open(self._template_manager):
            manager = self._template_manager
            if manager.active_stem != self._active_template_stem:
                manager.active_stem = self._active_template_stem
                manager.refresh()
        return self._active_template_stem

    def _poll_external_changes(self) -> None:
        try:
            if not self.busy:
                if templates_signature(ROOT) != self._templates_signature:
                    if not self._dialog_open(
                        self._format_dialog
                    ) and not self._dialog_open(self._requirements_dialog):
                        self.log("检测到模板目录变化，正在同步模板列表…")
                        self._sync_templates_from_disk()
                self._sync_settings_from_disk()
        except Exception as exc:
            self.log(f"外部文件变化同步失败：{exc}")
        finally:
            self.root.after(1500, self._poll_external_changes)

    def _sync_settings_from_disk(self) -> None:
        signature = file_signature(self._settings_path)
        if signature == self._settings_signature:
            return
        self._settings_signature = signature
        if not self._settings_path.exists():
            self.log("config/settings.json 已被删除，保留当前设置。")
            return
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.log(f"config/settings.json 读取失败，保留当前设置：{exc}")
            return
        if not isinstance(data, dict):
            self.log("config/settings.json 内容不是对象，保留当前设置。")
            return
        self._apply_external_settings(data)

    def _apply_external_settings(self, data: dict) -> None:
        changed = []

        if "confidence_threshold" in data:
            try:
                threshold = max(60, min(90, int(data["confidence_threshold"])))
            except (TypeError, ValueError):
                threshold = None
            if threshold is not None:
                self.settings["confidence_threshold"] = threshold
                if threshold != self._threshold_value():
                    self.threshold_var.set(threshold)
                    self._on_threshold_move(threshold)
                    changed.append("置信阈值")

        if "html_report" in data:
            value = bool(data["html_report"])
            self.settings["html_report"] = value
            if value != self.html_var.get():
                self.html_var.set(value)
                changed.append("HTML 报告")

        cleanup = data.get("cleanup")
        if isinstance(cleanup, dict):
            for key, var in self.cleanup_vars.items():
                if key in cleanup:
                    value = bool(cleanup[key])
                    self.settings.setdefault("cleanup", {})[key] = value
                    if value != var.get():
                        var.set(value)
                        changed.append(f"清理：{key}")

        backup = data.get("backup_dir", self.backup_dir_setting)
        output = data.get("output_dir", self.output_dir_setting)
        if isinstance(backup, str) and isinstance(output, str) and backup and output:
            if backup != self.backup_dir_setting or output != self.output_dir_setting:
                try:
                    new_handler = FileHandler(
                        ROOT, backup_dir=backup, output_dir=output
                    )
                except Exception as exc:
                    self.log(f"配置中的备份/输出目录不可用，保留原目录：{exc}")
                else:
                    self.handler = new_handler
                    self.backup_dir_setting = backup
                    self.output_dir_setting = output
                    self.settings["backup_dir"] = backup
                    self.settings["output_dir"] = output
                    changed.append("备份/输出目录")
                    self.log(f"备份目录已更新：{new_handler.backup_root}")
                    self.log(f"输出目录已更新：{new_handler.output_root}")

        default = data.get("default_template")
        if isinstance(default, str) and default:
            old_default = self.settings.get("default_template")
            self.settings["default_template"] = default
            if default != old_default:
                if (
                    not self._has_custom_edits()
                    and not self._dialog_open(self._requirements_dialog)
                    and not self._dialog_open(self._format_dialog)
                    and self._active_template_stem == old_default
                    and default in self.templates
                    and self._load_template(default)
                ):
                    self.template_state_var.set("")
                    changed.append("默认模板")
                    self.log(f"默认模板已切换为：{default}")
                else:
                    self.log(
                        f"config 中的默认模板已改为 {default}"
                        "（下次启动或手动选择时生效）。"
                    )

        if changed:
            self.log("检测到 config/settings.json 变化，已重新加载：" + "、".join(changed))
        else:
            self.log("检测到 config/settings.json 变化，已重新读取。")

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            if sys.platform.startswith("win") and "vista" in style.theme_names():
                style.theme_use("vista")
        except tk.TclError:
            pass
        try:
            import tkinter.font as tkfont

            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=9)
        except Exception:
            pass
        style.configure("Treeview", rowheight=25)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Hint.TLabel", foreground="#646a73")

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_file_area()
        self._build_config_area()
        self._build_paned_area()
        self._build_bottom_bar()

    def _build_file_area(self) -> None:
        frame = ttk.LabelFrame(self.root, text="1. 文件选择")
        frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        frame.columnconfigure(2, weight=1)

        ttk.Radiobutton(
            frame, text="单个文件", variable=self.mode_var, value="file"
        ).grid(row=0, column=0, sticky="w", padx=(8, 2), pady=6)
        ttk.Radiobutton(
            frame, text="整个文件夹批量处理", variable=self.mode_var, value="dir"
        ).grid(row=0, column=1, sticky="w", padx=(2, 10), pady=6)

        self.path_entry = ttk.Entry(frame, textvariable=self.path_var)
        self.path_entry.grid(row=0, column=2, sticky="ew", padx=4, pady=6)
        self.path_entry.bind("<Return>", lambda event: self.refresh_files())
        self.path_entry.bind("<Key>", self._on_path_key, add="+")

        self.browse_btn = ttk.Button(frame, text="选择文件/文件夹", command=self.choose_path)
        self.browse_btn.grid(row=0, column=3, padx=4, pady=6)
        self.refresh_btn = ttk.Button(frame, text="重新扫描", command=self.refresh_files)
        self.refresh_btn.grid(row=0, column=4, padx=(4, 8), pady=6)

        tree_box = ttk.Frame(frame)
        tree_box.grid(row=1, column=0, columnspan=5, sticky="ew", padx=8, pady=(0, 4))
        tree_box.columnconfigure(0, weight=1)

        self.file_tree = ttk.Treeview(
            tree_box,
            columns=("name", "status"),
            show="headings",
            height=3,
            selectmode="browse",
        )
        self.file_tree.heading("name", text="待处理文档")
        self.file_tree.heading("status", text="状态")
        self.file_tree.column("name", width=760, anchor="w")
        self.file_tree.column("status", width=260, anchor="w")
        self.file_tree.tag_configure("skipped", foreground="#9aa0a6")
        self.file_tree.grid(row=0, column=0, sticky="ew")
        file_scroll = ttk.Scrollbar(tree_box, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=file_scroll.set)
        file_scroll.grid(row=0, column=1, sticky="ns")
        self.file_tree.bind("<<TreeviewSelect>>", self._on_file_select)

        hint = self.dnd_hint_var
        ttk.Label(frame, textvariable=hint, style="Hint.TLabel").grid(
            row=2, column=0, columnspan=5, sticky="w", padx=10, pady=(0, 6)
        )

    def _build_config_area(self) -> None:
        frame = ttk.LabelFrame(self.root, text="2. 模板与参数")
        frame.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        frame.columnconfigure(4, weight=1)

        ttk.Label(frame, text="排版模板：").grid(row=0, column=0, sticky="w", padx=(8, 0), pady=6)
        self.template_combo = ttk.Combobox(
            frame,
            textvariable=self.template_var,
            values=self.template_displays,
            state="readonly",
            width=34,
        )
        self.template_combo.grid(row=0, column=1, sticky="w", padx=4, pady=6)
        self.template_combo.bind("<<ComboboxSelected>>", self._on_template_change)

        ttk.Checkbutton(
            frame, text="生成 HTML 可视化报告", variable=self.html_var
        ).grid(row=0, column=2, sticky="w", padx=(14, 0), pady=6)
        ttk.Checkbutton(
            frame,
            text="启用自动备份（强制）",
            variable=self.backup_var,
            state="disabled",
        ).grid(row=0, column=3, sticky="w", padx=(14, 0), pady=6)

        ttk.Label(
            frame, textvariable=self.template_state_var, style="Hint.TLabel"
        ).grid(row=0, column=4, sticky="w", padx=(12, 0), pady=6)

        self.format_btn = ttk.Button(
            frame, text="格式设置…", command=self.open_format_editor
        )
        self.format_btn.grid(row=0, column=5, sticky="e", padx=(8, 0), pady=6)
        self.adv_btn = ttk.Button(frame, text="高级选项 ▾", command=self._toggle_advanced)
        self.adv_btn.grid(row=0, column=6, sticky="e", padx=8, pady=6)

        tools = ttk.Frame(frame)
        tools.grid(row=1, column=0, columnspan=7, sticky="ew", padx=10, pady=(0, 6))
        ttk.Label(tools, text="模板工具：").pack(side="left")
        self.req_btn = ttk.Button(
            tools, text="识别格式要求…", command=self.open_requirements_dialog
        )
        self.req_btn.pack(side="left")
        self.manager_btn = ttk.Button(
            tools, text="管理模板…", command=self.open_template_manager
        )
        self.manager_btn.pack(side="left", padx=(6, 0))
        self.templates_btn = ttk.Button(
            tools,
            text="打开模板目录",
            command=lambda: self._open_folder(ROOT / "templates"),
        )
        self.templates_btn.pack(side="left", padx=(6, 0))
        ttk.Label(
            tools,
            text="粘贴格式要求自动识别；模板 JSON 被外部修改或删除时自动同步",
            style="Hint.TLabel",
        ).pack(side="left", padx=(14, 0))

        self.adv_frame = ttk.Frame(frame)
        self.adv_frame.grid(row=2, column=0, columnspan=7, sticky="ew", padx=10, pady=(0, 8))
        self.adv_frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            self.adv_frame,
            text="自动推断标题层级（无样式文档）",
            variable=self.infer_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Label(self.adv_frame, text="置信阈值：").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        slider_box = ttk.Frame(self.adv_frame)
        slider_box.grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.threshold_scale = ttk.Scale(
            slider_box,
            from_=60,
            to=90,
            variable=self.threshold_var,
            command=self._on_threshold_move,
            length=260,
        )
        self.threshold_scale.pack(side="left")
        self.threshold_label = ttk.Label(
            slider_box,
            text=f"{self._initial_threshold()}（低于该分数的段落不自动修改）",
            style="Hint.TLabel",
        )
        self.threshold_label.pack(side="left", padx=(10, 0))

        ttk.Label(self.adv_frame, text="附加清理：").grid(
            row=2, column=0, sticky="nw", pady=(8, 0)
        )
        cleanup_box = ttk.Frame(self.adv_frame)
        cleanup_box.grid(row=2, column=1, sticky="w", pady=(8, 0))
        for position, (key, label) in enumerate(CLEANUP_ITEMS):
            ttk.Checkbutton(
                cleanup_box,
                text=label,
                variable=self.cleanup_vars[key],
            ).grid(row=0, column=position, sticky="w", padx=(0, 14))

        ttk.Label(
            self.adv_frame,
            text="取消推断后，仅识别 Word 原生标题样式；中/低置信段落只标记不修改。",
            style="Hint.TLabel",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 2))

        ttk.Label(
            self.adv_frame,
            text="格式设置… 可自定义字体、字号、缩进、行距、段前段后与对齐，仅影响输出文件。",
            style="Hint.TLabel",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 2))

        self.adv_frame.grid_remove()

    def _build_paned_area(self) -> None:
        main_area = ttk.Frame(self.root)
        main_area.grid(row=2, column=0, sticky="nsew", padx=12, pady=4)
        main_area.rowconfigure(1, weight=1)
        main_area.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(main_area)
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        ttk.Label(toolbar, textvariable=self.summary_var).pack(side="left")
        ttk.Label(
            toolbar,
            text="右键段落可改判标签；单击左侧格式可高亮原文",
            style="Hint.TLabel",
        ).pack(side="right")

        self.main_notebook = ttk.Notebook(main_area)
        self.main_notebook.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self._build_format_tab()
        self._build_detail_tab()
        self._build_log_tab()

    def _build_format_tab(self) -> None:
        tab = ttk.Frame(self.main_notebook)
        self.main_notebook.add(tab, text="格式识别与原文")
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)

        self.format_paned = ttk.Panedwindow(tab, orient="horizontal")
        self.format_paned.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        left = ttk.Frame(self.format_paned)
        right = ttk.Frame(self.format_paned)
        self.format_left = left
        self.format_paned.add(left, weight=1)
        self.format_paned.add(right, weight=4)

        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        left_header = ttk.Frame(left)
        left_header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 2))
        self.format_count_var = tk.StringVar(value="识别到的格式")
        ttk.Label(
            left_header,
            textvariable=self.format_count_var,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left")

        tree_box = ttk.Frame(left)
        tree_box.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=6)
        tree_box.rowconfigure(0, weight=1)
        tree_box.columnconfigure(0, weight=1)

        self.format_tree = ttk.Treeview(
            tree_box,
            columns=("label", "count", "sample"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        self.format_tree.heading("label", text="格式")
        self.format_tree.heading("count", text="数量 / 置信度")
        self.format_tree.heading("sample", text="原文示例")
        self.format_tree.column("label", width=80, anchor="w")
        self.format_tree.column("count", width=130, anchor="w")
        self.format_tree.column("sample", width=170, anchor="w")
        for style in FORMAT_STYLES.values():
            self.format_tree.tag_configure(style["key"], foreground=style["fg"])
        self.format_tree.grid(row=0, column=0, sticky="nsew")
        format_scroll = ttk.Scrollbar(
            tree_box, orient="vertical", command=self.format_tree.yview
        )
        self.format_tree.configure(yscrollcommand=format_scroll.set)
        format_scroll.grid(row=0, column=1, sticky="ns")
        self.format_tree.bind("<<TreeviewSelect>>", self._on_format_select)

        ttk.Label(
            left,
            text="单击某一格式，右侧原文中同类段落将整体高亮。",
            style="Hint.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 6))

        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        right_header = ttk.Frame(right)
        right_header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 2))
        self.original_status_var = tk.StringVar(value="文档原文尚未加载")
        ttk.Label(right_header, textvariable=self.original_status_var).pack(side="left")
        ttk.Button(
            right_header,
            text="清除高亮",
            command=lambda: self.clear_highlight(clear_selection=True),
        ).pack(side="right")
        self.focus_btn = ttk.Button(
            right_header, text="专注原文", command=self._toggle_focus_mode
        )
        self.focus_btn.pack(side="right", padx=(0, 6))

        action_bar = ttk.Frame(right)
        action_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 2))
        ttk.Button(
            action_bar, text="合并所选", command=self._merge_selected
        ).pack(side="left")
        ttk.Button(
            action_bar, text="拆解所选", command=self._split_selected
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            action_bar, text="保护所选", command=self._protect_selected
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            action_bar, text="取消保护", command=self._unprotect_selected
        ).pack(side="left", padx=(6, 0))

        self.relabel_menu = tk.Menu(self.root, tearoff=0)
        for label in ALL_LABELS:
            self.relabel_menu.add_command(
                label=label,
                command=lambda value=label: self._apply_label_to_selection(value),
            )
        self.relabel_menu.add_separator()
        self.relabel_menu.add_command(
            label="恢复自动识别（清除保护/分组）",
            command=self._restore_selected_auto,
        )
        ttk.Menubutton(
            action_bar, text="改判为 ▾", menu=self.relabel_menu
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            action_bar,
            text="单击选段，Ctrl/Shift 多选；拆解可分离合并块",
            style="Hint.TLabel",
        ).pack(side="left", padx=(12, 0))

        text_box = ttk.Frame(right)
        text_box.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=6)
        text_box.rowconfigure(0, weight=1)
        text_box.columnconfigure(0, weight=1)

        self.original_text = tk.Text(
            text_box,
            wrap="word",
            relief="flat",
            background="#fcfcfd",
            font=("Microsoft YaHei UI", 11),
            padx=12,
            pady=10,
            spacing1=2,
            spacing3=2,
            selectbackground="#7fb8f5",
            selectforeground="#101828",
            exportselection=False,
            insertwidth=0,
        )
        try:
            self.original_text.configure(inactiveselectbackground="#7fb8f5")
        except tk.TclError:
            pass
        self.original_text.grid(row=0, column=0, sticky="nsew")
        original_scroll = ttk.Scrollbar(
            text_box, orient="vertical", command=self.original_text.yview
        )
        self.original_text.configure(yscrollcommand=original_scroll.set)
        original_scroll.grid(row=0, column=1, sticky="ns")
        self._configure_original_tags()

        self.original_text.bind("<<Selection>>", self._on_original_selection)
        self.original_text.bind("<Key>", self._on_readonly_key)
        self.original_text.bind("<Button-1>", self._on_original_click, add="+")
        self.original_text.bind("<ButtonRelease-1>", self._on_original_release, add="+")
        self.original_text.bind("<Button-3>", self._on_original_right_click)
        for sequence in ("<<Paste>>", "<<Cut>>", "<<PasteSelection>>", "<Button-2>"):
            self.original_text.bind(sequence, self._block_edit)

        self.original_menu = tk.Menu(self.root, tearoff=0)
        self.original_menu.add_command(
            label="合并所选段落", command=self._merge_selected
        )
        self.original_menu.add_command(
            label="拆解所选（恢复为独立块）", command=self._split_selected
        )
        self.original_menu.add_command(
            label="保护所选段落（不格式化）", command=self._protect_selected
        )
        self.original_menu.add_command(
            label="取消所选段落保护", command=self._unprotect_selected
        )
        self.original_menu.add_separator()
        self.original_menu.add_command(
            label="恢复自动合并（全部）", command=self._restore_auto_merge
        )
        self.original_menu.add_separator()
        self.original_label_menu = tk.Menu(self.original_menu, tearoff=0)
        for label in ALL_LABELS:
            self.original_label_menu.add_command(
                label=label,
                command=lambda value=label: self._apply_label_to_selection(value),
            )
        self.original_label_menu.add_separator()
        self.original_label_menu.add_command(
            label="恢复自动识别（清除保护/分组）",
            command=self._restore_selected_auto,
        )
        self.original_menu.add_cascade(label="改判为", menu=self.original_label_menu)

        ttk.Label(
            right,
            text="单击选段；Ctrl/Shift 多选；拖选后【拆解所选】可分离合并块；空段落只在段落明细显示。",
            style="Hint.TLabel",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 6))

    def _configure_original_tags(self) -> None:
        self.original_text.tag_configure(
            "meta", foreground="#9aa0a6", font=("Consolas", 9)
        )
        self.original_text.tag_configure(
            "prefix", foreground="#646a73", font=("Microsoft YaHei UI", 9, "bold")
        )
        self.original_text.tag_configure(
            "empty", foreground="#b0b4ba", font=("Microsoft YaHei UI", 10, "italic")
        )
        self.original_text.tag_configure("body_text", foreground="#1f2329")
        self.original_text.tag_configure(
            "match", background="#ffd54f", foreground="#1f2329"
        )
        for style in FORMAT_STYLES.values():
            key = style["key"]
            self.original_text.tag_configure(f"fmt_{key}", background=style["bg"])
            self.original_text.tag_configure(f"label_{key}", foreground=style["fg"])
            self.original_text.tag_configure(f"bar_{key}", foreground=style["fg"])
        self.original_text.tag_raise("sel")

    def _build_detail_tab(self) -> None:
        tab = ttk.Frame(self.main_notebook)
        self.main_notebook.add(tab, text="段落明细")
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        ttk.Label(
            tab,
            text="高/中/低置信度决定是否自动修改；右键段落行可手动改判。",
            style="Hint.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))

        tree_box = ttk.Frame(tab)
        tree_box.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        tree_box.rowconfigure(0, weight=1)
        tree_box.columnconfigure(0, weight=1)

        columns = ("idx", "label", "score", "band", "source", "text", "reason")
        self.preview_tree = ttk.Treeview(
            tree_box,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "idx": ("序号", 50),
            "label": ("段落类型", 90),
            "score": ("置信度", 65),
            "band": ("分级", 55),
            "source": ("来源", 110),
            "text": ("文本内容", 470),
            "reason": ("判定依据", 300),
        }
        for column, (text, width) in headings.items():
            self.preview_tree.heading(column, text=text)
            self.preview_tree.column(column, width=width, anchor="w")
        self.preview_tree.grid(row=0, column=0, sticky="nsew")

        preview_scroll = ttk.Scrollbar(
            tree_box, orient="vertical", command=self.preview_tree.yview
        )
        self.preview_tree.configure(yscrollcommand=preview_scroll.set)
        preview_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll = ttk.Scrollbar(
            tree_box, orient="horizontal", command=self.preview_tree.xview
        )
        self.preview_tree.configure(xscrollcommand=h_scroll.set)
        h_scroll.grid(row=1, column=0, sticky="ew")

        self.preview_tree.tag_configure("high", background="#e8f6ea", foreground="#1e5e2d")
        self.preview_tree.tag_configure("medium", background="#fff6d8", foreground="#7a5600")
        self.preview_tree.tag_configure("low", background="#fdeaea", foreground="#a3261b")
        self.preview_tree.tag_configure(
            "protected", background="#eef0f3", foreground="#5f6368"
        )

        self.label_menu = tk.Menu(self.root, tearoff=0)
        for label in ALL_LABELS:
            self.label_menu.add_command(
                label=label, command=lambda value=label: self._apply_manual_label(value)
            )
        self.label_menu.add_separator()
        self.label_menu.add_command(
            label="恢复自动识别（清除保护/分组）",
            command=self._clear_manual_label,
        )
        self.label_menu.add_separator()
        self.label_menu.add_command(
            label="拆解该段（独立块）",
            command=self._split_menu_row,
        )
        self.label_menu.add_command(
            label="恢复自动合并（全部）",
            command=self._restore_auto_merge,
        )
        self.preview_tree.bind("<Button-3>", self._on_preview_right_click)

    def _build_log_tab(self) -> None:
        tab = ttk.Frame(self.main_notebook)
        self.main_notebook.add(tab, text="实时日志")
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)

        log_box = ttk.Frame(tab)
        log_box.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        log_box.rowconfigure(0, weight=1)
        log_box.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_box,
            height=12,
            wrap="word",
            state="disabled",
            relief="flat",
            background="#fbfbfc",
            font=("Microsoft YaHei UI", 9),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")

    def _build_bottom_bar(self) -> None:
        bottom = ttk.Frame(self.root)
        bottom.grid(row=3, column=0, sticky="ew", padx=12, pady=(4, 10))

        self.pre_btn = ttk.Button(
            bottom, text="预扫描文档", command=self.start_pre_scan, style="Accent.TButton"
        )
        self.pre_btn.pack(side="left")
        self.run_btn = ttk.Button(
            bottom, text="执行格式化", command=self.start_execute, style="Accent.TButton"
        )
        self.run_btn.pack(side="left", padx=(8, 0))
        self.restore_btn = ttk.Button(
            bottom, text="恢复备份", command=self.open_restore_dialog
        )
        self.restore_btn.pack(side="left", padx=(8, 0))
        self.dir_btn = ttk.Button(
            bottom, text="目录设置…", command=self.open_directory_settings
        )
        self.dir_btn.pack(side="left", padx=(8, 0))

        ttk.Label(
            bottom,
            text="自动备份不可关闭 · 支持 docx / md",
            style="Hint.TLabel",
        ).pack(side="left", padx=16)

        ttk.Button(
            bottom, text="打开备份目录", command=lambda: self._open_folder(self.handler.backup_root)
        ).pack(side="right")
        ttk.Button(
            bottom, text="打开输出目录", command=lambda: self._open_folder(self.handler.output_root)
        ).pack(side="right", padx=(0, 8))

        self.root.after(300, self._set_initial_sash)

    def _set_initial_sash(self) -> None:
        try:
            width = self.format_paned.winfo_width()
            if width > 500:
                self.format_paned.sashpos(0, 380)
        except Exception:
            pass

    def _toggle_advanced(self) -> None:
        if self._adv_visible:
            self.adv_frame.grid_remove()
            self.adv_btn.configure(text="高级选项 ▾")
        else:
            self.adv_frame.grid()
            self.adv_btn.configure(text="高级选项 ▴")
        self._adv_visible = not self._adv_visible

    # ------------------------------------------------------------------
    # 拖拽与文件发现
    # ------------------------------------------------------------------

    def _bind_dnd(self) -> None:
        self.root.update_idletasks()
        self._log_dnd_diagnostics()
        backends = []

        if WIN_DROP_AVAILABLE:
            try:
                self._win_drop = WinDropTarget(self.root, self._on_native_drop)
                backends.append("native")
                self.log(
                    "Windows 原生拖放已启用（整窗支持文件/文件夹，"
                    f"已注册 {self._win_drop.window_count} 个窗口）"
                )
            except Exception as exc:
                self.log(f"Windows 原生拖拽初始化失败：{exc}")

        if DND_AVAILABLE:
            registered, failures = self._register_tkdnd_tree(self.root)
            if registered:
                backends.append("tkinterdnd2")
                self.log(
                    f"tkinterdnd2 拖放已启用（注册 {registered} 个控件，OLE 拖放）"
                )
                if failures:
                    self.log(
                        f"tkinterdnd2 有 {len(failures)} 个控件注册失败，"
                        f"首个原因：{failures[0]}"
                    )
            else:
                self.log(
                    "tkinterdnd2 注册失败："
                    + ("；".join(failures[:2]) if failures else "当前窗口不支持")
                )
        else:
            self.log("tkinterdnd2 不可用（当前 Python 未安装）。")

        if backends:
            self._dnd_backend = "+".join(backends)
            self.dnd_hint_var.set(
                "支持把多个文件/文件夹拖入窗口；拖入后自动预扫描（docx / md）"
            )
            self.log(f"拖拽后端：{self._dnd_backend}")
            if "tkinterdnd2" not in backends:
                self.log(
                    "提示：若资源管理器拖入仍无反应，请为当前 Python 安装 tkinterdnd2："
                    f"{sys.executable} -m pip install tkinterdnd2"
                )
            return

        self._dnd_backend = None
        self.dnd_hint_var.set("拖拽不可用，请点击【选择文件/文件夹】载入文档")
        self.log(
            "拖拽未启用：Windows 原生拖放与 tkinterdnd2 均不可用，请使用按钮选择文件。"
        )

    def _register_tkdnd_tree(self, root_widget):
        registered = 0
        failures = []

        def walk(widget) -> None:
            nonlocal registered
            if hasattr(widget, "drop_target_register"):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self._on_drop)
                    registered += 1
                except Exception as exc:
                    failures.append(f"{widget.__class__.__name__}: {exc}")
            try:
                children = widget.winfo_children()
            except Exception:
                children = []
            for child in children:
                walk(child)

        walk(root_widget)
        return registered, failures

    def _log_dnd_diagnostics(self) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            if ctypes.windll.shell32.IsUserAnAdmin():
                self.log(
                    "警告：当前以管理员身份运行。Windows 会阻止从资源管理器拖入文件，"
                    "请用普通权限启动本程序。"
                )

            kernel32 = ctypes.windll.kernel32
            advapi32 = ctypes.windll.advapi32
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            advapi32.OpenProcessToken.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.HANDLE),
            ]
            advapi32.OpenProcessToken.restype = wintypes.BOOL
            advapi32.IsTokenRestricted.argtypes = [wintypes.HANDLE]
            advapi32.IsTokenRestricted.restype = wintypes.BOOL

            token = wintypes.HANDLE()
            if advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
            ):
                try:
                    if advapi32.IsTokenRestricted(token):
                        self.log(
                            "警告：当前进程使用受限令牌运行（例如 runas /trustlevel）。"
                            "Windows 会阻止资源管理器向该进程拖放文件，导致拖拽无反应。"
                            "请直接运行 python gui.py；如果当前终端是管理员权限，"
                            "请从普通权限的终端或资源管理器启动本程序。"
                        )
                finally:
                    kernel32.CloseHandle(token)
        except Exception:
            pass

    @staticmethod
    def _decode_drop_path(value) -> str:
        if isinstance(value, bytes):
            for encoding in ("utf-8", "mbcs", "gbk", sys.getfilesystemencoding()):
                try:
                    return value.decode(encoding)
                except (UnicodeDecodeError, LookupError):
                    continue
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _on_path_key(self, event=None) -> None:
        self._drop_targets = None

    def _on_native_drop(self, paths) -> None:
        self._start_drop_paths(paths)

    def _on_drop(self, event) -> None:
        try:
            items = self.root.tk.splitlist(event.data)
        except tk.TclError:
            items = [str(event.data)]
        self._start_drop_paths(items)

    def _start_drop_paths(self, paths) -> None:
        normalized = []
        for value in paths:
            text = self._decode_drop_path(value).strip().strip('"')
            if text:
                normalized.append(text)
        if not normalized:
            return

        now = time.time()
        signature = tuple(normalized)
        if (
            signature == self._last_drop_signature
            and now - self._last_drop_time < 1.0
        ):
            return
        self._last_drop_time = now
        self._last_drop_signature = signature

        targets = [Path(text) for text in normalized]
        self.log(
            f"收到拖入 {len(targets)} 个路径："
            + "；".join(str(target) for target in targets[:3])
            + ("…" if len(targets) > 3 else "")
        )

        self._drop_targets = targets
        if len(targets) == 1:
            self.path_var.set(str(targets[0]))
            self.mode_var.set("dir" if targets[0].is_dir() else "file")
        else:
            try:
                base = Path(
                    os.path.commonpath(
                        [
                            str(target.parent if target.is_file() else target)
                            for target in targets
                        ]
                    )
                )
            except ValueError:
                base = targets[0].parent if targets[0].is_file() else targets[0]
            self.path_var.set(str(base))
            self.mode_var.set("dir")

        self.refresh_files()
        if self.files:
            self.log("拖入载入完成，自动开始预扫描…")
            self._schedule_auto_pre_scan()

    def _cancel_pending_auto_scan(self) -> None:
        if self._pending_scan_job is None:
            return
        try:
            self.root.after_cancel(self._pending_scan_job)
        except Exception:
            pass
        self._pending_scan_job = None

    def _schedule_auto_pre_scan(self, delay: int = 150) -> None:
        self._cancel_pending_auto_scan()
        self._pending_scan_job = self.root.after(delay, self._run_auto_pre_scan)

    def _run_auto_pre_scan(self) -> None:
        self._pending_scan_job = None
        if self.busy:
            self._pending_scan_job = self.root.after(200, self._run_auto_pre_scan)
            return
        self.start_pre_scan()

    def choose_path(self) -> None:
        self._drop_targets = None
        self._cancel_pending_auto_scan()
        if self.mode_var.get() == "dir":
            selected = filedialog.askdirectory(title="选择包含 docx / md 的文件夹")
        else:
            selected = filedialog.askopenfilename(
                title="选择 docx / md 文档",
                filetypes=[
                    ("支持的文件", "*.docx *.md *.markdown"),
                    ("Word 文档", "*.docx"),
                    ("Markdown", "*.md *.markdown"),
                    ("所有文件", "*.*"),
                ],
            )
        if selected:
            self.path_var.set(selected)
            self.refresh_files()

    def refresh_files(self) -> None:
        if self.busy:
            return

        targets = list(self._drop_targets) if self._drop_targets else None
        if targets is None:
            raw = self.path_var.get().strip().strip('"')
            if not raw:
                self._drop_targets = None
                self.files = []
                self.skipped = []
                self.base_dir = None
                self.previews.clear()
                self.overrides.clear()
                self.protected.clear()
                self.groups.clear()
                self.forced_breaks.clear()
                self.file_status.clear()
                self.current_file = None
                self._populate_file_tree()
                self._clear_preview("请选择文件或文件夹")
                return

            target = Path(raw)
            if not target.exists():
                messagebox.showerror("路径不存在", f"找不到路径：\n{target}")
                return
            targets = [target]

        files = []
        skipped = []
        for target in targets:
            if not target.exists():
                messagebox.showerror("路径不存在", f"找不到路径：\n{target}")
                return
            try:
                found, skips = self.handler.discover(target)
            except Exception as exc:
                messagebox.showerror("扫描失败", str(exc))
                return
            files.extend(found)
            skipped.extend(skips)

        unique = []
        seen = set()
        for path in files:
            key = str(path)
            if key not in seen:
                seen.add(key)
                unique.append(path)
        files = unique

        if len(targets) == 1 and targets[0].is_dir():
            base = targets[0]
        elif files:
            try:
                base = Path(os.path.commonpath([str(path.parent) for path in files]))
            except ValueError:
                base = files[0].parent
        else:
            base = targets[0].parent if targets[0].is_file() else targets[0]
        self.base_dir = base

        if len(targets) == 1:
            self.mode_var.set("dir" if targets[0].is_dir() else "file")
        else:
            self.mode_var.set("dir")

        self.files = files
        self.skipped = skipped
        self.previews.clear()
        self.overrides.clear()
        self.protected.clear()
        self.groups.clear()
        self.forced_breaks.clear()
        self.file_status.clear()
        self.current_file = None
        self._populate_file_tree()
        self._clear_preview(
            "已发现文件，点击【预扫描文档】查看识别结果"
            if files
            else "未找到支持的 docx / md 文件"
        )
        self.log(f"发现 {len(files)} 个文件（docx/md），跳过 {len(skipped)} 个文件。")
        for item in skipped[:15]:
            self.log(f"  跳过 {self._relative(item.path)}：{item.reason}")
        if len(skipped) > 15:
            self.log(f"  ... 其余 {len(skipped) - 15} 个跳过项")
        if not files:
            self.log("警告：所选路径中没有可处理的 docx / md 文件。")

    def _populate_file_tree(self) -> None:
        children = self.file_tree.get_children()
        if children:
            self.file_tree.delete(*children)
        for path in self.files:
            iid = str(path)
            status = self.file_status.get(iid, "待预扫描")
            self.file_tree.insert(
                "", "end", iid=iid, values=(self._relative(path), status)
            )
        self.skipped_rows = {}
        for index, item in enumerate(self.skipped):
            iid = f"skip::{index}"
            self.skipped_rows[iid] = item
            self.file_tree.insert(
                "",
                "end",
                iid=iid,
                values=(self._relative(item.path), f"已跳过 · {item.reason}"),
                tags=("skipped",),
            )

    def _relative(self, path) -> str:
        if self.base_dir is None:
            return Path(path).name
        try:
            return str(Path(path).relative_to(self.base_dir))
        except ValueError:
            return Path(path).name

    def _set_file_status(self, path, text: str) -> None:
        iid = str(path)
        self.file_status[iid] = text
        if self.file_tree.exists(iid):
            self.file_tree.set(iid, "status", text)

    @staticmethod
    def _status_text(stats: dict) -> str:
        text = (
            f"已扫描 · 高 {stats.get('high', 0)} / "
            f"中 {stats.get('medium', 0)} / 低 {stats.get('low', 0)}"
        )
        if stats.get("protected"):
            text += f" · 保护 {stats['protected']}"
        if stats.get("groups"):
            text += f" · 合并组 {stats['groups']}"
        return text

    # ------------------------------------------------------------------
    # 预览与手动覆盖
    # ------------------------------------------------------------------

    def _on_file_select(self, event=None) -> None:
        selection = self.file_tree.selection()
        if not selection:
            return
        path = selection[0]
        if path in self.skipped_rows:
            self.current_file = None
            self._clear_preview(f"已跳过：{self.skipped_rows[path].reason}")
            return
        self.current_file = path
        if path in self.previews:
            self._show_preview(path)
        else:
            self._clear_preview("该文件尚未预扫描，点击【预扫描文档】")

    def _show_preview(self, path: str) -> None:
        preview = self.previews.get(path)
        if preview is None:
            return
        self._render_results(preview)

    def _render_results(self, preview) -> None:
        children = self.preview_tree.get_children()
        if children:
            self.preview_tree.delete(*children)
        for result in preview.results:
            self.preview_tree.insert(
                "",
                "end",
                iid=str(result.index),
                values=(
                    result.index + 1,
                    result.label,
                    result.score,
                    result.band_text,
                    result.source_text,
                    preview_text(result.text),
                    preview_text("；".join(result.reasons), 60),
                ),
                tags=("protected" if result.protected else result.band,),
            )
        self.current_results = preview.results
        self.current_preview = preview

        stats = preview.stats
        name = Path(preview.source).name
        if getattr(preview, "source_type", "docx") == "markdown":
            summary = (
                f"{name}｜Markdown 文档（结构规范化）｜"
                f"共 {stats.get('total', 0)} 块"
            )
        else:
            summary = (
                f"{name}｜正文基准字号 {preview.scan.body_size:g}pt｜"
                f"高 {stats.get('high', 0)} / 中 {stats.get('medium', 0)} / "
                f"低 {stats.get('low', 0)}｜共 {stats.get('total', 0)} 段"
            )
        if stats.get("protected"):
            summary += f"｜保护 {stats['protected']}"
        if stats.get("groups"):
            summary += f"｜合并组 {stats['groups']} 个"
        self.summary_var.set(summary)

        self._render_format_list(preview)
        self._render_original_text(preview)
        self.clear_highlight(clear_selection=True)

    def _render_format_list(self, preview) -> None:
        children = self.format_tree.get_children()
        if children:
            self.format_tree.delete(*children)
        self.format_rows = {}

        counts = preview.stats.get("labels", {})
        labels = [label for label in FORMAT_ORDER if counts.get(label, 0)]
        extras = sorted(set(counts) - set(FORMAT_ORDER) - {LABEL_EMPTY})
        labels.extend(extras)

        normal_count = 0
        for label in labels:
            results = [
                result
                for result in preview.results
                if result.label == label and not result.protected
            ]
            if not results:
                continue
            normal_count += 1
            high = sum(1 for result in results if result.band == "high")
            medium = sum(1 for result in results if result.band == "medium")
            low = sum(1 for result in results if result.band == "low")
            count_text = f"{len(results)} 段"
            if medium or low:
                count_text += f"（高{high}/中{medium}/低{low}）"
            sample = next(
                (result.text.strip() for result in results if result.text.strip()),
                "",
            )
            style = FORMAT_STYLES.get(label, FORMAT_STYLES["正文"])
            self.format_rows[label] = label
            self.format_tree.insert(
                "",
                "end",
                iid=label,
                values=(label, count_text, preview_text(sample, 42)),
                tags=(style["key"],),
            )

        protected_results = [result for result in preview.results if result.protected]
        if protected_results:
            sample = next(
                (result.text.strip() for result in protected_results if result.text.strip()),
                "",
            )
            self.format_rows[PROTECT_ROW] = "保护"
            self.format_tree.insert(
                "",
                "end",
                iid=PROTECT_ROW,
                values=(
                    "保护",
                    f"{len(protected_results)} 段（不格式化）",
                    preview_text(sample, 42),
                ),
                tags=("protect",),
            )

        table_count = (
            getattr(preview.scan, "table_count", 0)
            if getattr(preview, "source_type", "docx") == "docx"
            else 0
        )
        if table_count:
            samples = getattr(preview.scan, "table_samples", []) or []
            sample = next((item for item in samples if item), "表格")
            self.format_rows[TABLE_ROW] = "表格"
            self.format_tree.insert(
                "",
                "end",
                iid=TABLE_ROW,
                values=("表格", f"{table_count} 个", preview_text(sample, 42)),
                tags=("table",),
            )

        if normal_count:
            text = f"识别到的格式（{normal_count} 类）"
        else:
            text = "未识别到可展示的格式"
        if protected_results:
            text += f"，保护 {len(protected_results)} 段"
        if table_count:
            text += f"，表格 {table_count} 个"
        self.format_count_var.set(text)
        for item in self.format_tree.selection():
            self.format_tree.selection_remove(item)

    def _display_groups(self, preview):
        path = self.current_file or preview.source
        breaks = self.forced_breaks.get(path, set())
        groups = []
        current = []
        previous = None
        previous_key = None
        empty_gap = 0

        for result in preview.results:
            if result.label == LABEL_EMPTY:
                empty_gap += 1
                continue

            key = (result.label, result.protected, result.group_id)
            can_merge = (
                previous is not None
                and key == previous_key
                and (previous.index, result.index) not in breaks
                and (
                    result.group_id is not None
                    or empty_gap <= MAX_MERGE_EMPTY_GAP
                )
            )
            if can_merge:
                current.append(result)
            else:
                if current:
                    groups.append(current)
                current = [result]

            previous = result
            previous_key = key
            empty_gap = 0

        if current:
            groups.append(current)
        return groups

    def _render_original_text(self, preview) -> None:
        widget = self.original_text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.tag_remove("match", "1.0", "end")
        self.text_ranges = {}
        self.line_map = {}
        self.group_label_ranges = []
        self.paragraph_spans = {}
        self._mouse_press_xy = None
        self._mouse_press_state = 0
        self._selection_anchor_index = None
        self._para_selection_snapshot = set()
        self._active_paragraph = None

        groups = self._display_groups(preview)
        for group in groups:
            group_size = len(group)
            for position, result in enumerate(group):
                start = widget.index("end-1c")
                style = (
                    FORMAT_STYLES["保护"]
                    if result.protected
                    else FORMAT_STYLES.get(result.label, FORMAT_STYLES["正文"])
                )
                key = style["key"]

                marks = []
                if result.group_id is not None:
                    marks.append(f"组{result.group_id}")
                if result.protected:
                    marks.append("保护")
                label_text = result.label + (
                    f"·{'·'.join(marks)}" if marks else ""
                )
                if position == 0 and group_size > 1:
                    label_text += f" · {group_size}段"

                widget.insert("end", "│ ", f"bar_{key}")
                widget.insert("end", f"{result.index + 1:04d} ", "meta")
                if position == 0:
                    prefix_start = widget.index("end-1c")
                    widget.insert("end", f"[{label_text}] ", "prefix")
                    prefix_end = widget.index("end-1c")
                    widget.tag_add(f"label_{key}", prefix_start, prefix_end)
                    if group_size > 1:
                        self.group_label_ranges.append(
                            (
                                prefix_start,
                                prefix_end,
                                [member.index for member in group],
                            )
                        )
                else:
                    widget.insert("end", "  ↳ ", "prefix")

                text = (result.text or "").replace("\n", " ")
                if text.strip():
                    widget.insert("end", text, "body_text")
                    line_end = widget.index("end-1c")
                    widget.tag_add(f"fmt_{key}", start, line_end)
                    self.text_ranges[result.index] = (start, line_end)
                else:
                    widget.insert("end", "（空内容）", "empty")
                    line_end = widget.index("end-1c")
                line_no = int(widget.index("end-1c").split(".")[0])
                self.line_map[line_no] = result.index
                self.paragraph_spans[result.index] = (start, line_end)
                if position < group_size - 1:
                    widget.insert("end", "\n")
                else:
                    widget.insert("end", "\n\n")

        if not groups:
            widget.insert("1.0", "文档没有可展示的非空段落。", "empty")

        widget.tag_raise("match")
        widget.tag_raise("sel")
        widget.see("1.0")
        self._set_default_original_status()

    def _on_format_select(self, event=None) -> None:
        selection = self.format_tree.selection()
        if not selection:
            return
        iid = selection[0]
        if iid == PROTECT_ROW:
            self._highlight_format("保护")
            return
        if iid == TABLE_ROW:
            count = getattr(self.current_preview.scan, "table_count", 0)
            self.original_status_var.set(
                f"文档包含 {count} 个表格；结构保留，表格内文字按「表格」格式统一"
            )
            return
        label = self.format_rows.get(iid)
        if label:
            self._highlight_format(label)

    def _highlight_format(self, label: str) -> None:
        preview = self.current_preview
        if preview is None:
            return
        if label == "保护":
            indices = [result.index for result in preview.results if result.protected]
            self._highlight_indices(indices, f"已高亮保护段落：{len(indices)} 段")
            return
        indices = [
            result.index
            for result in preview.results
            if result.label == label and not result.protected
        ]
        self._highlight_indices(indices, f"已高亮「{label}」：{len(indices)} 段")

    def _highlight_indices(self, indices, status: str = "") -> None:
        indices = sorted(set(indices))
        widget = self.original_text
        widget.tag_remove("match", "1.0", "end")
        spans = []
        for index in indices:
            span = self.text_ranges.get(index)
            if span:
                widget.tag_add("match", span[0], span[1])
                spans.append(span)
        widget.tag_raise("match")
        widget.tag_raise("sel")
        if spans:
            widget.see(spans[0][0])
        self.original_status_var.set(
            status or f"已高亮 {len(spans)} 段"
        )

    def _set_default_original_status(self) -> None:
        preview = self.current_preview
        if preview is None:
            self.original_status_var.set("文档原文尚未加载")
            return
        stats = preview.stats
        groups = self._display_groups(preview)
        text = f"文档原文 · 共 {len(preview.results)} 段"
        if groups:
            text += f" · 合并为 {len(groups)} 个块"
        if stats.get("protected"):
            text += f" · 保护 {stats['protected']} 段"
        if stats.get("groups"):
            text += f" · 合并组 {stats['groups']} 个"
        self.original_status_var.set(text + " · 单击选段 / 拖选文字 / 可拆解")

    def clear_highlight(self, clear_selection: bool = False) -> None:
        widget = self.original_text
        widget.tag_remove("match", "1.0", "end")
        widget.tag_raise("sel")
        if clear_selection:
            for item in self.format_tree.selection():
                self.format_tree.selection_remove(item)
        self._set_default_original_status()

    def _clear_preview(self, message: str) -> None:
        children = self.preview_tree.get_children()
        if children:
            self.preview_tree.delete(*children)
        format_children = self.format_tree.get_children()
        if format_children:
            self.format_tree.delete(*format_children)

        self.current_results = []
        self.current_preview = None
        self.text_ranges = {}
        self.format_rows = {}
        self.line_map = {}
        self.group_label_ranges = []
        self.paragraph_spans = {}
        self._mouse_press_xy = None
        self._mouse_press_state = 0
        self._selection_anchor_index = None
        self._para_selection_snapshot = set()
        self._active_paragraph = None
        self.summary_var.set(message)
        self.format_count_var.set("识别到的格式")
        self.original_status_var.set("文档原文尚未加载")

        widget = self.original_text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", message, "empty")

    def _on_preview_right_click(self, event) -> None:
        row = self.preview_tree.identify_row(event.y)
        if not row:
            return
        self.preview_tree.selection_set(row)
        self.menu_row = row
        try:
            self.label_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.label_menu.grab_release()

    def _apply_manual_label(self, label: str) -> None:
        if self.busy:
            self.log("预扫描进行中，请等待完成后再手动修改标签。")
            return
        if not self.current_file or not self.menu_row:
            return
        try:
            index = int(self.menu_row)
        except ValueError:
            return
        self._set_label_for_indices([index], label)

    def _clear_manual_label(self) -> None:
        if self.busy:
            self.log("预扫描进行中，请等待完成后再修改标签。")
            return
        if not self.current_file or not self.menu_row:
            return
        try:
            index = int(self.menu_row)
        except ValueError:
            return
        self._restore_auto_for_indices([index])

    # ------------------------------------------------------------------
    # 原文选区操作：合并、保护、改判、恢复
    # ------------------------------------------------------------------

    def _on_readonly_key(self, event):
        keysym = event.keysym
        ctrl = bool(event.state & 0x4)
        if ctrl and keysym.lower() in ("c", "a"):
            return None
        navigation = {
            "Left",
            "Right",
            "Up",
            "Down",
            "Home",
            "End",
            "Prior",
            "Next",
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
            "Alt_L",
            "Alt_R",
            "Caps_Lock",
            "Num_Lock",
            "Escape",
        }
        if keysym in navigation:
            return None
        if keysym.startswith("F") and keysym[1:].isdigit():
            return None
        return "break"

    @staticmethod
    def _block_edit(event=None):
        return "break"

    def _paragraph_at_line(self, line_no: int):
        index = self.line_map.get(line_no)
        if index is None and line_no > 1:
            index = self.line_map.get(line_no - 1)
        return index

    def _on_original_click(self, event):
        self._mouse_press_xy = (event.x, event.y)
        self._mouse_press_state = event.state
        if event.state & (0x4 | 0x1):
            self._para_selection_snapshot = set(self._selected_indices_from_tags())
        else:
            self._para_selection_snapshot = set()
        try:
            line_no = int(
                self.original_text.index(f"@{event.x},{event.y}").split(".")[0]
            )
        except (tk.TclError, ValueError):
            return None
        index = self._paragraph_at_line(line_no)
        if index is not None:
            self._active_paragraph = index
        return None

    def _on_original_release(self, event):
        press = self._mouse_press_xy
        press_state = self._mouse_press_state
        self._mouse_press_xy = None
        if self.current_preview is None:
            return None
        moved = True
        if press is not None:
            moved = abs(event.x - press[0]) > 4 or abs(event.y - press[1]) > 4
        if moved:
            selected = self._selected_indices_from_tags()
            if selected:
                self._selection_anchor_index = min(selected)
            self._para_selection_snapshot = set()
            return None

        try:
            click_index = self.original_text.index(f"@{event.x},{event.y}")
            line_no = int(click_index.split(".")[0])
        except (tk.TclError, ValueError):
            return None
        index = self._paragraph_at_line(line_no)
        if index is None:
            self._para_selection_snapshot = set()
            return None

        for start, end, members in self.group_label_ranges:
            if (
                self.original_text.compare(start, "<=", click_index)
                and self.original_text.compare(click_index, "<", end)
            ):
                self._select_paragraphs(members)
                self._selection_anchor_index = members[0] if members else None
                self._para_selection_snapshot = set()
                self._on_original_selection()
                return "break"

        ctrl = bool(press_state & 0x4)
        shift = bool(press_state & 0x1)
        if ctrl:
            selected = set(self._para_selection_snapshot)
            if index in selected:
                selected.discard(index)
            else:
                selected.add(index)
            self._select_paragraphs(selected)
            self._selection_anchor_index = index
            if not selected:
                self._active_paragraph = None
        elif shift:
            anchor = self._selection_anchor_index
            if anchor is None:
                selected = {index}
                self._selection_anchor_index = index
            else:
                selected = set(
                    range(min(anchor, index), max(anchor, index) + 1)
                )
            self._select_paragraphs(selected)
        else:
            self._select_paragraphs({index})
            self._selection_anchor_index = index

        self._para_selection_snapshot = set()
        self._on_original_selection()
        return "break"

    def _select_paragraphs(self, indices):
        selected = [
            index for index in sorted(set(indices)) if index in self.paragraph_spans
        ]
        widget = self.original_text
        widget.tag_remove("sel", "1.0", "end")
        for index in selected:
            start, end = self.paragraph_spans[index]
            widget.tag_add("sel", start, end)
        if selected:
            widget.tag_raise("sel")
            first_start = self.paragraph_spans[selected[0]][0]
            widget.mark_set("insert", first_start)
            widget.see(first_start)
            widget.focus_set()
        return selected

    def _on_original_right_click(self, event):
        try:
            click_index = self.original_text.index(f"@{event.x},{event.y}")
            line_no = int(click_index.split(".")[0])
        except (tk.TclError, ValueError):
            return
        index = self._paragraph_at_line(line_no)
        if index is not None:
            self._active_paragraph = index

        ranges = self.original_text.tag_ranges("sel")
        inside_selection = False
        for position in range(0, len(ranges) - 1, 2):
            start = self.original_text.index(ranges[position])
            end = self.original_text.index(ranges[position + 1])
            if (
                self.original_text.compare(start, "<=", click_index)
                and self.original_text.compare(click_index, "<", end)
            ):
                inside_selection = True
                break
        if not inside_selection:
            self.original_text.tag_remove("sel", "1.0", "end")
            if index is not None:
                self._select_paragraphs({index})
                self._selection_anchor_index = index
                self._on_original_selection()
        try:
            self.original_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.original_menu.grab_release()

    def _on_original_selection(self, event=None):
        if self._suppress_selection_status:
            return
        if self.current_preview is None:
            return
        self.original_text.tag_raise("sel")
        indices = self._selected_indices()
        if indices:
            self.original_status_var.set(
                f"已选择 {len(indices)} 段，可合并、保护或改判（作用于整段）"
            )
        else:
            self._set_default_original_status()

    def _selected_indices_from_tags(self):
        indices = set()
        ranges = self.original_text.tag_ranges("sel")
        for position in range(0, len(ranges) - 1, 2):
            start = self.original_text.index(ranges[position])
            end = self.original_text.index(ranges[position + 1])
            for index, (span_start, span_end) in self.text_ranges.items():
                if (
                    self.original_text.compare(start, "<", span_end)
                    and self.original_text.compare(span_start, "<", end)
                ):
                    indices.add(index)
        return indices

    def _selected_indices(self):
        indices = self._selected_indices_from_tags()
        if not indices and self._active_paragraph is not None:
            indices.add(self._active_paragraph)
        return sorted(indices)

    def _group_members(self, index: int):
        if not self.current_file:
            return {index}
        groups = self.groups.get(self.current_file, {})
        group_id = groups.get(index)
        if group_id is None:
            return {index}
        return {
            member
            for member, member_group in groups.items()
            if member_group == group_id
        }

    def _expand_group_indices(self, indices):
        expanded = set()
        for index in indices:
            expanded.update(self._group_members(index))
        return sorted(expanded)

    def _cleanup_manual_state(self, path: str) -> None:
        groups = self.groups.get(path, {})
        if groups:
            by_group = {}
            for index, group_id in groups.items():
                by_group.setdefault(group_id, []).append(index)
            for members in by_group.values():
                if len(members) < 2:
                    for index in members:
                        groups.pop(index, None)
            if not groups:
                self.groups.pop(path, None)
        if not self.overrides.get(path):
            self.overrides.pop(path, None)
        if not self.protected.get(path):
            self.protected.pop(path, None)

    def _suppress_status_briefly(self) -> None:
        self._suppress_selection_status = True
        if self._selection_status_job is not None:
            try:
                self.root.after_cancel(self._selection_status_job)
            except Exception:
                pass
        self._selection_status_job = self.root.after(
            350, self._end_selection_status_suppression
        )

    def _refresh_after_manual(self, indices, message: str) -> None:
        if not self.current_file:
            return
        preview = self.previews.get(self.current_file)
        if preview is None:
            return
        self._suppress_status_briefly()
        self._cleanup_manual_state(self.current_file)
        self._redetect_current(preview)
        self._highlight_indices(indices, message)
        self.log(message)

    def _end_selection_status_suppression(self) -> None:
        self._suppress_selection_status = False
        self._selection_status_job = None

    def _merge_selected(self) -> None:
        if self.busy or not self.current_file:
            return
        indices = self._selected_indices()
        if len(indices) < 2:
            self.original_status_var.set("请先拖选至少两个段落，再进行合并")
            return
        preview = self.previews.get(self.current_file)
        if preview is None:
            return

        by_index = {result.index: result for result in preview.results}
        target_label = None
        for index in indices:
            result = by_index.get(index)
            if result is not None and result.label != LABEL_EMPTY:
                target_label = result.label
                break
        if target_label is None:
            self.original_status_var.set("所选内容都是空段落，无法合并")
            return

        groups = self.groups.setdefault(self.current_file, {})
        group_id = max(groups.values(), default=0) + 1
        overrides = self.overrides.setdefault(self.current_file, {})
        for index in indices:
            overrides[index] = target_label
            groups[index] = group_id

        protected = self.protected.get(self.current_file, set())
        protected_count = sum(1 for index in indices if index in protected)
        breaks = self.forced_breaks.get(self.current_file)
        if breaks:
            selected = set(indices)
            for pair in list(breaks):
                if pair[0] in selected and pair[1] in selected:
                    breaks.discard(pair)
            if not breaks:
                self.forced_breaks.pop(self.current_file, None)
        message = f"已合并 {len(indices)} 段为组{group_id}（{target_label}）"
        if protected_count:
            message += f"，其中 {protected_count} 段已保护、不会格式化"
        self._refresh_after_manual(indices, message)

    def _split_selected(self) -> None:
        if self.busy or not self.current_file:
            return
        indices = self._selected_indices()
        if not indices:
            self.original_status_var.set("请先拖选需要拆解的段落")
            return
        self._split_indices(indices)

    def _split_menu_row(self) -> None:
        if self.busy or not self.current_file or not self.menu_row:
            return
        try:
            index = int(self.menu_row)
        except (TypeError, ValueError):
            return
        self._split_indices([index])

    def _split_indices(self, indices) -> None:
        if self.busy or not self.current_file or self.current_preview is None:
            return
        indices = sorted(set(indices))
        if not indices:
            return

        groups = self._display_groups(self.current_preview)
        breaks = self.forced_breaks.setdefault(self.current_file, set())
        affected = set()
        changed = 0

        for group in groups:
            if len(group) < 2:
                continue
            members = [result.index for result in group]
            selected = [index for index in members if index in indices]
            if not selected:
                continue
            affected.update(selected)
            first_position = members.index(selected[0])
            last_position = members.index(selected[-1])
            if first_position > 0:
                pair = (members[first_position - 1], members[first_position])
                if pair not in breaks:
                    breaks.add(pair)
                    changed += 1
            if last_position < len(members) - 1:
                pair = (members[last_position], members[last_position + 1])
                if pair not in breaks:
                    breaks.add(pair)
                    changed += 1

        if not changed:
            if not breaks:
                self.forced_breaks.pop(self.current_file, None)
            self.original_status_var.set("所选内容已在独立块中，无需拆解")
            return

        self._suppress_status_briefly()
        self._render_results(self.current_preview)
        count = len(affected) or len(indices)
        self._highlight_indices(indices, f"已拆解 {count} 段为独立块")
        self.log(f"拆解合并块：{count} 段")

    def _restore_auto_merge(self) -> None:
        if self.busy or not self.current_file or self.current_preview is None:
            return
        breaks = self.forced_breaks.pop(self.current_file, None)
        if not breaks:
            self.original_status_var.set("当前没有手动拆解记录")
            return
        self._suppress_status_briefly()
        self._render_results(self.current_preview)
        self.original_status_var.set("已恢复自动合并")
        self.log("恢复自动合并")

    def _protect_selected(self) -> None:
        if self.busy or not self.current_file:
            return
        indices = self._selected_indices()
        if not indices:
            self.original_status_var.set("请先拖选需要保护的段落")
            return
        members = self._expand_group_indices(indices)
        self.protected.setdefault(self.current_file, set()).update(members)
        self._refresh_after_manual(
            members, f"已保护 {len(members)} 段，执行时保留原格式"
        )

    def _unprotect_selected(self) -> None:
        if self.busy or not self.current_file:
            return
        indices = self._selected_indices()
        if not indices:
            self.original_status_var.set("请先拖选需要取消保护的段落")
            return
        members = self._expand_group_indices(indices)
        protected = self.protected.get(self.current_file, set())
        removed = [index for index in members if index in protected]
        if not removed:
            self.original_status_var.set("所选段落未处于保护状态")
            return
        for index in members:
            protected.discard(index)
        self._refresh_after_manual(members, f"已取消 {len(removed)} 段保护")

    def _apply_label_to_selection(self, label: str) -> None:
        if self.busy:
            return
        indices = self._selected_indices()
        if not indices:
            self.original_status_var.set("请先拖选需要改判的段落")
            return
        self._set_label_for_indices(indices, label)

    def _set_label_for_indices(self, indices, label: str) -> None:
        if self.busy or not self.current_file:
            return
        members = self._expand_group_indices(indices)
        overrides = self.overrides.setdefault(self.current_file, {})
        for index in members:
            overrides[index] = label
        self._refresh_after_manual(
            members, f"已将 {len(members)} 段改判为「{label}」"
        )

    def _restore_selected_auto(self) -> None:
        if self.busy:
            return
        indices = self._selected_indices()
        if not indices:
            self.original_status_var.set("请先拖选需要恢复自动识别的段落")
            return
        self._restore_auto_for_indices(indices)

    def _restore_auto_for_indices(self, indices) -> None:
        if self.busy or not self.current_file:
            return
        members = self._expand_group_indices(indices)
        overrides = self.overrides.get(self.current_file, {})
        groups = self.groups.get(self.current_file, {})
        protected = self.protected.get(self.current_file, set())
        for index in members:
            overrides.pop(index, None)
            groups.pop(index, None)
            protected.discard(index)
        self._refresh_after_manual(
            members,
            f"已恢复 {len(members)} 段的自动识别，并清除保护/分组",
        )

    def _toggle_focus_mode(self) -> None:
        try:
            if not self._focus_mode:
                if self.format_left is not None:
                    self.format_paned.forget(self.format_left)
                self.focus_btn.configure(text="退出专注")
                self._focus_mode = True
                self.original_status_var.set("专注原文：已隐藏格式列表")
            else:
                if self.format_left is not None:
                    self.format_paned.insert(0, self.format_left, weight=1)
                self.focus_btn.configure(text="专注原文")
                self._focus_mode = False
                self.root.after(120, self._set_initial_sash)
        except Exception as exc:
            self.log(f"切换专注模式失败：{exc}")

    def _redetect_current(self, preview) -> None:
        overrides = self.overrides.get(self.current_file, {})
        protected = self.protected.get(self.current_file, set())
        groups = self.groups.get(self.current_file, {})
        if getattr(preview, "source_type", "docx") == "markdown":
            document = markdown_handler.parse_markdown(preview.source)
            results = markdown_handler.build_results(
                document,
                overrides,
                protected,
                groups,
            )
        else:
            detector = StructureDetector(
                threshold=self._threshold_value(),
                infer_headings=bool(self.infer_var.get()),
            )
            results = detector.detect(
                preview.scan,
                overrides,
                protected,
                groups,
            )
        preview.results = results
        preview.stats = summarize_results(results)
        self._render_results(preview)
        if self.current_file:
            self._set_file_status(self.current_file, self._status_text(preview.stats))

    # ------------------------------------------------------------------
    # 预扫描与执行
    # ------------------------------------------------------------------

    def _threshold_value(self) -> int:
        try:
            value = int(round(float(self.threshold_var.get())))
        except (TypeError, ValueError):
            value = 70
        return max(60, min(90, value))

    def _on_threshold_move(self, raw) -> None:
        try:
            value = int(round(float(raw)))
        except (TypeError, ValueError):
            value = 70
        self.threshold_label.configure(text=f"{value}（低于该分数的段落不自动修改）")

    def _collect_settings(self) -> dict:
        return {
            "template": self._template_stem(),
            "template_data": copy.deepcopy(self.template_data),
            "threshold": self._threshold_value(),
            "infer": bool(self.infer_var.get()),
            "html": bool(self.html_var.get()),
            "backup_dir": self.backup_dir_setting,
            "output_dir": self.output_dir_setting,
            "cleanup": {
                key: bool(self.cleanup_vars[key].get()) for key, _ in CLEANUP_ITEMS
            },
        }

    def start_pre_scan(self) -> None:
        if self.busy:
            return
        self._cancel_pending_auto_scan()
        if not self.files:
            messagebox.showwarning("提示", "请先选择文件或文件夹。")
            return
        if not self._template_stem():
            messagebox.showerror("模板错误", "templates 目录没有可用模板。")
            return
        if not self.template_data:
            messagebox.showerror("模板错误", "当前模板数据为空，请检查格式设置。")
            return

        settings = self._collect_settings()
        overrides = {
            path: dict(values) for path, values in self.overrides.items()
        }
        protected = {
            path: set(values) for path, values in self.protected.items()
        }
        groups = {
            path: dict(values) for path, values in self.groups.items()
        }
        files = list(self.files)

        self._set_busy(True)
        self.previews.clear()
        self.current_file = None
        for path in files:
            self._set_file_status(path, "扫描中…")
        self._clear_preview("正在预扫描…")
        self.log(
            f"开始预扫描 {len(files)} 个文件（模板 {settings['template']}，"
            f"阈值 {settings['threshold']}）。"
        )

        thread = threading.Thread(
            target=self._pre_scan_worker,
            args=(files, settings, overrides, protected, groups),
            daemon=True,
        )
        thread.start()

    def _pre_scan_worker(self, files, settings, overrides, protected, groups) -> None:
        try:
            pipeline = Pipeline(
                ROOT,
                settings["template"],
                threshold=settings["threshold"],
                infer_headings=settings["infer"],
                cleanup=settings["cleanup"],
                html_report=settings["html"],
                logger=self.log,
                template_data=settings.get("template_data"),
                backup_dir=settings.get("backup_dir"),
                output_dir=settings.get("output_dir"),
            )
            for position, path in enumerate(files, start=1):
                try:
                    preview = pipeline.preview_file(
                        path,
                        overrides.get(str(path), {}),
                        protected.get(str(path), set()),
                        groups.get(str(path), {}),
                    )
                except Exception as exc:
                    self.log(f"预扫描失败：{Path(path).name}：{exc}")
                    self.ui_queue.put(("file_status", (str(path), "扫描失败")))
                    continue
                self.ui_queue.put(("preview", (str(path), preview)))
                self.log(
                    f"预扫描 [{position}/{len(files)}] {Path(path).name}："
                    f"高 {preview.stats.get('high', 0)} / "
                    f"中 {preview.stats.get('medium', 0)} / "
                    f"低 {preview.stats.get('low', 0)}"
                )
            self.ui_queue.put(("scan_done", None))
        except Exception as exc:
            self.ui_queue.put(("fatal", f"预扫描失败：{exc}"))

    def start_execute(self) -> None:
        if self.busy:
            return
        self._cancel_pending_auto_scan()
        if not self.files:
            messagebox.showwarning("提示", "请先选择文件或文件夹。")
            return
        if not self._template_stem():
            messagebox.showerror("模板错误", "templates 目录没有可用模板。")
            return
        if not self.template_data:
            messagebox.showerror("模板错误", "当前模板数据为空，请检查格式设置。")
            return

        settings = self._collect_settings()
        overrides = {
            path: dict(values) for path, values in self.overrides.items()
        }
        protected = {
            path: set(values) for path, values in self.protected.items()
        }
        groups = {
            path: dict(values) for path, values in self.groups.items()
        }
        files = list(self.files)
        skipped = [f"{item.path}：{item.reason}" for item in self.skipped]

        stale_previews = [
            preview
            for preview in self.previews.values()
            if preview.threshold != settings["threshold"]
            or preview.infer_headings != settings["infer"]
        ]
        if stale_previews:
            self.log("提示：预览使用的阈值或推断开关与当前设置不同，建议重新预扫描。")
        if not self.previews:
            self.log("提示：尚未预扫描，将直接按自动识别结果执行。")
        self._set_busy(True)
        self.log("开始备份并执行格式化……")

        thread = threading.Thread(
            target=self._execute_worker,
            args=(files, self.base_dir, settings, overrides, skipped, protected, groups),
            daemon=True,
        )
        thread.start()

    def _execute_worker(self, files, base_dir, settings, overrides, skipped, protected, groups) -> None:
        try:
            pipeline = Pipeline(
                ROOT,
                settings["template"],
                threshold=settings["threshold"],
                infer_headings=settings["infer"],
                cleanup=settings["cleanup"],
                html_report=settings["html"],
                logger=self.log,
                template_data=settings.get("template_data"),
                backup_dir=settings.get("backup_dir"),
                output_dir=settings.get("output_dir"),
            )
            task = pipeline.execute(
                files,
                base_dir,
                overrides,
                skipped,
                protected_map=protected,
                groups_map=groups,
            )
            self.ui_queue.put(("task_done", task))
        except Exception as exc:
            self.ui_queue.put(("fatal", f"处理失败：{exc}"))

    def _handle_task_done(self, task) -> None:
        self._set_busy(False)
        successes = [item for item in task.files if not item.error]
        failures = [item for item in task.files if item.error]
        for item in task.files:
            self._set_file_status(
                item.source, "已输出" if not item.error else "失败"
            )

        self.log(f"处理完成：成功 {len(successes)}，失败 {len(failures)}。")
        self.log(f"备份目录：{task.backup_dir}")
        if task.txt_report:
            self.log(f"TXT 报告：{task.txt_report}")
        if task.html_report:
            self.log(f"HTML 报告：{task.html_report}")

        summary = (
            f"成功 {len(successes)} 个，失败 {len(failures)} 个。\n\n"
            f"备份目录：\n{task.backup_dir}\n\n"
            f"TXT 报告：\n{task.txt_report or '未生成'}"
        )
        if task.html_report:
            summary += f"\n\nHTML 报告：\n{task.html_report}"
        if failures:
            detail = "\n".join(
                f"{Path(item.source).name}：{item.error}" for item in failures[:5]
            )
            summary += f"\n\n失败详情：\n{detail}"
        messagebox.showinfo("处理完成", summary)

    # ------------------------------------------------------------------
    # 日志与后台队列
    # ------------------------------------------------------------------

    def log(self, message) -> None:
        if threading.current_thread() is threading.main_thread():
            self._append_log(str(message))
        else:
            self.ui_queue.put(("log", str(message)))

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                try:
                    if kind == "log":
                        self._append_log(payload)
                    elif kind == "preview":
                        path, preview = payload
                        self.previews[path] = preview
                        self._set_file_status(path, self._status_text(preview.stats))
                        if self.current_file is None:
                            self.current_file = path
                            if self.file_tree.exists(path):
                                self.file_tree.selection_set(path)
                                self.file_tree.see(path)
                            self._show_preview(path)
                    elif kind == "file_status":
                        path, status = payload
                        self._set_file_status(path, status)
                    elif kind == "scan_done":
                        self._set_busy(False)
                        if self.previews:
                            self.log(f"预扫描完成：共 {len(self.previews)} 个文件。")
                        else:
                            self.log("预扫描结束，没有可显示的结果。")
                            self._clear_preview("预扫描未产生结果，请查看日志")
                    elif kind == "task_done":
                        self._handle_task_done(payload)
                    elif kind == "fatal":
                        self._set_busy(False)
                        self.log(str(payload))
                        messagebox.showerror("错误", str(payload))
                except Exception as exc:
                    self._append_log(f"界面更新异常：{exc}")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in (
            self.pre_btn,
            self.run_btn,
            self.restore_btn,
            self.browse_btn,
            self.refresh_btn,
            self.format_btn,
            self.req_btn,
            self.manager_btn,
            self.templates_btn,
            self.dir_btn,
        ):
            button.configure(state=state)
        self.path_entry.configure(state=state)
        self.template_combo.configure(state="disabled" if busy else "readonly")

    # ------------------------------------------------------------------
    # 备份恢复与目录
    # ------------------------------------------------------------------

    def open_restore_dialog(self) -> None:
        if self.busy:
            return
        backups = self.handler.list_backups()
        if not backups:
            messagebox.showinfo("恢复备份", "暂无历史备份。")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("恢复备份")
        dialog.geometry("860x500")
        dialog.minsize(760, 420)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.rowconfigure(1, weight=1)
        dialog.columnconfigure(0, weight=1)

        ttk.Label(
            dialog,
            text="选择一次备份任务，恢复将覆盖对应的原始文档。",
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 6))

        body = ttk.Frame(dialog)
        body.grid(row=1, column=0, sticky="nsew", padx=12)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        list_box = ttk.Frame(body)
        list_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        list_box.rowconfigure(0, weight=1)
        list_box.columnconfigure(0, weight=1)

        listbox = tk.Listbox(list_box, exportselection=False)
        listbox.grid(row=0, column=0, sticky="nsew")
        list_scroll = ttk.Scrollbar(list_box, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=list_scroll.set)
        list_scroll.grid(row=0, column=1, sticky="ns")

        detail = tk.Text(body, wrap="word", state="disabled", relief="solid", borderwidth=1)
        detail.grid(row=0, column=1, sticky="nsew")

        for info in backups:
            flag = "" if info.manifest_ok else " [缺少清单]"
            listbox.insert("end", f"{info.directory.name}　{info.file_count} 个文件{flag}")

        def show_detail(event=None) -> None:
            selection = listbox.curselection()
            if not selection:
                return
            info = backups[selection[0]]
            lines = [
                f"备份目录：{info.directory}",
                f"创建时间：{info.created_at}",
                f"文件数量：{info.file_count}",
                "",
            ]
            for entry in info.entries:
                lines.append(f"原始路径：{entry.source or '（清单缺失，无法定位）'}")
                lines.append(f"备份文件：{entry.backup}")
                lines.append("")
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("1.0", "\n".join(lines))
            detail.configure(state="disabled")

        listbox.bind("<<ListboxSelect>>", show_detail)
        if backups:
            listbox.selection_set(0)
            show_detail()

        def do_restore() -> None:
            selection = listbox.curselection()
            if not selection:
                messagebox.showwarning("恢复备份", "请先选择一个备份。", parent=dialog)
                return
            info = backups[selection[0]]
            if not info.manifest_ok:
                messagebox.showerror(
                    "无法恢复",
                    "该备份缺少 manifest.json，无法确定原始文件路径。",
                    parent=dialog,
                )
                return
            if not messagebox.askyesno(
                "确认恢复",
                f"将用备份 {info.directory.name} 覆盖 {info.file_count} 个原始文档。\n"
                "此操作不可撤销，是否继续？",
                parent=dialog,
            ):
                return
            try:
                restored, missing = self.handler.restore_backup(info.directory)
            except Exception as exc:
                messagebox.showerror("恢复失败", str(exc), parent=dialog)
                return
            self.log(f"已从备份 {info.directory.name} 恢复 {len(restored)} 个文档。")
            message = f"已恢复 {len(restored)} 个文档。"
            if missing:
                message += f"\n有 {len(missing)} 个备份文件缺失，未恢复。"
            messagebox.showinfo("恢复完成", message, parent=dialog)
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=2, column=0, sticky="e", padx=12, pady=10)
        ttk.Button(buttons, text="恢复选中备份", command=do_restore).pack(side="right")
        ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(
            side="right", padx=(0, 8)
        )

    @staticmethod
    def _open_folder(path) -> None:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as exc:
            messagebox.showerror("打开失败", f"无法打开目录：\n{target}\n\n{exc}")


def main() -> None:
    if DND_AVAILABLE:
        try:
            root = TkinterDnD.Tk()
        except Exception:
            globals()["DND_AVAILABLE"] = False
            root = tk.Tk()
    else:
        root = tk.Tk()
    DocFormatFixApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
