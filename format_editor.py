"""GUI 格式设置对话框。

允许用户修改每种段落类型的输出格式，并将整套设置另存为新的 JSON 模板。
"""

from __future__ import annotations

import copy
import re
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from src.format_engine import save_template, template_path, validate_template_name

FORMAT_LABELS = [
    "一级标题",
    "二级标题",
    "三级标题",
    "正文",
    "参考文献",
    "表格",
    "图注",
    "表注",
]

DEFAULT_SPECS = {
    "一级标题": {
        "font": "黑体",
        "size": 16,
        "bold": True,
        "indent": False,
        "line_spacing": 1.5,
        "space_before": 12,
        "space_after": 12,
    },
    "二级标题": {
        "font": "黑体",
        "size": 14,
        "bold": True,
        "indent": False,
        "line_spacing": 1.5,
        "space_before": 6,
        "space_after": 6,
    },
    "三级标题": {
        "font": "黑体",
        "size": 12.5,
        "bold": True,
        "indent": False,
        "line_spacing": 1.5,
        "space_before": 3,
        "space_after": 3,
    },
    "正文": {
        "font": "宋体",
        "size": 12,
        "bold": False,
        "indent": True,
        "line_spacing": 1.5,
    },
    "参考文献": {
        "font": "宋体",
        "size": 10.5,
        "bold": False,
        "indent": False,
        "line_spacing": 1.25,
        "space_before": 0,
        "space_after": 0,
    },
    "表格": {
        "font": "宋体",
        "size": 10.5,
        "bold": False,
        "line_spacing": 1.25,
        "space_before": 0,
        "space_after": 0,
    },
    "图注": {"font": "宋体", "size": 10.5, "bold": False, "align": "center"},
    "表注": {"font": "宋体", "size": 10.5, "bold": False, "align": "left"},
}

FONT_CHOICES = ["宋体", "黑体", "楷体", "仿宋", "微软雅黑", "Times New Roman"]
SPACING_CHOICES = ["1.0", "1.15", "1.25", "1.5", "1.75", "2.0", "20pt"]
ALIGN_OPTIONS = [
    ("不设置", ""),
    ("左对齐", "left"),
    ("居中", "center"),
    ("右对齐", "right"),
    ("两端对齐", "justify"),
]


