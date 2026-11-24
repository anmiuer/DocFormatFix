"""格式要求解析与模板管理工具测试。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from docx import Document

from src.format_engine import (
    FormatEngine,
    delete_template,
    duplicate_template,
    list_templates,
    load_template,
    rename_template,
    save_template,
    templates_signature,
    validate_template_name,
)
from src.format_requirements import parse_requirements

BASE_TEMPLATE = {
    "一级标题": {"font": "黑体", "size": 16, "bold": True},
    "正文": {"font": "宋体", "size": 12, "bold": False, "space_before": 6},
    "图注": {"font": "宋体", "size": 10.5, "bold": False},
}


class FormatRequirementsTest(unittest.TestCase):
    def test_parse_common_requirements(self) -> None:
        text = (
            "一级标题：黑体，三号，加粗，段前12磅，段后12磅，1.5倍行距\n"
            "正文：宋体，小四，首行缩进2字符，行距固定值20磅\n"
            "图注：宋体，五号，居中\n"
        )
        result = parse_requirements(text, BASE_TEMPLATE)
        labels = result["labels"]

        self.assertEqual(labels["一级标题"]["font"], "黑体")
        self.assertEqual(labels["一级标题"]["size"], 16)
        self.assertTrue(labels["一级标题"]["bold"])
        self.assertEqual(labels["一级标题"]["line_spacing"], 1.5)
        self.assertEqual(labels["一级标题"]["space_before"], 12)

        self.assertEqual(labels["正文"]["size"], 12)
        self.assertEqual(labels["正文"]["line_spacing"], "20pt")
        self.assertEqual(labels["正文"]["indent"], 2)
        self.assertEqual(labels["图注"]["align"], "center")

        # 未识别到的字段保留原模板值
        self.assertEqual(result["template"]["正文"]["space_before"], 6)
        self.assertEqual(result["template"]["正文"]["line_spacing"], "20pt")
        self.assertEqual(result["warnings"], [])

    def test_chinese_size_priority(self) -> None:
        cases = {
            "三号宋体": 16,
            "小三号宋体": 15,
            "四号宋体": 14,
            "小四号宋体": 12,
            "二号宋体": 22,
            "小二号宋体": 18,
            "五号宋体": 10.5,
            "小五号宋体": 9,
            "初号宋体": 42,
            "小初宋体": 36,
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                result = parse_requirements(f"正文：{source}", {})
                self.assertEqual(result["labels"]["正文"]["size"], expected)

        result = parse_requirements(
            "二级标题：小三号宋体，顶格，加粗\n"
            "正文：首行缩进2字符，小四号宋体",
            {},
        )
        self.assertEqual(result["labels"]["二级标题"]["size"], 15)
        self.assertEqual(result["labels"]["正文"]["size"], 12)

    def test_markdown_table_and_unlabeled_text(self) -> None:
        table = (
            "| 段落类型 | 字体 | 字号 | 其他 |\n"
            "| --- | --- | --- | --- |\n"
            "| 一级标题 | 黑体 | 小三 | 加粗 |\n"
            "| 正文 | 宋体 | 12pt | 首行缩进两字符 |\n"
        )
        result = parse_requirements(table, {})
        self.assertEqual(result["labels"]["一级标题"]["size"], 15)
        self.assertEqual(result["labels"]["正文"]["size"], 12)
        self.assertEqual(result["labels"]["正文"]["indent"], 2)

        unlabeled = parse_requirements("宋体，小四，1.5倍行距", {})
        self.assertEqual(unlabeled["labels"]["正文"]["font"], "宋体")
        self.assertEqual(unlabeled["labels"]["正文"]["line_spacing"], 1.5)
        self.assertTrue(
            any("按「正文」处理" in warning for warning in unlabeled["warnings"])
        )

    def test_unsupported_and_fixed_spacing(self) -> None:
        text = "正文：宋体，小四。页边距上下2.54cm，行距固定值22磅。"
        result = parse_requirements(text, {})
        self.assertEqual(result["labels"]["正文"]["line_spacing"], "22pt")
        warnings = " ".join(result["warnings"])
        self.assertIn("页面边距", warnings)

    def test_engine_fixed_spacing_and_numeric_indent(self) -> None:
        doc = Document()
        paragraph = doc.add_paragraph("正文内容")
        results = [
            SimpleNamespace(band="high", label="正文", index=0, protected=False)
        ]
        template = {
            "正文": {
                "font": "宋体",
                "size": 12,
                "bold": False,
                "indent": 1.5,
                "line_spacing": "20pt",
            }
        }
        modifications, warnings, cleanup = FormatEngine(template).apply(doc, results)

        self.assertEqual(len(modifications), 1)
        self.assertEqual(warnings, [])
        self.assertEqual(cleanup, [])
        self.assertAlmostEqual(paragraph.paragraph_format.line_spacing.pt, 20, places=1)
        self.assertAlmostEqual(
            paragraph.paragraph_format.first_line_indent.pt, 18, places=1
        )
        self.assertIn("行距固定值 20磅", modifications[0].detail)
        self.assertIn("首行缩进1.5字符", modifications[0].detail)


class TemplateStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = {"正文": {"font": "宋体", "size": 12}}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_save_load_rename_duplicate_delete(self) -> None:
        save_template(self.root, "自定义模板", self.data)
        self.assertEqual(list_templates(self.root), ["自定义模板"])
        self.assertEqual(load_template(self.root, "自定义模板"), self.data)

        with self.assertRaises(FileExistsError):
            save_template(self.root, "自定义模板", self.data)

        renamed = rename_template(self.root, "自定义模板", "改名模板")
        self.assertTrue(renamed.exists())
        self.assertEqual(list_templates(self.root), ["改名模板"])

        duplicate_template(self.root, "改名模板", "副本模板")
        self.assertEqual(list_templates(self.root), ["副本模板", "改名模板"])

        signature = templates_signature(self.root)
        self.assertEqual(len(signature), 2)
        self.assertTrue(all(name.endswith(".json") for name, _mtime, _size in signature))

        delete_template(self.root, "副本模板")
        self.assertEqual(list_templates(self.root), ["改名模板"])

    def test_name_validation(self) -> None:
        self.assertEqual(validate_template_name(" 论文模板 "), "论文模板")
        for invalid in ("", "a/b", "a.b", "模板（一）"):
            with self.assertRaises(ValueError):
                validate_template_name(invalid)


if __name__ == "__main__":
    unittest.main()
