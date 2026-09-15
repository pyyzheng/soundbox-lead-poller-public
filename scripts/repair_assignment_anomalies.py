#!/usr/bin/env python3
"""一键修复「分配异常」线索：子办回填 → 渠道轮转异常 → assignment-unblock。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    os.environ.setdefault("FEISHU_API_TIMEOUT", "60")
    env = os.environ.copy()

    steps = [
        ("suboffice-assignee-fix", [sys.executable, str(ROOT / "cloud-suboffice-assignee-fix.py")]),
        ("fix-assignment-anomalies", [sys.executable, str(ROOT / "scripts" / "fix_assignment_anomalies.py")]),
        ("tagline-field-fix", [sys.executable, str(ROOT / "cloud-tagline-field-fix.py")]),
        ("assignment-unblock", [sys.executable, str(ROOT / "cloud-assignment-unblock.py")]),
    ]
    for name, cmd in steps:
        print(f"\n=== {name} ===")
        result = subprocess.run(cmd, cwd=ROOT, env=env)
        if result.returncode != 0:
            print(f"{name} failed with code {result.returncode}", file=sys.stderr)
            return result.returncode
    print("\nrepair complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
