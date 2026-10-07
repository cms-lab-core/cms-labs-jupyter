from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


TITLE_LIMIT = 80
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def _plain_title(value: str) -> str:
    value = re.sub(r"!\[([^]]*)]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", value)
    value = re.sub(r"[*_`~]", "", value).strip()
    if len(value) > TITLE_LIMIT:
        return value[: TITLE_LIMIT - 1].rstrip() + "…"
    return value


def notebook_title(path: Path) -> str:
    fallback = path.stem
    try:
        notebook = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return fallback
    for cell in notebook.get("cells", []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "markdown":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(part for part in source if isinstance(part, str))
        if not isinstance(source, str):
            continue
        for line in source.splitlines():
            match = HEADING.match(line)
            if match and (title := _plain_title(match.group(1))):
                return title
    return fallback


def launcher_entries(root: Path, workspace_root: Path | None = None) -> list[dict[str, Any]]:
    workspace_root = workspace_root or root
    entries: list[dict[str, Any]] = []
    for notebook in sorted(root.rglob("*.ipynb"), key=lambda path: path.as_posix()):
        if any(part.startswith(".") for part in notebook.relative_to(root).parts):
            continue
        try:
            relative = notebook.relative_to(workspace_root).as_posix()
        except ValueError:
            continue
        title = notebook_title(notebook)
        entries.append(
            {
                "title": title,
                "description": relative,
                "type": "jupyterlab-commands",
                "source": [
                    {
                        "label": title,
                        "id": "filebrowser:open-path",
                        "args": {"path": relative},
                    }
                ],
                "catalog": "Задания",
            }
        )
    return entries


def write_launcher_config(
    root: Path,
    output: Path,
    workspace_root: Path | None = None,
) -> None:
    entries = launcher_entries(root, workspace_root) if root.is_dir() else []
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build JupyterLab task launcher config")
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workspace-root", type=Path)
    args = parser.parse_args()
    workspace_root = args.workspace_root.resolve() if args.workspace_root else None
    write_launcher_config(args.root.resolve(), args.output.resolve(), workspace_root)


if __name__ == "__main__":
    main()
