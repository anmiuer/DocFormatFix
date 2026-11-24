"""流水线模块：串联预扫描、结构识别、格式校准、报告生成。

CLI 与 GUI 共用本模块，避免重复业务逻辑。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from docx import Document

from . import markdown_handler, reporter
from .file_handler import FileHandler
from .format_engine import FormatEngine, load_template
from .pre_scanner import PreScanner, ScanResult
from .structure_detector import StructureDetector, summarize_results


@dataclass
class PreviewResult:
    source: str
    scan: ScanResult
    results: list
    stats: dict
    warnings: list
    threshold: int = 70
    infer_headings: bool = True
    template: str = ""
    source_type: str = "docx"


@dataclass
class FileResult:
    source: str
    output: str = None
    error: str = None
    results: list = field(default_factory=list)
    modifications: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    cleanup_actions: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    source_type: str = "docx"


@dataclass
class TaskResult:
    backup_dir: str
    files: list
    skipped: list
    template: str
    threshold: int
    created_at: str
    txt_report: str = None
    html_report: str = None


class Pipeline:
    def __init__(
        self,
        project_root,
        template_name: str,
        threshold: int = 70,
        infer_headings: bool = True,
        cleanup: dict = None,
        html_report: bool = False,
        logger=None,
        template_data: dict = None,
        backup_dir=None,
        output_dir=None,
    ):
        self.root = Path(project_root).resolve()
        self.file_handler = FileHandler(
            self.root,
            backup_dir=backup_dir,
            output_dir=output_dir,
        )
        self.template_name = template_name
        if template_data is not None:
            if not isinstance(template_data, dict) or not template_data:
                raise ValueError("自定义模板数据为空或格式不正确")
            self.template = copy.deepcopy(template_data)
        else:
            self.template = load_template(self.root, template_name)
        self.threshold = max(50, min(100, int(threshold)))
        self.infer_headings = bool(infer_headings)
        self.cleanup = cleanup or {}
        self.html_report = bool(html_report)
        self.logger = logger or (lambda message: None)

        self.scanner = PreScanner()
        self.detector = StructureDetector(self.threshold, self.infer_headings)
        self.engine = FormatEngine(self.template, self.cleanup)

    def detect(
        self,
        scan: ScanResult,
        overrides: dict = None,
        protected: set = None,
        groups: dict = None,
    ) -> list:
        return self.detector.detect(
            scan,
            overrides or {},
            protected or set(),
            groups or {},
        )

    def preview_file(
        self,
        path,
        overrides: dict = None,
        protected: set = None,
        groups: dict = None,
    ) -> PreviewResult:
        source = Path(path).resolve()
        if source.suffix.lower() in markdown_handler.MARKDOWN_SUFFIXES:
            document = markdown_handler.parse_markdown(source)
            results = markdown_handler.build_results(
                document,
                overrides or {},
                protected or set(),
                groups or {},
            )
            scan = markdown_handler.MarkdownScan(
                warnings=list(document.warnings),
                table_count=document.table_count,
                table_samples=list(document.table_samples),
                source=str(source),
            )
            return PreviewResult(
                source=str(source),
                scan=scan,
                results=results,
                stats=summarize_results(results),
                warnings=list(document.warnings),
                threshold=self.threshold,
                infer_headings=self.infer_headings,
                template=self.template_name,
                source_type="markdown",
            )

        doc = Document(str(source))
        scan = self.scanner.scan(doc, source=str(source))
        results = self.detector.detect(
            scan,
            overrides or {},
            protected or set(),
            groups or {},
        )
        return PreviewResult(
            source=str(source),
            scan=scan,
            results=results,
            stats=summarize_results(results),
            warnings=list(scan.warnings),
            threshold=self.threshold,
            infer_headings=self.infer_headings,
            template=self.template_name,
            source_type="docx",
        )

    def execute(
        self,
        paths,
        base_dir,
        overrides_map: dict = None,
        skipped: list = None,
        protected_map: dict = None,
        groups_map: dict = None,
    ) -> TaskResult:
        sources = []
        seen = set()
        for item in paths:
            resolved = str(Path(item).resolve())
            if resolved not in seen:
                seen.add(resolved)
                sources.append(Path(resolved))
        if not sources:
            raise ValueError("没有可处理的文件")

        base = Path(base_dir).resolve() if base_dir else sources[0].parent
        overrides_map = self._normalize_key_map(overrides_map)
        protected_map = self._normalize_key_map(protected_map)
        groups_map = self._normalize_key_map(groups_map)

        try:
            backup = self.file_handler.create_backup(sources, base)
        except Exception as exc:
            raise RuntimeError(f"自动备份失败，已中止处理：{exc}") from exc
        self.logger(f"已备份 {len(sources)} 个原始文档到 {backup.directory}")

        file_results = []
        for source in sources:
            self.logger(f"处理中：{source.name}")
            try:
                suffix = source.suffix.lower()
                if suffix in markdown_handler.MARKDOWN_SUFFIXES:
                    document = markdown_handler.parse_markdown(source)
                    results = markdown_handler.build_results(
                        document,
                        overrides_map.get(str(source), {}),
                        protected_map.get(str(source), set()),
                        groups_map.get(str(source), {}),
                    )
                    output = self.file_handler.output_path(source, base)
                    modifications, md_warnings = markdown_handler.write_markdown(
                        document,
                        results,
                        output,
                    )
                    file_results.append(
                        FileResult(
                            source=str(source),
                            output=str(output),
                            results=results,
                            modifications=modifications,
                            warnings=list(document.warnings) + list(md_warnings),
                            cleanup_actions=[],
                            stats=summarize_results(results),
                            source_type="markdown",
                        )
                    )
                    self.logger(
                        f"完成：{source.name} → {output.name}（修改 {len(modifications)} 处）"
                    )
                    continue

                doc = Document(str(source))
                scan = self.scanner.scan(doc, source=str(source))
                results = self.detector.detect(
                    scan,
                    overrides_map.get(str(source), {}),
                    protected_map.get(str(source), set()),
                    groups_map.get(str(source), {}),
                )
                modifications, engine_warnings, cleanup_actions = self.engine.apply(doc, results)
                output = self.file_handler.output_path(source, base)
                doc.save(str(output))

                stats = summarize_results(results)
                warnings = list(scan.warnings) + list(engine_warnings)
                file_results.append(
                    FileResult(
                        source=str(source),
                        output=str(output),
                        results=results,
                        modifications=modifications,
                        warnings=warnings,
                        cleanup_actions=cleanup_actions,
                        stats=stats,
                        source_type="docx",
                    )
                )
                self.logger(
                    f"完成：{source.name} → {output.name}（修改 {len(modifications)} 段）"
                )
            except Exception as exc:
                file_results.append(
                    FileResult(
                        source=str(source),
                        error=str(exc),
                        stats={"total": 0, "high": 0, "medium": 0, "low": 0, "protected": 0, "groups": 0, "labels": {}},
                    )
                )
                self.logger(f"失败：{source.name}：{exc}")

        task = TaskResult(
            backup_dir=str(backup.directory),
            files=file_results,
            skipped=list(skipped or []),
            template=self.template_name,
            threshold=self.threshold,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        try:
            task.txt_report = reporter.write_txt(self.file_handler.report_path("txt"), task)
            self.logger(f"TXT 报告：{task.txt_report}")
        except Exception as exc:
            self.logger(f"TXT 报告生成失败：{exc}")
        if self.html_report:
            try:
                task.html_report = reporter.write_html(self.file_handler.report_path("html"), task)
                self.logger(f"HTML 报告：{task.html_report}")
            except Exception as exc:
                self.logger(f"HTML 报告生成失败：{exc}")

        return task

    @staticmethod
    def _normalize_key_map(mapping: dict) -> dict:
        normalized = {}
        for key, value in (mapping or {}).items():
            try:
                normalized[str(Path(key).resolve())] = value
            except (OSError, ValueError):
                normalized[str(key)] = value
        return normalized
