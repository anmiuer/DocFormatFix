"""文件处理模块：遍历、过滤、备份、恢复与输出路径管理。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

TEMP_PREFIX = "~$"
SUPPORTED_SUFFIXES = {".docx", ".md", ".markdown"}
OFFICE_SUFFIXES = {".doc", ".docm", ".dot", ".dotx", ".pdf", ".wps", ".rtf", ".odt"}

_SKIP_DIR_NAMES = {
    ".git",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}


@dataclass
class SkippedFile:
    path: Path
    reason: str


@dataclass
class BackupEntry:
    source: str
    backup: str


@dataclass
class BackupInfo:
    directory: Path
    created_at: str
    entries: list = field(default_factory=list)
    manifest_ok: bool = False

    @property
    def file_count(self) -> int:
        return len(self.entries)


class FileHandler:
    """负责文件发现、备份与恢复。备份是强制动作。"""

    def __init__(self, project_root, backup_dir=None, output_dir=None):
        self.root = Path(project_root).resolve()
        self.backup_root = self._resolve_dir(backup_dir, "backup")
        self.output_root = self._resolve_dir(output_dir, "output")
        self.report_root = self.output_root / "reports"
        try:
            for directory in (self.backup_root, self.output_root, self.report_root):
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"无法创建备份/输出目录：{exc}") from exc

    def _resolve_dir(self, value, default_name: str) -> Path:
        if value:
            path = Path(str(value)).expanduser()
            if not path.is_absolute():
                path = self.root / path
        else:
            path = self.root / default_name
        return path.resolve()

    # ---------- 文件发现 ----------

    def discover(self, target) -> tuple:
        target = Path(target)
        files = []
        skipped = []

        if not target.exists():
            raise FileNotFoundError(f"路径不存在：{target}")

        if target.is_file():
            self._classify(target, files, skipped)
            if not files and not skipped:
                skipped.append(
                    SkippedFile(
                        target.resolve(),
                        "不支持的文件类型，仅处理 .docx / .md / .markdown",
                    )
                )
            return sorted(files, key=lambda path: str(path).lower()), skipped

        for path in sorted(target.rglob("*"), key=lambda item: str(item).lower()):
            if not path.is_file():
                continue
            if self._is_internal(path):
                continue
            relative_parts = path.relative_to(target).parts[:-1]
            if any(part.lower() in _SKIP_DIR_NAMES for part in relative_parts):
                continue
            self._classify(path, files, skipped)

        return sorted(files, key=lambda path: str(path).lower()), skipped

    def _is_internal(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
            return self.backup_root in resolved.parents or self.output_root in resolved.parents
        except OSError:
            return False

    @staticmethod
    def _classify(path: Path, files: list, skipped: list) -> None:
        suffix = path.suffix.lower()
        if path.name.startswith(TEMP_PREFIX):
            if suffix in OFFICE_SUFFIXES or suffix in SUPPORTED_SUFFIXES:
                skipped.append(SkippedFile(path.resolve(), "临时文件，已跳过"))
            return
        if suffix in SUPPORTED_SUFFIXES:
            files.append(path.resolve())
        elif suffix in OFFICE_SUFFIXES:
            skipped.append(
                SkippedFile(
                    path.resolve(),
                    f"不支持的文件类型 {suffix}，仅处理 .docx / .md / .markdown",
                )
            )

    # ---------- 备份 ----------

    def create_backup(self, files: Iterable, base_dir) -> BackupInfo:
        files = [Path(item).resolve() for item in files]
        base = Path(base_dir).resolve()
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        backup_dir = self.backup_root / stamp
        counter = 2
        while backup_dir.exists():
            backup_dir = self.backup_root / f"{stamp}_{counter}"
            counter += 1
        backup_dir.mkdir(parents=True, exist_ok=False)

        entries = []
        for source in files:
            try:
                relative = source.relative_to(base)
            except ValueError:
                relative = Path(source.name)
            if any(part == ".." for part in relative.parts):
                relative = Path(source.name)
            destination = backup_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            entries.append(BackupEntry(str(source), relative.as_posix()))

        created_at = datetime.now().isoformat(timespec="seconds")
        manifest = {
            "app": "DocFormatFix",
            "created_at": created_at,
            "base_dir": str(base),
            "files": [{"source": entry.source, "backup": entry.backup} for entry in entries],
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return BackupInfo(backup_dir, created_at, entries, manifest_ok=True)

    def list_backups(self) -> list:
        if not self.backup_root.exists():
            return []
        items = []
        directories = [path for path in self.backup_root.iterdir() if path.is_dir()]
        for directory in sorted(directories, key=lambda item: item.name, reverse=True):
            manifest_path = directory / "manifest.json"
            entries = []
            created_at = directory.name
            manifest_ok = False
            if manifest_path.exists():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    created_at = data.get("created_at") or directory.name
                    for item in data.get("files", []):
                        source = item.get("source", "")
                        backup = item.get("backup", "")
                        if backup:
                            entries.append(BackupEntry(str(source), str(backup)))
                    manifest_ok = True
                except (json.JSONDecodeError, OSError):
                    entries = []
            if not entries:
                for path in sorted(directory.rglob("*.docx")):
                    entries.append(
                        BackupEntry("", path.relative_to(directory).as_posix())
                    )
            items.append(BackupInfo(directory, str(created_at), entries, manifest_ok))
        return items

    def restore_backup(self, backup_dir) -> tuple:
        directory = Path(backup_dir)
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError("备份缺少 manifest.json，无法确定原始路径")
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"备份清单解析失败：{exc}") from exc

        restored = []
        missing = []
        for item in data.get("files", []):
            source = item.get("source")
            relative = item.get("backup")
            if not source or not relative:
                continue
            backup_file = directory / relative
            if not backup_file.exists():
                missing.append(relative)
                continue
            destination = Path(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, destination)
            restored.append(destination)
        return restored, missing

    # ---------- 输出与报告路径 ----------

    def output_path(self, source, base_dir=None) -> Path:
        source = Path(source).resolve()
        relative = Path(source.name)
        if base_dir is not None:
            try:
                relative = source.relative_to(Path(base_dir).resolve())
            except ValueError:
                relative = Path(source.name)
        if any(part == ".." for part in relative.parts):
            relative = Path(source.name)
        target = self.output_root / relative.parent / f"{relative.stem}_formatted{relative.suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def report_path(self, extension: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.report_root / f"docformatfix_{stamp}.{extension}"
        counter = 2
        while target.exists():
            target = self.report_root / f"docformatfix_{stamp}_{counter}.{extension}"
            counter += 1
        return target
