from __future__ import annotations

import asyncio
import base64
import json
import os
import importlib.util
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace


entrypoint = Path("/usr/local/bin/notebook.entrypoint.sh")
assert entrypoint.is_file() and os.access(entrypoint, os.X_OK)
assert Path("/usr/local/bin/start-notebook.d/10-cms-labs.sh").is_symlink()
assert importlib.util.find_spec("jupyterhub") is None
assert shutil.which("jupyterhub-singleuser") is None
assert shutil.which("start-singleuser.py") is None
assert shutil.which("tshark") is not None

from cms_labs_jupyter.identity import ProxyIdentityProvider

assert ProxyIdentityProvider is not None
identity = {"sub": "42", "username": "student", "name": "Иван Иванов"}
encoded_identity = base64.urlsafe_b64encode(json.dumps(identity).encode()).decode().rstrip("=")
handler = SimpleNamespace(request=SimpleNamespace(headers={"X-CMS-Identity": encoded_identity}))
user = asyncio.run(ProxyIdentityProvider().get_user(handler))
assert user is not None
assert user.username == "42"
assert user.display_name == "Иван Иванов"

with tempfile.TemporaryDirectory() as home:
    environment = {
        **os.environ,
        "HOME": home,
        "JUPYTER_APP_LAUNCHER_PATH": f"{home}/.cms-labs/launcher",
    }
    task = Path(home) / "task"
    task.mkdir()
    (task / "01-routing.ipynb").write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "markdown",
                        "source": ["# Routing task\n"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    subprocess.run([str(entrypoint)], check=True, env=environment)
    subprocess.run([str(entrypoint)], check=True, env=environment)

    startup = Path(home) / ".ipython/profile_default/startup"
    assert (startup / "01-postman-widget.py").is_file()
    assert (startup / "02-ssh-magic-cell.py").is_file()
    assert (startup / "03-capture-traffic.py").is_file()
    assert (startup / "04-view-traffic.py").is_file()
    launcher_config = (
        Path(home) / ".cms-labs/launcher/jp_app_launcher_tasks.yaml"
    )
    launcher_entries = json.loads(launcher_config.read_text(encoding="utf-8"))
    assert launcher_entries[0]["title"] == "Routing task"
    assert launcher_entries[0]["source"][0]["args"]["path"] == (
        "task/01-routing.ipynb"
    )

    check_magics = """
shell = get_ipython()
assert 'postman' in shell.magics_manager.magics['line']
assert 'ssh' in shell.magics_manager.magics['cell']
assert 'capture_traffic' in shell.magics_manager.magics['cell']
assert 'capture_traffic' in shell.magics_manager.magics['line']
assert 'view_traffic' in shell.magics_manager.magics['line']
from cms_labs_jupyter.traffic_capture import parse_magic_options, parse_view_options
options = parse_magic_options('r1:eth1 --filter "icmp" --timeout 7 --save captures/test.pcap')
assert options.node == 'r1'
assert options.interface == 'eth1'
assert options.display_filter == 'icmp'
assert options.timeout == 7
view_options = parse_view_options('captures/test.pcap --filter "icmp" --limit 50')
assert str(view_options.path) == 'captures/test.pcap'
assert view_options.display_filter == 'icmp'
assert view_options.limit == 50
from pysnmp.entity.rfc3413.oneliner import cmdgen
assert cmdgen.CommunityData('public').community_name == 'public'
assert cmdgen.UdpTransportTarget(('127.0.0.1', 161)).transport_addr[1] == 161
snmp_result = cmdgen.CommandGenerator().getCmd(
    cmdgen.CommunityData('public'),
    cmdgen.UdpTransportTarget(('127.0.0.1', 9), timeout=0.05, retries=0),
    '1.3.6.1.2.1.1.5.0',
)
assert len(snmp_result) == 4
"""
    subprocess.run(
        ["ipython", "--quick", "-c", check_magics],
        check=True,
        env=environment,
    )

for module in (
    "ipywidgets",
    "lxml",
    "matplotlib",
    "ncclient",
    "numpy",
    "pandas",
    "paramiko",
    "PIL",
    "pysnmp",
    "requests",
    "xmltodict",
    "yaml",
):
    __import__(module)


class CaptureHandler(BaseHTTPRequestHandler):
    state = "capturing"
    pcap = bytes.fromhex("d4c3b2a1020004000000000000000000ffff000001000000")

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def record(self) -> dict[str, object]:
        return {
            "id": "capture-test",
            "node": "r1",
            "interface": "eth1",
            "state": type(self).state,
            "bytes": len(self.pcap),
            "startedAt": "2026-10-04T00:00:00Z",
        }

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/v1/captures":
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            assert payload["node"] == "r1" and payload["interface"] == "eth1"
            type(self).state = "capturing"
            self.send_json(201, self.record())
            return
        if self.path == "/v1/captures/capture-test/stop":
            type(self).state = "stopped"
            self.send_json(202, self.record())
            return
        self.send_error(404)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/v1/captures/capture-test":
            self.send_json(200, self.record())
            return
        if self.path == "/v1/captures/capture-test/download":
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.tcpdump.pcap")
            self.send_header("Content-Length", str(len(self.pcap)))
            self.end_headers()
            self.wfile.write(self.pcap)
            return
        self.send_error(404)

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_: object) -> None:
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    from cms_labs_jupyter.traffic_capture import CaptureClient, PcapViewer, TrafficCapture

    capture_client = CaptureClient(f"http://127.0.0.1:{server.server_port}")
    capture = TrafficCapture("r1", "eth1", duration=5, client=capture_client).start()
    capture.stop()
    assert capture.wait(timeout=1).state == "stopped"
    with tempfile.TemporaryDirectory() as directory:
        pcap_path = capture.save(Path(directory) / "test.pcap")
        assert pcap_path.read_bytes() == CaptureHandler.pcap
        assert PcapViewer(pcap_path).packets().empty
    capture.delete()
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

print("standalone notebook image smoke test passed")
