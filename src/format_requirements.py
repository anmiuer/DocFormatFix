"""把自然语言格式要求解析为排版模板字段。

纯规则解析，不依赖网络或大模型。支持常见的“标签：规格”写法、
逐行规格、制表符/竖线表格，以及“一级标题”“正文”等常见同义标签。

示例输入::

    一级标题：黑体，三号，加粗，段前12磅，段后12磅，1.5倍行距
    正文：宋体，小四，首行缩进2字符，行距固定值20磅
    图注：宋体，五号，居中

解析结果只包含识别到的字段；调用方可以把结果合并进现有模板。
"""

from __future__ import annotations

import copy
import re

_LABEL_ALIASES = {
    "一级标题": [
        "一级标题",
        "1级标题",
        "大标题",
        "章标题",
        "章名",
        "标题一",
        "标题1",
        "heading 1",
        "heading1",
        "h1",
    ],
    "二级标题": [
        "二级标题",
        "2级标题",
        "节标题",
        "标题二",
        "标题2",
        "heading 2",
        "heading2",
        "h2",
    ],
    "三级标题": [
        "三级标题",
        "3级标题",
        "小节标题",
        "标题三",
        "标题3",
        "heading 3",
        "heading3",
        "h3",
    ],
    "正文": [
        "正文文字",
        "正文内容",
        "正文",
        "body",
    ],
    "参考文献": [
        "参考文献",
        "引用文献",
        "references",
        "reference",
    ],
    "表格": [
        "表格内文字",
        "表内文字",
        "表格文字",
        "表格内容",
        "表格",
        "table",
    ],
    "图注": [
        "图片说明",
        "图题",
        "图注",
        "figure caption",
        "figure",
        "fig",
    ],
    "表注": [
        "表格说明",
        "表题",
        "表注",
        "table caption",
    ],
}

_ALIAS_TO_LABEL = {}
for _label, _aliases in _LABEL_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_LABEL[_alias.lower()] = _label

_ALIAS_PATTERN = "|".join(
    re.escape(alias) for alias in sorted(_ALIAS_TO_LABEL, key=len, reverse=True)
)
_SECTION_RE = re.compile(
    rf"({_ALIAS_PATTERN})\s*(?:的)?(?:格式|要求|设置|样式|字体|排版)?\s*"
    r"(?::|\s+|用|为)",
    re.IGNORECASE,
)

_CN_SIZE_MAP = {
    "初号": 42,
    "小初": 36,
    "一号": 26,
    "小一": 24,
    "二号": 22,
    "小二": 18,
    "三号": 16,
    "小三": 15,
    "四号": 14,
    "小四": 12,
    "五号": 10.5,
    "小五": 9,
    "六号": 7.5,
    "小六": 6.5,
    "七号": 5.5,
    "小七": 5,
    "八号": 5,
}

_KNOWN_FONTS = [
    "方正小标宋简体",
    "方正仿宋_GB2312",
    "方正黑体_GB2312",
    "仿宋_GB2312",
    "楷体_GB2312",
    "华文中宋",
    "华文宋体",
    "华文仿宋",
    "华文楷体",
    "微软雅黑",
    "Times New Roman",
    "Courier New",
    "Arial",
    "Calibri",
    "Cambria",
    "Georgia",
    "Garamond",
    "Tahoma",
    "Verdana",
    "Consolas",
    "新宋体",
    "宋体",
    "黑体",
    "楷体",
    "仿宋",
    "等线",
    "幼圆",
    "隶书",
]

_ALIGN_PATTERNS = [
    (re.compile(r"两端对齐|分散对齐|justify", re.I), "justify"),
    (re.compile(r"居中对齐|居中|中心对齐|center", re.I), "center"),
    (re.compile(r"左对齐|靠左|左端对齐|居左|left", re.I), "left"),
    (re.compile(r"右对齐|靠右|右端对齐|居右|right", re.I), "right"),
]

