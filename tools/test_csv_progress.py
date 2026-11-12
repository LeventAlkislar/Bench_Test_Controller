"""Keep watchdog CSV checks independent of measurement/UI/Excel imports."""
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CsvProgressImportsTests(unittest.TestCase):
    def check_fresh_import(self, statement, forbidden):
        script = f"""
import sys
{statement}
for prefix in {forbidden!r}:
    loaded = [name for name in sys.modules if name == prefix or name.startswith(prefix + '.')]
    assert not loaded, (prefix, loaded)
"""
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shared_checker_needs_no_third_party_packages(self):
        self.check_fresh_import("import bench_test.csv_progress",
                                ["PyQt6", "numpy", "openpyxl", "PIL", "bench_test.measurement"])

    def test_watchdog_entry_does_not_import_measurement_or_gui(self):
        self.check_fresh_import("import watchdog_main",
                                ["PyQt6.QtWidgets", "PyQt6.QtGui", "numpy", "openpyxl", "PIL",
                                 "bench_test.measurement"])

    def test_existing_btc_import_uses_same_checker_and_constants(self):
        from bench_test import csv_progress
        from bench_test.measurement import data_io

        self.assertIs(data_io.inspect_csv_progress, csv_progress.inspect_csv_progress)
        for name in ("CSV_SEPARATOR", "CSV_COL_TIME", "CSV_COL_CURRENT", "CSV_WATCHDOG_READ_LIMIT"):
            self.assertEqual(getattr(data_io, name), getattr(csv_progress, name))


if __name__ == "__main__":
    unittest.main()
