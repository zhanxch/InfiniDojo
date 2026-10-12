"""Live structured LLM scene compiler with bounded validator feedback."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from infinidojo.io import write_json
from infinidojo.specification import CAPABILITY, SCHEMA, validate


def compile_request(
    request: str, output: Path, *, expected=None, attempts=3, executable="codex", model=None, timeout=240
) -> dict:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "schema.json", SCHEMA)
    (output / "request.txt").write_text(request)
    history = []
    for attempt in range(attempts):
        folder = output / f"attempt-{attempt}"
        folder.mkdir()
        prompt = (
            "You are the scene-constraint compiler in a procedural generator. Return JSON only. "
            "Do not use tools, inspect files, run commands, or delegate. All input is below.\n"
            + CAPABILITY
            + "\nSCENE REQUEST:\n"
            + request
            + "\nPREVIOUS PROPOSALS AND VALIDATOR FEEDBACK:\n"
            + json.dumps(history)
        )
        (folder / "prompt.txt").write_text(prompt)
        command = [
            executable,
            "--no-daemon",
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-c",
            'web_search="disabled"',
            "--output-schema",
            str(output / "schema.json"),
            "--output-last-message",
            str(folder / "response.json"),
            "--json",
        ]
        if model:
            command += ["--model", model]
        command += ["-"]
        start = time.monotonic()
        write_json(folder / "invocation.json", {"command": command, "backend": "Codex CLI"})
        with (
            tempfile.TemporaryDirectory(prefix="infinidojo-llm-") as cwd,
            (folder / "events.jsonl").open("w") as stdout,
            (folder / "stderr.log").open("w") as stderr,
        ):
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
            try:
                process.communicate(prompt, timeout=timeout)
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
                raise
        if process.returncode or not (folder / "response.json").exists():
            raise RuntimeError(f"LLM backend failed; inspect {folder}")
        spec = json.loads((folder / "response.json").read_text())
        errors = validate(spec, expected)
        history.append({"proposal": spec, "errors": errors, "wall_seconds": time.monotonic() - start})
        write_json(output / "history.json", history)
        if not errors:
            write_json(output / "specification.json", spec)
            return {"accepted": True, "attempts": len(history), "specification": spec}
    return {"accepted": False, "attempts": len(history), "errors": history[-1]["errors"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--model")
    args = parser.parse_args()
    expected = json.loads(args.expected.read_text()) if args.expected else None
    result = compile_request(
        args.request.read_text(),
        args.output,
        expected=expected,
        attempts=args.attempts,
        executable=args.executable,
        model=args.model,
    )
    write_json(args.output / "result.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