_ALIGN_TEXT = {
    "left": "左对齐",
    "center": "居中",
    "right": "右对齐",
    "justify": "两端对齐",
}

_UNSUPPORTED_KEYWORDS = {
    "页边距": "页面边距",
    "页眉": "页眉",
    "页脚": "页脚",
    "页码": "页码",
    "纸张": "纸张大小",
    "A4": "纸张大小",
    "装订线": "装订线",
    "分栏": "分栏",
    "水印": "水印",
    "字符间距": "字符间距",
    "字间距": "字符间距",
    "首字下沉": "首字下沉",
    "底纹": "底纹",
    "表格边框": "表格边框",
    "最小值": "行距最小值",
    "段前分页": "段前分页",
}

_LINE_SPACING_PATTERNS = [
    (re.compile(r"固定值\s*(?:为|是)?\s*:?\s*(\d+(?:\.\d+)?)\s*(?:磅|pt)", re.I), "fixed"),
    (re.compile(r"行(?:间)?距\s*(?:为|是)?\s*:?\s*(\d+(?:\.\d+)?)\s*(?:磅|pt)", re.I), "fixed"),
    (re.compile(r"行(?:间)?距\s*(?:为|是)?\s*:?\s*(\d+(?:\.\d+)?)\s*倍", re.I), "multiple"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*倍行(?:间)?距", re.I), "multiple"),
    (re.compile(r"行(?:间)?距\s*(?:为|是)?\s*:?\s*(\d+(?:\.\d+)?)\s*(?=$|[,;])", re.I), "multiple"),
    (re.compile(r"单倍行(?:间)?距"), "one"),
    (re.compile(r"(?:双倍|两倍)\s*行(?:间)?距"), "two"),
    (re.compile(r"多倍行(?:间)?距\s*(?:为|是)?\s*:?\s*(\d+(?:\.\d+)?)", re.I), "multiple"),
]

_SPACE_BOTH_RE = re.compile(
    r"段前\s*(?:和|与|及|/|,)?\s*段后\s*(?:各|均|都)?\s*(?:为|是)?\s*:?\s*"
    r"(\d+(?:\.\d+)?)\s*(磅|pt|行)",
    re.I,
)
_SPACE_BEFORE_RE = re.compile(
    r"段前\s*(?:间距|距离)?\s*(?:为|是)?\s*:?\s*(\d+(?:\.\d+)?)\s*(磅|pt|行)",
    re.I,
)
_SPACE_AFTER_RE = re.compile(
    r"段后\s*(?:间距|距离)?\s*(?:为|是)?\s*:?\s*(\d+(?:\.\d+)?)\s*(磅|pt|行)",
    re.I,
)

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def parse_requirements(text: str, base_template: dict = None) -> dict:
    """解析格式要求文本。

    返回::

        {
            "labels": {标签: 识别字段},
            "template": 与 base_template 合并后的完整模板,
            "recognized": [{"label", "fields", "display", "source"}],
            "warnings": [文本警告],
        }
    """
    normalized = _normalize_text(text)
    warnings = []
    _collect_unsupported(normalized, warnings)

    recognized = []
    labels = {}

    for label, content in _split_sections(normalized):
        if not content:
            continue
        if label is None:
            label = _find_alias_label(content)
        spec = _parse_fields(content, warnings, label)
        if not spec:
            if label:
                warnings.append(
                    f"「{label}」未识别到可用的格式项：{_shorten(content)}"
                )
            continue
        if label is None:
            label = "正文"
            warnings.append(
                f"未标注段落类型的要求已按「正文」处理：{_shorten(content)}"
            )
        merged = dict(labels.get(label) or {})
        merged.update(spec)
        labels[label] = merged
        recognized.append(
            {
                "label": label,
                "fields": dict(spec),
                "display": describe_spec(spec),
                "source": _shorten(content, 120),
            }
        )

    template = copy.deepcopy(base_template) if isinstance(base_template, dict) else {}
    for label, spec in labels.items():
        current = template.get(label)
        current = dict(current) if isinstance(current, dict) else {}
        current.update(spec)
        template[label] = current

    return {
        "labels": labels,
        "template": template,
        "recognized": recognized,
        "warnings": warnings,
    }


def describe_spec(spec: dict) -> str:
    """把模板字段描述成一行中文，供识别结果预览。"""
    if not isinstance(spec, dict):
        return ""
    parts = []
    font = spec.get("font")
    if font:
        parts.append(str(font))

    size = spec.get("size")
    if size is not None:
        try:
            parts.append(f"{float(size):g}磅")
        except (TypeError, ValueError):
            parts.append(f"{size}磅")

    bold = spec.get("bold")
    if bold is True:
        parts.append("加粗")
    elif bold is False:
        parts.append("不加粗")

    indent = spec.get("indent")
    if indent is True:
        parts.append("首行缩进2字符")
    elif isinstance(indent, (int, float)) and not isinstance(indent, bool):
        if indent > 0:
            parts.append(f"首行缩进{indent:g}字符")
        else:
            parts.append("首行不缩进")

    line_spacing = spec.get("line_spacing")
    if line_spacing is not None:
        text = str(line_spacing)
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:pt|磅)", text, re.I)
        if match:
            parts.append(f"行距固定值{float(match.group(1)):g}磅")
        else:
            parts.append(f"行距{text}倍")

    if "space_before" in spec:
        parts.append(f"段前{_format_number(spec['space_before'])}磅")
    if "space_after" in spec:
        parts.append(f"段后{_format_number(spec['space_after'])}磅")

    align = spec.get("align")
    if align in _ALIGN_TEXT:
        parts.append(_ALIGN_TEXT[align])

    return "、".join(parts)