class FormatEditorDialog:
    def __init__(
        self,
        parent,
        template_name: str,
        template_data: dict,
        project_root,
        counts: dict = None,
        on_apply=None,
        on_saved=None,
        logger=None,
    ):
        self.parent = parent
        self.template_name = template_name
        self.template_data = template_data
        self.project_root = Path(project_root)
        self.counts = counts or {}
        self.on_apply = on_apply or (lambda: None)
        self.on_saved = on_saved or (lambda name, data: None)
        self.logger = logger or (lambda message: None)

        self._labels = []
        self._current_label = None
        self._loading = False
        self._dirty = False

        self.font_var = tk.StringVar()
        self.size_var = tk.StringVar()
        self.bold_var = tk.BooleanVar()
        self.indent_var = tk.BooleanVar()
        self.line_spacing_var = tk.StringVar()
        self.space_before_var = tk.StringVar()
        self.space_after_var = tk.StringVar()
        self.align_var = tk.StringVar()
        self.current_label_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self._align_display_to_value = {display: value for display, value in ALIGN_OPTIONS}
        self._align_value_to_display = {value: display for display, value in ALIGN_OPTIONS}

        self.top = tk.Toplevel(parent)
        self.top.title(f"格式设置 - {template_name}")
        self.top.geometry("1000x650")
        self.top.minsize(900, 580)
        self.top.transient(parent)
        self.top.grab_set()
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(1, weight=1)

        self._build()
        self._select_initial()

        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda event: self.close())
        self._center()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build(self) -> None:
        header = ttk.Frame(self.top)
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        ttk.Label(
            header,
            text="每种段落类型可单独设置输出格式；修改仅影响执行结果，不会改动原文。",
        ).pack(side="left")
        ttk.Label(
            header,
            text="可另存为新模板以便下次复用",
            style="Hint.TLabel",
        ).pack(side="right")

        body = ttk.Frame(self.top)
        body.grid(row=1, column=0, sticky="nsew", padx=14)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        left = ttk.LabelFrame(body, text="段落格式")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.label_tree = ttk.Treeview(
            left,
            columns=("label", "count"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        self.label_tree.heading("label", text="格式")
        self.label_tree.heading("count", text="当前文档")
        self.label_tree.column("label", width=130, anchor="w")
        self.label_tree.column("count", width=90, anchor="center")
        self.label_tree.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.label_tree.yview)
        self.label_tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns", pady=6)
        self.label_tree.bind("<<TreeviewSelect>>", self._on_label_select)

        right = ttk.LabelFrame(body, text="格式参数")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        form = ttk.Frame(right)
        form.grid(row=0, column=0, sticky="nsew", padx=12, pady=10)
        form.columnconfigure(1, weight=0)
        form.columnconfigure(2, weight=1)

        ttk.Label(form, textvariable=self.current_label_var, font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )

        ttk.Label(form, text="字体：").grid(row=1, column=0, sticky="w", pady=5)
        self.font_combo = ttk.Combobox(
            form, textvariable=self.font_var, values=FONT_CHOICES, width=22
        )
        self.font_combo.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=5)

        ttk.Label(form, text="字号：").grid(row=2, column=0, sticky="w", pady=5)
        size_box = ttk.Frame(form)
        size_box.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=5)
        ttk.Spinbox(
            size_box,
            from_=6,
            to=72,
            increment=0.5,
            textvariable=self.size_var,
            width=8,
        ).pack(side="left")
        ttk.Label(size_box, text="pt", style="Hint.TLabel").pack(side="left", padx=(6, 0))

        ttk.Label(form, text="样式：").grid(row=3, column=0, sticky="nw", pady=5)
        style_box = ttk.Frame(form)
        style_box.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=5)
        ttk.Checkbutton(style_box, text="加粗", variable=self.bold_var).pack(side="left")
        ttk.Checkbutton(
            style_box, text="首行缩进 2 字符", variable=self.indent_var
        ).pack(side="left", padx=(16, 0))

        ttk.Label(form, text="行距：").grid(row=4, column=0, sticky="w", pady=5)
        self.spacing_combo = ttk.Combobox(
            form,
            textvariable=self.line_spacing_var,
            values=SPACING_CHOICES,
            width=10,
        )
        self.spacing_combo.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=5)
        ttk.Label(
            form,
            text="倍数（如 1.5）或固定值（如 20pt）",
            style="Hint.TLabel",
        ).grid(row=4, column=2, sticky="w", padx=(10, 0), pady=5)

        ttk.Label(form, text="段前段后：").grid(row=5, column=0, sticky="w", pady=5)
        spacing_box = ttk.Frame(form)
        spacing_box.grid(row=5, column=1, sticky="w", padx=(8, 0), pady=5)
        ttk.Label(spacing_box, text="段前").pack(side="left")
        ttk.Spinbox(
            spacing_box,
            from_=0,
            to=200,
            increment=1,
            textvariable=self.space_before_var,
            width=6,
        ).pack(side="left", padx=(4, 10))
        ttk.Label(spacing_box, text="磅").pack(side="left")
        ttk.Label(spacing_box, text="段后").pack(side="left", padx=(14, 0))
        ttk.Spinbox(
            spacing_box,
            from_=0,
            to=200,
            increment=1,
            textvariable=self.space_after_var,
            width=6,
        ).pack(side="left", padx=(4, 10))
        ttk.Label(spacing_box, text="磅").pack(side="left")

        ttk.Label(form, text="对齐：").grid(row=6, column=0, sticky="w", pady=5)
        self.align_combo = ttk.Combobox(
            form,
            textvariable=self.align_var,
            values=[display for display, _value in ALIGN_OPTIONS],
            state="readonly",
            width=12,
        )
        self.align_combo.grid(row=6, column=1, sticky="w", padx=(8, 0), pady=5)

        preview_frame = ttk.LabelFrame(form, text="效果预览")
        preview_frame.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(14, 4))
        self.preview_label = tk.Label(
            preview_frame,
            text="文档格式自动校准 Aa 123",
            anchor="w",
            justify="left",
            background="#ffffff",
            foreground="#1f2329",
            padx=14,
            pady=14,
        )
        self.preview_label.pack(fill="x", padx=8, pady=8)

        ttk.Label(form, textvariable=self.status_var, style="Hint.TLabel").grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

        buttons = ttk.Frame(self.top)
        buttons.grid(row=2, column=0, sticky="ew", padx=14, pady=12)
        ttk.Button(
            buttons, text="应用当前格式", command=self.apply_current, style="Accent.TButton"
        ).pack(side="left")
        ttk.Button(
            buttons, text="载入内置默认", command=self.load_default
        ).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="关闭", command=self.close).pack(side="right")
        ttk.Button(
            buttons, text="另存为模板…", command=self.save_as_template
        ).pack(side="right", padx=(0, 8))

        for var in (
            self.font_var,
            self.size_var,
            self.bold_var,
            self.indent_var,
            self.line_spacing_var,
            self.space_before_var,
            self.space_after_var,
            self.align_var,
        ):
            var.trace_add("write", self._on_field_change)

    def _center(self) -> None:
        try:
            self.top.update_idletasks()
            width = self.top.winfo_width()
            height = self.top.winfo_height()
            x = self.parent.winfo_rootx() + max((self.parent.winfo_width() - width) // 2, 0)
            y = self.parent.winfo_rooty() + max((self.parent.winfo_height() - height) // 2, 0)
            self.top.geometry(f"+{x}+{y}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 数据与交互
    # ------------------------------------------------------------------

    def _select_initial(self) -> None:
        labels = []
        for label in FORMAT_LABELS:
            labels.append(label)
        for label in self.counts:
            if label not in labels and label != "空段落":
                labels.append(label)
        for label in self.template_data:
            if label not in labels and label != "空段落":
                labels.append(label)
        self._labels = labels

        for label in labels:
            count = self.counts.get(label, 0)
            self.label_tree.insert(
                "",
                "end",
                iid=label,
                values=(label, f"{count} 段" if count else "—"),
            )

        initial = next((label for label in labels if self.counts.get(label, 0)), None)
        if initial is None:
            initial = labels[0] if labels else None
        if initial:
            self.label_tree.selection_set(initial)
            self._load_label(initial)

    def _select_row(self, label: str) -> None:
        if label and self.label_tree.exists(label):
            self.label_tree.selection_set(label)
            self.label_tree.see(label)

    def _on_label_select(self, event=None) -> None:
        selection = self.label_tree.selection()
        if not selection:
            return
        label = selection[0]
        if label == self._current_label:
            return
        if self._dirty:
            answer = messagebox.askyesnocancel(
                "未应用的修改",
                "当前格式字段尚未应用，是否应用后再切换？",
                parent=self.top,
            )
            if answer is None:
                self._select_row(self._current_label)
                return
            if answer and not self.apply_current():
                self._select_row(self._current_label)
                return
        self._load_label(label)

    def _load_label(self, label: str) -> None:
        self._current_label = label
        spec = self.template_data.get(label)
        if not isinstance(spec, dict):
            spec = DEFAULT_SPECS.get(label, DEFAULT_SPECS["正文"])
        self._apply_spec_to_form(spec)
        self.current_label_var.set(f"正在编辑：{label}")
        self.status_var.set("")

    def _apply_spec_to_form(self, spec: dict) -> None:
        self._loading = True
        self.font_var.set(str(spec.get("font", "宋体")))
        self.size_var.set(self._format_number(spec.get("size", 12)))
        self.bold_var.set(bool(spec.get("bold", False)))
        self.indent_var.set(bool(spec.get("indent", False)))
        self.line_spacing_var.set(self._format_number(spec.get("line_spacing", 1.5)))
        self.space_before_var.set(self._format_number(spec.get("space_before", 0)))
        self.space_after_var.set(self._format_number(spec.get("space_after", 0)))
        align = str(spec.get("align", ""))
        self.align_var.set(self._align_value_to_display.get(align, "不设置"))
        self._loading = False
        self._dirty = False
        self._update_preview()

    @staticmethod
    def _format_number(value) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if abs(number - round(number)) < 1e-9:
            return str(int(round(number)))
        return f"{number:g}"

    def _on_field_change(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self.status_var.set("有未应用的修改")
        self._update_preview()

    def _update_preview(self) -> None:
        try:
            size = float(self.size_var.get())
        except (TypeError, ValueError):
            size = 12
        size = max(6, min(72, int(round(size))))
        family = self.font_var.get().strip() or "宋体"
        weight = "bold" if self.bold_var.get() else "normal"
        self.preview_label.configure(
            font=(family, size, weight),
            text=f"文档格式自动校准 Aa 123　{family} {size}pt",
        )

    def _collect_spec(self) -> dict:
        font = self.font_var.get().strip()
        if not font:
            raise ValueError("字体不能为空")

        try:
            size = float(self.size_var.get())
        except (TypeError, ValueError):
            raise ValueError("字号必须是数字")
        if not 6 <= size <= 72:
            raise ValueError("字号需在 6~72 之间")

        raw_spacing = self.line_spacing_var.get().strip()
        fixed_match = re.fullmatch(
            r"(\d+(?:\.\d+)?)\s*(?:pt|磅)", raw_spacing, re.IGNORECASE
        )
        if fixed_match:
            fixed_value = float(fixed_match.group(1))
            if not 5 <= fixed_value <= 200:
                raise ValueError("固定行距需在 5~200 磅之间")
            line_spacing = f"{self._clean_number(fixed_value)}pt"
        else:
            try:
                multiple = float(raw_spacing)
            except (TypeError, ValueError):
                raise ValueError("行距需填写倍数（如 1.5）或固定值（如 20pt）")
            if not 0.5 <= multiple <= 5:
                raise ValueError("倍数行距需在 0.5~5 之间")
            line_spacing = self._clean_number(multiple)

        def number(value, name: str) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name}必须是数字")
            if not 0 <= parsed <= 200:
                raise ValueError(f"{name}需在 0~200 之间")
            return parsed

        before = number(self.space_before_var.get(), "段前")
        after = number(self.space_after_var.get(), "段后")

        spec = {
            "font": font,
            "size": self._clean_number(size),
            "bold": bool(self.bold_var.get()),
            "indent": bool(self.indent_var.get()),
            "line_spacing": self._clean_number(line_spacing),
            "space_before": self._clean_number(before),
            "space_after": self._clean_number(after),
        }
        align = self._align_display_to_value.get(self.align_var.get(), "")
        if align:
            spec["align"] = align
        return spec

    @staticmethod
    def _clean_number(value):
        if isinstance(value, str):
            return value
        if abs(value - round(value)) < 1e-9:
            return int(round(value))
        return round(value, 2)

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def apply_current(self, silent: bool = False) -> bool:
        if not self._current_label:
            return False
        try:
            spec = self._collect_spec()
        except ValueError as exc:
            if not silent:
                messagebox.showerror("格式参数错误", str(exc), parent=self.top)
            return False
        self.template_data[self._current_label] = spec
        self._dirty = False
        self.status_var.set(f"已应用「{self._current_label}」格式（仅本次任务生效）")
        try:
            self.on_apply()
        except Exception:
            pass
        return True

    def load_default(self) -> None:
        if not self._current_label:
            return
        spec = DEFAULT_SPECS.get(self._current_label, DEFAULT_SPECS["正文"])
        self._apply_spec_to_form(spec)
        self._dirty = True
        self.status_var.set("已载入内置默认值，点击【应用当前格式】后生效")

    def save_as_template(self) -> None:
        if not self.apply_current():
            return
        default_name = "custom_" + datetime.now().strftime("%Y%m%d_%H%M")
        name = simpledialog.askstring(
            "另存为模板",
            "请输入模板名称（可含中文，不能包含路径符号或括号）：",
            initialvalue=default_name,
            parent=self.top,
        )
        if name is None:
            return
        name = name.strip()
        try:
            name = validate_template_name(name)
        except ValueError as exc:
            messagebox.showerror("模板名称不合法", str(exc), parent=self.top)
            return

        path = template_path(self.project_root, name)
        if path.exists():
            if not messagebox.askyesno(
                "覆盖模板",
                f"模板 {name}.json 已存在，是否覆盖？",
                parent=self.top,
            ):
                return

        data = copy.deepcopy(self.template_data)
        try:
            save_template(self.project_root, name, data, overwrite=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.top)
            return

        self.logger(f"已保存自定义模板：{path.name}")
        try:
            self.on_saved(name, data)
        except Exception:
            pass
        messagebox.showinfo(
            "保存成功",
            f"模板已保存为 {name}.json，并切换为当前模板。",
            parent=self.top,
        )
        self.close(force=True)

    def close(self, force: bool = False) -> None:
        if not force and self._dirty:
            answer = messagebox.askyesnocancel(
                "未应用的修改",
                "当前格式字段尚未应用，是否应用后关闭？",
                parent=self.top,
            )
            if answer is None:
                return
            if answer and not self.apply_current():
                return
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.top.destroy()
