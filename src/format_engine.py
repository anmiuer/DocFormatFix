"""格式校准引擎：加载模板并应用段落/字体格式，执行可选的清理动作。

约束：只修改排版与空白字符，不修改正文文字内容。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.run import Run

from .structure_detector import HIGH, LABEL_BODY, LABEL_EMPTY

TEMPLATE_DIR = "templates"
BUILTIN_TEMPLATES = {
    "undergraduate_thesis",
    "competition_report",
    "common_homework",
}
_TEMPLATE_NAME_RE = re.compile(r"[^\\/:*?\"<>|（）().]{1,40}")
_FIXED_SPACING_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:pt|磅)$", re.I)

_ALIGN_MAP = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}

_SENTINEL_TAGS = (
    "w:br",
    "w:drawing",
    "w:pict",
    "w:object",
    "w:sectPr",
    "w:fldChar",
    "w:instrText",
    "w:footnoteReference",
    "w:endnoteReference",
)


@dataclass
class Modification:
    paragraph_index: int
    label: str
    detail: str


def list_templates(project_root) -> list:
    directory = Path(project_root) / TEMPLATE_DIR
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def template_path(project_root, name: str) -> Path:
    return Path(project_root) / TEMPLATE_DIR / f"{name}.json"


def validate_template_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned or not _TEMPLATE_NAME_RE.fullmatch(cleaned):
        raise ValueError(
            "模板名称不能为空，且不能包含 \\ / : * ? \" < > | （ ） ( ) . "
            "等字符，长度不超过 40。"
        )
    return cleaned


def save_template(project_root, name: str, data: dict, overwrite: bool = False) -> Path:
    """保存模板 JSON；先写临时文件再替换，避免外部监听读到半截内容。"""
    name = validate_template_name(name)
    if not isinstance(data, dict) or not data:
        raise ValueError("模板内容为空或格式不正确")
    path = template_path(project_root, name)
    if path.exists() and not overwrite:
        raise FileExistsError(f"模板已存在：{path.name}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        temp = path.with_name(f".{path.name}.tmp")
        try:
            temp.write_text(payload, encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise OSError(f"写入模板失败：{path}：{exc}") from exc
    return path


def delete_template(project_root, name: str) -> Path:
    path = template_path(project_root, name)
    if not path.exists():
        raise FileNotFoundError(f"模板不存在：{path.name}")
    path.unlink()
    return path


def rename_template(project_root, old_name: str, new_name: str) -> Path:
    new_name = validate_template_name(new_name)
    source = template_path(project_root, old_name)
    if not source.exists():
        raise FileNotFoundError(f"模板不存在：{source.name}")
    if old_name == new_name:
        return source
    target = template_path(project_root, new_name)
    if target.exists():
        raise FileExistsError(f"模板已存在：{target.name}")
    source.rename(target)
    return target


def duplicate_template(project_root, source_name: str, new_name: str) -> Path:
    data = load_template(project_root, source_name)
    return save_template(project_root, new_name, data, overwrite=False)


def templates_signature(project_root) -> tuple:
    directory = Path(project_root) / TEMPLATE_DIR
    if not directory.exists():
        return ()
    signature = []
    for path in sorted(directory.glob("*.json")):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def file_signature(path) -> tuple:
    try:
        stat = Path(path).stat()
    except OSError:
        return ()
    return (stat.st_mtime_ns, stat.st_size)


def load_template(project_root, name: str) -> dict:
    path = template_path(project_root, name)
    if not path.exists():
        available = "、".join(list_templates(project_root)) or "无"
        raise FileNotFoundError(f"模板不存在：{name}.json（可用模板：{available}）")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"模板 JSON 解析失败：{path.name}：{exc}") from exc
    if not isinstance(data, dict) or not data:
        raise ValueError(f"模板内容为空或格式不正确：{path.name}")
    return data


def _unwrap(element) -> None:
    parent = element.getparent()
    if parent is None:
        return
    position = parent.index(element)
    for child in list(element):
        element.remove(child)
        parent.insert(position, child)
        position += 1
    parent.remove(element)


class FormatEngine:
    """根据识别结果应用模板格式。中/低置信段落一律跳过。"""

    def __init__(self, template: dict, cleanup_options: dict = None):
        self.template = template or {}
        self.cleanup = cleanup_options or {}
        self.last_cleanup_actions = []

    def apply(self, doc, results: list) -> tuple:
        modifications = []
        warnings = []

        for result in results:
            if result.band != HIGH or result.label == LABEL_EMPTY:
                continue
            if getattr(result, "protected", False):
                continue
            if result.index < 0 or result.index >= len(doc.paragraphs):
                continue

            paragraph = doc.paragraphs[result.index]
            spec = self.template.get(result.label) or self.template.get(LABEL_BODY)
            if not isinstance(spec, dict):
                warnings.append(f"模板缺少「{result.label}」格式规则，已跳过")
                continue
            if not list(self._iter_runs(paragraph)):
                continue
            detail = self._apply_paragraph(paragraph, spec)
            modifications.append(
                Modification(
                    paragraph_index=result.index,
                    label=result.label,
                    detail=detail,
                )
            )

        table_spec = self.template.get("表格")
        if isinstance(table_spec, dict):
            table_modification, table_warnings = self._format_tables(doc, table_spec)
            if table_modification:
                modifications.append(table_modification)
            warnings.extend(table_warnings)
        elif doc.tables:
            warnings.append("模板缺少「表格」格式，表格内文字保持不变")

        cleanup_actions = self._run_cleanup(doc)
        return modifications, warnings, cleanup_actions

    @staticmethod
    def _paragraph_has_math(paragraph) -> bool:
        try:
            return bool(paragraph._p.xpath(".//*[local-name()='oMath']"))
        except Exception:
            return False

    def _format_tables(self, doc, spec: dict):
        """保留表格结构，只统一单元格段落与文字格式。"""
        table_count = 0
        paragraph_count = 0
        math_skipped = 0

        def walk(tables) -> None:
            nonlocal table_count, paragraph_count, math_skipped
            for table in tables:
                table_count += 1
                for row in table.rows:
                    for cell in row.cells:
                        for paragraph in cell.paragraphs:
                            if self._paragraph_has_math(paragraph):
                                math_skipped += 1
                                continue
                            if not paragraph.text.strip() or not paragraph.runs:
                                continue
                            self._apply_paragraph(paragraph, spec)
                            paragraph_count += 1
                        walk(cell.tables)

        walk(doc.tables)

        warnings = []
        if math_skipped:
            warnings.append(
                f"表格内有 {math_skipped} 个含数学公式的段落，已保持原样"
            )

        if not paragraph_count:
            return None, warnings
        detail = (
            f"处理 {table_count} 个表格、{paragraph_count} 个单元格段落"
        )
        if math_skipped:
            detail += f"，跳过 {math_skipped} 个含公式段落"
        return Modification(-1, "表格", detail), warnings

    def _apply_paragraph(self, paragraph, spec: dict) -> str:
        size = float(spec.get("size", 12))
        font = spec.get("font")
        bold = spec.get("bold")
        line_spacing = spec.get("line_spacing")

        parts = []
        fmt = paragraph.paragraph_format
        if line_spacing is not None:
            fixed = self._fixed_spacing_points(line_spacing)
            if fixed is not None:
                fmt.line_spacing = Pt(fixed)
                parts.append(f"行距固定值 {fixed:g}磅")
            else:
                fmt.line_spacing = float(line_spacing)
                parts.append(f"行距 {line_spacing}")
        if "space_before" in spec:
            fmt.space_before = Pt(float(spec["space_before"]))
        if "space_after" in spec:
            fmt.space_after = Pt(float(spec["space_after"]))
        if "indent" in spec:
            chars = self._indent_chars(spec.get("indent"))
            if chars > 0:
                fmt.first_line_indent = Pt(size * chars)
                parts.append(f"首行缩进{chars:g}字符")
            else:
                fmt.first_line_indent = Pt(0)
        align = spec.get("align")
        if align in _ALIGN_MAP:
            fmt.alignment = _ALIGN_MAP[align]
            parts.append(f"对齐 {align}")

        if font:
            parts.append(font)
        parts.append(f"{size:g}pt")
        if bold is True:
            parts.append("加粗")
        elif bold is False:
            parts.append("不加粗")

        for run in self._iter_runs(paragraph):
            if font:
                self._set_font(run, font)
            run.font.size = Pt(size)
            self._set_complex_size(run, size)
            if bold is not None:
                run.font.bold = bool(bold)

        return "，".join(parts)

    @staticmethod
    def _fixed_spacing_points(value):
        """把 "20pt" / "20磅" 解析为固定行距磅值；倍数返回 None。"""
        if isinstance(value, str):
            match = _FIXED_SPACING_RE.match(value.strip())
            if match:
                return float(match.group(1))
        return None

    @staticmethod
    def _indent_chars(value) -> float:
        if value is True:
            return 2.0
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, min(10.0, float(value)))
        return 0.0

    @staticmethod
    def _iter_runs(paragraph):
        seen = set()
        for run in paragraph.runs:
            seen.add(id(run._element))
            yield run
        for hyperlink in paragraph._p.xpath("./w:hyperlink"):
            for run_element in hyperlink.xpath("./w:r"):
                if id(run_element) not in seen:
                    seen.add(id(run_element))
                    yield Run(run_element, paragraph)

    @staticmethod
    def _set_font(run, font_name: str) -> None:
        run.font.name = font_name
        r_pr = run._element.get_or_add_rPr()
        r_fonts = r_pr.find(qn("w:rFonts"))
        if r_fonts is None:
            r_fonts = OxmlElement("w:rFonts")
            r_pr.insert(0, r_fonts)
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            if r_fonts.get(qn(attr)) is not None:
                del r_fonts.attrib[qn(attr)]
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            r_fonts.set(qn(attr), font_name)

    @staticmethod
    def _set_complex_size(run, size: float) -> None:
        r_pr = run._element.get_or_add_rPr()
        size_cs = r_pr.find(qn("w:szCs"))
        if size_cs is None:
            size_cs = OxmlElement("w:szCs")
            r_pr.append(size_cs)
        size_cs.set(qn("w:val"), str(int(round(size * 2))))

    def _run_cleanup(self, doc) -> list:
        actions = []
        if self.cleanup.get("revisions"):
            stats = self._remove_revisions(doc)
            total = sum(stats.values())
            if total:
                actions.append(
                    "清理修订/隐藏文字："
                    f"插入修订 {stats['insertions']} 处、"
                    f"删除修订 {stats['deletions']} 处、"
                    f"隐藏文字 {stats['hidden']} 处"
                )
        if self.cleanup.get("spaces"):
            count = self._normalize_spaces(doc)
            if count:
                actions.append(f"清理多余空格：修改 {count} 个文本片段")
        if self.cleanup.get("merge_empty"):
            count = self._merge_empty_paragraphs(doc)
            if count:
                actions.append(f"合并连续空段落：删除 {count} 个多余空段")
        self.last_cleanup_actions = actions
        return actions

    @staticmethod
    def _remove_revisions(doc) -> dict:
        body = doc.element.body
        stats = {"insertions": 0, "deletions": 0, "hidden": 0}

        for element in body.xpath(".//w:ins | .//w:moveTo"):
            _unwrap(element)
            stats["insertions"] += 1
        for element in body.xpath(".//w:del | .//w:moveFrom"):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
                stats["deletions"] += 1
        for vanish in body.xpath(".//w:rPr/w:vanish"):
            value = vanish.get(qn("w:val"))
            if value is not None and value.lower() in ("0", "false", "off"):
                continue
            r_pr = vanish.getparent()
            run = r_pr.getparent() if r_pr is not None else None
            if run is not None and run.getparent() is not None:
                run.getparent().remove(run)
                stats["hidden"] += 1
        return stats

    @staticmethod
    def _normalize_spaces(doc) -> int:
        changed = 0
        for paragraph in doc.paragraphs:
            runs = list(paragraph.runs)
            text_runs = [run for run in runs if run.text]
            if not text_runs:
                continue
            first = text_runs[0]
            last = text_runs[-1]
            for run in runs:
                text = run.text
                if not text:
                    continue
                new_text = text.replace("\u3000", " ")
                new_text = re.sub(r" {2,}", " ", new_text)
                if run is first:
                    new_text = new_text.lstrip(" ")
                if run is last:
                    new_text = new_text.rstrip(" ")
                if new_text != text:
                    run.text = new_text
                    changed += 1
        return changed

    @classmethod
    def _merge_empty_paragraphs(cls, doc) -> int:
        removed = 0
        previous_empty = False
        for paragraph in list(doc.paragraphs):
            if cls._is_removable_empty(paragraph):
                if previous_empty:
                    element = paragraph._element
                    parent = element.getparent()
                    if parent is not None:
                        parent.remove(element)
                        removed += 1
                else:
                    previous_empty = True
            else:
                previous_empty = False
        return removed

    @staticmethod
    def _is_removable_empty(paragraph) -> bool:
        if (paragraph.text or "").strip():
            return False
        for tag in _SENTINEL_TAGS:
            try:
                if paragraph._p.xpath(f".//{tag}"):
                    return False
            except Exception:
                return False
        return True
