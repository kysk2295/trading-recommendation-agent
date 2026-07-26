from __future__ import annotations

import os
from pathlib import Path

Path(os.environ["DASHBOARD_ESCAPE_CANARY"]).write_text("escaped")
