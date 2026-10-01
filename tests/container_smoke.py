from __future__ import annotations

import asyncio
import base64
import json
import os
import importlib.util
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace


entrypoint = Path("/usr/local/bin/notebook.entrypoint.sh")
assert entrypoint.is_file() and os.access(entrypoint, os.X_OK)
assert Path("/usr/local/bin/start-notebook.d/10-cms-labs.sh").is_symlink()
assert importlib.util.find_spec("jupyterhub") is None
assert shutil.which("jupyterhub-singleuser") is None
assert shutil.which("start-singleuser.py") is None

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
    environment = {**os.environ, "HOME": home}
    subprocess.run([str(entrypoint)], check=True, env=environment)
    subprocess.run([str(entrypoint)], check=True, env=environment)

    startup = Path(home) / ".ipython/profile_default/startup"
    assert (startup / "01-postman-widget.py").is_file()
    assert (startup / "02-ssh-magic-cell.py").is_file()

    check_magics = """
shell = get_ipython()
assert 'postman' in shell.magics_manager.magics['line']
assert 'ssh' in shell.magics_manager.magics['cell']
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

print("standalone notebook image smoke test passed")
