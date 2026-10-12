"""Bounded generation, physical execution and independent validation per request.

G1 samples from reset occupancy and replans each layout. G2 obtains one full
reference, reserves its occupancy, then replays it. G3 can make one event-guided
task-anchor change after failed completion and obtain a new physical reference.
All use the same assets, sampler, validator and explicit budgets. Asset creation is outside
this runner's timer and must be accounted for separately in formal experiments.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from infinidojo.completion import validate_request
from infinidojo.io import git_revision, sha256, write_json
from infinidojo.repair import (
    initial_positions,
    position_choices,
    rank_repairs,
    task_positions,
    with_positions,
)


def terminate_group(process):
    """Reap the owned child and stop any surviving descendants in its group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # A child can ignore SIGTERM after its parent exits.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_stage(command, log_path, *, deadline, cwd, env):
    """Bound only our child process group; retain logs even on timeout/error."""
    started = time.monotonic()
    record = {
        "command": [str(x) for x in command],
        "status": "not_started",
        "returncode": None,
        "wall_seconds": 0.0,
    }
    if started >= deadline:
        record["status"] = "budget_exhausted"
        return record
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process = None
    try:
        with log_path.open("wb") as stream:
            process = subprocess.Popen(
                command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True
            )
            record["pid"] = process.pid
            write_json(log_path.with_suffix(".stage.json"), {**record, "status": "running"})
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
                record["status"] = "completed" if process.returncode == 0 else "process_error"
            except subprocess.TimeoutExpired:
                record["status"] = "budget_exhausted"
                terminate_group(process)
            record["returncode"] = process.returncode
    except OSError as exc:
        if process is not None:
            terminate_group(process)
        record.update(status="process_error", error=f"{type(exc).__name__}: {exc}")
    except BaseException as exc:
        if process is not None:
            terminate_group(process)
        record.update(
            status="interrupted",
            error=type(exc).__name__,
            returncode=process.returncode if process else None,
            wall_seconds=time.monotonic() - started,
        )
        write_json(log_path.with_suffix(".stage.json"), record)
        raise
    record["wall_seconds"] = time.monotonic() - started
    record["deadline_overrun_seconds"] = max(0.0, time.monotonic() - deadline)
    if time.monotonic() > deadline:
        record["status"] = "budget_exhausted"
    write_json(log_path.with_suffix(".stage.json"), record)
    return record


def source_hashes(source):
    return {p.name: sha256(p) for p in sorted((source / "src/infinidojo").glob("*.py"))}


def read_json(path):
    return json.loads(path.read_text())


