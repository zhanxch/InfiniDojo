"""Development pair using the existing, explicitly named Codex EEF transport.

The adapter workspace must contain the pinned XPolicyLab checkout plus the
local Codex bridge. Its source is snapshotted by policy_eval for each episode.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

from infinidojo.episode import load_episode
from infinidojo.io import git_revision, sha256, write_json


def stop(process):
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter-workspace", type=Path, required=True)
    parser.add_argument("--robodojo-root", type=Path, required=True)
    parser.add_argument("--sim-python", type=Path, required=True)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--native-layout", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.adapter_workspace.resolve()
    xpl, root = workspace / "XPolicyLab", args.robodojo_root.resolve()
    python = args.sim_python.absolute()  # retain the virtualenv's symlink entrypoint
    output, episode = args.output.resolve(), args.episode.resolve()
    source = Path(__file__).resolve().parents[1]
    manifest, _, _ = load_episode(episode)
    if manifest["robodojo_commit"] != git_revision(root):
        raise ValueError("Episode and simulation revisions differ")
    if git_revision(xpl) != "f6e309e8ece20bfa33c0e5d7b05a5f3287522757":
        raise ValueError("This development configuration requires the declared XPolicyLab revision")
    policy = "GPT_6_Astra_Codex_EEF"
    for path in (workspace / "codex_bridge.py", xpl / "policy" / policy / "deploy.py"):
        if not path.is_file():
            raise FileNotFoundError(path)
    output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("L3_INSPECT_", "L4_INSPECT_", "CODEX_PLANNER_", "CODEX_CONTROL_", "ROBODOJO_", "INFINIDOJO_")
        )
    }
    base_prefix = subprocess.check_output(
        [str(python), "-c", "import sys; print(sys.base_prefix)"], text=True
    ).strip()
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(map(str, (source / "src", root, workspace, xpl))),
            "PYTHONUNBUFFERED": "1",
            "OMNI_KIT_ACCEPT_EULA": "YES",
            "ACCEPT_EULA": "Y",
            "LD_PRELOAD": str(Path(base_prefix) / "lib/libstdc++.so.6"),
            "CUDA_VISIBLE_DEVICES": "0",
            "ROBODOJO_ROOT": str(root),
            "EVAL_ENV_TYPE": "sim",
            "EVAL_NUM": "1",
            "L4_INSPECT_PLANNER": "astra",
            "L4_INSPECT_MODEL": "gpt-6-astra",
            "L4_INSPECT_REASONING_EFFORT": "medium",
            "L4_INSPECT_DEPTH": "off",
            "L4_INSPECT_KEEP_ALL_IMAGES": "0",
            "L4_INSPECT_IMAGE_HORIZON": "2",
            "L4_INSPECT_MAX_LLM_CALLS": "100",
            "L4_INSPECT_BASE_URL": "codex-cli://chatgpt-login",
            "CODEX_PLANNER_TIMEOUT_S": "240",
        }
    )
    server_command = [
        str(python),
        str(xpl / "setup_policy_server.py"),
        "--config_path",
        str(xpl / "policy" / policy / "deploy.yml"),
        "--overrides",
        f"port={port}",
        "host=localhost",
        "bench_name=RoboDojo",
        "task_name=stack_blocks",
        "ckpt_name=codex-account",
        "env_cfg_type=arx_x5",
        "seed=0",
        "action_dim=14",
    ]
    planned = [
        ("native", ["--native-layout", str(args.native_layout)]),
        ("generated", ["--episode", str(episode)]),
    ]
    record = {
        "development_only": True,
        "policy": policy,
        "official_api_reproduction": False,
        "model": "gpt-6-astra",
        "max_calls": 100,
        "reasoning_effort": "medium",
        "call_timeout_s": 240,
        "native_layout": args.native_layout,
        "episode_sha256": sha256(episode / "episode.json"),
        "infinidojo_commit": git_revision(source),
        "launcher_sha256": sha256(Path(__file__)),
        "server_command": server_command,
        "conditions": [x[0] for x in planned],
        "runs": [],
        "transport_differences": [
            "Codex base instructions",
            "serialized conversation roles",
            "structured JSON tool selection",
            "fresh CLI invocation per decision",
        ],
        "selection": "Layout 0 and the existing six-clutter language-generated episode, before scores.",
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
                server_command, env=env, cwd=xpl, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
            deadline = time.monotonic() + 120
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Policy service exited before readiness")
                try:
                    with socket.create_connection(("localhost", port), timeout=0.5):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Policy service readiness timed out")
                    time.sleep(0.5)
            for name, arguments in planned:
                if server.poll() is not None:
                    raise RuntimeError("Policy service exited")
                destination = output / name
                child_env = dict(
                    env,
                    L4_INSPECT_TRACE_DIR=str(destination / "traces"),
                    CODEX_PLANNER_TRACE_DIR=str(destination / "decisions"),
                )
                command = [
                    str(python),
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
                    policy,
                    "--port",
                    str(port),
                    "--headless",
                    "--enable_cameras",
                    *arguments,
                ]
                run = {"condition": name, "command": command, "status": "running"}
                record["runs"].append(run)
                write_json(output / "pair.json", record)
                with (output / f"{name}.log").open("w") as client_log:
                    client = subprocess.Popen(
                        command,
                        env=child_env,
                        cwd=source,
                        stdout=client_log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    run["pid"] = client.pid
                    write_json(output / "pair.json", record)
                    run["returncode"] = client.wait()
                    run["status"] = "process_exited"
                result_path = destination / "result.json"
                run["result"] = json.loads(result_path.read_text()) if result_path.exists() else None
                write_json(output / "pair.json", record)
                print(json.dumps(run), flush=True)
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
