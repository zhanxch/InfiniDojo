import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from infinidojo.request_run import run_request, run_stage


def test_existing_output_is_rejected_before_preflight(tmp_path):
    marker = tmp_path / "run.json"
    marker.write_text('{"status": "accepted"}')
    args = SimpleNamespace(
        output=tmp_path,
        robodojo_root=tmp_path,
        request=tmp_path / "missing-request.json",
        wall_seconds=1,
    )
    with pytest.raises(FileExistsError):
        run_request(args)
    assert marker.read_text() == '{"status": "accepted"}'


def test_expired_budget_does_not_launch_command(tmp_path):
    marker = tmp_path / "launched"
    result = run_stage(
        [sys.executable, "-c", "from pathlib import Path; Path('launched').touch()"],
        tmp_path / "stage.log",
        deadline=time.monotonic() - 1,
        cwd=tmp_path,
        env=os.environ,
    )
    assert result["status"] == "budget_exhausted"
    assert not marker.exists()


def test_live_stage_is_terminated_at_deadline_and_retains_logs(tmp_path):
    result = run_stage(
        [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(10)"],
        tmp_path / "stage.log",
        deadline=time.monotonic() + 0.25,
        cwd=tmp_path,
        env=os.environ,
    )
    assert result["status"] == "budget_exhausted"
    assert result["returncode"] < 0
    assert result["wall_seconds"] < 2
    assert "started" in (tmp_path / "stage.log").read_text()
    assert (tmp_path / "stage.stage.json").exists()


def test_nonzero_exit_is_infrastructure_failure_not_task_failure(tmp_path):
    result = run_stage(
        [sys.executable, "-c", "import sys; print('deployment error'); sys.exit(7)"],
        tmp_path / "stage.log",
        deadline=time.monotonic() + 5,
        cwd=tmp_path,
        env=os.environ,
    )
    assert result["status"] == "process_error" and result["returncode"] == 7
    assert "deployment error" in (tmp_path / "stage.log").read_text()


def test_interrupted_wait_reaps_the_live_process(tmp_path, monkeypatch):
    original = subprocess.Popen.wait

    def interrupt_once(process, *args, **kwargs):
        if not getattr(process, "test_interrupted", False):
            process.test_interrupted = True
            raise KeyboardInterrupt
        return original(process, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "wait", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        run_stage(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            tmp_path / "stage.log",
            deadline=time.monotonic() + 15,
            cwd=tmp_path,
            env=os.environ,
        )
    record = json.loads((tmp_path / "stage.stage.json").read_text())
    assert record["status"] == "interrupted" and record["returncode"] < 0
    with pytest.raises(ProcessLookupError):
        os.kill(record["pid"], 0)
