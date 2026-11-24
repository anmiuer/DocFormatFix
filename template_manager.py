"""模板管理面板。

提供选用、重命名、复制、删除、导入与刷新；内置模板只允许复制，
模板目录被外部修改或删除时，面板会自动刷新。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from src.format_engine import (
    BUILTIN_TEMPLATES,
    delete_template,
    duplicate_template,
    list_templates,
    load_template,
    rename_template,
    save_template,
    templates_signature,
    validate_template_name,
)


class TemplateManagerDialog:
    def __init__(
        self,
        parent,
        project_root,
        active_stem: str = "",
        on_activate=None,
        on_changed=None,
        logger=None,
    ):
        self.parent = parent
        self.project_root = Path(project_root)
        self.active_stem = active_stem or ""
        self.on_activate = on_activate or (lambda stem: None)
        self.on_changed = on_changed or (lambda select: None)
        self.logger = logger or (lambda message: None)

        self._valid = {}
        self._signature = templates_signature(self.project_root)
        self._poll_job = None

        self.count_var = tk.StringVar(value="")

        self.top = tk.Toplevel(parent)
        self.top.title("模板管理")
        self.top.geometry("820x560")
        self.top.minsize(720, 480)
        self.top.transient(parent)
        self.top.grab_set()
        self.top.rowconfigure(2, weight=1)
        self.top.columnconfigure(0, weight=1)

        self._build()
        self.refresh(select=self.active_stem)
        self._schedule_poll()

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
            text=(
                "管理 templates 目录中的排版模板。内置模板可复制后修改，"
                "自定义模板可重命名、复制或删除。"
            ),
            wraplength=760,
        ).pack(side="left")

        tree_box = ttk.Frame(self.top)
        tree_box.grid(row=2, column=0, sticky="nsew", padx=14, pady=4)
        tree_box.rowconfigure(0, weight=1)
        tree_box.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tree_box,
            columns=("name", "kind", "status"),
            show="headings",
            selectmode="browse",
            height=14,
        )
        self.tree.heading("name", text="模板文件")
        self.tree.heading("kind", text="类型")
        self.tree.heading("status", text="状态")
        self.tree.column("name", width=300, anchor="w")
        self.tree.column("kind", width=110, anchor="center")
        self.tree.column("status", width=330, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("active", background="#e8f2ff")
        self.tree.tag_configure("builtin", foreground="#24538a")
        self.tree.tag_configure("bad", foreground="#b42318")
        self.tree.bind("<<TreeviewSelect>>", self._update_buttons)
        self.tree.bind("<Double-1>", lambda event: self.activate_selected())

        actions = ttk.Frame(self.top)
        actions.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 4))
        self.activate_btn = ttk.Button(
            actions, text="选用", command=self.activate_selected, style="Accent.TButton"
        )
        self.activate_btn.pack(side="left")
        self.rename_btn = ttk.Button(
            actions, text="重命名…", command=self.rename_selected
        )
        self.rename_btn.pack(side="left", padx=(8, 0))
        self.copy_btn = ttk.Button(
            actions, text="复制…", command=self.copy_selected
        )
        self.copy_btn.pack(side="left", padx=(8, 0))
        self.delete_btn = ttk.Button(
            actions, text="删除", command=self.delete_selected
        )
        self.delete_btn.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="导入…", command=self.import_template).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions, text="刷新", command=self.refresh).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions, text="打开模板目录", command=self.open_folder).pack(
            side="left", padx=(8, 0)
        )

        footer = ttk.Frame(self.top)
        footer.grid(row=4, column=0, sticky="ew", padx=14, pady=(4, 12))
        ttk.Label(footer, textvariable=self.count_var, style="Hint.TLabel").pack(
            side="left"
        )
        ttk.Button(footer, text="关闭", command=self.close).pack(side="right")
        ttk.Label(
            footer,
            text="外部修改 / 删除模板文件后会自动刷新；双击模板可直接选用",
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
    # 列表刷新
    # ------------------------------------------------------------------

    def refresh(self, select: str = None) -> None:
        self._signature = templates_signature(self.project_root)
        current = select or self._selected_stem() or self.active_stem
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)

        self._valid = {}
        templates = list_templates(self.project_root)
        for stem in templates:
            try:
                data = load_template(self.project_root, stem)
                status = f"可用 · {len(data)} 类格式"
                valid = True
            except Exception as exc:
                status = f"不可用：{exc}"
                valid = False
            self._valid[stem] = valid
            kind = "内置" if stem in BUILTIN_TEMPLATES else "自定义"
            if not valid:
                tag = "bad"
            elif stem == self.active_stem:
                tag = "active"
            elif stem in BUILTIN_TEMPLATES:
                tag = "builtin"
            else:
                tag = ""
            self.tree.insert(
                "",
                "end",
                iid=stem,
                values=(f"{stem}.json", kind, status),
                tags=(tag,) if tag else (),
            )

        if current and self.tree.exists(current):
            self.tree.selection_set(current)
            self.tree.see(current)
        elif templates:
            self.tree.selection_set(templates[0])
        self._update_buttons()
        builtin = sum(1 for stem in templates if stem in BUILTIN_TEMPLATES)
        self.count_var.set(
            f"共 {len(templates)} 个模板（内置 {builtin}，自定义 {len(templates) - builtin}）"
        )

    def _selected_stem(self) -> str:
        selection = self.tree.selection()
        return selection[0] if selection else ""

    def _update_buttons(self, event=None) -> None:
        stem = self._selected_stem()
        valid = bool(stem) and self._valid.get(stem, False)
        builtin = stem in BUILTIN_TEMPLATES
        self.activate_btn.configure(state="normal" if valid else "disabled")
        self.copy_btn.configure(state="normal" if valid else "disabled")
        self.rename_btn.configure(
            state="normal" if valid and not builtin else "disabled"
        )
        self.delete_btn.configure(
            state="normal" if valid and not builtin else "disabled"
        )

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def activate_selected(self) -> None:
        stem = self._selected_stem()
        if not stem:
            return
        if not self._valid.get(stem):
            messagebox.showerror(
                "模板不可用",
                f"模板 {stem}.json 无法读取，请先修复或删除该文件。",
                parent=self.top,
            )
            return
        try:
            accepted = self.on_activate(stem)
        except Exception as exc:
            messagebox.showerror("切换模板失败", str(exc), parent=self.top)
            return
        if accepted is False:
            return
        self.active_stem = stem
        self.refresh(select=stem)
        self.logger(f"已从模板管理面板选用：{stem}")

    def rename_selected(self) -> None:
        stem = self._selected_stem()
        if not stem:
            return
        if stem in BUILTIN_TEMPLATES:
            messagebox.showinfo(
                "内置模板",
                "内置模板不能重命名，可先【复制…】再修改副本。",
                parent=self.top,
            )
            return
        new_name = simpledialog.askstring(
            "重命名模板",
            "请输入新的模板名称：",
            initialvalue=stem,
            parent=self.top,
        )
        if new_name is None:
            return
        try:
            new_name = validate_template_name(new_name)
            if new_name == stem:
                return
            rename_template(self.project_root, stem, new_name)
        except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
            messagebox.showerror("重命名失败", str(exc), parent=self.top)
            return

        self.logger(f"模板已重命名：{stem}.json → {new_name}.json")
        if self.active_stem == stem:
            self.active_stem = new_name
        self.refresh(select=new_name)
        self._notify_changed(new_name)

    def copy_selected(self) -> None:
        stem = self._selected_stem()
        if not stem:
            return
        new_name = simpledialog.askstring(
            "复制模板",
            "请输入副本名称：",
            initialvalue=f"{stem}_副本",
            parent=self.top,
        )
        if new_name is None:
            return
        try:
            new_name = validate_template_name(new_name)
            duplicate_template(self.project_root, stem, new_name)
        except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
            messagebox.showerror("复制失败", str(exc), parent=self.top)
            return

        self.logger(f"已复制模板：{stem}.json → {new_name}.json")
        self.refresh(select=new_name)
        self._notify_changed(new_name)

    def delete_selected(self) -> None:
        stem = self._selected_stem()
        if not stem:
            return
        if stem in BUILTIN_TEMPLATES:
            messagebox.showinfo(
                "内置模板",
                "内置模板不能删除；如需精简，可删除自定义模板。",
                parent=self.top,
            )
            return
        templates = list_templates(self.project_root)
        valid_others = []
        for item in templates:
            if item == stem:
                continue
            try:
                load_template(self.project_root, item)
            except Exception:
                continue
            valid_others.append(item)
        if not valid_others:
            messagebox.showwarning(
                "至少保留一个可用模板",
                "删除后 templates 目录将没有可读取的模板，请先复制或导入一个可用模板。",
                parent=self.top,
            )
            return
        if not messagebox.askyesno(
            "删除模板",
            f"确定删除模板 {stem}.json 吗？\n删除后无法恢复。",
            parent=self.top,
        ):
            return
        try:
            delete_template(self.project_root, stem)
        except (FileNotFoundError, OSError) as exc:
            messagebox.showerror("删除失败", str(exc), parent=self.top)
            return

        self.logger(f"已删除模板：{stem}.json")
        remaining = list_templates(self.project_root)
        select = remaining[0] if remaining else None
        self.refresh(select=select)
        self._notify_changed(select)

    def import_template(self) -> None:
        selected = filedialog.askopenfilename(
            title="导入模板 JSON",
            parent=self.top,
            filetypes=[("模板 JSON", "*.json"), ("所有文件", "*.*")],
        )
        if not selected:
            return
        path = Path(selected)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("导入失败", f"无法读取模板 JSON：{exc}", parent=self.top)
            return
        if not isinstance(data, dict) or not data:
            messagebox.showerror(
                "导入失败", "模板内容为空或不是 JSON 对象。", parent=self.top
            )
            return

        name = simpledialog.askstring(
            "导入模板",
            "请输入导入后的模板名称：",
            initialvalue=path.stem,
            parent=self.top,
        )
        if name is None:
            return
        try:
            name = validate_template_name(name)
        except ValueError as exc:
            messagebox.showerror("模板名称不合法", str(exc), parent=self.top)
            return

        target = self.project_root / "templates" / f"{name}.json"
        overwrite = target.exists()
        if overwrite and not messagebox.askyesno(
            "覆盖模板",
            f"模板 {name}.json 已存在，是否覆盖？",
            parent=self.top,
        ):
            return
        try:
            save_template(self.project_root, name, data, overwrite=overwrite)
        except (OSError, ValueError) as exc:
            messagebox.showerror("导入失败", str(exc), parent=self.top)
            return

        self.logger(f"已导入模板：{path.name} → {name}.json")
        self.refresh(select=name)
        self._notify_changed(name)

    def open_folder(self) -> None:
        target = self.project_root / "templates"
        target.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.top)

    # ------------------------------------------------------------------
    # 外部变化轮询
    # ------------------------------------------------------------------

    def _schedule_poll(self) -> None:
        self._poll_job = self.top.after(1200, self._poll)

    def _poll(self) -> None:
        try:
            signature = templates_signature(self.project_root)
            if signature != self._signature:
                self._signature = signature
                self.refresh()
                self.logger("检测到模板目录变化，已刷新模板管理面板。")
                self._notify_changed(None)
        finally:
            self._schedule_poll()

    def _notify_changed(self, select) -> None:
        active = None
        try:
            active = self.on_changed(select)
        except Exception:
            pass
        if isinstance(active, str) and active and active != self.active_stem:
            self.active_stem = active
            self.refresh(select=select or active)

    def close(self) -> None:
        if self._poll_job is not None:
            try:
                self.top.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.top.destroy()
