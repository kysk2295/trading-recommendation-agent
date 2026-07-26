from __future__ import annotations

import os
import sys

os.execv(
    sys.executable,
    (
        sys.executable,
        "-c",
        "from pathlib import Path; Path('/tmp/dashboard-exec-escape').write_text('escaped')",
    ),
)
