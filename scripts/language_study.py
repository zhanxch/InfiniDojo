"""Run or resume explicit development descriptions; labels are scored afterwards."""

import argparse
import json
import math
import subprocess
from pathlib import Path

from infinidojo.frontend import compile_request
from infinidojo.io import sha256, write_json
from infinidojo.stack_specification import normalized_request, validate


def equivalent(actual, expected):
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and set(actual) == set(expected)
            and all(equivalent(actual[k], v) for k, v in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(equivalent(a, b) for a, b in zip(actual, expected, strict=True))
        )
    if isinstance(expected, (float, int)) and not isinstance(expected, bool):
        return (
            isinstance(actual, (float, int))
            and not isinstance(actual, bool)
            and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9)
        )
    return actual == expected


def canonical_request(request, context):
    request = json.loads(json.dumps(request))
    options = request.get("task_xy_options_m") or {
        label: [xy] for label, xy in context["initial_task_xy_m"].items()
    }
    request["task_xy_options_m"] = {label: sorted(points) for label, points in options.items()}
    if request["clutter"].get("pair_distance_m") is None:
        request["clutter"].pop("pair_distance_m", None)
    return request


def score(proposal, case, context):
    if proposal is None or validate(proposal, context):
        return {"intrinsic_valid": False, "request_faithful": False}
    faithful = proposal["status"] == case["expected_status"]
    if faithful and proposal["status"] == "ready":
        faithful = equivalent(
            canonical_request(normalized_request(proposal), context),
            canonical_request(case["expected_request"], context),
        ) and set(proposal["asset_pool_ids"]) == set(case["expected_asset_pool_ids"])
    return {"intrinsic_valid": True, "request_faithful": faithful}


def run(args):
    study = json.loads(args.spec.read_text())
    context = json.loads(args.context.read_text())
    for name, expected in (("study-config.json", study), ("context.json", context)):
        existing = args.output / name
        if existing.exists() and (not args.resume or json.loads(existing.read_text()) != expected):
            raise ValueError("Study configuration differs or resume was not requested; use a fresh output")
    # Validate all existing cases before writing any study-level metadata.
    for case in study["cases"]:
        folder = args.output / case["id"]
        if folder.exists():
            if not args.resume:
                raise FileExistsError(f"Use --resume for a completed existing case: {folder}")
            if (
                not (folder / "result.json").is_file()
                or (folder / "request.txt").read_text() != case["description"]
                or json.loads((folder / "context.json").read_text()) != context
                or json.loads((folder / "provenance.json").read_text())["model_argument"] != study["model"]
            ):
                raise ValueError("Existing case is incomplete or its compiler inputs differ")
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "study-config.json", study)
    write_json(args.output / "context.json", context)
    rows = []
    for case in study["cases"]:
        folder = args.output / case["id"]
        if folder.exists():
            result = json.loads((folder / "result.json").read_text())
        else:
            print(f"START {case['id']}", flush=True)
            try:
                result = compile_request(
                    case["description"],
                    folder,
                    attempts=study["max_attempts"],
                    executable=args.executable,
                    model=study["model"],
                    timeout=study["timeout_per_call_s"],
                    profile=study["profile"],
                    context=context,
                )
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                result = {
                    "accepted": False,
                    "status": "infrastructure_error",
                    "error_type": type(exc).__name__,
                }
            write_json(folder / "result.json", result)
        history_path = folder / "history.json"
        history = json.loads(history_path.read_text()) if history_path.exists() else []
        first = score(history[0]["proposal"], case, context) if history else None
        final = score(result.get("specification"), case, context)
        row = {
            "id": case["id"],
            "expected_status": case["expected_status"],
            "status": result["status"],
            "accepted": result["accepted"],
            "attempts": len(history),
            "first": first,
            "final": final,
            "wall_seconds": result.get("wall_seconds"),
            "result_sha256": sha256(folder / "result.json"),
        }
        rows.append(row)
        write_json(args.output / "case-rows.json", rows)
        print(json.dumps(row), flush=True)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--resume", action="store_true")
    run(parser.parse_args())
