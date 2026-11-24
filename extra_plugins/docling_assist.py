"""可选插件：使用 Docling 辅助校验标题识别结果。

本插件不属于默认依赖，默认也不会被主流程调用。
安装方式：pip install docling
"""

from __future__ import annotations

from pathlib import Path


def available() -> bool:
    """检测当前环境是否安装了 docling。"""
    try:
        import docling  # noqa: F401
    except Exception:
        return False
    return True


def suggest_heading_levels(docx_path) -> dict:
    """返回 {标题文本: 1/2/3} 的辅助建议。

    该函数为实验性能力，失败时静默返回空字典，不影响主流程。
    """
    if not available():
        return {}
    path = Path(docx_path)
    if not path.exists():
        return {}
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(str(path))
        document = result.document
        suggestions = {}
        for item, _level in document.iterate_items():
            label = getattr(item, "label", None)
            label_name = getattr(label, "value", str(label)).lower()
            if label_name not in ("section_header", "title"):
                continue
            text = (getattr(item, "text", "") or "").strip()
            if not text:
                continue
            level = getattr(item, "level", None) or 1
            suggestions[text] = max(1, min(int(level), 3))
        return suggestions
    except Exception:
        return {}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法：python extra_plugins/docling_assist.py <docx路径>")
        print("docling 可用：", available())
    else:
        print(suggest_heading_levels(sys.argv[1]))
