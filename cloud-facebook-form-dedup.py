#!/usr/bin/env python3
"""定时删除 Facebook 表单重复线索（兜底，与 webhook/poller 飞书级去重配合）。"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "scripts" / "cleanup_facebook_form_duplicates.py"

if __name__ == "__main__":
    env = os.environ.copy()
    env.setdefault("FB_FORM_DEDUP_DRY_RUN", "false")
    env.setdefault("FB_FORM_DEDUP_DAYS", "90")
    raise SystemExit(subprocess.call([sys.executable, str(SCRIPT)], env=env, cwd=str(ROOT)))
