#!/usr/bin/env python3
"""批量部署分配相关工作流（含双语字段名迁移）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = (
    "patch_suboffice_assignment_workflow.py",
    "patch_channel_rotation_workflow.py",
    "patch_agent_assignment_workflow.py",
    "patch_assignment_notify_workflow.py",
    "patch_enquiry_update_notify_workflow.py",
)


def main() -> int:
    for name in PATCHES:
        script = ROOT / "scripts" / name
        print(f"\n=== {name} ===")
        result = subprocess.run([sys.executable, str(script)], cwd=ROOT)
        if result.returncode != 0:
            print(f"{name} failed", file=sys.stderr)
            return result.returncode
    print("\nall assignment workflows patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