# ----------------------------------------------------------------------
# 文本切分
# ----------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u3000", " ").replace("\t", " ")
    for source, target in (("：", ":"), ("，", ","), ("；", ";"), ("、", ",")):
        normalized = normalized.replace(source, target)
    return normalized


def _split_sections(text: str):
    sections = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        sections.extend(_split_line(line))
    return sections


def _split_line(line: str):
    stripped = line.strip()
    if stripped.startswith("|"):
        cells = [
            cell.strip().strip("*` ")
            for cell in stripped.strip("|").split("|")
        ]
        if len(cells) >= 2:
            label = _lookup_label(cells[0])
            if label:
                content = ",".join(cell for cell in cells[1:] if cell)
                return [(label, content)]
        return []

    stripped = re.sub(
        r"^(?:[-*•·]\s+|\d+[.)、]\s+|\(\d+\)\s+)",
        "",
        stripped,
    )

    matches = []
    for match in _SECTION_RE.finditer(stripped):
        if _acceptable_prefix(stripped[: match.start()]):
            matches.append(match)
    if not matches:
        return [(None, stripped)]

    sections = []
    for position, match in enumerate(matches):
        label = _ALIAS_TO_LABEL.get(match.group(1).lower())
        end = matches[position + 1].start() if position + 1 < len(matches) else len(stripped)
        content = stripped[match.end() : end].strip(" \t,:;")
        content = re.sub(r"^[-*•·]\s*", "", content)
        sections.append((label, content))
    return sections


def _acceptable_prefix(prefix: str) -> bool:
    prefix = prefix.strip()
    if not prefix:
        return True
    if re.fullmatch(r"[-*•·\s]+", prefix):
        return True
    return prefix[-1] in "。;,.!?:"


def _lookup_label(text: str):
    return _ALIAS_TO_LABEL.get((text or "").strip().lower())


def _find_alias_label(text: str):
    found = []
    for alias in sorted(_ALIAS_TO_LABEL, key=len, reverse=True):
        if re.search(re.escape(alias), text, re.I):
            found.append(_ALIAS_TO_LABEL[alias])
    unique = []
    for label in found:
        if label not in unique:
            unique.append(label)
    if len(unique) == 1:
        return unique[0]
    return None


