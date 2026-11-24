"""结构识别模块：为每个段落推断类型与置信分数。

判定顺序：
1. GUI 手动覆盖（最高优先级）
2. 空段落
3. Word 原生标题样式
4. 图注 / 表注
5. 首行缩进 > 1 磅 -> 正文
6. 多特征打分（字号、加粗、短句、段首编号、关键词、长段落/句末标点惩罚）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

from .pre_scanner import ParagraphInfo, ScanResult

LABEL_H1 = "一级标题"
LABEL_H2 = "二级标题"
LABEL_H3 = "三级标题"
LABEL_BODY = "正文"
LABEL_REF = "参考文献"
LABEL_FIG = "图注"
LABEL_TAB = "表注"
LABEL_EMPTY = "空段落"
LABEL_FORMULA = "公式"

ALL_LABELS = [
    LABEL_H1,
    LABEL_H2,
    LABEL_H3,
    LABEL_BODY,
    LABEL_REF,
    LABEL_FIG,
    LABEL_TAB,
    LABEL_EMPTY,
]
_HEADING_LABELS = {1: LABEL_H1, 2: LABEL_H2, 3: LABEL_H3}

HIGH = "high"
MEDIUM = "medium"
LOW = "low"
_BAND_TEXT = {HIGH: "高", MEDIUM: "中", LOW: "低"}

# 段首编号：仅匹配段落开头的编号，句内编号不生效
_NUMBERING_PATTERNS = [
    (3, re.compile(r"^\d+[.．]\d+[.．]\d+")),
    (2, re.compile(r"^\d+[.．]\d+(?![\d.．])")),
    (1, re.compile(r"^第[一二三四五六七八九十百零〇两\d]+[章节篇部分]")),
    (1, re.compile(r"^[一二三四五六七八九十百零〇两]+[、.．]")),
    (2, re.compile(r"^[（(][一二三四五六七八九十百零〇两]+[）)]")),
    (3, re.compile(r"^[（(]\d+[）)]")),
    (3, re.compile(r"^\d+[、.．)）](?!\d)")),
    (3, re.compile(r"^[①-⑳]")),
]

_CAPTION_RE = re.compile(r"^(图|表)\s*\d+(?:\s*[-－—.．]\s*\d+)*")
_CHINESE_L1_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百零〇两\d]+[章节篇部分]|[一二三四五六七八九十百零〇两]+[、.．])"
)
_CHINESE_L2_RE = re.compile(r"^[（(][一二三四五六七八九十百零〇两]+[）)]")
_SIMPLE_NUMBER_RE = re.compile(r"^\d+[、.．)）](?!\d)")
_SENTENCE_END = "。！？!?；;…"

_SECTION_KEYWORDS = {
    "摘要",
    "abstract",
    "目录",
    "参考文献",
    "致谢",
    "附录",
    "绪论",
    "引言",
    "前言",
    "结论",
    "结束语",
    "结语",
}

_REFERENCE_TITLES = {"参考文献", "references", "reference"}
_REFERENCE_END_TITLES = {
    "致谢",
    "附录",
    "作者简介",
    "个人简历",
    "攻读学位期间",
    "发表论文",
    "声明",
    "原创性声明",
    "使用授权",
    "摘要",
    "abstract",
    "目录",
    "结论",
    "结语",
    "结束语",
}
_REFERENCE_ENTRY_RE = re.compile(r"^\s*(?:[\[［]|\(\s*\d|（\s*\d|\d+\s*[.、．])")


@dataclass
class ParagraphResult:
    """单个段落的识别结果。"""

    index: int
    text: str
    label: str
    score: int
    band: str
    reasons: list
    manual: bool = False
    protected: bool = False
    group_id: Optional[int] = None

    @property
    def band_text(self) -> str:
        return _BAND_TEXT.get(self.band, self.band)

    @property
    def source_text(self) -> str:
        marks = []
        if self.manual:
            marks.append("手动")
        if self.protected:
            marks.append("保护")
        if self.group_id is not None:
            marks.append(f"组{self.group_id}")
        return "·".join(marks) if marks else "自动"


def preview_text(text: str, limit: int = 60) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat
    return flat[: max(limit - 1, 1)] + "…"


def detect_numbering(text: str) -> tuple:
    """返回 (候选层级, 匹配到的编号文本)，未匹配返回 (None, '')。"""
    stripped = (text or "").strip()
    for level, pattern in _NUMBERING_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return level, match.group(0)
    return None, ""


def _is_section_keyword(text: str) -> bool:
    value = (text or "").strip().rstrip("：:").strip().lower()
    return value in _SECTION_KEYWORDS


def detect_caption(info: ParagraphInfo):
    """识别图注/表注：段首为“图/表 + 编号”，且不是带句末标点的正文句子。"""
    if info.indent_pt > 1:
        return None
    text = (info.text or "").strip()
    if not text or len(text) > 80:
        return None
    match = _CAPTION_RE.match(text)
    if not match:
        return None
    if text.endswith(tuple(_SENTENCE_END)):
        return None
    label = LABEL_FIG if match.group(1) == "图" else LABEL_TAB
    score = 90 if len(text) > 45 else 95
    return label, score, [f"段首识别到{match.group(1)}编号", f"文本长度 {len(text)}"]


def _score_heading(info: ParagraphInfo, scan: ScanResult, number_level: Optional[int], keyword: bool):
    text = (info.text or "").strip()
    reasons = ["基础分 +20"]
    score = 20

    if info.size >= scan.body_size + 0.4:
        score += 30
        reasons.append(f"字号 {info.size:g}pt 高于正文 {scan.body_size:g}pt（+30）")
    if info.bold:
        score += 20
        reasons.append("加粗（+20）")

    ends_with_punct = text.endswith(tuple(_SENTENCE_END))
    if len(text) <= 30 and not ends_with_punct:
        score += 15
        reasons.append("短句且无句末标点（+15）")
    if number_level:
        score += 30
        reasons.append("段首编号匹配（+30）")
    if keyword:
        score += 25
        reasons.append("常见章节标题关键词（+25）")

    if len(text) > 40:
        score -= 20
        reasons.append("段落较长（-20）")
    if len(text) > 80:
        score -= 20
        reasons.append("段落过长（-20）")
    if ends_with_punct:
        score -= 25
        reasons.append("句末标点（-25）")

    return max(0, min(100, score)), reasons


def _band(score: int, threshold: int) -> str:
    if score >= threshold:
        return HIGH
    if score >= 60:
        return MEDIUM
    return LOW


def _normalize_section_title(text: str) -> str:
    value = (text or "").strip()
    _level, prefix = detect_numbering(value)
    if prefix:
        value = value[len(prefix):]
    return re.sub(r"[\s：:]+", "", value).lower()


def _looks_like_reference_entry(text: str) -> bool:
    return bool(_REFERENCE_ENTRY_RE.match(text or ""))


def _body_confidence(info: ParagraphInfo, heading_score: int) -> int:
    text = (info.text or "").strip()
    if info.indent_pt > 1:
        return 95
    if len(text) >= 60:
        return 90
    if text.endswith(tuple(_SENTENCE_END)):
        return 90
    if heading_score >= 45:
        return 55
    return 75


def summarize_results(results: list) -> dict:
    stats = {
        "total": len(results),
        "high": 0,
        "medium": 0,
        "low": 0,
        "protected": 0,
        "groups": 0,
        "labels": {},
    }
    group_ids = set()
    for result in results:
        stats[result.band] = stats.get(result.band, 0) + 1
        stats["labels"][result.label] = stats["labels"].get(result.label, 0) + 1
        if getattr(result, "protected", False):
            stats["protected"] += 1
        group_id = getattr(result, "group_id", None)
        if group_id is not None:
            group_ids.add(group_id)
    stats["groups"] = len(group_ids)
    return stats


class StructureDetector:
    """根据预扫描特征推断段落类型。"""

    def __init__(self, threshold: int = 70, infer_headings: bool = True):
        self.threshold = max(0, min(100, int(threshold)))
        self.infer_headings = bool(infer_headings)

    def detect(
        self,
        scan: ScanResult,
        overrides: Optional[dict] = None,
        protected: Optional[set] = None,
        groups: Optional[dict] = None,
    ) -> list:
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

        math_indices = {
            info.index
            for info in scan.paragraphs
            if getattr(info, "has_math", False)
        }

        candidates = []
        for info in scan.paragraphs:
            if info.index in manual or info.is_empty or info.native_level is not None:
                continue
            if not self.infer_headings:
                continue
            if detect_caption(info) is not None:
                continue
            if info.indent_pt > 1:
                continue
            number_level, _ = detect_numbering(info.text)
            keyword = _is_section_keyword(info.text)
            score, reasons = _score_heading(info, scan, number_level, keyword)
            candidates.append(
                {
                    "info": info,
                    "score": score,
                    "number_level": number_level,
                    "keyword": keyword,
                    "reasons": reasons,
                }
            )

        # 无编号标题按字号层级排序：最大字号 -> 一级标题
        tier_sizes = sorted(
            {
                item["info"].size
                for item in candidates
                if item["score"] >= 60 and not item["number_level"] and not item["keyword"]
            },
            reverse=True,
        )
        tier_map = {size: min(position + 1, 3) for position, size in enumerate(tier_sizes)}

        # “1.” 这类简单编号的层级取决于文档上下文：
        # 存在“一、”和“（一）”时按三级；只存在“一、”时按二级；独立使用时按一级。
        has_chinese_l1 = any(
            _CHINESE_L1_RE.match((info.text or "").strip())
            for info in scan.paragraphs
            if not info.is_empty
        )
        has_chinese_l2 = any(
            _CHINESE_L2_RE.match((info.text or "").strip())
            for info in scan.paragraphs
            if not info.is_empty
        )
        simple_number_level = 3 if (has_chinese_l1 and has_chinese_l2) else (2 if has_chinese_l1 else 1)

        detected = {}
        for item in candidates:
            info = item["info"]
            score = item["score"]
            candidate_level = item["number_level"]
            if candidate_level == 3 and _SIMPLE_NUMBER_RE.match((info.text or "").strip()):
                candidate_level = simple_number_level
            if score >= 60:
                level = candidate_level or (1 if item["keyword"] else tier_map.get(info.size, 1))
                detected[info.index] = ParagraphResult(
                    index=info.index,
                    text=info.text,
                    label=_HEADING_LABELS[level],
                    score=score,
                    band=_band(score, self.threshold),
                    reasons=item["reasons"],
                )
            else:
                confidence = _body_confidence(info, score)
                detected[info.index] = ParagraphResult(
                    index=info.index,
                    text=info.text,
                    label=LABEL_BODY,
                    score=confidence,
                    band=_band(confidence, self.threshold),
                    reasons=[f"标题评分 {score}，低于标题候选线 60，按正文处理"],
                )

        results = []
        for info in scan.paragraphs:
            if info.index in manual:
                label = manual[info.index]
                results.append(
                    ParagraphResult(
                        index=info.index,
                        text=info.text,
                        label=label,
                        score=100,
                        band=HIGH,
                        reasons=["GUI 手动指定，覆盖自动识别结果"],
                        manual=True,
                    )
                )
                continue

            if info.is_empty:
                results.append(
                    ParagraphResult(
                        index=info.index,
                        text=info.text,
                        label=LABEL_EMPTY,
                        score=100,
                        band=HIGH,
                        reasons=["空段落，仅参与清理"],
                    )
                )
                continue

            if info.native_level is not None:
                results.append(
                    ParagraphResult(
                        index=info.index,
                        text=info.text,
                        label=_HEADING_LABELS[info.native_level],
                        score=100,
                        band=HIGH,
                        reasons=["Word 原生标题样式 / 大纲级别"],
                    )
                )
                continue

            caption = detect_caption(info)
            if caption is not None:
                label, score, reasons = caption
                results.append(
                    ParagraphResult(
                        index=info.index,
                        text=info.text,
                        label=label,
                        score=score,
                        band=_band(score, self.threshold),
                        reasons=reasons,
                    )
                )
                continue

            if not self.infer_headings:
                results.append(
                    ParagraphResult(
                        index=info.index,
                        text=info.text,
                        label=LABEL_BODY,
                        score=95,
                        band=_band(95, self.threshold),
                        reasons=["未启用标题推断，无原生标题样式的段落按正文处理"],
                    )
                )
                continue

            if info.indent_pt > 1:
                results.append(
                    ParagraphResult(
                        index=info.index,
                        text=info.text,
                        label=LABEL_BODY,
                        score=95,
                        band=_band(95, self.threshold),
                        reasons=[f"首行缩进 {info.indent_pt:g}pt（>1pt），直接判定正文"],
                    )
                )
                continue

            if info.index in detected:
                results.append(detected[info.index])
                continue

            results.append(
                ParagraphResult(
                    index=info.index,
                    text=info.text,
                    label=LABEL_BODY,
                    score=75,
                    band=_band(75, self.threshold),
                    reasons=["无标题特征，按正文处理"],
                )
            )

        return self._apply_manual_marks(
            self._apply_reference_section(results),
            protected_indices,
            group_map,
            math_indices,
        )

    def _apply_manual_marks(
        self,
        results: list,
        protected_indices: set,
        group_map: dict,
        math_indices: set = None,
    ) -> list:
        """应用手动保护、数学公式自动保护与合并组。"""
        math_indices = math_indices or set()
        by_index = {result.index: result for result in results}

        for index in sorted(protected_indices | math_indices):
            result = by_index.get(index)
            if result is None:
                continue
            reasons = list(result.reasons)
            if index in math_indices:
                reasons.append("包含数学公式，自动保护以保留公式")
            if index in protected_indices:
                reasons.append("手动保护：保留原格式，不参与格式化")
            if index in math_indices:
                by_index[index] = replace(
                    result,
                    label=LABEL_FORMULA,
                    score=95,
                    band=HIGH,
                    protected=True,
                    reasons=reasons,
                )
            else:
                by_index[index] = replace(result, protected=True, reasons=reasons)

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
                    score=100,
                    band=HIGH,
                    manual=True,
                    group_id=group_id,
                    reasons=reasons,
                )

        return [by_index[result.index] for result in results]

    def _apply_reference_section(self, results: list) -> list:
        """把“参考文献”标题之后的条目统一标记为参考文献格式。"""
        start = None
        for position, result in enumerate(results):
            if _normalize_section_title(result.text) in _REFERENCE_TITLES:
                start = position
                break
        if start is None:
            return results

        end = len(results)
        for position in range(start + 1, len(results)):
            result = results[position]
            if _normalize_section_title(result.text) in _REFERENCE_END_TITLES:
                end = position
                break
            if (
                result.label in (LABEL_H1, LABEL_H2, LABEL_H3)
                and not _looks_like_reference_entry(result.text)
            ):
                end = position
                break

        for position in range(start + 1, end):
            result = results[position]
            if result.manual or result.label == LABEL_EMPTY:
                continue
            results[position] = ParagraphResult(
                index=result.index,
                text=result.text,
                label=LABEL_REF,
                score=95,
                band=_band(95, self.threshold),
                reasons=["位于参考文献章节内"],
            )
        return results
