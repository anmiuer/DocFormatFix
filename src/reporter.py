"""报告模块：生成 txt 与可选 HTML 可视化报告。"""

from __future__ import annotations

import html
from pathlib import Path

from .structure_detector import preview_text

_BAND_TEXT = {"high": "高", "medium": "中", "low": "低"}


def _band_text(band: str) -> str:
    return _BAND_TEXT.get(band, band)


def _totals(task) -> dict:
    stats = {
        "files": len(task.files),
        "success": 0,
        "failed": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "protected": 0,
        "groups": 0,
    }
    for file in task.files:
        if file.error:
            stats["failed"] += 1
        else:
            stats["success"] += 1
        for key in ("high", "medium", "low", "protected", "groups"):
            stats[key] += file.stats.get(key, 0)
    return stats


def write_txt(path, task) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    totals = _totals(task)
    lines = [
        "DocFormatFix 文档格式校准报告",
        "=" * 76,
        f"生成时间：{task.created_at}",
        f"使用模板：{task.template}",
        f"置信阈值：{task.threshold}",
        f"备份目录：{task.backup_dir or '（未创建）'}",
        f"文件总数：{totals['files']}（成功 {totals['success']}，失败 {totals['failed']}）",
        f"段落统计：共 {totals['high'] + totals['medium'] + totals['low']} 段"
        f"（高 {totals['high']} / 中 {totals['medium']} / 低 {totals['low']}）"
        + (f" | 保护 {totals['protected']} 段" if totals["protected"] else "")
        + (f" | 合并组 {totals['groups']} 个" if totals["groups"] else ""),
    ]
    if task.skipped:
        lines.append("")
        lines.append("跳过文件：")
        for item in task.skipped:
            lines.append(f"  - {item}")

    for file in task.files:
        lines.append("")
        lines.append("=" * 76)
        lines.append(f"文件：{file.source}")
        lines.append(f"输出：{file.output or '（未生成）'}")
        doc_type = (
            "Markdown（结构规范化）"
            if getattr(file, "source_type", "docx") == "markdown"
            else "Word（排版模板）"
        )
        lines.append(f"文档类型：{doc_type}")
        if file.error:
            lines.append(f"状态：失败 - {file.error}")
        else:
            lines.append("状态：成功")
        lines.append(
            "统计：共 {total} 段 | 高置信 {high} | 中置信 {medium} | 低置信 {low}"
            " | 保护 {protected} | 合并组 {groups}".format(
                total=file.stats.get("total", 0),
                high=file.stats.get("high", 0),
                medium=file.stats.get("medium", 0),
                low=file.stats.get("low", 0),
                protected=file.stats.get("protected", 0),
                groups=file.stats.get("groups", 0),
            )
        )

        if file.warnings:
            lines.append("")
            lines.append("警告 / 风险条目：")
            for warning in file.warnings:
                lines.append(f"  ! {warning}")

        if file.results:
            lines.append("")
            lines.append("段落识别明细：")
            for result in file.results:
                lines.append(
                    f"  [{result.index + 1:04d}] {result.label} | "
                    f"置信度 {result.score}（{_band_text(result.band)}）| {result.source_text}"
                )
                if result.text.strip():
                    lines.append(f"         文本：{preview_text(result.text, 80)}")
                if result.reasons:
                    lines.append(f"         原因：{'；'.join(result.reasons)}")

        if file.modifications:
            lines.append("")
            lines.append("格式修改记录：")
            for modification in file.modifications:
                position = (
                    "全局"
                    if modification.paragraph_index < 0
                    else f"段落 {modification.paragraph_index + 1}"
                )
                lines.append(
                    f"  - {position} "
                    f"[{modification.label}] {modification.detail}"
                )

        if file.cleanup_actions:
            lines.append("")
            lines.append("清理记录：")
            for action in file.cleanup_actions:
                lines.append(f"  - {action}")

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target)


