"""智能识别格式要求对话框。

只有点击主界面的【识别格式要求…】按钮后才会弹出，粘贴文本即可解析，
识别结果可应用到当前模板（仅本次任务），也可以直接保存为新模板。
"""

from __future__ import annotations

import copy
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from src.format_engine import save_template, template_path, validate_template_name
from src.format_requirements import parse_requirements

EXAMPLE_TEXT = """一级标题：黑体，三号，加粗，段前12磅，段后12磅，1.5倍行距
二级标题：黑体，四号，加粗，段前6磅，段后6磅
三级标题：黑体，小四，加粗，段前3磅，段后3磅
正文：宋体，小四，首行缩进2字符，行距固定值20磅
参考文献：宋体，五号，行距1.25倍
表格内文字：宋体，五号，单倍行距
图注：宋体，五号，居中
表注：宋体，五号，左对齐"""


class FormatRequirementsDialog:
    def __init__(
        self,
        parent,
        base_template: dict,
        project_root,
        on_apply=None,
        on_saved=None,
        logger=None,
    ):
        self.parent = parent
        self.base_template = copy.deepcopy(base_template or {})
        self.project_root = Path(project_root)
        self.on_apply = on_apply or (lambda template: None)
        self.on_saved = on_saved or (lambda name, data: None)
        self.logger = logger or (lambda message: None)
        self.parsed = None
        self._last_parsed_text = ""

        self.status_var = tk.StringVar(value="粘贴格式要求后点击【识别格式要求】")

        self.top = tk.Toplevel(parent)
        self.top.title("智能识别格式要求")
        self.top.geometry("980x780")
        self.top.minsize(880, 680)
        self.top.transient(parent)
        self.top.grab_set()
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(1, weight=1)
        self.top.rowconfigure(3, weight=1)

        self._build()
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda event: self.close())
        self.top.bind("<Control-Return>", lambda event: self._parse())
        self._center()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build(self) -> None:
        header = ttk.Frame(self.top)
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        ttk.Label(
            header,
            text=(
                "把格式要求原文粘贴到下方输入框，程序会按规则识别字体、字号、"
                "加粗、行距、段前段后、缩进与对齐。"
            ),
            wraplength=920,
        ).pack(side="left")

        input_frame = ttk.LabelFrame(self.top, text="格式要求文本（支持直接粘贴 / Ctrl+Enter 识别）")
        input_frame.grid(row=1, column=0, sticky="nsew", padx=14, pady=4)
        input_frame.rowconfigure(0, weight=1)
        input_frame.columnconfigure(0, weight=1)

        self.input_text = tk.Text(
            input_frame,
            height=11,
            wrap="word",
            undo=True,
            relief="flat",
            background="#fcfcfd",
            font=("Microsoft YaHei UI", 10),
            padx=10,
            pady=8,
        )
        self.input_text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        input_scroll = ttk.Scrollbar(
            input_frame, orient="vertical", command=self.input_text.yview
        )
        self.input_text.configure(yscrollcommand=input_scroll.set)
        input_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)

        toolbar = ttk.Frame(self.top)
        toolbar.grid(row=2, column=0, sticky="ew", padx=14, pady=4)
        ttk.Button(
            toolbar,
            text="识别格式要求",
            command=self._parse,
            style="Accent.TButton",
        ).pack(side="left")
        ttk.Button(
            toolbar, text="载入示例", command=self._load_example
        ).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="清空", command=self._clear).pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(
            toolbar, textvariable=self.status_var, style="Hint.TLabel"
        ).pack(side="left", padx=(16, 0))

        result_frame = ttk.LabelFrame(self.top, text="识别结果")
        result_frame.grid(row=3, column=0, sticky="nsew", padx=14, pady=4)
        result_frame.rowconfigure(0, weight=1)
        result_frame.rowconfigure(1, weight=0)
        result_frame.columnconfigure(0, weight=1)

        tree_box = ttk.Frame(result_frame)
        tree_box.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        tree_box.rowconfigure(0, weight=1)
        tree_box.columnconfigure(0, weight=1)

        self.result_tree = ttk.Treeview(
            tree_box,
            columns=("label", "format", "source"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        self.result_tree.heading("label", text="段落类型")
        self.result_tree.heading("format", text="识别到的格式")
        self.result_tree.heading("source", text="原文片段")
        self.result_tree.column("label", width=110, anchor="w")
        self.result_tree.column("format", width=430, anchor="w")
        self.result_tree.column("source", width=360, anchor="w")
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        result_scroll = ttk.Scrollbar(
            tree_box, orient="vertical", command=self.result_tree.yview
        )
        self.result_tree.configure(yscrollcommand=result_scroll.set)
        result_scroll.grid(row=0, column=1, sticky="ns")

        warning_box = ttk.Frame(result_frame)
        warning_box.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        warning_box.columnconfigure(0, weight=1)
        self.warning_text = tk.Text(
            warning_box,
            height=4,
            wrap="word",
            state="disabled",
            relief="flat",
            background="#fff8f2",
            foreground="#9a4d00",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=6,
        )
        self.warning_text.grid(row=0, column=0, sticky="ew")
        warning_scroll = ttk.Scrollbar(
            warning_box, orient="vertical", command=self.warning_text.yview
        )
        self.warning_text.configure(yscrollcommand=warning_scroll.set)
        warning_scroll.grid(row=0, column=1, sticky="ns")
        self._set_warnings([])

        buttons = ttk.Frame(self.top)
        buttons.grid(row=4, column=0, sticky="ew", padx=14, pady=12)
        ttk.Button(
            buttons,
            text="应用到当前模板（本次任务）",
            command=self.apply_requirements,
            style="Accent.TButton",
        ).pack(side="left")
        ttk.Button(
            buttons, text="保存为模板…", command=self.save_requirements
        ).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="关闭", command=self.close).pack(side="right")
        ttk.Label(
            buttons,
            text="未识别到的字段会保留当前模板原值",
            style="Hint.TLabel",
        ).pack(side="right", padx=(0, 12))

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
    # 解析与展示
    # ------------------------------------------------------------------

    def _parse(self) -> bool:
        text = self.input_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning(
                "识别格式要求", "请先粘贴格式要求文本。", parent=self.top
            )
            return False

        result = parse_requirements(text, self.base_template)
        self.parsed = result
        self._last_parsed_text = text
        self._render(result)

        count = len(result["recognized"])
        if count:
            self.status_var.set(
                f"识别到 {count} 条格式描述，涉及 {len(result['labels'])} 类段落"
            )
            self.logger(f"智能识别格式要求：{count} 条描述，{len(result['labels'])} 类段落")
        else:
            self.status_var.set("未识别到可用的格式项，请检查文本写法")
        return count > 0

    def _render(self, result: dict) -> None:
        children = self.result_tree.get_children()
        if children:
            self.result_tree.delete(*children)
        for item in result["recognized"]:
            self.result_tree.insert(
                "",
                "end",
                values=(item["label"], item["display"], item["source"]),
            )
        self._set_warnings(result["warnings"])

    def _set_warnings(self, warnings) -> None:
        self.warning_text.configure(state="normal")
        self.warning_text.delete("1.0", "end")
        if warnings:
            self.warning_text.insert(
                "1.0", "\n".join(f"• {warning}" for warning in warnings)
            )
        else:
            self.warning_text.insert("1.0", "未发现需要提示的问题。")
        self.warning_text.configure(state="disabled")

    def _load_example(self) -> None:
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", EXAMPLE_TEXT)
        self._parse()

    def _clear(self) -> None:
        self.input_text.delete("1.0", "end")
        children = self.result_tree.get_children()
        if children:
            self.result_tree.delete(*children)
        self._set_warnings([])
        self.parsed = None
        self.status_var.set("粘贴格式要求后点击【识别格式要求】")

    # ------------------------------------------------------------------
    # 应用与保存
    # ------------------------------------------------------------------

    def _ensure_template(self):
        text = self.input_text.get("1.0", "end-1c").strip()
        if (
            self.parsed is None
            or not self.parsed.get("labels")
            or text != self._last_parsed_text
        ):
            if not self._parse():
                return None
        return copy.deepcopy(self.parsed["template"])

    def apply_requirements(self) -> None:
        template = self._ensure_template()
        if template is None:
            return
        try:
            self.on_apply(template)
        except Exception as exc:
            messagebox.showerror("应用失败", str(exc), parent=self.top)
            return
        self.status_var.set("已应用到当前模板（仅本次任务生效），可继续保存为模板")
        self.logger("已应用智能识别的格式要求（仅本次任务生效）。")

    def save_requirements(self) -> None:
        template = self._ensure_template()
        if template is None:
            return

        default_name = "requirements_" + datetime.now().strftime("%Y%m%d_%H%M")
        name = simpledialog.askstring(
            "保存为模板",
            "请输入模板名称（可含中文，不能包含路径符号或括号）：",
            initialvalue=default_name,
            parent=self.top,
        )
        if name is None:
            return
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

        try:
            save_template(self.project_root, name, template, overwrite=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.top)
            return

        self.logger(f"已保存智能识别模板：{path.name}")
        try:
            self.on_saved(name, template)
        except Exception:
            pass
        messagebox.showinfo(
            "保存成功",
            f"模板已保存为 {name}.json，并切换为当前模板。",
            parent=self.top,
        )
        self.close()

    def close(self) -> None:
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.top.destroy()
