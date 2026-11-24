"""核心流水线单元测试。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from src.file_handler import FileHandler
from src.pipeline import Pipeline
from src.structure_detector import (
    LABEL_BODY,
    LABEL_EMPTY,
    LABEL_FIG,
    LABEL_H1,
    LABEL_H2,
    LABEL_REF,
)


def make_sample(path: Path) -> None:
    doc = Document()

    first = doc.add_paragraph()
    run = first.add_run("第一章 绪论")
    run.font.size = Pt(16)
    run.bold = True

    second = doc.add_paragraph()
    run = second.add_run("1.1 研究背景")
    run.font.size = Pt(14)
    run.bold = True

    body = doc.add_paragraph()
    run = body.add_run(
        "本文围绕文档格式自动校准展开研究，提出了一种基于多特征评分的文档结构识别方法。"
    )
    run.font.size = Pt(12)

    doc.add_paragraph("")
    doc.add_paragraph("")

    caption = doc.add_paragraph()
    run = caption.add_run("图1 系统总体架构")
    run.font.size = Pt(10.5)
    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.save(str(path))


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        shutil.copytree(ROOT / "templates", self.project / "templates")
        self.input_dir = self.project / "input"
        self.input_dir.mkdir()
        self.sample = self.input_dir / "sample.docx"
        make_sample(self.sample)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_pipeline(self, **kwargs) -> Pipeline:
        options = {
            "threshold": 70,
            "infer_headings": True,
            "cleanup": {},
            "html_report": False,
        }
        options.update(kwargs)
        return Pipeline(self.project, "competition_report", **options)

    def test_preview_labels(self) -> None:
        preview = self.make_pipeline().preview_file(self.sample)
        labels = [result.label for result in preview.results]

        self.assertEqual(labels[0], LABEL_H1)
        self.assertEqual(labels[1], LABEL_H2)
        self.assertEqual(labels[2], LABEL_BODY)
        self.assertEqual(labels[3], LABEL_EMPTY)
        self.assertEqual(labels[4], LABEL_EMPTY)
        self.assertEqual(labels[5], LABEL_FIG)
        self.assertEqual(preview.results[0].band, "high")
        self.assertGreaterEqual(preview.results[2].score, 70)
        self.assertEqual(preview.scan.body_size, 12.0)

    def test_execute_formats_and_backs_up(self) -> None:
        pipeline = self.make_pipeline(cleanup={"merge_empty": True})
        task = pipeline.execute([self.sample], self.input_dir)

        self.assertEqual(len(task.files), 1)
        self.assertIsNone(task.files[0].error)
        self.assertTrue(Path(task.files[0].output).exists())
        self.assertTrue((Path(task.backup_dir) / "manifest.json").exists())
        self.assertTrue(task.txt_report and Path(task.txt_report).exists())

        output_doc = Document(task.files[0].output)
        paragraphs = output_doc.paragraphs
        # 两个连续空段落被合并为一个
        self.assertEqual(len(paragraphs), 5)

        heading = paragraphs[0]
        self.assertEqual(heading.text, "第一章 绪论")
        self.assertEqual(heading.runs[0].font.size.pt, 16)
        self.assertEqual(heading.runs[0].font.name, "黑体")
        r_fonts = heading.runs[0]._element.rPr.rFonts
        self.assertEqual(r_fonts.get(qn("w:eastAsia")), "黑体")

        body = paragraphs[2]
        self.assertAlmostEqual(body.paragraph_format.first_line_indent.pt, 24, places=0)

        caption = paragraphs[4]
        self.assertEqual(caption.alignment, WD_ALIGN_PARAGRAPH.CENTER)
        self.assertEqual(caption.runs[0].font.size.pt, 10.5)

    def test_manual_override(self) -> None:
        pipeline = self.make_pipeline()
        preview = pipeline.preview_file(self.sample, {2: LABEL_H1})
        result = preview.results[2]

        self.assertEqual(result.label, LABEL_H1)
        self.assertTrue(result.manual)
        self.assertEqual(result.score, 100)
        self.assertEqual(result.band, "high")

    def test_no_inference_only_native_styles(self) -> None:
        pipeline = self.make_pipeline(infer_headings=False)
        preview = pipeline.preview_file(self.sample)
        self.assertEqual(preview.results[0].label, LABEL_BODY)
        self.assertEqual(preview.results[1].label, LABEL_BODY)
        self.assertEqual(preview.results[5].label, LABEL_FIG)

    def test_restore_backup(self) -> None:
        pipeline = self.make_pipeline()
        task = pipeline.execute([self.sample], self.input_dir)
        self.assertIsNone(task.files[0].error)

        doc = Document(self.sample)
        doc.paragraphs[0].runs[0].text = "已被改动"
        doc.save(str(self.sample))

        handler = FileHandler(self.project)
        restored, missing = handler.restore_backup(task.backup_dir)
        self.assertEqual(len(restored), 1)
        self.assertEqual(missing, [])

        restored_doc = Document(self.sample)
        self.assertEqual(restored_doc.paragraphs[0].text, "第一章 绪论")

    def test_discover_filters_unsupported(self) -> None:
        (self.input_dir / "old.doc").write_text("unsupported", encoding="utf-8")
        (self.input_dir / "~$temp.docx").write_text("temp", encoding="utf-8")
        (self.input_dir / "notes.md").write_text("# 标题\n", encoding="utf-8")

        handler = FileHandler(self.project)
        files, skipped = handler.discover(self.input_dir)
        self.assertEqual(len(files), 2)
        reasons = " ".join(item.reason for item in skipped)
        self.assertIn("不支持", reasons)
        self.assertIn("临时文件", reasons)

    def test_cleanup_revisions_and_spaces(self) -> None:
        doc = Document()
        paragraph = doc.add_paragraph()
        paragraph.add_run("  可见   文本  ")
        hidden = paragraph.add_run("隐藏内容")
        vanish = OxmlElement("w:vanish")
        hidden._element.get_or_add_rPr().append(vanish)
        doc.save(str(self.sample))

        pipeline = self.make_pipeline(cleanup={"revisions": True, "spaces": True})
        task = pipeline.execute([self.sample], self.input_dir)
        self.assertIsNone(task.files[0].error)

        output_doc = Document(task.files[0].output)
        self.assertEqual(output_doc.paragraphs[0].text, "可见 文本")
        actions = " ".join(task.files[0].cleanup_actions)
        self.assertIn("隐藏文字", actions)
        self.assertIn("空格", actions)

    def test_simple_numbering_level_context(self) -> None:
        doc = Document()
        paragraph = doc.add_paragraph()
        run = paragraph.add_run("1. 引言")
        run.font.size = Pt(16)
        run.bold = True
        paragraph = doc.add_paragraph()
        run = paragraph.add_run("1.1 研究背景")
        run.font.size = Pt(14)
        run.bold = True
        doc.add_paragraph("本文围绕文档格式自动校准展开研究，提出了一种基于多特征评分的文档结构识别方法。")
        doc.save(str(self.sample))

        preview = self.make_pipeline().preview_file(self.sample)
        self.assertEqual(preview.results[0].label, LABEL_H1)
        self.assertEqual(preview.results[1].label, LABEL_H2)

    def test_reference_section_detection(self) -> None:
        doc = Document()
        paragraph = doc.add_paragraph()
        run = paragraph.add_run("参考文献")
        run.font.size = Pt(16)
        run.bold = True
        doc.add_paragraph("[1] 张三. 文档格式自动校准研究[J]. 计算机应用, 2025.")
        doc.add_paragraph("[2] 李四. 文档结构识别方法[M]. 北京: 科学出版社, 2024.")
        paragraph = doc.add_paragraph()
        run = paragraph.add_run("致谢")
        run.font.size = Pt(16)
        run.bold = True
        doc.add_paragraph("感谢导师的指导。")
        doc.save(str(self.sample))

        preview = self.make_pipeline().preview_file(self.sample)
        labels = [result.label for result in preview.results]
        self.assertEqual(labels[0], LABEL_H1)
        self.assertEqual(labels[1], LABEL_REF)
        self.assertEqual(labels[2], LABEL_REF)
        self.assertEqual(labels[3], LABEL_H1)
        self.assertEqual(labels[4], LABEL_BODY)
        self.assertGreaterEqual(preview.results[1].score, 70)

    def test_custom_template_data(self) -> None:
        template_path = self.project / "templates" / "competition_report.json"
        data = json.loads(template_path.read_text(encoding="utf-8"))
        data["正文"]["size"] = 14
        data["正文"]["font"] = "仿宋"

        pipeline = self.make_pipeline(template_data=data)
        task = pipeline.execute([self.sample], self.input_dir)
        self.assertIsNone(task.files[0].error)

        output_doc = Document(task.files[0].output)
        body = output_doc.paragraphs[2]
        self.assertEqual(body.runs[0].font.size.pt, 14)
        self.assertEqual(body.runs[0].font.name, "仿宋")

    def test_protected_paragraph_keeps_original_format(self) -> None:
        doc = Document()
        paragraph = doc.add_paragraph()
        run = paragraph.add_run("第一章 绪论")
        run.font.size = Pt(16)
        run.bold = True
        paragraph = doc.add_paragraph()
        run = paragraph.add_run("特别说明：本段保留原格式。")
        run.font.size = Pt(9)
        doc.save(str(self.sample))

        pipeline = self.make_pipeline()
        task = pipeline.execute(
            [self.sample],
            self.input_dir,
            protected_map={str(self.sample): {1}},
        )
        self.assertIsNone(task.files[0].error)
        self.assertEqual(task.files[0].stats.get("protected"), 1)

        output_doc = Document(task.files[0].output)
        self.assertEqual(output_doc.paragraphs[1].runs[0].font.size.pt, 9)

    def test_merged_group_uses_first_label(self) -> None:
        doc = Document()
        paragraph = doc.add_paragraph()
        run = paragraph.add_run("第一章 绪论")
        run.font.size = Pt(16)
        run.bold = True
        paragraph = doc.add_paragraph()
        run = paragraph.add_run("作者：张三")
        run.font.size = Pt(9)
        doc.save(str(self.sample))

        pipeline = self.make_pipeline()
        preview = pipeline.preview_file(
            self.sample,
            overrides={0: LABEL_H1, 1: LABEL_BODY},
            groups={0: 1, 1: 1},
        )
        self.assertEqual(preview.results[0].label, LABEL_H1)
        self.assertEqual(preview.results[1].label, LABEL_H1)
        self.assertEqual(preview.results[0].group_id, 1)
        self.assertEqual(preview.results[1].group_id, 1)
        self.assertTrue(preview.results[1].manual)
        self.assertIn("组1", preview.results[1].source_text)

    def test_markdown_normalization_and_custom_dirs(self) -> None:
        markdown = self.input_dir / "paper.md"
        markdown.write_text(
            "#  一级标题\n\n"
            "正文第一段  \n\n"
            "## 二级标题\n\n"
            "| 列1 | 列2 |\n"
            "| --- | --- |\n"
            "| A | B |\n\n"
            "```python\n"
            "print(1)\n"
            "```\n\n"
            "$$\n"
            "E = mc^2\n"
            "$$\n",
            encoding="utf-8",
        )

        pipeline = self.make_pipeline(
            backup_dir="custom_backup",
            output_dir="custom_output",
        )
        preview = pipeline.preview_file(markdown)
        self.assertEqual(preview.source_type, "markdown")
        labels = [result.label for result in preview.results]
        self.assertIn("表格", labels)
        self.assertIn("代码块", labels)
        self.assertIn("数学公式", labels)

        task = pipeline.execute([markdown], self.input_dir)
        self.assertIsNone(task.files[0].error)
        output = Path(task.files[0].output)
        self.assertTrue(output.exists())
        self.assertTrue(
            str(output).startswith(str((self.project / "custom_output").resolve()))
        )
        self.assertTrue(
            str(Path(task.backup_dir)).startswith(
                str((self.project / "custom_backup").resolve())
            )
        )

        content = output.read_text(encoding="utf-8")
        self.assertIn("# 一级标题", content)
        self.assertIn("| 列1 | 列2 |", content)
        self.assertIn("```python", content)
        self.assertIn("E = mc^2", content)

    def test_markdown_protected_block_keeps_original(self) -> None:
        markdown = self.input_dir / "protected.md"
        markdown.write_text("#  保留标题\n\n正文  \n", encoding="utf-8")

        pipeline = self.make_pipeline()
        preview = pipeline.preview_file(markdown)
        heading_index = preview.results[0].index
        task = pipeline.execute(
            [markdown],
            self.input_dir,
            protected_map={str(markdown): {heading_index}},
        )
        self.assertIsNone(task.files[0].error)
        content = Path(task.files[0].output).read_text(encoding="utf-8")
        self.assertIn("#  保留标题", content)

    def test_docx_table_and_formula(self) -> None:
        doc = Document()
        doc.add_paragraph("第一章 绪论")
        paragraph = doc.add_paragraph("本文围绕文档格式自动校准展开研究。")
        paragraph.runs[0].font.size = Pt(12)

        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "列1"
        table.cell(0, 1).text = "列2"
        table.cell(1, 0).text = "数据A"
        table.cell(1, 1).text = "数据B"
        for row in table.rows:
            for cell in row.cells:
                for cell_paragraph in cell.paragraphs:
                    for run in cell_paragraph.runs:
                        run.font.size = Pt(8)

        math_paragraph = doc.add_paragraph()
        run = math_paragraph.add_run("公式：")
        run.font.size = Pt(20)
        math = OxmlElement("m:oMath")
        math_run = OxmlElement("m:r")
        math_text = OxmlElement("m:t")
        math_text.text = "x+y=z"
        math_run.append(math_text)
        math.append(math_run)
        math_paragraph._p.append(math)
        doc.save(str(self.sample))

        pipeline = self.make_pipeline()
        preview = pipeline.preview_file(self.sample)
        self.assertEqual(preview.scan.table_count, 1)
        self.assertEqual(preview.scan.math_paragraph_count, 1)
        formula_results = [
            result for result in preview.results if result.label == "公式"
        ]
        self.assertEqual(len(formula_results), 1)
        self.assertTrue(formula_results[0].protected)

        task = pipeline.execute([self.sample], self.input_dir)
        self.assertIsNone(task.files[0].error)
        output_doc = Document(task.files[0].output)
        self.assertEqual(len(output_doc.tables), 1)
        self.assertEqual(len(output_doc.tables[0].rows), 2)
        for row in output_doc.tables[0].rows:
            for cell in row.cells:
                cell_run = cell.paragraphs[0].runs[0]
                self.assertEqual(cell_run.font.name, "宋体")
                self.assertAlmostEqual(cell_run.font.size.pt, 10.5, places=1)
        self.assertAlmostEqual(output_doc.paragraphs[2].runs[0].font.size.pt, 20)


if __name__ == "__main__":
    unittest.main()