def _render_html_result(result) -> str:
    text = html.escape(preview_text(result.text, 100))
    reasons = html.escape("；".join(result.reasons or []))
    row_class = "protected" if getattr(result, "protected", False) else result.band
    return (
        f"<tr class=\"{row_class}\">"
        f"<td>{result.index + 1}</td>"
        f"<td>{html.escape(result.label)}</td>"
        f"<td>{result.score}</td>"
        f"<td><span class=\"badge {result.band}\">{_band_text(result.band)}</span></td>"
        f"<td>{html.escape(result.source_text)}</td>"
        f"<td class=\"text\">{text}</td>"
        f"<td class=\"reason\">{reasons}</td>"
        "</tr>"
    )


def _render_file(file) -> str:
    status = (
        f"<span class=\"status fail\">失败：{html.escape(file.error)}</span>"
        if file.error
        else "<span class=\"status ok\">成功</span>"
    )
    parts = [
        "<section class=\"file-card\">",
        f"<h2>{html.escape(Path(file.source).name)}</h2>",
        f"<p class=\"path\">源文件：{html.escape(file.source)}</p>",
        f"<p class=\"path\">输出文件：{html.escape(file.output or '（未生成）')}</p>",
        f"<p class=\"meta\">文档类型："
        f"{'Markdown（结构规范化）' if getattr(file, 'source_type', 'docx') == 'markdown' else 'Word（排版模板）'}"
        f"　{status}　共 {file.stats.get('total', 0)} 段，"
        f"高 {file.stats.get('high', 0)} / 中 {file.stats.get('medium', 0)} / "
        f"低 {file.stats.get('low', 0)}"
        + (f"　保护 {file.stats.get('protected', 0)} 段" if file.stats.get("protected") else "")
        + (f"　合并组 {file.stats.get('groups', 0)} 个" if file.stats.get("groups") else "")
        + f"</p>",
    ]

    if file.warnings:
        parts.append("<div class=\"warnings\"><strong>警告 / 风险</strong><ul>")
        for warning in file.warnings:
            parts.append(f"<li>{html.escape(warning)}</li>")
        parts.append("</ul></div>")

    if file.results:
        parts.append(
            "<table><thead><tr>"
            "<th>#</th><th>类型</th><th>置信度</th><th>分级</th>"
            "<th>来源</th><th>文本</th><th>判定依据</th>"
            "</tr></thead><tbody>"
        )
        parts.extend(_render_html_result(result) for result in file.results)
        parts.append("</tbody></table>")

    if file.modifications:
        parts.append(f"<h3>格式修改记录（{len(file.modifications)} 项）</h3><ul class=\"changes\">")
        for modification in file.modifications:
            position = (
                "全局"
                if modification.paragraph_index < 0
                else f"段落 {modification.paragraph_index + 1}"
            )
            parts.append(
                f"<li>{position} "
                f"<code>{html.escape(modification.label)}</code> "
                f"{html.escape(modification.detail)}</li>"
            )
        parts.append("</ul>")

    if file.cleanup_actions:
        parts.append("<h3>清理记录</h3><ul class=\"changes\">")
        for action in file.cleanup_actions:
            parts.append(f"<li>{html.escape(action)}</li>")
        parts.append("</ul>")

    parts.append("</section>")
    return "\n".join(parts)


def write_html(path, task) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    totals = _totals(task)

    skipped_html = ""
    if task.skipped:
        items = "".join(f"<li>{html.escape(item)}</li>" for item in task.skipped)
        skipped_html = f"<div class=\"warnings\"><strong>跳过文件</strong><ul>{items}</ul></div>"

    sections = "\n".join(_render_file(file) for file in task.files)
    first_output = next((file.output for file in task.files if file.output), None)
    output_dir = str(Path(first_output).parent) if first_output else "（无）"
    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DocFormatFix 校准报告</title>
