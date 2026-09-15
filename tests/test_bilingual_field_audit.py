"""双语字段名静态审计测试（CI 门禁）。"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestBilingualFieldAudit(unittest.TestCase):
    def test_no_deprecated_field_literals_in_production_code(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "audit_bilingual_fields.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.fail(result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
