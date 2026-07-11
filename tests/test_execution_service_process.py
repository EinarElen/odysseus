import json
import os
from pathlib import Path
import signal
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_active_run_survives_launcher_replacement(tmp_path):
    start_script = r'''
import json, os
from urllib.request import Request, urlopen
from src.execution_service import HEADER, ensure_running

old = ensure_running(startup_timeout_s=30)
url = f"http://127.0.0.1:{old['port']}/api/execution/probe/restart-proof?delay=0.5"
response = urlopen(Request(url, data=b'{}', method='POST', headers={HEADER: old['secret']}), timeout=5)
first = response.readline().decode().strip()
response.close()
print(json.dumps({'first': first, 'old_pid': old['pid']}))
'''
    reconnect_script = r'''
import json, os, time
from src.execution_service import ensure_running
new = ensure_running(startup_timeout_s=30)
time.sleep(0.7)
record = json.load(open(os.path.join(os.environ['ODYSSEUS_DATA_DIR'], 'agent_runs.json')))['restart-proof']
print(json.dumps({'new_pid': new['pid'], 'status': record['status']}))
'''
    env = os.environ.copy()
    env.update({
        "ODYSSEUS_DATA_DIR": str(tmp_path),
        "AUTH_ENABLED": "false",
        "ODYSSEUS_EXECUTION_TEST_PROBE": "1",
    })
    first_result = subprocess.run(
        [sys.executable, "-c", start_script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert first_result.returncode == 0, first_result.stderr
    first = json.loads(first_result.stdout.strip().splitlines()[-1])
    second_result = subprocess.run(
        [sys.executable, "-c", reconnect_script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert second_result.returncode == 0, second_result.stderr
    second = json.loads(second_result.stdout.strip().splitlines()[-1])
    try:
        assert first["first"] == 'data: {"delta":"started"}'
        assert second["status"] == "done"
        assert second["new_pid"] == first["old_pid"]
    finally:
        try:
            os.kill(int(second["new_pid"]), signal.SIGTERM)
        except (OSError, KeyError):
            pass
