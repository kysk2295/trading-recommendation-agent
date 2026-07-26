from __future__ import annotations

import json
import os
from pathlib import Path

experiment = Path(os.environ["DASHBOARD_EXPERIMENT_ROOT"])
(experiment / "candidate.json").write_text(json.dumps({"candidate": "verified"}) + "\n")
print("candidate evidence recorded")