def run_request(args):
    started = time.monotonic()
    deadline = started + args.wall_seconds
    source = Path(__file__).resolve().parents[2]
    output, root = args.output.resolve(), args.robodojo_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    compilation = None
    compiler_context = None
    requested_assets = args.assets
    if getattr(args, "compiled", None):
        from infinidojo.stack_specification import load_compilation

        request, requested_assets, compiler_context, compilation = load_compilation(
            args.compiled, args.assets
        )
        compilation = {**compilation, "manifest_sha256": sha256(args.compiled / "compilation.json")}
    else:
        request = read_json(args.request)
    validate_request(request)
    frozen = source_hashes(source)
    for name in frozen:
        folder = output / "source/infinidojo"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "src/infinidojo" / name, folder / name)
    write_json(output / "request.json", request)
    assets = [folder.resolve() for folder in requested_assets]
    asset_records = []
    for folder in assets:
        record = read_json(folder / "provenance.json")
        for name, digest in record["files"].items():
            if sha256(folder / name) != digest:
                raise ValueError(f"Asset hash mismatch: {folder.name}/{name}")
        asset_records.append(
            {"asset_id": record["seed"], "provenance_sha256": sha256(folder / "provenance.json")}
        )
    if len({r["asset_id"] for r in asset_records}) != len(asset_records):
        raise ValueError("Asset pool contains duplicate identities")
    selected = np.random.default_rng(args.seed).integers(0, len(assets), request["clutter"]["count"]).tolist()
    write_json(output / "asset-selection.json", selected)
    native_layout = (
        args.layout.resolve()
        if args.layout
        else root / "Assets/Eval_Layout/RoboDojo/arx_x5/0/stack_blocks_0.json"
    )
    if compiler_context and sha256(native_layout) != compiler_context["native_layout_sha256"]:
        raise ValueError("Native layout differs from the language compiler context")
    shutil.copyfile(native_layout, output / "base-layout.json")
    base_layout = read_json(output / "base-layout.json")
    current_layout = with_positions(base_layout, initial_positions(request, base_layout, args.seed))
    write_json(output / "initial-layout.json", current_layout)
    choices = position_choices(request, base_layout)
    tried_positions = [task_positions(current_layout)]
    manifest = {
        "status": "running",
        "method": args.method,
        "seed": args.seed,
        "request": request,
        "infinidojo_commit": git_revision(source),
        "robodojo_commit": git_revision(root),
        "source_sha256": frozen,
        "initial_layout_sha256": sha256(output / "initial-layout.json"),
        "base_layout_sha256": sha256(output / "base-layout.json"),
        "initial_task_positions": task_positions(current_layout),
        "language_compilation": compilation,
        "repair_enabled": args.method == "g3" and not args.disable_repair,
        "task_position_updates": [],
        "assets": asset_records,
        "asset_indices": selected,
        "budgets": {
            "wall_seconds": args.wall_seconds,
            "max_layouts": args.max_layouts,
            "total_proposals": args.total_proposals,
            "proposals_per_layout": args.proposals_per_layout,
            "cluster_proposals": args.cluster_proposals,
            "physics_steps_per_execution": args.max_steps,
        },
        "cost_scope": "precomputed assets; includes reset/reference acquisition, all candidate generation, "
        "simulator startup/shutdown, planning, execution, independent validation and episode export",
        "asset_generation_seconds": None,
        "protocol_status": "development; thresholds and formal protocol not frozen",
        "attempts": [],
        "stages": [],
        "accepted_attempt": None,
        "total_proposals_used": 0,
        "unreported_proposals_upper_bound": 0,
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(source / "src"), env.get("PYTHONPATH", "")])
    sim_base = [
        str(args.sim_python.resolve()),
        "-u",
        "-m",
        "infinidojo.native_stack",
        "--robodojo-root",
        str(root),
        "--seed",
        str(args.seed),
        "--max-steps",
        str(args.max_steps),
        "--headless",
        "--enable_cameras",
    ]
    if not args.video:
        sim_base.append("--no-video")

    def save():
        manifest["wall_seconds"] = time.monotonic() - started
        manifest["deadline_overrun_seconds"] = max(0.0, manifest["wall_seconds"] - args.wall_seconds)
        if manifest["status"] == "accepted" and manifest["deadline_overrun_seconds"] > 0:
            manifest["status"] = "budget_exhausted"
            manifest["validated_but_over_budget_attempt"] = manifest["accepted_attempt"]
            manifest["accepted_attempt"] = None
        write_json(output / "run.json", manifest)

    def stage(name, command):
        if source_hashes(source) != frozen:
            raise RuntimeError("Source changed during request; use a new run after freezing code")
        result = run_stage(command, output / (name + ".log"), deadline=deadline, cwd=source, env=env)
        manifest["stages"].append({"name": name, **result})
        save()
        if source_hashes(source) != frozen:
            raise RuntimeError("Source changed during request; result cannot be accepted")
        if result["status"] != "completed":
            manifest["status"] = (
                "budget_exhausted" if result["status"] == "budget_exhausted" else "infrastructure_error"
            )
            return False
        return True

    def execution_result(folder):
        # Isaac can exit with code zero after a Python exception; check artifacts.
        if (folder / "error.json").exists() or not (folder / "result.json").exists():
            manifest["status"] = "infrastructure_error"
            manifest["error"] = (
                read_json(folder / "error.json")
                if (folder / "error.json").exists()
                else "Simulator did not produce result.json"
            )
            return None
        return read_json(folder / "result.json")

    save()
    try:
        state = output / "initial-state" if args.method == "g1" else output / "reference"
        mode = "--capture-initial-state" if args.method == "g1" else "--scene-aware"
        if not stage(
            "state-acquisition",
            [*sim_base, "--layout", str(output / "initial-layout.json"), "--output", str(state), mode],
        ):
            return manifest
        initial_result = execution_result(state)
        if initial_result is None:
            return manifest
        if args.method != "g1" and not initial_result.get("native_task_success"):
            manifest["status"] = "reference_unverified"
            return manifest
        pending_repair, repair_used = None, False
        for index in range(args.max_layouts):
            remaining_proposals = args.total_proposals - manifest["total_proposals_used"]
            if time.monotonic() >= deadline or remaining_proposals <= 0:
                manifest["status"] = "budget_exhausted"
                break
            attempt = output / f"attempt-{index:02d}"
            generation, execution = attempt / "generation", attempt / "execution"
            layout_seed = args.seed + index * 1_000_003
            row = {"index": index, "layout_seed": layout_seed, "status": "running"}
            row["proposal_limit"] = min(args.proposals_per_layout, remaining_proposals)
            manifest["attempts"].append(row)
            positions = None
            if args.method == "g1" and index:
                remaining = [choice for choice in choices if choice not in tried_positions]
                if remaining:
                    positions = remaining[int(np.random.default_rng(layout_seed).integers(len(remaining)))]
                    update = {"selection": "uniform_untried_task_positions", "after": positions}
            elif pending_repair is not None:
                positions = task_positions(current_layout)
                positions[pending_repair["anchor"]] = pending_repair["after_xy_m"]
                update = {
                    "selection": "event_conditioned_anchor_repair",
                    **pending_repair,
                    "after": positions,
                }
                pending_repair = None
            if positions is not None:
                current_layout = with_positions(base_layout, positions)
                tried_positions.append(positions)
                attempt.mkdir(parents=True, exist_ok=False)
                changed_layout = attempt / "task-layout.json"
                write_json(changed_layout, current_layout)
                state = attempt / "initial-state" if args.method == "g1" else attempt / "reference"
                update.update(attempt=index, layout_sha256=sha256(changed_layout))
                manifest["task_position_updates"].append(update)
                mode = "--capture-initial-state" if args.method == "g1" else "--scene-aware"
                if not stage(
                    f"attempt-{index:02d}/state-acquisition",
                    [*sim_base, "--layout", str(changed_layout), "--output", str(state), mode],
                ):
                    row["status"] = manifest["status"]
                    break
                changed_result = execution_result(state)
                if changed_result is None:
                    row["status"] = "infrastructure_error"
                    break
                if args.method != "g1" and not changed_result.get("native_task_success"):
                    manifest["status"] = row["status"] = "reference_unverified"
                    break
            row["task_positions"] = task_positions(current_layout)
            row["state_source"] = str(state.relative_to(output))
            command = [
                sys.executable,
                "-m",
                "infinidojo.completion",
                "--robodojo-root",
                str(root),
                "--reference",
                str(state),
                "--assets",
                *map(str, assets),
                "--request",
                str(output / "request.json"),
                "--output",
                str(generation),
                "--seed",
                str(layout_seed),
                "--asset-selection",
                str(output / "asset-selection.json"),
                "--attempt-budget",
                str(min(args.proposals_per_layout, remaining_proposals)),
                "--cluster-budget",
                str(args.cluster_proposals),
            ]
            if args.method == "g1":
                command.append("--initial-state-only")
            if not stage(f"attempt-{index:02d}/generation", command):
                row["status"] = manifest["status"]
                row["proposals"] = None
                manifest["unreported_proposals_upper_bound"] += row["proposal_limit"]
                break
            generated = read_json(generation / "generation.json")
            if generated["asset_indices"] != selected:
                raise RuntimeError("A retry changed the selected asset identities")
            row["proposals"] = generated["attempts"]
            manifest["total_proposals_used"] += generated["attempts"]
            if not generated["accepted"]:
                row["status"] = "layout_not_found"
                if (
                    args.method == "g3"
                    and not args.disable_repair
                    and not repair_used
                    and index + 1 < args.max_layouts
                ):
                    ranked = rank_repairs(
                        request,
                        current_layout,
                        generated,
                        read_json(state / "interaction-program.json"),
                        read_json(state / "robot-identities.json"),
                    )
                    write_json(
                        attempt / "repair-ranking.json",
                        {
                            "ranking": ranked,
                            "generation_sha256": sha256(generation / "generation.json"),
                            "program_sha256": sha256(state / "interaction-program.json"),
                            "note": "Directional search heuristic only; replan and execute the full task before use",
                        },
                    )
                    if ranked:
                        pending_repair, repair_used = ranked[0], True
                        row["repair_scheduled"] = pending_repair
                save()
                continue
            mode_args = ["--scene-aware"] if args.method == "g1" else ["--reference", str(state)]
            command = [
                *sim_base,
                "--layout",
                str(generation / "layout.json"),
                "--asset-map",
                str(generation / "asset-map.json"),
                "--output",
                str(execution),
                *mode_args,
            ]
            if not stage(f"attempt-{index:02d}/execution", command):
                row["status"] = manifest["status"]
                break
            result = execution_result(execution)
            if result is None:
                row["status"] = "infrastructure_error"
                break
            row.update(
                native_task_success=result["native_task_success"],
                controller_status=result["status"],
                controller_error=result["error"],
                physics_steps=result["steps"],
                motions_planned=len(result["plans"]),
            )
            validation = attempt / "validation.json"
            if not stage(
                f"attempt-{index:02d}/validation",
                [
                    sys.executable,
                    "-m",
                    "infinidojo.validation",
                    "--execution",
                    str(execution),
                    "--generation",
                    str(generation),
                    "--robodojo-root",
                    str(root),
                    "--output",
                    str(validation),
                ],
            ):
                row["status"] = manifest["status"]
                break
            audit = read_json(validation)
            row.update(status=audit["status"], failed_checks=audit["failed_checks"])
            if (
                audit["status"] != "validated_under_development_checks"
                or not audit["checks"]
                or not all(audit["checks"].values())
            ):
                save()
                continue
            if not stage(
                f"attempt-{index:02d}/export",
                [
                    sys.executable,
                    "-m",
                    "infinidojo.episode",
                    "--execution",
                    str(execution),
                    "--generation",
                    str(generation),
                    "--validation",
                    str(validation),
                    "--output",
                    str(output / "episode"),
                ],
            ):
                row["status"] = manifest["status"]
                break
            manifest.update(status="accepted", accepted_attempt=index)
            manifest["episode_manifest_sha256"] = sha256(output / "episode/episode.json")
            break
        else:
            manifest["status"] = "layout_budget_exhausted"
        return manifest
    except BaseException as exc:
        manifest.update(
            status="infrastructure_error" if isinstance(exc, Exception) else "interrupted",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        save()


def main():
    def stop(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=["g1", "g2", "g3"], required=True)
    parser.add_argument(
        "--disable-repair", action="store_true", help="G3 ablation: retain the first reference"
    )
    parser.add_argument("--robodojo-root", type=Path, required=True)
    parser.add_argument("--sim-python", type=Path, required=True)
    parser.add_argument("--assets", type=Path, nargs="+", required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--request", type=Path)
    inputs.add_argument("--compiled", type=Path, help="Accepted stack-v1 language compilation directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--layout", type=Path)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--wall-seconds", type=float, default=240)
    parser.add_argument("--max-layouts", type=int, default=4)
    parser.add_argument("--total-proposals", type=int, default=4000)
    parser.add_argument("--proposals-per-layout", type=int, default=1000)
    parser.add_argument("--cluster-proposals", type=int, default=128)
    parser.add_argument("--max-steps", type=int, default=20000)
    parser.add_argument("--video", action="store_true")
    args = parser.parse_args()
    if args.disable_repair and args.method != "g3":
        parser.error("--disable-repair is only meaningful for G3")
    if (
        not np.isfinite(args.wall_seconds)
        or min(
            args.wall_seconds,
            args.max_layouts,
            args.total_proposals,
            args.proposals_per_layout,
            args.cluster_proposals,
            args.max_steps,
        )
        <= 0
    ):
        parser.error("All budgets must be positive and finite")
    try:
        result = run_request(args)
    except FileExistsError:
        raise  # Never write into a pre-existing request directory.
    except Exception as exc:
        if args.output.is_dir():
            error = {"type": type(exc).__name__, "message": str(exc)}
            write_json(args.output / "error.json", error)
            if not (args.output / "run.json").exists():
                write_json(
                    args.output / "run.json",
                    {
                        "status": "infrastructure_error",
                        "method": args.method,
                        "seed": args.seed,
                        "error": error,
                        "phase": "preflight",
                        "accepted_attempt": None,
                    },
                )
        raise
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("method", "status", "accepted_attempt", "total_proposals_used", "wall_seconds")
            }
        )
    )
    if result["status"] == "infrastructure_error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