<style>
  :root {{
    --bg: #f4f5f7;
    --card: #ffffff;
    --border: #e3e6ea;
    --text: #1f2329;
    --muted: #646a73;
    --green: #e8f6ea;
    --green-tag: #1e7b34;
    --yellow: #fff7dc;
    --yellow-tag: #8a6100;
    --red: #fdeaea;
    --red-tag: #b42318;
    --blue: #2f5fdf;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 14px; line-height: 1.65;
  }}
  .wrap {{ max-width: 1200px; margin: 0 auto; padding: 36px 24px 64px; }}
  h1 {{ font-size: 26px; margin: 0 0 6px; }}
  .subtitle {{ color: var(--muted); margin: 0 0 26px; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 18px; min-width: 130px;
  }}
  .card .value {{ font-size: 22px; font-weight: 700; }}
  .card .label {{ color: var(--muted); font-size: 12px; }}
  .meta-box {{
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 22px;
  }}
  .meta-box p {{ margin: 4px 0; }}
  .path {{ color: var(--muted); font-size: 12px; word-break: break-all; margin: 2px 0; }}
  .file-card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 22px; margin-bottom: 22px; overflow: hidden;
  }}
  .file-card h2 {{ margin: 0 0 8px; font-size: 19px; }}
  .file-card h3 {{ font-size: 15px; margin: 20px 0 8px; }}
  .meta {{ color: var(--muted); margin: 8px 0 14px; }}
  .status.ok {{ color: var(--green-tag); font-weight: 700; }}
  .status.fail {{ color: var(--red-tag); font-weight: 700; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }}
  th, td {{ border: 1px solid var(--border); padding: 8px 10px; vertical-align: top; }}
  th {{ background: #f8f9fa; text-align: left; font-weight: 600; }}
  tr.high td {{ background: var(--green); }}
  tr.medium td {{ background: var(--yellow); }}
  tr.low td {{ background: var(--red); }}
  tr.protected td {{ background: #eef0f3; color: #4b5563; }}
  td.text {{ max-width: 360px; word-break: break-all; }}
  td.reason {{ color: var(--muted); font-size: 12px; max-width: 280px; }}
  .badge {{
    display: inline-block; border-radius: 999px; padding: 0 9px;
    font-size: 12px; line-height: 20px; font-weight: 600;
  }}
  .badge.high {{ background: #d3efd8; color: var(--green-tag); }}
  .badge.medium {{ background: #ffe9a8; color: var(--yellow-tag); }}
  .badge.low {{ background: #f9c9c9; color: var(--red-tag); }}
  .warnings {{
    background: #fff8e6; border: 1px solid #f0d590; border-radius: 8px;
    padding: 12px 16px; margin: 14px 0;
  }}
  .warnings ul, .changes {{ margin: 6px 0 0; padding-left: 22px; }}
  .changes li {{ margin: 3px 0; }}
  code {{ background: #f1f3f5; padding: 1px 5px; border-radius: 4px; font-size: 12px; }}
  footer {{ text-align: center; color: var(--muted); font-size: 12px; margin-top: 30px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>DocFormatFix 文档格式校准报告</h1>
  <p class="subtitle">生成时间 {html.escape(task.created_at)}　·　模板 {html.escape(task.template)}　·　置信阈值 {task.threshold}</p>

  <div class="cards">
    <div class="card"><div class="value">{totals['files']}</div><div class="label">文件总数</div></div>
    <div class="card"><div class="value">{totals['success']}</div><div class="label">处理成功</div></div>
    <div class="card"><div class="value">{totals['failed']}</div><div class="label">处理失败</div></div>
    <div class="card"><div class="value">{totals['high']}</div><div class="label">高置信段落</div></div>
    <div class="card"><div class="value">{totals['medium']}</div><div class="label">中置信段落</div></div>
    <div class="card"><div class="value">{totals['low']}</div><div class="label">低置信段落</div></div>
    <div class="card"><div class="value">{totals['protected']}</div><div class="label">保护段落</div></div>
    <div class="card"><div class="value">{totals['groups']}</div><div class="label">合并组</div></div>
  </div>

  <div class="meta-box">
    <p><strong>备份目录：</strong>{html.escape(task.backup_dir or '（未创建）')}</p>
    <p><strong>输出目录：</strong>{html.escape(output_dir)}</p>
    <p><strong>说明：</strong>绿色为高置信且已自动修改；黄色为中置信，仅标记；红色为低置信，已跳过；灰色为手动保护，不参与格式化。手动修改的标签以“手动”标注，合并块以“组N”标注。</p>
  </div>

  {skipped_html}
  {sections}
  <footer>DocFormatFix · 仅修改排版，不改动正文文字 · 自动备份不可关闭</footer>
</div>
</body>
</html>
"""
    target.write_text(document, encoding="utf-8")
    return str(target)
