"""DocFormatFix 命令行入口。

示例：
    python main.py --dir ./tests/test_docs --template competition_report
    python main.py --file ./论文.docx --template undergraduate_thesis --html
    python main.py --list-backups
    python main.py --restore latest
    python main.py --restore ./backup/20261120_2124
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import __version__  # noqa: E402
from src.file_handler import FileHandler  # noqa: E402
from src.format_engine import list_templates  # noqa: E402
from src.pipeline import Pipeline  # noqa: E402

DEFAULT_SETTINGS = {
    "confidence_threshold": 70,
    "default_template": "competition_report",
    "html_report": False,
    "backup_dir": "backup",
    "output_dir": "output",
    "cleanup": {"merge_empty": False, "revisions": False, "spaces": False},
}


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    settings["cleanup"] = dict(DEFAULT_SETTINGS["cleanup"])
    path = ROOT / "config" / "settings.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in (
                    "confidence_threshold",
                    "default_template",
                    "html_report",
                    "backup_dir",
                    "output_dir",
                ):
                    if key in data:
                        settings[key] = data[key]
                if isinstance(data.get("cleanup"), dict):
                    settings["cleanup"].update(data["cleanup"])
        except (json.JSONDecodeError, OSError) as exc:
            print(f"警告：读取 config/settings.json 失败，使用默认设置：{exc}")
    return settings


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}")


def build_parser() -> argparse.ArgumentParser:
    templates = list_templates(ROOT)
    parser = argparse.ArgumentParser(
        prog="DocFormatFix",
        description="docx / Markdown 文档结构识别与格式校准（Word 只修改排版，Markdown 规范化结构）",
    )
    parser.add_argument("--version", action="version", version=f"DocFormatFix {__version__}")
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="FILE",
        help="待处理 .docx / .md 文件，可重复",
    )
    parser.add_argument("--dir", action="append", default=[], metavar="DIR", help="待处理文件夹，可重复")
    parser.add_argument(
        "--template",
        choices=templates or None,
        help=f"Word 排版模板（可用：{'、'.join(templates) or '无'}）",
    )
    parser.add_argument("--threshold", type=int, metavar="N", help="置信阈值 50~100，默认 70")
    parser.add_argument("--no-infer", action="store_true", help="关闭标题层级推断，仅使用 Word 原生标题样式")
    parser.add_argument("--html", action="store_true", help="生成 HTML 可视化报告")
    parser.add_argument(
        "--backup-dir",
        metavar="DIR",
        help="自定义备份目录（默认读取 config/settings.json）",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        help="自定义输出目录（默认读取 config/settings.json）",
    )
    parser.add_argument(
        "--cleanup",
        nargs="*",
        choices=["merge_empty", "revisions", "spaces"],
        default=None,
        metavar="ITEM",
        help="附加清理：merge_empty（连续空段落）/ revisions（修订与隐藏文字）/ spaces（多余空格）",
    )
    parser.add_argument("--list-backups", action="store_true", help="列出历史备份")
    parser.add_argument("--restore", metavar="BACKUP_DIR", help="恢复指定备份目录，latest 表示最近一次")
    return parser


def handle_list_backups(handler: FileHandler) -> int:
    backups = handler.list_backups()
    if not backups:
        print("暂无历史备份。")
        return 0
    print(f"备份根目录：{handler.backup_root}\n")
    for info in backups:
        flag = "" if info.manifest_ok else "（缺少 manifest，无法恢复）"
        print(f"{info.directory.name}  文件数 {info.file_count}  创建时间 {info.created_at} {flag}")
        for entry in info.entries[:20]:
            print(f"    - {entry.source or '（原路径未知）'}")
        if info.file_count > 20:
            print(f"    ... 其余 {info.file_count - 20} 个文件")
    return 0


def handle_restore(handler: FileHandler, target: str) -> int:
    if target.lower() == "latest":
        backups = handler.list_backups()
        if not backups:
            print("没有可恢复的备份。")
            return 2
        backup_dir = backups[0].directory
    else:
        backup_dir = Path(target)
    try:
        restored, missing = handler.restore_backup(backup_dir)
    except Exception as exc:
        print(f"恢复失败：{exc}")
        return 1

    print(f"已从备份恢复 {len(restored)} 个文档：{backup_dir}")
    for path in restored:
        print(f"  - {path}")
    if missing:
        print(f"警告：{len(missing)} 个备份文件缺失，未恢复。")
    return 0 if restored and not missing else 1


def main(argv=None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    backup_dir = args.backup_dir or settings.get("backup_dir") or "backup"
    output_dir = args.output_dir or settings.get("output_dir") or "output"
    try:
        handler = FileHandler(ROOT, backup_dir=backup_dir, output_dir=output_dir)
    except RuntimeError as exc:
        print(f"错误：{exc}")
        return 2

    if args.list_backups:
        return handle_list_backups(handler)
    if args.restore:
        return handle_restore(handler, args.restore)

    if not args.file and not args.dir:
        parser.error("请至少提供 --file 或 --dir（也可使用 --list-backups / --restore）")

    templates = list_templates(ROOT)
    if not templates:
        print("错误：templates 目录中没有可用模板。")
        return 2

    template = args.template or settings.get("default_template") or templates[0]
    if template not in templates:
        print(f"错误：模板 {template} 不存在，可用模板：{'、'.join(templates)}")
        return 2

    threshold = args.threshold if args.threshold is not None else int(settings.get("confidence_threshold", 70))
    if not 50 <= threshold <= 100:
        print("错误：--threshold 必须在 50~100 之间。")
        return 2

    if args.cleanup is not None:
        cleanup = {key: key in args.cleanup for key in ("merge_empty", "revisions", "spaces")}
    else:
        cleanup = {
            key: bool(settings.get("cleanup", {}).get(key, False))
            for key in ("merge_empty", "revisions", "spaces")
        }

    files = []
    skipped = []
    for target in list(args.dir) + list(args.file):
        try:
            found, skips = handler.discover(target)
        except FileNotFoundError as exc:
            print(f"错误：{exc}")
            return 2
        files.extend(found)
        skipped.extend(skips)

    unique = []
    seen = set()
    for path in files:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    files = unique

    if not files:
        print("未找到可处理的 .docx / .md 文件。")
        for item in skipped:
            print(f"  跳过 {item.path}：{item.reason}")
        return 2

    try:
        base_dir = Path(os.path.commonpath([str(path.parent) for path in files]))
    except ValueError:
        base_dir = files[0].parent

    print("DocFormatFix 文档格式校准")
    print(f"模板：{template}　阈值：{threshold}　推断标题：{'否' if args.no_infer else '是'}")
    print(f"待处理：{len(files)} 个文件　跳过：{len(skipped)} 个文件")
    for item in skipped[:20]:
        print(f"  跳过 {item.path}：{item.reason}")
    if len(skipped) > 20:
        print(f"  ... 其余 {len(skipped) - 20} 个跳过项")

    pipeline = Pipeline(
        ROOT,
        template,
        threshold=threshold,
        infer_headings=not args.no_infer,
        cleanup=cleanup,
        html_report=args.html or bool(settings.get("html_report", False)),
        logger=log,
        backup_dir=backup_dir,
        output_dir=output_dir,
    )

    try:
        task = pipeline.execute(
            files,
            base_dir,
            overrides_map={},
            skipped=[f"{item.path}：{item.reason}" for item in skipped],
        )
    except RuntimeError as exc:
        print(f"错误：{exc}")
        return 1

    successes = [file for file in task.files if not file.error]
    failures = [file for file in task.files if file.error]
    print("")
    print(f"处理完成：成功 {len(successes)}，失败 {len(failures)}")
    print(f"备份目录：{task.backup_dir}")
    print(f"输出目录：{handler.output_root}")
    if task.txt_report:
        print(f"TXT 报告：{task.txt_report}")
    if task.html_report:
        print(f"HTML 报告：{task.html_report}")
    for file in failures:
        print(f"  失败 {file.source}：{file.error}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