# ----------------------------------------------------------------------
# 字段解析
# ----------------------------------------------------------------------


def _parse_fields(content: str, warnings: list, label: str = None) -> dict:
    text = content
    spec = {}
    prefix = f"「{label}」" if label else ""

    # 行距（先处理，避免“行距20磅”被当成字号）
    for pattern, kind in _LINE_SPACING_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if kind == "fixed":
            value = _clean_number(float(match.group(1)))
            spec["line_spacing"] = f"{value}pt"
        elif kind == "multiple":
            spec["line_spacing"] = _clean_number(float(match.group(1)))
        elif kind == "one":
            spec["line_spacing"] = 1
        else:
            spec["line_spacing"] = 2
        text = _mask(text, match)
        break

    # 首行缩进
    indent_match = re.search(r"(?:悬挂缩进|左缩进|右缩进)", text)
    if indent_match:
        warnings.append(f"{prefix}只支持首行缩进，「{indent_match.group(0)}」已忽略")
        text = _mask(text, indent_match)

    unit_indent = re.search(
        r"(?:首行\s*)?(?:缩进|空)\s*([0-9一二两三四五六七八九十]+)?\s*"
        r"(cm|厘米|mm|毫米|磅|pt)",
        text,
        re.I,
    )
    if unit_indent:
        warnings.append(f"{prefix}首行缩进仅支持字符数，已按 2 字符处理")
        spec["indent"] = 2
        text = _mask(text, unit_indent)
    else:
        negative_indent = re.search(
            r"(?:首行\s*)?(?:不缩进|无缩进|取消缩进|不要缩进|无需缩进)", text
        )
        if negative_indent:
            spec["indent"] = False
            text = _mask(text, negative_indent)
        else:
            positive_indent = re.search(
                r"(?:首行\s*)?(?:空|缩进)\s*([0-9一二两三四五六七八九十]+)?\s*"
                r"(?:格|字符|个字|字)?",
                text,
            )
            if positive_indent:
                token = positive_indent.group(1)
                chars = _int_from_token(token) if token else 2
                if chars is None:
                    chars = 2
                if chars > 10:
                    warnings.append(f"{prefix}首行缩进 {chars} 字符偏大，请确认")
                spec["indent"] = _clean_number(float(chars))
                text = _mask(text, positive_indent)

    # 段前 / 段后（暂存，字号确定后再把“行”换算成磅）
    space_before = None
    space_after = None
    both = _SPACE_BOTH_RE.search(text)
    if both:
        raw = (float(both.group(1)), both.group(2).lower())
        space_before = raw
        space_after = raw
        text = _mask(text, both)
    else:
        before = _SPACE_BEFORE_RE.search(text)
        if before:
            space_before = (float(before.group(1)), before.group(2).lower())
            text = _mask(text, before)
        after = _SPACE_AFTER_RE.search(text)
        if after:
            space_after = (float(after.group(1)), after.group(2).lower())
            text = _mask(text, after)

    # 字号
    size = None
    # 同一段文本里“小三号”同时包含“小三”和“三号”，必须优先匹配“小X”。
    for name in sorted(
        _CN_SIZE_MAP,
        key=lambda item: (len(item), item.startswith("小")),
        reverse=True,
    ):
        if name in text:
            size = float(_CN_SIZE_MAP[name])
            text = text.replace(name, ",", 1)
            break

    explicit_size = re.search(
        r"(?:字号|字体大小|字大小)\s*(?:为|是)?\s*:?\s*(\d+(?:\.\d+)?)",
        text,
    )
    unit_size = re.search(r"(\d+(?:\.\d+)?)\s*(?:pt|磅)", text, re.I)
    if explicit_size:
        size = float(explicit_size.group(1))
        text = _mask(text, explicit_size)
    if unit_size:
        size = float(unit_size.group(1))
        text = _mask(text, unit_size)
    if size is not None:
        if not 5 <= size <= 72:
            warnings.append(f"{prefix}字号 {size:g} 磅超出常用范围，请确认")
        spec["size"] = _clean_number(size)

    # 字体
    font, text = _extract_font(text, warnings, prefix)
    if font:
        spec["font"] = font

    # 加粗
    negative_bold = re.search(r"(?:不|无需|不用|非)\s*(?:加粗|粗体|使用粗体|bold)", text, re.I)
    if negative_bold:
        spec["bold"] = False
        text = _mask(text, negative_bold)
    else:
        positive_bold = re.search(r"加粗|粗体|bold", text, re.I)
        if positive_bold:
            spec["bold"] = True
            text = _mask(text, positive_bold)

    # 对齐
    for pattern, align in _ALIGN_PATTERNS:
        match = pattern.search(text)
        if match:
            spec["align"] = align
            text = _mask(text, match)
            break

    # 段前段后换算
    base_size = float(spec.get("size", 12))
    if space_before is not None:
        spec["space_before"] = _convert_space(
            space_before, base_size, "段前", warnings, prefix
        )
    if space_after is not None:
        spec["space_after"] = _convert_space(
            space_after, base_size, "段后", warnings, prefix
        )

    return spec


