"""Markdown 文档解析与结构规范化。

只调整 Markdown 的结构与空白：标题标记、块间空行、行尾空白；
表格、代码块、数学公式、YAML 元数据等内容保持原样。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .format_engine import Modification
from .structure_detector import (
    ALL_LABELS,
    HIGH,
    LABEL_BODY,
    LABEL_H1,
    LABEL_H2,
    LABEL_H3,
    ParagraphResult,
)

MARKDOWN_SUFFIXES = {".md", ".markdown"}

LABEL_TABLE = "表格"
LABEL_CODE = "代码块"
LABEL_MATH = "数学公式"
LABEL_LIST = "列表"
LABEL_QUOTE = "引用"
LABEL_HR = "分隔线"

_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_QUOTE_RE = re.compile(r"^\s*>\s?")
_HR_RE = re.compile(r"^\s*(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$")
_SETEXT_RE = re.compile(r"^\s*(=+|-+)\s*$")


def _heading_label(level: int) -> str:
    if level <= 1:
        return LABEL_H1
    if level == 2:
        return LABEL_H2
    return LABEL_H3


def _label_level(label: str):
    return {LABEL_H1: 1, LABEL_H2: 2, LABEL_H3: 3}.get(label)


def _join_preview(lines) -> str:
    return " ".join(line.strip() for line in lines if line.strip())


def _code_preview(lines) -> str:
    if not lines:
        return "代码块"
    opening = lines[0].strip()
    return f"{opening} …" if opening else "代码块"


def _table_sample(lines) -> str:
    for line in lines:
        if line.strip():
            return line.strip()
    return "表格"


def _list_preview(lines) -> str:
    for line in lines:
        stripped = line.strip()
        if stripped:
            return _LIST_RE.sub("", stripped, count=1).strip()
    return "列表"


def _quote_preview(lines) -> str:
    for line in lines:
        stripped = _QUOTE_RE.sub("", line, count=1).strip()
        if stripped:
            return stripped
    return "引用"


def _is_table_separator(line: str) -> bool:
    return bool(_TABLE_SEPARATOR_RE.match(line))


def _starts_block(lines, index: int) -> bool:
    line = lines[index]
    if not line.strip():
        return True
    if _FENCE_RE.match(line) or _HEADING_RE.match(line):
        return True
    if _LIST_RE.match(line) or _QUOTE_RE.match(line) or _HR_RE.match(line):
        return True
    if line.strip().startswith("$$"):
        return True
    if index + 1 < len(lines):
        if _is_table_separator(lines[index + 1]) and "|" in line:
            return True
        if (
            _SETEXT_RE.match(lines[index + 1])
            and line.strip()
            and not _HR_RE.match(line)
        ):
            return True
    return False


@dataclass
class MarkdownBlock:
    index: int
    kind: str
    label: str
    lines: list
    text: str
    level: int = 0
    setext: bool = False


@dataclass
class MarkdownDocument:
    source: str
    blocks: list
    warnings: list
    table_count: int
    table_samples: list
    original_text: str


@dataclass
class MarkdownScan:
    body_size: float = 0.0
    warnings: list = field(default_factory=list)
    table_count: int = 0
    table_samples: list = field(default_factory=list)
    source: str = ""
    paragraphs: list = field(default_factory=list)


def parse_markdown(path) -> MarkdownDocument:
    source = Path(path).resolve()
    text = source.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    warnings = []
    blocks = []
    table_samples = []
    count = len(lines)
    index = 0

    if count and lines[0].strip() == "---":
        closing = None
        for position in range(1, count):
            if lines[position].strip() in ("---", "..."):
                closing = position
                break
        if closing is not None:
            blocks.append(
                MarkdownBlock(
                    index=0,
                    kind="front_matter",
                    label=LABEL_BODY,
                    lines=lines[: closing + 1],
                    text="YAML 元数据",
                )
            )
            index = closing + 1
        else:
            warnings.append("检测到未闭合的 YAML 元数据，按普通内容处理")

    while index < count:
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        fence_match = _FENCE_RE.match(line)
        if fence_match:
            fence = fence_match.group(2)
            char = fence[0]
            minimum = len(fence)
            block_lines = [line]
            position = index + 1
            closed = False
            closing_re = re.compile(
                r"^\s*" + re.escape(char) + "{" + str(minimum) + r",}\s*$"
            )
            while position < count:
                block_lines.append(lines[position])
                if closing_re.match(lines[position]):
                    closed = True
                    position += 1
                    break
                position += 1
            if not closed:
                warnings.append("检测到未闭合的代码围栏，已保留到文件结尾")
            blocks.append(
                MarkdownBlock(
                    index=len(blocks),
                    kind="code",
                    label=LABEL_CODE,
                    lines=block_lines,
                    text=_code_preview(block_lines),
                )
            )
            index = position
            continue

        stripped = line.strip()
        if stripped.startswith("$$"):
            block_lines = [line]
            closed = stripped.count("$$") >= 2 and len(stripped) > 2
            position = index + 1
            if not closed:
                while position < count:
                    block_lines.append(lines[position])
                    if "$$" in lines[position]:
                        closed = True
                        position += 1
                        break
                    position += 1
            if not closed:
                warnings.append("检测到未闭合的数学公式块，已保留到文件结尾")
            blocks.append(
                MarkdownBlock(
                    index=len(blocks),
                    kind="math",
                    label=LABEL_MATH,
                    lines=block_lines,
                    text=_join_preview(block_lines),
                )
            )
            index = position
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            content = heading_match.group(2).strip()
            blocks.append(
                MarkdownBlock(
                    index=len(blocks),
                    kind="heading",
                    label=_heading_label(level),
                    lines=[line],
                    text=content,
                    level=level,
                )
            )
            index += 1
            continue

        if (
            index + 1 < count
            and line.strip()
            and _SETEXT_RE.match(lines[index + 1])
            and not _HR_RE.match(line)
        ):
            underline = lines[index + 1].strip()
            level = 1 if underline.startswith("=") else 2
            blocks.append(
                MarkdownBlock(
                    index=len(blocks),
                    kind="heading",
                    label=_heading_label(level),
                    lines=[line, lines[index + 1]],
                    text=line.strip(),
                    level=level,
                    setext=True,
                )
            )
            index += 2
            continue

        if (
            index + 1 < count
            and "|" in line
            and _is_table_separator(lines[index + 1])
        ):
            table_lines = [line, lines[index + 1]]
            position = index + 2
            while (
                position < count
                and lines[position].strip()
                and "|" in lines[position]
            ):
                table_lines.append(lines[position])
                position += 1
            sample = _table_sample(table_lines)
            table_samples.append(sample)
            blocks.append(
                MarkdownBlock(
                    index=len(blocks),
                    kind="table",
                    label=LABEL_TABLE,
                    lines=table_lines,
                    text=sample,
                )
            )
            index = position
            continue

        if _LIST_RE.match(line):
            list_lines = [line]
            position = index + 1
            while position < count:
                candidate = lines[position]
                if not candidate.strip():
                    break
                if _LIST_RE.match(candidate) or candidate.startswith((" ", "\t")):
                    list_lines.append(candidate)
                    position += 1
                    continue
                break
            blocks.append(
                MarkdownBlock(
                    index=len(blocks),
                    kind="list",
                    label=LABEL_LIST,
                    lines=list_lines,
                    text=_list_preview(list_lines),
                )
            )
            index = position
            continue

        if _QUOTE_RE.match(line):
            quote_lines = [line]
            position = index + 1
            while position < count and _QUOTE_RE.match(lines[position]):
                quote_lines.append(lines[position])
                position += 1
            blocks.append(
                MarkdownBlock(
                    index=len(blocks),
                    kind="quote",
                    label=LABEL_QUOTE,
                    lines=quote_lines,
                    text=_quote_preview(quote_lines),
                )
            )
            index = position
            continue

        if _HR_RE.match(line):
            blocks.append(
                MarkdownBlock(
                    index=len(blocks),
                    kind="hr",
                    label=LABEL_HR,
                    lines=[line],
                    text="---",
                )
            )
            index += 1
            continue

        paragraph_lines = [line]
        position = index + 1
        while position < count and not _starts_block(lines, position):
            paragraph_lines.append(lines[position])
            position += 1
        blocks.append(
            MarkdownBlock(
                index=len(blocks),
                kind="paragraph",
                label=LABEL_BODY,
                lines=paragraph_lines,
                text=_join_preview(paragraph_lines),
            )
        )
        index = position

    return MarkdownDocument(
        source=str(source),
        blocks=blocks,
        warnings=warnings,
        table_count=len(table_samples),
        table_samples=table_samples[:3],
        original_text=text,
    )


def _block_reason(block: MarkdownBlock) -> str:
    if block.kind == "heading":
        return f"Markdown 标题（原始 {block.level} 级）"
    if block.kind == "table":
        return "Markdown 表格块（保留原结构）"
    if block.kind == "code":
        return "Markdown 代码块（保留原文）"
    if block.kind == "math":
        return "Markdown 数学公式块（保留原文）"
    if block.kind == "list":
        return "Markdown 列表块"
    if block.kind == "quote":
        return "Markdown 引用块"
    if block.kind == "hr":
        return "Markdown 分隔线"
    if block.kind == "front_matter":
        return "YAML 元数据（保留原文）"
    return "Markdown 普通段落"


def build_results(document: MarkdownDocument, overrides=None, protected=None, groups=None) -> list:
    manual = {}
    for key, label in (overrides or {}).items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        if label in ALL_LABELS:
            manual[index] = label

    protected_indices = set()
    for value in protected or []:
        try:
            protected_indices.add(int(value))
        except (TypeError, ValueError):
            continue

    group_map = {}
    for key, value in (groups or {}).items():
        try:
            index = int(key)
            group_id = int(value)
        except (TypeError, ValueError):
            continue
        if group_id > 0:
            group_map[index] = group_id

    results = []
    for block in document.blocks:
        label = block.label
        is_manual = block.index in manual
        if is_manual:
            label = manual[block.index]
        reasons = [_block_reason(block)]
        if is_manual:
            reasons.append("GUI 手动指定，覆盖自动识别结果")
        is_protected = block.index in protected_indices
        if is_protected:
            reasons.append("手动保护：保留原格式，不参与规范化")
        results.append(
            ParagraphResult(
                index=block.index,
                text=block.text,
                label=label,
                score=100,
                band=HIGH,
                reasons=reasons,
                manual=is_manual,
                protected=is_protected,
            )
        )

    by_index = {result.index: result for result in results}
    members_by_group = {}
    for index, group_id in group_map.items():
        if index in by_index:
            members_by_group.setdefault(group_id, []).append(index)
    for group_id, indices in sorted(members_by_group.items()):
        if len(indices) < 2:
            continue
        members = [by_index[index] for index in sorted(indices)]
        target_label = members[0].label
        for result in members:
            reasons = list(result.reasons)
            reasons.append(f"手动合并为同一识别块（组{group_id}）")
            by_index[result.index] = replace(
                result,
                label=target_label,
                manual=True,
                group_id=group_id,
                reasons=reasons,
            )
    return [by_index[result.index] for result in results]


def _normalized_block_lines(block: MarkdownBlock, result: ParagraphResult, warnings: set):
    if result.protected:
        return list(block.lines)

    if block.kind in ("code", "math", "front_matter"):
        return list(block.lines)

    if block.kind == "heading":
        target_level = _label_level(result.label) if result.manual else block.level
        if result.manual and result.label == LABEL_BODY:
            return [block.text.strip()]
        if result.manual and target_level is None:
            warnings.add("特殊块或特殊标签不适用于 Markdown 标题，已保持原样")
            return [line.rstrip() for line in block.lines]
        return [f"{'#' * max(1, target_level)} {block.text.strip()}"]

    if block.kind == "paragraph":
        if result.manual:
            target_level = _label_level(result.label)
            if target_level:
                content = " ".join(line.strip() for line in block.lines)
                return [f"{'#' * target_level} {content}"]
            if result.label != LABEL_BODY:
                warnings.add("特殊块或特殊标签不适用于 Markdown 段落，已保持原样")
        # 保留 Markdown 行内硬换行（行尾两个空格）等原始内容
        return list(block.lines)

    if result.manual and result.label not in (block.label, LABEL_BODY):
        warnings.add("表格/代码/公式/列表等特殊块已保持原结构，不参与改判")
    return list(block.lines)


def write_markdown(document: MarkdownDocument, results: list, output_path) -> tuple:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    by_index = {result.index: result for result in results}
    warnings = set()
    modifications = []
    output_blocks = []

    for block in document.blocks:
        result = by_index.get(block.index)
        if result is None:
            output_blocks.append(list(block.lines))
            continue
        normalized = _normalized_block_lines(block, result, warnings)
        output_blocks.append(normalized)
        if normalized != block.lines:
            detail = "规范化 Markdown 结构"
            if block.kind == "heading":
                if block.setext:
                    detail = "Setext 标题转换为 ATX 标题"
                elif result.manual and result.label == LABEL_BODY:
                    detail = "移除标题标记，改为正文"
                elif result.manual:
                    detail = f"改判为{result.label}并规范化标题标记"
                else:
                    detail = "规范化标题标记与行尾空白"
            elif block.kind == "paragraph":
                detail = f"改判为{result.label}并添加标题标记"
            modifications.append(
                Modification(
                    paragraph_index=block.index,
                    label=result.label,
                    detail=detail,
                )
            )

    rendered_lines = []
    for lines in output_blocks:
        if not lines:
            continue
        if rendered_lines:
            rendered_lines.append("")
        rendered_lines.extend(lines)
    rendered = "\n".join(rendered_lines).rstrip() + "\n"
    target.write_text(rendered, encoding="utf-8")

    original = document.original_text.replace("\r\n", "\n").strip()
    if rendered.strip() != original:
        modifications.append(
            Modification(
                paragraph_index=-1,
                label="Markdown",
                detail="规范化块间空行与文件结尾",
            )
        )

    return modifications, sorted(warnings)
