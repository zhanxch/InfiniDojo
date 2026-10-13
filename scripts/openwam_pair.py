"""Run a preselected native/generated development pair on one OpenWAM service."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

from astra_pair import stop

from infinidojo.episode import load_episode
from infinidojo.io import git_revision, sha256, write_json
from infinidojo.openwam_service import XPL_REVISION
from infinidojo.policy_results import audit_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "xpolicylab-root",
        "robodojo-root",
        "sim-python",
        "model-python",
        "checkpoint",
        "episode",
        "output",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--native-layout", type=int, default=0)
    args = parser.parse_args()
    root, xpl, output = args.robodojo_root.resolve(), args.xpolicylab_root.resolve(), args.output.resolve()
    source, episode = Path(__file__).resolve().parents[1], args.episode.resolve()
    manifest, _, _ = load_episode(episode)
    task = manifest["task"]
    if manifest["robodojo_commit"] != git_revision(root) or git_revision(xpl) != XPL_REVISION:
        raise ValueError("Episode, service and evaluator must use the declared revisions")
    native = root / f"Assets/Eval_Layout/RoboDojo/arx_x5/0/{task}_{args.native_layout}.json"
    if not native.is_file():
        raise FileNotFoundError(native)
    output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    service_record = output / "service/service.json"
    env = dict(os.environ, PYTHONPATH=str(source / "src"), PYTHONUNBUFFERED="1")
    sim_env = dict(env, OMNI_KIT_ACCEPT_EULA="YES", ACCEPT_EULA="Y", EVAL_ENV_TYPE="sim", EVAL_NUM="1")
    base = subprocess.check_output(
        [str(args.sim_python.absolute()), "-c", "import sys; print(sys.base_prefix)"], text=True
    ).strip()
    sim_env["LD_PRELOAD"] = str(Path(base) / "lib/libstdc++.so.6")
    server_command = [
        str(args.model_python.absolute()),
        "-u",
        "-m",
        "infinidojo.openwam_service",
        "--xpolicylab-root",
        str(xpl),
        "--robodojo-root",
        str(root),
        "--checkpoint",
        str(args.checkpoint.resolve()),
        "--output",
        str(output / "service"),
        "--port",
        str(port),
        "--task",
        task,
    ]
    planned = [
        ("native", ["--native-layout", str(args.native_layout)]),
        ("generated", ["--episode", str(episode)]),
    ]
    record = {
        "development_only": True,
        "policy": "OpenWAM",
        "task": task,
        "native_layout": args.native_layout,
        "native_layout_sha256": sha256(native),
        "episode_sha256": sha256(episode / "episode.json"),
        "infinidojo_commit": git_revision(source),
        "launcher_sha256": sha256(Path(__file__)),
        "server_command": server_command,
        "conditions": [name for name, _ in planned],
        "runs": [],
        "selection": "Inputs recorded before execution; default inputs match the Astra development pair.",
    }
    write_json(output / "pair.json", record)
    server = client = None
    started = time.monotonic()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Received signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        with (output / "server.log").open("w") as log:
            server = subprocess.Popen(
                server_command,
                env=env,
                cwd=source,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            record["server_pid"] = server.pid
            write_json(output / "pair.json", record)
            deadline = time.monotonic() + 300
            while True:
                if server.poll() is not None:
                    raise RuntimeError("OpenWAM service exited before readiness")
                ready = (
                    service_record.is_file() and json.loads(service_record.read_text())["status"] == "ready"
                )
                if ready:
                    try:
                        with socket.create_connection(("localhost", port), timeout=0.5):
                            break
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("OpenWAM service readiness timed out")
                time.sleep(0.5)
            record["service_sha256"] = sha256(service_record)
            for name, arguments in planned:
                if server.poll() is not None:
                    raise RuntimeError("OpenWAM service exited")
                if sha256(service_record) != record["service_sha256"]:
                    raise RuntimeError("OpenWAM deployment changed between conditions")
                destination = output / name
                command = [
                    str(args.sim_python.absolute()),
                    "-u",
                    "-m",
                    "infinidojo.policy_eval",
                    "--robodojo-root",
                    str(root),
                    "--xpolicylab-root",
                    str(xpl),
                    "--output",
                    str(destination),
                    "--policy",
                    "OpenWAM",
                    "--task",
                    task,
                    "--port",
                    str(port),
                    "--policy-service-record",
                    str(service_record),
                    "--headless",
                    "--enable_cameras",
                    *arguments,
                ]
                run = {"condition": name, "command": command, "status": "running"}
                record["runs"].append(run)
                write_json(output / "pair.json", record)
                with (output / f"{name}.log").open("w") as log:
                    client = subprocess.Popen(
                        command,
                        env=sim_env,
                        cwd=source,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    run["pid"] = client.pid
                    write_json(output / "pair.json", record)
                    run["returncode"] = client.wait()
                run["status"] = "process_exited"
                # Native Kit shutdown can return zero after an exception. The
                # explicit error/result evidence determines evaluation status.
                try:
                    run["audit"] = audit_run(destination, run["returncode"])
                except (ValueError, KeyError, OSError) as exc:
                    run["audit"] = {"status": "audit_error", "error": str(exc), "native_success": None}
                write_json(output / "pair.json", record)
                print(json.dumps({"condition": name, "audit": run["audit"]}), flush=True)
    except BaseException as exc:
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        stop(client)
        stop(server)
        record["wall_seconds"] = time.monotonic() - started
        write_json(output / "pair.json", record)


if __name__ == "__main__":
    main()