def _extract_font(text: str, warnings: list, prefix: str):
    found = []
    for font in _KNOWN_FONTS:
        match = re.search(re.escape(font), text, re.I)
        if match:
            found.append((match.start(), font))

    if found:
        found.sort()
        chinese = [item for item in found if _has_cjk(item[1])]
        western = [item for item in found if not _has_cjk(item[1])]
        chosen = chinese[0][1] if chinese else found[0][1]
        if chinese and western:
            warnings.append(
                f"{prefix}同时提到中文与西文字体，模板仅支持一种字体，已采用「{chosen}」"
            )
        text = re.sub(re.escape(chosen), ",", text, count=1, flags=re.I)
        return chosen, text

    generic = re.search(
        r"(?:中文字体|西文字体|字体)\s*(?:为|是|使用|用)?\s*:?\s*"
        r"([A-Za-z\u4e00-\u9fa5][A-Za-z\u4e00-\u9fa5 _-]{0,30})",
        text,
        re.I,
    )
    if generic:
        candidate = re.split(
            r"(?:加粗|不加粗|字号|字体大小|居中|左对齐|右对齐|两端对齐|行距|段前|段后|首行缩进)",
            generic.group(1),
        )[0].strip(" ,;_-")
        if candidate:
            return candidate, _mask(text, generic)
    return None, text


def _convert_space(raw, base_size: float, name: str, warnings: list, prefix: str):
    value, unit = raw
    if unit == "行":
        points = round(value * base_size * 1.2, 1)
        warnings.append(
            f"{prefix}{name} {value:g} 行按约 {points:g} 磅换算（Word 行高受行距影响）"
        )
        return _clean_number(points)
    return _clean_number(value)


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------


def _mask(text: str, match) -> str:
    if match is None:
        return text
    return text[: match.start()] + "," + text[match.end() :]


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _int_from_token(token: str):
    if token is None:
        return None
    token = token.strip()
    if token.isdigit():
        return int(token)
    if token in _CN_DIGITS:
        return _CN_DIGITS[token]
    if "十" in token:
        parts = token.split("十")
        tens = _CN_DIGITS.get(parts[0], 1) if parts[0] else 1
        ones = _CN_DIGITS.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return None


def _clean_number(value):
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return int(round(number))
    return round(number, 2)


def _format_number(value) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _shorten(text: str, limit: int = 48) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _collect_unsupported(text: str, warnings: list) -> None:
    seen = set()
    for keyword, name in _UNSUPPORTED_KEYWORDS.items():
        if keyword in text and name not in seen:
            seen.add(name)
            warnings.append(f"暂不支持「{name}」相关要求，已忽略对应描述")
