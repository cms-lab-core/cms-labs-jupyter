#!/usr/bin/env python3
"""Capture client, PCAP viewer, IPython magic and CLI for CMS Labs."""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import ipywidgets as widgets
import pandas as pd
import requests
from IPython.core.error import UsageError
from IPython.core.magic import Magics, line_cell_magic, line_magic, magics_class
from IPython.display import FileLink, Markdown, display


SUMMARY_FIELDS = (
    "frame.number",
    "frame.time_relative",
    "_ws.col.Source",
    "_ws.col.Destination",
    "_ws.col.Protocol",
    "frame.len",
    "_ws.col.Info",
)
SUMMARY_COLUMNS = ("№", "Time", "Source", "Destination", "Protocol", "Length", "Info")


class CaptureError(RuntimeError):
    """The capture service or tshark could not complete an operation."""


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    id: str
    node: str
    interface: str
    state: str
    bytes: int = 0
    started_at: str = ""
    completed_at: str = ""
    error: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CaptureRecord":
        return cls(
            id=str(payload.get("id", "")),
            node=str(payload.get("node", "")),
            interface=str(payload.get("interface", "")),
            state=str(payload.get("state", "")),
            bytes=int(payload.get("bytes", 0)),
            started_at=str(payload.get("startedAt", "")),
            completed_at=str(payload.get("completedAt", "")),
            error=str(payload.get("error", "")),
        )

    @property
    def finished(self) -> bool:
        return self.state in {"completed", "stopped", "failed"}


@dataclass(frozen=True, slots=True)
class CaptureResult:
    record: CaptureRecord
    path: Path
    display_filter: str = ""


