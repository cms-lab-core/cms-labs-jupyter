from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cms_labs_jupyter.tasks_launcher import (
    launcher_entries,
    notebook_title,
    write_launcher_config,
)


def write_notebook(path: Path, cells: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cells": cells}), encoding="utf-8")


class TasksLauncherTest(unittest.TestCase):
    def test_notebook_title_uses_first_markdown_heading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "01-routing.ipynb"
            write_notebook(
                path,
                [
                    {"cell_type": "code", "source": ["# not a markdown heading"]},
                    {
                        "cell_type": "markdown",
                        "source": ["Text\n", "# **Лаба 1** — маршрутизация\n"],
                    },
                    {"cell_type": "markdown", "source": ["# Later"]},
                ],
            )
            self.assertEqual(notebook_title(path), "Лаба 1 — маршрутизация")

    def test_launcher_is_sorted_and_falls_back_to_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "task"
            write_notebook(root / "02.ipynb", [{"cell_type": "code", "source": []}])
            write_notebook(
                root / "01.ipynb",
                [{"cell_type": "markdown", "source": ["# First"]}],
            )
            write_notebook(root / ".ipynb_checkpoints" / "ignored.ipynb", [])

            entries = launcher_entries(root, workspace)

            self.assertEqual([entry["title"] for entry in entries], ["First", "02"])
            self.assertEqual(entries[0]["catalog"], "Задания")
            command = entries[0]["source"][0]
            self.assertEqual(command["id"], "filebrowser:open-path")
            self.assertEqual(command["args"], {"path": "task/01.ipynb"})

    def test_write_launcher_config_is_valid_yaml_compatible_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "task"
            write_notebook(
                root / "lab.ipynb",
                [{"cell_type": "markdown", "source": "# Lab"}],
            )
            output = workspace / "launcher" / "jp_app_launcher_tasks.yaml"

            write_launcher_config(root, output, workspace)

            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))[0]["title"],
                "Lab",
            )

    def test_workspace_scan_uses_paths_relative_to_jupyter_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            write_notebook(
                workspace / "nested" / "lab.ipynb",
                [{"cell_type": "markdown", "source": "# Nested lab"}],
            )

            entries = launcher_entries(workspace, workspace)

            self.assertEqual(
                entries[0]["source"][0]["args"],
                {"path": "nested/lab.ipynb"},
            )


if __name__ == "__main__":
    unittest.main()
