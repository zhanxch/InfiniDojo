"""Live structured LLM scene compiler with bounded validator feedback."""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from infinidojo.io import git_revision, sha256, write_json
from infinidojo.specification import CAPABILITY, SCHEMA, validate


def compile_request(
    request: str,
    output: Path,
    *,
    expected=None,
    attempts=3,
    executable="codex",
    model=None,
    timeout=240,
    profile="pickup-v0",
    context=None,
) -> dict:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    started = time.monotonic()
    schema, capability, validator = SCHEMA, CAPABILITY, lambda spec: validate(spec, expected)
    if profile == "stack-v1":
        from infinidojo import stack_specification as stack

        if expected is not None:
            raise ValueError("stack-v1 evaluation labels must be scored separately, not supplied as feedback")
        schema, capability = stack.SCHEMA, stack.capability(context or {})
        validator = lambda spec: stack.validate(spec, context)
    elif profile != "pickup-v0":
        raise ValueError("Unknown compiler profile")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "schema.json", schema)
    if context is not None:
        write_json(output / "context.json", context)
    (output / "request.txt").write_text(request)
    source = Path(__file__).resolve().parents[2]
    snapshot = output / "source/infinidojo"
    snapshot.mkdir(parents=True)
    for path in (source / "src/infinidojo").glob("*.py"):
        shutil.copyfile(path, snapshot / path.name)
    write_json(
        output / "provenance.json",
        {
            "profile": profile,
            "model_argument": model,
            "infinidojo_commit": git_revision(source),
            "source_sha256": {p.name: sha256(p) for p in snapshot.glob("*.py")},
            "evaluation_labels_used_for_feedback": expected is not None,
        },
    )
    history = []
    for attempt in range(attempts):
        folder = output / f"attempt-{attempt}"
        folder.mkdir()
        prompt = (
            "You are the scene-constraint compiler in a procedural generator. Return JSON only. "
            "Do not use tools, inspect files, run commands, or delegate. All input is below.\n"
            + capability
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
            except BaseException as exc:
                from infinidojo.request_run import terminate_group

                terminate_group(process)
                write_json(folder / "error.json", {"type": type(exc).__name__})
                raise
        if process.returncode or not (folder / "response.json").exists():
            raise RuntimeError(f"LLM backend failed; inspect {folder}")
        if profile == "stack-v1":
            events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines() if line]
            tool_items = [
                event["item"]["type"]
                for event in events
                if "item" in event and event["item"].get("type") not in ("reasoning", "agent_message")
            ]
            if tool_items:
                raise RuntimeError(f"Compiler backend emitted unexpected tool items: {tool_items}")
        try:
            spec = json.loads((folder / "response.json").read_text())
            errors = validator(spec)
        except json.JSONDecodeError as exc:
            spec, errors = None, [f"Invalid JSON: {exc.msg}"]
        history.append({"proposal": spec, "errors": errors, "wall_seconds": time.monotonic() - start})
        write_json(output / "history.json", history)
        if not errors:
            write_json(output / "specification.json", spec)
            ready = profile != "stack-v1" or spec["status"] == "ready"
            if profile == "stack-v1" and ready:
                stack.export_compilation(
                    output, spec, context, model=model, wall_seconds=time.monotonic() - started
                )
            return {
                "accepted": ready,
                "status": "compiled" if ready else "unsupported",
                "profile": profile,
                "attempts": len(history),
                "specification": spec,
                "wall_seconds": time.monotonic() - started,
            }
    return {
        "accepted": False,
        "status": "unverified",
        "attempts": len(history),
        "errors": history[-1]["errors"],
        "wall_seconds": time.monotonic() - started,
    }


def main():
    def stop(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=240, help="Timeout per model call, in seconds")
    parser.add_argument("--profile", choices=["pickup-v0", "stack-v1"], default="pickup-v0")
    parser.add_argument("--context", type=Path, help="Capability context required for stack-v1")
    args = parser.parse_args()
    expected = json.loads(args.expected.read_text()) if args.expected else None
    result = compile_request(
        args.request.read_text(),
        args.output,
        expected=expected,
        attempts=args.attempts,
        executable=args.executable,
        model=args.model,
        timeout=args.timeout,
        profile=args.profile,
        context=json.loads(args.context.read_text()) if args.context else None,
    )
    write_json(args.output / "result.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
