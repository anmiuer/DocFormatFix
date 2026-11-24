"""数学公式保留测试。

验证 Word OMML 公式结构不会被格式校准破坏，Markdown 公式与反斜杠
（尤其是矩阵换行 `\\\\`）在规范化后保持原样。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from docx import Document
from docx.oxml import parse_xml

from src.pipeline import Pipeline

M_NS = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'

OMML_DISPLAY = f"""<m:oMathPara {M_NS}>
  <m:oMath>
    <m:f>
      <m:num><m:r><m:t>1</m:t></m:r></m:num>
      <m:den><m:r><m:t>2</m:t></m:r></m:den>
    </m:f>
    <m:r><m:t>+</m:t></m:r>
    <m:sSup>
      <m:e><m:r><m:t>x</m:t></m:r></m:e>
      <m:sup><m:r><m:t>2</m:t></m:r></m:sup>
    </m:sSup>
    <m:r><m:t>+</m:t></m:r>
    <m:rad>
      <m:radPr><m:degHide m:val="1"/></m:radPr>
      <m:deg/>
      <m:e><m:r><m:t>y</m:t></m:r></m:e>
    </m:rad>
  </m:oMath>
</m:oMathPara>"""

OMML_INLINE = f"""<m:oMath {M_NS}>
  <m:sSup>
    <m:e><m:r><m:t>E</m:t></m:r></m:e>
    <m:sup><m:r><m:t>2</m:t></m:r></m:sup>
  </m:sSup>
</m:oMath>"""

OMML_MATRIX = f"""<m:oMathPara {M_NS}>
  <m:oMath>
    <m:m>
      <m:mr>
        <m:e><m:r><m:t>a</m:t></m:r></m:e>
        <m:e><m:r><m:t>b</m:t></m:r></m:e>
      </m:mr>
      <m:mr>
        <m:e><m:r><m:t>c</m:t></m:r></m:e>
        <m:e><m:r><m:t>d</m:t></m:r></m:e>
      </m:mr>
    </m:m>
  </m:oMath>
</m:oMathPara>"""


class FormulaPreservationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        shutil.copytree(ROOT / "templates", self.project / "templates")
        self.input_dir = self.project / "input"
        self.input_dir.mkdir()
        self.docx_path = self.input_dir / "formulas.docx"
        self.md_path = self.input_dir / "formulas.md"
        self.pipeline = Pipeline(
            self.project,
            "competition_report",
            backup_dir=str(self.project / "backup"),
            output_dir=str(self.project / "output"),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _make_docx(self) -> None:
        doc = Document()
        doc.add_paragraph("数学公式保留测试")

        display = doc.add_paragraph()
        display._p.append(parse_xml(OMML_DISPLAY))

        inline = doc.add_paragraph()
        inline.add_run("质能方程 ")
        inline._p.append(parse_xml(OMML_INLINE))
        inline.add_run(" 是相对论结论。")

        matrix = doc.add_paragraph()
        matrix._p.append(parse_xml(OMML_MATRIX))

        table = doc.add_table(rows=1, cols=1)
        cell_paragraph = table.cell(0, 0).paragraphs[0]
        cell_paragraph.add_run("矩阵：")
        cell_paragraph._p.append(parse_xml(OMML_MATRIX))

        doc.save(str(self.docx_path))

    @staticmethod
    def _tag_counts(document, names) -> dict:
        body = document.element.body
        return {
            name: len(body.xpath(f".//*[local-name()='{name}']"))
            for name in names
        }

    def test_docx_complex_omml_is_preserved(self) -> None:
        self._make_docx()
        task = self.pipeline.execute([self.docx_path], self.input_dir)
        self.assertIsNone(task.files[0].error)

        output = Document(task.files[0].output)
        counts = self._tag_counts(
            output,
            ("oMath", "oMathPara", "f", "sSup", "rad", "m"),
        )
        self.assertGreaterEqual(counts["oMath"], 4)
        self.assertGreaterEqual(counts["oMathPara"], 3)
        self.assertEqual(counts["f"], 1)
        self.assertEqual(counts["sSup"], 2)
        self.assertEqual(counts["rad"], 1)
        self.assertEqual(counts["m"], 2)

        xml = output.element.body.xml
        self.assertIn("<m:f", xml)
        self.assertIn("<m:sSup", xml)
        self.assertIn("<m:rad", xml)
        self.assertIn("<m:m", xml)

        paragraph_texts = [paragraph.text for paragraph in output.paragraphs]
        self.assertIn("质能方程  是相对论结论。", paragraph_texts)
        self.assertEqual(len(output.tables), 1)
        self.assertEqual(len(output.tables[0].rows), 1)

    def test_markdown_math_delimiters_and_backslashes_are_preserved(self) -> None:
        self.md_path.write_text(
            "# 公式测试\n\n"
            "行内公式 $E=mc^2$ 与 $\\frac{a}{b}$。\n\n"
            "$$\n"
            "\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}\n"
            "$$\n\n"
            "$$\n"
            "\\int_0^1 x^2\\,dx = \\frac{1}{3}\n"
            "$$\n",
            encoding="utf-8",
        )
        task = self.pipeline.execute([self.md_path], self.input_dir)
        self.assertIsNone(task.files[0].error)

        content = Path(task.files[0].output).read_text(encoding="utf-8")
        self.assertIn("$E=mc^2$", content)
        self.assertIn("$\\frac{a}{b}$", content)
        self.assertIn(
            "\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}",
            content,
        )
        self.assertIn(
            "\\int_0^1 x^2\\,dx = \\frac{1}{3}",
            content,
        )
        self.assertEqual(content.count("$$"), 4)


if __name__ == "__main__":
    unittest.main()
