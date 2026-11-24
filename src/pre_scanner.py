"""预扫描模块：只读文档，采集段落排版特征并推断全局特征。

输出内容：
- 正文基准字号（按字符数加权的字号直方图，频次最高者）
- 候选标题字号集合
- 是否包含 Word 原生标题样式（Heading 1/标题 1，或段落大纲级别）
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from docx.document import Document as DocumentObject
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

DEFAULT_BODY_SIZE = 12.0

_NATIVE_HEADING_RE = re.compile(r"^(?:heading|标题)\s*([1-9])$", re.IGNORECASE)
_TITLE_STYLE_NAMES = {"title", "标题"}


def _build_align_names() -> dict:
    mapping = {}
    for attr, value in (
        ("LEFT", "left"),
        ("CENTER", "center"),
        ("RIGHT", "right"),
        ("JUSTIFY", "justify"),
        ("DISTRIBUTE", "distribute"),
        ("JUSTIFY_MED", "justify"),
        ("JUSTIFY_HI", "justify"),
        ("JUSTIFY_LOW", "justify"),
    ):
        member = getattr(WD_ALIGN_PARAGRAPH, attr, None)
        if member is not None:
            mapping[member] = value
    return mapping


_ALIGN_NAMES = _build_align_names()


@dataclass
class ParagraphInfo:
    """单个段落的排版特征（只读）。"""

    index: int
    text: str
    size: float
    bold: bool
    indent_pt: float
    style_name: str
    alignment: str
    is_empty: bool
    native_level: Optional[int]
    has_runs: bool
    has_math: bool = False


@dataclass
class ScanResult:
    """整篇文档的预扫描结果。"""

    paragraphs: list
    body_size: float
    heading_sizes: list
    has_native_heading: bool
    histogram: dict
    warnings: list
    table_count: int
    textbox_count: int
    source: str = ""
    table_samples: list = field(default_factory=list)
    math_paragraph_count: int = 0

    @property
    def paragraph_count(self) -> int:
        return len(self.paragraphs)


def _style_chain(style):
    guard = 0
    while style is not None and guard < 10:
        yield style
        style = style.base_style
        guard += 1


def _style_font_size(style) -> Optional[float]:
    for item in _style_chain(style):
        try:
            size = item.font.size
        except Exception:
            size = None
        if size is not None:
            return float(size.pt)
    return None


def _style_bold(style) -> Optional[bool]:
    for item in _style_chain(style):
        try:
            value = item.font.bold
        except Exception:
            value = None
        if value is not None:
            return bool(value)
    return None


def _native_level(paragraph) -> Optional[int]:
    """识别 Word 原生标题样式或大纲级别。"""
    for style in _style_chain(paragraph.style):
        name = (style.name or "").strip()
        match = _NATIVE_HEADING_RE.match(name)
        if match:
            return min(int(match.group(1)), 3)
        if name.lower() in _TITLE_STYLE_NAMES:
            return 1

    p_pr = paragraph._p.pPr
    if p_pr is not None:
        outline = p_pr.find(qn("w:outlineLvl"))
        if outline is not None:
            raw = outline.get(qn("w:val"))
            if raw is not None and raw.isdigit():
                level = int(raw) + 1
                # 9 表示正文级别，忽略
                if 1 <= level <= 8:
                    return min(level, 3)
    return None


def _resolve_size(paragraph, runs, style_size: Optional[float]) -> float:
    weights = defaultdict(float)
    for run in runs:
        text = run.text
        if not text or not text.strip():
            continue
        size = run.font.size.pt if run.font.size is not None else style_size
        if size is None:
            continue
        weights[round(float(size) * 2) / 2] += len(text)
    if weights:
        return max(weights.items(), key=lambda item: (item[1], -item[0]))[0]
    if style_size is not None:
        return round(style_size, 1)
    return DEFAULT_BODY_SIZE


def _resolve_bold(paragraph, runs, style_bold: Optional[bool]) -> bool:
    total = 0
    bold_chars = 0
    for run in runs:
        text = run.text
        if not text:
            continue
        total += len(text)
        is_bold = run.bold if run.bold is not None else bool(style_bold)
        if is_bold:
            bold_chars += len(text)
    return total > 0 and (bold_chars / total) >= 0.6


def _resolve_indent(paragraph) -> float:
    indent = paragraph.paragraph_format.first_line_indent
    if indent is None:
        for style in _style_chain(paragraph.style):
            try:
                indent = style.paragraph_format.first_line_indent
            except Exception:
                indent = None
            if indent is not None:
                break
    if indent is None:
        return 0.0
    return round(float(indent.pt), 2)


class PreScanner:
    """全文预扫描器。只读取文档，不修改任何内容。"""

    def scan(self, doc: DocumentObject, source: str = "") -> ScanResult:
        paragraphs = []
        histogram = defaultdict(float)
        warnings = []

        for index, paragraph in enumerate(doc.paragraphs):
            text = paragraph.text or ""
            runs = list(paragraph.runs)
            style_size = _style_font_size(paragraph.style)
            style_bold = _style_bold(paragraph.style)
            size = _resolve_size(paragraph, runs, style_size)
            bold = _resolve_bold(paragraph, runs, style_bold)
            indent = _resolve_indent(paragraph)
            alignment = _ALIGN_NAMES.get(paragraph.alignment, "")
            is_empty = not text.strip()
            native_level = _native_level(paragraph)
            try:
                has_math = bool(
                    paragraph._p.xpath(".//*[local-name()='oMath']")
                )
            except Exception:
                has_math = False

            info = ParagraphInfo(
                index=index,
                text=text,
                size=size,
                bold=bold,
                indent_pt=indent,
                style_name=(paragraph.style.name if paragraph.style is not None else "") or "",
                alignment=alignment,
                is_empty=is_empty,
                native_level=native_level,
                has_runs=bool(runs),
                has_math=has_math,
            )
            paragraphs.append(info)

            if not is_empty:
                histogram[round(size * 2) / 2] += max(len(text), 1)

        if histogram:
            body_size = max(histogram.items(), key=lambda item: (item[1], -item[0]))[0]
        else:
            body_size = DEFAULT_BODY_SIZE

        heading_sizes = sorted(
            (size for size in histogram if size >= body_size + 0.5),
            reverse=True,
        )
        has_native_heading = any(info.native_level is not None for info in paragraphs)

        table_count = len(doc.tables)
        try:
            textbox_count = len(doc.element.body.xpath(".//w:txbxContent"))
        except Exception:
            textbox_count = 0

        table_samples = []
        seen_samples = set()
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    sample = (cell.text or "").strip()
                    if not sample or sample in seen_samples:
                        continue
                    seen_samples.add(sample)
                    table_samples.append(sample[:60])
                    if len(table_samples) >= 3:
                        break
                if len(table_samples) >= 3:
                    break
            if len(table_samples) >= 3:
                break

        math_paragraph_count = sum(1 for info in paragraphs if info.has_math)

        if table_count:
            warnings.append(
                f"文档包含 {table_count} 个表格；表格结构保留，表格内文字将按「表格」格式统一"
            )
        if textbox_count:
            warnings.append(f"检测到 {textbox_count} 个文本框，文本框内文字已跳过")
        if math_paragraph_count:
            warnings.append(
                f"检测到 {math_paragraph_count} 个含数学公式的段落，已自动保护以保留公式"
            )
        if paragraphs and not has_native_heading:
            warnings.append("未检测到 Word 原生标题样式，无样式段落将使用启发式推断")
        if not paragraphs:
            warnings.append("文档中没有可处理的段落")

        return ScanResult(
            paragraphs=paragraphs,
            body_size=body_size,
            heading_sizes=heading_sizes,
            has_native_heading=has_native_heading,
            histogram=dict(sorted(histogram.items())),
            warnings=warnings,
            table_count=table_count,
            textbox_count=textbox_count,
            source=source,
            table_samples=table_samples,
            math_paragraph_count=math_paragraph_count,
        )
