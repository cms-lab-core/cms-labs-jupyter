from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.partition("#")[0].strip()
        if not line:
            continue
        match = re.match(r"[A-Za-z0-9_.-]+", line)
        if match:
            names.add(match.group(0).lower().replace("_", "-"))
    return names


for startup in sorted((ROOT / "ipython_startup").glob("*.py")):
    ast.parse(startup.read_text(encoding="utf-8"), filename=str(startup))

requested = requirement_names(ROOT / "requirements.in")
locked = requirement_names(ROOT / "requirements.lock")
missing = sorted(requested - locked)
assert not missing, f"requirements missing from lock: {', '.join(missing)}"

dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
requirements = (ROOT / "requirements.in").read_text(encoding="utf-8").lower()
assert "jupyterhub" not in requirements
assert "uv pip uninstall --system jupyterhub" in dockerfile
assert 'CMD ["start-notebook.py"]' in dockerfile
assert "USER 1000" in dockerfile
assert "tshark" in dockerfile
assert "jupyter-app-launcher" in requirements
assert (ROOT / "cms_labs_jupyter/traffic_capture.py").is_file()
assert (ROOT / "cms_labs_jupyter/tasks_launcher.py").is_file()
assert (ROOT / "ipython_startup/03-capture-traffic.py").is_file()
assert (ROOT / "ipython_startup/04-view-traffic.py").is_file()

print(f"validated {len(requested)} direct and {len(locked)} locked packages")