class CaptureClient:
    """Low-level HTTP client for a namespace-local cms-labs-capture API."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 10,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("CMS_LABS_CAPTURE_URL") or "http://cms-labs-capture:8080"
        ).rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def targets(self) -> list[dict[str, Any]]:
        payload = self._json("GET", "/v1/targets")
        targets = payload.get("targets", [])
        if not isinstance(targets, list):
            raise CaptureError("capture service returned an invalid target list")
        return targets

    def start(
        self,
        node: str,
        interface: str,
        *,
        duration: int = 15,
        packet_limit: int = 10_000,
        max_bytes: int = 0,
        snaplen: int = 0,
    ) -> CaptureRecord:
        payload = self._json(
            "POST",
            "/v1/captures",
            json={
                "node": node,
                "interface": interface,
                "durationSeconds": duration,
                "packetLimit": packet_limit,
                "maxBytes": max_bytes,
                "snaplen": snaplen,
            },
        )
        record = CaptureRecord.from_payload(payload)
        if not record.id:
            raise CaptureError("capture service returned no capture id")
        return record

    def get(self, capture_id: str) -> CaptureRecord:
        return CaptureRecord.from_payload(self._json("GET", f"/v1/captures/{capture_id}"))

    def stop(self, capture_id: str) -> CaptureRecord:
        return CaptureRecord.from_payload(self._json("POST", f"/v1/captures/{capture_id}/stop"))

    def wait(self, capture_id: str, *, timeout: float = 75, interval: float = 0.25) -> CaptureRecord:
        deadline = time.monotonic() + timeout
        while True:
            record = self.get(capture_id)
            if record.finished:
                return record
            if time.monotonic() >= deadline:
                raise CaptureError(f"capture {capture_id} did not finish within {timeout:g}s")
            time.sleep(interval)

    def download(self, capture_id: str, destination: str | Path) -> Path:
        path = Path(destination).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        response: requests.Response | None = None
        try:
            response = self.session.request(
                "GET",
                self.base_url + f"/v1/captures/{capture_id}/download",
                stream=True,
                timeout=(self.timeout, max(self.timeout, 60)),
            )
            self._raise_for_status(response)
            with partial.open("wb") as output:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        output.write(chunk)
            partial.replace(path)
        except (OSError, requests.RequestException, CaptureError) as error:
            partial.unlink(missing_ok=True)
            if isinstance(error, CaptureError):
                raise
            raise CaptureError(f"cannot download capture: {error}") from error
        finally:
            if response is not None:
                response.close()
        return path

    def delete(self, capture_id: str, *, missing_ok: bool = False) -> None:
        try:
            self._json("DELETE", f"/v1/captures/{capture_id}", expect_json=False)
        except CaptureError as error:
            if not missing_ok or "HTTP 404" not in str(error):
                raise

    def _json(self, method: str, path: str, *, expect_json: bool = True, **kwargs: Any) -> dict[str, Any]:
        response: requests.Response | None = None
        try:
            response = self.session.request(method, self.base_url + path, timeout=self.timeout, **kwargs)
            self._raise_for_status(response)
            if not expect_json:
                return {}
            payload = response.json()
        except requests.RequestException as error:
            raise CaptureError(f"capture service is unavailable: {error}") from error
        except ValueError as error:
            raise CaptureError("capture service returned invalid JSON") from error
        finally:
            if response is not None:
                response.close()
        if not isinstance(payload, dict):
            raise CaptureError("capture service returned invalid JSON")
        return payload

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        if response.status_code < 400:
            return
        try:
            message = response.json().get("error", "")
        except (ValueError, AttributeError):
            message = response.text.strip()
        detail = f": {message}" if message else ""
        raise CaptureError(f"capture service returned HTTP {response.status_code}{detail}")


class TrafficCapture:
    """One capture lifecycle that can be used from Python, magic or the CLI."""

    def __init__(
        self,
        node: str,
        interface: str,
        *,
        duration: int = 15,
        packet_limit: int = 10_000,
        max_bytes: int = 0,
        snaplen: int = 0,
        client: CaptureClient | None = None,
    ) -> None:
        self.node = node
        self.interface = interface
        self.duration = duration
        self.packet_limit = packet_limit
        self.max_bytes = max_bytes
        self.snaplen = snaplen
        self.client = client or CaptureClient()
        self.record: CaptureRecord | None = None

    def start(self) -> "TrafficCapture":
        if self.record is not None:
            raise CaptureError("capture has already been started")
        self.record = self.client.start(
            self.node,
            self.interface,
            duration=self.duration,
            packet_limit=self.packet_limit,
            max_bytes=self.max_bytes,
            snaplen=self.snaplen,
        )
        return self

    def stop(self) -> CaptureRecord:
        record = self._require_record()
        if not record.finished:
            self.record = self.client.stop(record.id)
        return self.record

    def wait(self, *, timeout: float | None = None) -> CaptureRecord:
        record = self._require_record()
        if not record.finished:
            self.record = self.client.wait(
                record.id,
                timeout=timeout if timeout is not None else max(self.duration + 20, 30),
            )
        return self.record

    def save(self, destination: str | Path) -> Path:
        record = self.wait()
        return self.client.download(record.id, destination)

    def delete(self, *, missing_ok: bool = True) -> None:
        if self.record is not None:
            self.client.delete(self.record.id, missing_ok=missing_ok)

    def __enter__(self) -> "TrafficCapture":
        return self.start()

    def __exit__(self, *_: object) -> None:
        try:
            self.stop()
        finally:
            self.wait()

    def _require_record(self) -> CaptureRecord:
        if self.record is None:
            raise CaptureError("capture has not been started")
        return self.record


class PcapViewer:
    """Read an existing PCAP and render packet details lazily."""

    def __init__(self, path: str | Path, display_filter: str = "", *, limit: int = 200) -> None:
        self.path = Path(path).expanduser()
        self.display_filter = display_filter
        self.limit = limit
        if not self.path.is_file():
            raise CaptureError(f"PCAP does not exist: {self.path}")

    def packets(self, display_filter: str | None = None) -> pd.DataFrame:
        arguments = ["-c", str(self.limit)]
        selected_filter = self.display_filter if display_filter is None else display_filter
        if selected_filter:
            arguments.extend(["-Y", selected_filter])
        arguments.extend(["-T", "fields", "-E", "separator=\t", "-E", "quote=d", "-E", "occurrence=f"])
        for field in SUMMARY_FIELDS:
            arguments.extend(["-e", field])
        output = self._tshark(arguments)
        rows = list(csv.reader(io.StringIO(output), delimiter="\t", quotechar='"'))
        normalized = [(row + [""] * len(SUMMARY_COLUMNS))[: len(SUMMARY_COLUMNS)] for row in rows]
        return pd.DataFrame(normalized, columns=SUMMARY_COLUMNS)

    def packet(self, frame: str | int, mode: str = "protocols") -> str:
        selector = ["-Y", f"frame.number == {int(frame)}"]
        if mode == "protocols":
            return self._tshark([*selector, "-V"])
        if mode == "hex":
            return self._tshark([*selector, "-x"])
        if mode == "json":
            return self._tshark([*selector, "-T", "json"])
        raise CaptureError(f"unknown packet detail mode: {mode}")

    def display(self) -> widgets.Widget:
        table_output = widgets.Output()
        detail_outputs = [widgets.Output() for _ in range(3)]
        selector = widgets.Dropdown(description="Packet", options=[])
        filter_input = widgets.Text(
            value=self.display_filter,
            description="Filter",
            placeholder="icmp or tcp.port == 80",
        )
        apply_filter = widgets.Button(description="Apply", icon="filter")
        tabs = widgets.Tab(children=detail_outputs)
        tabs.set_title(0, "Protocols")
        tabs.set_title(1, "Hex dump")
        tabs.set_title(2, "Raw JSON")

        def render_detail(*_: object) -> None:
            if not selector.value:
                return
            for output, mode in zip(detail_outputs, ("protocols", "hex", "json"), strict=True):
                output.clear_output(wait=True)
                with output:
                    try:
                        print(self.packet(str(selector.value), mode))
                    except CaptureError as error:
                        print(f"Viewer error: {error}")

        def render_table(*_: object) -> None:
            table_output.clear_output(wait=True)
            with table_output:
                try:
                    frame = self.packets(filter_input.value.strip())
                    display(frame)
                    selector.options = [(str(value), str(value)) for value in frame["№"].tolist() if value]
                    if selector.options:
                        selector.value = selector.options[0][1]
                        render_detail()
                except CaptureError as error:
                    selector.options = []
                    print(f"Viewer error: {error}")

        selector.observe(render_detail, names="value")
        apply_filter.on_click(render_table)
        title = widgets.HTML(
            f"<b>Traffic capture</b> — {html.escape(self.path.name)}, {self.path.stat().st_size:,} bytes"
        )
        link_output = widgets.Output()
        with link_output:
            display(FileLink(str(self.path), result_html_prefix="Download PCAP: "))
        root = widgets.VBox(
            [title, link_output, widgets.HBox([filter_input, apply_filter]), table_output, selector, tabs]
        )
        render_table()
        display(root)
        return root

    def _tshark(self, arguments: list[str]) -> str:
        executable = shutil.which("tshark")
        if executable is None:
            raise CaptureError("tshark is not installed in this Jupyter image")
        try:
            result = subprocess.run(
                [executable, "-n", "-r", str(self.path), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CaptureError(f"tshark failed: {error}") from error
        if result.returncode != 0:
            raise CaptureError(result.stderr.strip() or "tshark could not read the capture")
        return result.stdout


@dataclass(frozen=True, slots=True)
class CaptureOptions:
    node: str
    interface: str
    timeout: int
    packet_limit: int
    max_bytes: int
    snaplen: int
    display_filter: str
    save: str
    no_view: bool


class _MagicArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def _split_target(target: str) -> tuple[str, str]:
    if ":" not in target:
        raise ValueError("target must use node:interface format, for example r1:eth1")
    node, interface = target.split(":", 1)
    if not node or not interface:
        raise ValueError("node and interface must not be empty")
    return node, interface


def parse_magic_options(line: str) -> CaptureOptions:
    parser = _MagicArgumentParser(prog="%%capture_traffic", add_help=False)
    parser.add_argument("target")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--packets", type=int, default=10_000)
    parser.add_argument("--max-bytes", type=int, default=0)
    parser.add_argument("--snaplen", type=int, default=0)
    parser.add_argument("--filter", default="")
    parser.add_argument("--save", default="")
    parser.add_argument("--no-view", action="store_true")
    try:
        arguments = parser.parse_args(shlex.split(line))
        node, interface = _split_target(arguments.target)
    except ValueError as error:
        raise UsageError(str(error)) from error
    if arguments.timeout <= 0 or arguments.packets <= 0 or arguments.max_bytes < 0 or arguments.snaplen < 0:
        raise UsageError("capture limits must be positive")
    return CaptureOptions(
        node=node,
        interface=interface,
        timeout=arguments.timeout,
        packet_limit=arguments.packets,
        max_bytes=arguments.max_bytes,
        snaplen=arguments.snaplen,
        display_filter=arguments.filter,
        save=arguments.save,
        no_view=arguments.no_view,
    )


def _default_capture_path(node: str, interface: str) -> Path:
    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{node}-{interface}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("captures") / f"{safe_target}-{timestamp}.pcap"


@magics_class
class CaptureMagics(Magics):
    @line_cell_magic
    def capture_traffic(self, line: str, cell: str | None = None) -> CaptureResult:
        """Capture node traffic while the body of a notebook cell executes."""
        options = parse_magic_options(line)
        capture = TrafficCapture(
            options.node,
            options.interface,
            duration=options.timeout,
            packet_limit=options.packet_limit,
            max_bytes=options.max_bytes,
            snaplen=options.snaplen,
        )
        try:
            capture.start()
        except CaptureError as error:
            raise UsageError(str(error)) from error

        display(Markdown(f"● Capturing `{options.node}:{options.interface}` …"))
        execution_error: BaseException | None = None
        try:
            if cell is not None and cell.strip():
                self.shell.run_cell(cell)
        except BaseException as error:  # capture must stop even on KeyboardInterrupt
            execution_error = error
        finally:
            try:
                capture.stop()
            except CaptureError:
                pass

        destination = Path(options.save).expanduser() if options.save else _default_capture_path(
            options.node, options.interface
        )
        try:
            record = capture.wait()
            path = capture.save(destination)
        except CaptureError as error:
            raise UsageError(str(error)) from error
        finally:
            try:
                capture.delete()
            except CaptureError:
                pass

        result = CaptureResult(record=record, path=path, display_filter=options.display_filter)
        display(Markdown(f"Capture `{record.state}`: **{record.bytes:,} bytes**, saved to `{path}`."))
        if not options.no_view:
            PcapViewer(path, options.display_filter).display()
        if execution_error is not None:
            raise execution_error
        return result


@dataclass(frozen=True, slots=True)
class ViewOptions:
    path: Path
    display_filter: str
    limit: int


def parse_view_options(line: str) -> ViewOptions:
    parser = _MagicArgumentParser(prog="%view_traffic", add_help=False)
    parser.add_argument("pcap")
    parser.add_argument("--filter", default="")
    parser.add_argument("--limit", type=int, default=200)
    try:
        arguments = parser.parse_args(shlex.split(line))
    except ValueError as error:
        raise UsageError(str(error)) from error
    if arguments.limit <= 0:
        raise UsageError("packet limit must be positive")
    return ViewOptions(
        path=Path(arguments.pcap).expanduser(),
        display_filter=arguments.filter,
        limit=arguments.limit,
    )


@magics_class
class ViewTrafficMagics(Magics):
    @line_magic
    def view_traffic(self, line: str) -> widgets.Widget:
        """Open an existing PCAP in the interactive notebook viewer."""
        options = parse_view_options(line)
        try:
            return PcapViewer(options.path, options.display_filter, limit=options.limit).display()
        except CaptureError as error:
            raise UsageError(str(error)) from error


def register_capture_magic(ipython: Any) -> None:
    ipython.register_magics(CaptureMagics)


def register_view_magic(ipython: Any) -> None:
    ipython.register_magics(ViewTrafficMagics)


def load_ipython_extension(ipython: Any) -> None:
    register_capture_magic(ipython)
    register_view_magic(ipython)


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cms-labs-pcap", description="Capture and inspect CMS Labs traffic")
    parser.add_argument("--url", default=None, help="capture API URL (defaults to CMS_LABS_CAPTURE_URL)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("targets", help="list available node interfaces")

    capture = commands.add_parser("capture", help="capture traffic and save a PCAP")
    capture.add_argument("target", help="node:interface, for example r1:eth1")
    capture.add_argument("-o", "--output", required=True)
    capture.add_argument("--timeout", type=int, default=15)
    capture.add_argument("--packets", type=int, default=10_000)
    capture.add_argument("--max-bytes", type=int, default=0)
    capture.add_argument("--snaplen", type=int, default=0)

    view = commands.add_parser("view", help="print packets or one packet detail from a saved PCAP")
    view.add_argument("pcap")
    view.add_argument("--filter", default="")
    view.add_argument("--limit", type=int, default=200)
    view.add_argument("--frame", type=int)
    view.add_argument("--format", choices=("protocols", "hex", "json"), default="protocols")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_cli()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "targets":
            print(json.dumps(CaptureClient(arguments.url).targets(), ensure_ascii=False, indent=2))
            return 0
        if arguments.command == "view":
            viewer = PcapViewer(arguments.pcap, arguments.filter, limit=arguments.limit)
            if arguments.frame is not None:
                print(viewer.packet(arguments.frame, arguments.format))
            else:
                print(viewer.packets().to_string(index=False))
            return 0

        node, interface = _split_target(arguments.target)
        capture = TrafficCapture(
            node,
            interface,
            duration=arguments.timeout,
            packet_limit=arguments.packets,
            max_bytes=arguments.max_bytes,
            snaplen=arguments.snaplen,
            client=CaptureClient(arguments.url),
        )
        capture.start()
        print(f"capturing {node}:{interface} as {capture.record.id}", file=sys.stderr)
        try:
            capture.wait()
        except KeyboardInterrupt:
            capture.stop()
            capture.wait()
        try:
            path = capture.save(arguments.output)
            print(path)
        finally:
            capture.delete()
        return 0
    except (CaptureError, ValueError) as error:
        parser.exit(1, f"cms-labs-pcap: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
