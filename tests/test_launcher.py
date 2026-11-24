"""启动器环境检测测试。

重点验证 tkinterdnd2 只是可选依赖，不应触发“环境缺失”对话框或阻止启动。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import launcher


class LauncherScanTest(unittest.TestCase):
    def test_tkinterdnd2_missing_is_optional_not_critical(self) -> None:
        scan = launcher._build_scan([3, 13, 9], ["tkinterdnd2"], None, "python.exe")
        self.assertTrue(scan["ok"])
        self.assertEqual(scan["critical"], [])
        self.assertTrue(scan["optional"])
        self.assertEqual(scan["warnings"], [])

    def test_docx_missing_is_critical(self) -> None:
        scan = launcher._build_scan([3, 13, 9], ["docx"], None, "python.exe")
        self.assertFalse(scan["ok"])
        self.assertTrue(any("python-docx" in item for item in scan["critical"]))
        self.assertEqual(scan["optional"], [])

    def test_optional_only_launches_gui_without_dialog(self) -> None:
        original_scan = launcher.scan_current
        original_config = launcher.load_launcher_config
        original_launch = launcher.launch_gui
        original_log = launcher.log
        old_argv = sys.argv
        launched = []

        launcher.scan_current = lambda: {
            "python": "python.exe",
            "version": [3, 13, 9],
            "missing": ["tkinterdnd2"],
            "critical": [],
            "optional": ["tkinterdnd2（OLE 拖放增强，缺少时仍可使用原生拖放）"],
            "warnings": [],
            "ok": True,
        }
        launcher.load_launcher_config = lambda: {}
        launcher.launch_gui = lambda: launched.append(True)
        launcher.log = lambda message: None
        sys.argv = ["launcher.py"]

        try:
            launcher.main()
        finally:
            launcher.scan_current = original_scan
            launcher.load_launcher_config = original_config
            launcher.launch_gui = original_launch
            launcher.log = original_log
            sys.argv = old_argv

        self.assertEqual(launched, [True])


if __name__ == "__main__":
    unittest.main()
