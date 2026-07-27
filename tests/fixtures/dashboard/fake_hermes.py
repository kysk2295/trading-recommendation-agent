from __future__ import annotations

import json
import os
from pathlib import Path

experiment = Path(os.environ["DASHBOARD_EXPERIMENT_ROOT"])
candidate = experiment / "candidate.json"
candidate.write_text(json.dumps({"candidate": "verified"}) + "\n")
candidate.chmod(0o600)
print("candidate evidence recorded")
