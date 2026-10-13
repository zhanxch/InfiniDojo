"""Natural language -> procedural task assets -> interaction -> native episode.

Use --description for live compilation, or --compiled to continue a previously
accepted scene-v2 compilation. No repairs or policy inference are hidden here.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import numpy as np

from infinidojo.assets import matches_parameters, pickup_layout, verified_asset
from infinidojo.episode import load_episode
from infinidojo.frontend import compile_request
from infinidojo.io import git_revision, sha256, write_json
from infinidojo.request_run import run_stage, source_hashes
from infinidojo.scene_specification import load_compilation, make_context, minimal_layout, normalized_request


def run_route(args):
    output, root = args.output.resolve(), args.robodojo_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started, source = time.monotonic(), Path(__file__).resolve().parents[2]
    deadline = started + args.wall_seconds
    frozen = source_hashes(source)
    snapshot = output / "source/infinidojo"
    snapshot.mkdir(parents=True)
    for name in frozen:
        shutil.copyfile(source / "src/infinidojo" / name, snapshot / name)
    manifest = {
        "status": "running",
        "seed": args.seed,
        "profile": "scene-v2",
        "source_sha256": frozen,
        "infinidojo_commit": git_revision(source),
        "robodojo_commit": git_revision(root),
        "infinigen_commit": git_revision(args.infinigen_root),
        "wall_budget_seconds": args.wall_seconds,
        "stages": [],
        "cost_scope": "language compilation if requested, new asset creation, reference construction, scene completion, execution, export",
        "repair_enabled": False,
        "phase": "compilation",
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(source / "src"), env.get("PYTHONPATH", "")])

    def save():
        manifest["wall_seconds"] = time.monotonic() - started
        write_json(output / "route.json", manifest)

    def stage(name, command):
        if source_hashes(source) != frozen:
            raise RuntimeError("Source changed during route execution")
        manifest["phase"] = name
        save()
        result = run_stage(command, output / (name + ".log"), deadline=deadline, cwd=source, env=env)
        manifest["stages"].append({"name": name, **result})
        save()
        if source_hashes(source) != frozen:
            raise RuntimeError("Source changed during route execution")
        if result["status"] != "completed":
            manifest["status"] = (
                "budget_exhausted" if result["status"] == "budget_exhausted" else "infrastructure_error"
            )
            return False
        return True

    save()
    try:
        compiled = output / "compiled"
        if args.compiled:
            load_compilation(args.compiled, root)
            shutil.copytree(args.compiled, compiled)
            manifest["compilation_origin"] = "existing verified compilation; prior language cost excluded"
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                manifest["status"] = "budget_exhausted"
                return manifest
            result = compile_request(
                args.description.read_text(),
                compiled,
                attempts=args.llm_attempts,
                executable=args.llm_executable,
                model=args.model,
                profile="scene-v2",
                context=make_context(root),
                timeout=min(args.llm_timeout, remaining / args.llm_attempts),
            )
            write_json(compiled / "result.json", result)
            manifest["language_result"] = result
            manifest["compilation_origin"] = "live language compilation"
            if not result["accepted"]:
                manifest["status"] = result["status"]
                return manifest
        spec, compilation = load_compilation(compiled, root)
        manifest["compilation_sha256"] = sha256(compiled / "compilation.json")
        manifest["compilation_model"] = compilation["model"]
        manifest["task"] = spec["task"]
        generated_assets = {}
        profiles = {"clutter": spec["clutter"]["asset"]}
        if spec["target"]:
            profiles["target"] = spec["target"]["asset"]
        for role, profile in profiles.items():
            # Same role seed across appearance-only variants preserves geometry.
            asset_seed = args.seed * 16 + (1 if role == "clutter" else 0)
            folder = output / "assets" / role
            if not stage(
                f"asset-{role}",
                [
                    sys.executable,
                    "-m",
                    "infinidojo.assets",
                    "--infinigen-root",
                    str(args.infinigen_root.resolve()),
                    "--output",
                    str(folder),
                    "--seed",
                    str(asset_seed),
                    "--radius",
                    str(profile["radius_m"]),
                    "--height",
                    str(profile["height_m"]),
                    "--shape",
                    profile["shape"],
                    "--color",
                    profile["color"],
                ],
            ):
                return manifest
            if not matches_parameters(verified_asset(folder), profile):
                raise ValueError("Generated asset does not implement the compiled requirements")
            generated_assets[role] = folder
        task = spec["task"]
        template = root / f"Assets/Eval_Layout/RoboDojo/arx_x5/0/{task}_0.json"
        base = json.loads(template.read_text())
        layout = (
            pickup_layout(base, generated_assets["target"], spec["target"]["xy_m"])
            if task == "general_pickup"
            else minimal_layout(base, task)
        )
        write_json(output / "task-layout.json", layout)
        write_json(output / "generator-request.json", normalized_request(spec))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            manifest["status"] = "budget_exhausted"
            return manifest
        command = [
            sys.executable,
            "-m",
            "infinidojo.request_run",
            "--method",
            "g2",
            "--robodojo-root",
            str(root),
            "--sim-python",
            str(args.sim_python.resolve()),
            "--assets",
            str(generated_assets["clutter"]),
            "--request",
            str(output / "generator-request.json"),
            "--layout",
            str(output / "task-layout.json"),
            "--seed",
            str(args.seed),
            "--output",
            str(output / "generation"),
            "--wall-seconds",
            str(max(0.001, remaining - 2)),
            "--max-layouts",
            str(args.max_layouts),
            "--total-proposals",
            str(args.total_proposals),
            "--proposals-per-layout",
            str(args.proposals_per_layout),
            "--cluster-proposals",
            "128",
            "--max-steps",
            str(args.max_steps),
        ]
        if "target" in generated_assets:
            command.extend(["--task-assets", str(generated_assets["target"])])
        if args.video:
            command.append("--video")
        if not stage("generation", command):
            return manifest
        result = json.loads((output / "generation/run.json").read_text())
        manifest["status"] = result["status"]
        manifest["generation_run_sha256"] = sha256(output / "generation/run.json")
        if result["status"] == "accepted":
            episode, _, _ = load_episode(output / "generation/episode")
            if episode["task"] != spec["task"]:
                raise ValueError("Exported episode changed the requested task")
            manifest["episode"] = "generation/episode"
            manifest["episode_manifest_sha256"] = sha256(output / "generation/episode/episode.json")
        return manifest
    except BaseException as exc:
        manifest.update(
            status="infrastructure_error" if isinstance(exc, Exception) else "interrupted",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        if manifest["status"] == "accepted" and time.monotonic() > deadline:
            manifest["status"] = "budget_exhausted"
            manifest["note"] = "An episode exists, but the route exceeded its declared wall budget"
        save()


def main():
    def stop(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--description", type=Path, help="Natural-language request text file")
    inputs.add_argument("--compiled", type=Path, help="Previously accepted scene-v2 compilation")
    parser.add_argument("--robodojo-root", type=Path, required=True)
    parser.add_argument("--infinigen-root", type=Path, required=True)
    parser.add_argument("--sim-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--model")
    parser.add_argument("--llm-executable", default="codex")
    parser.add_argument("--llm-attempts", type=int, default=2)
    parser.add_argument("--llm-timeout", type=float, default=180)
    parser.add_argument("--wall-seconds", type=float, default=1800)
    parser.add_argument("--max-layouts", type=int, default=4)
    parser.add_argument("--total-proposals", type=int, default=4000)
    parser.add_argument("--proposals-per-layout", type=int, default=1000)
    parser.add_argument("--max-steps", type=int, default=20000)
    parser.add_argument("--video", action="store_true")
    args = parser.parse_args()
    budgets = [
        args.wall_seconds,
        args.llm_timeout,
        args.llm_attempts,
        args.max_layouts,
        args.total_proposals,
        args.proposals_per_layout,
        args.max_steps,
    ]
    if not np.isfinite(budgets).all() or min(budgets) <= 0 or not 0 <= args.seed < 2**27:
        parser.error("Budgets must be positive and finite; seed must be in [0,2**27)")
    result = run_route(args)
    print(json.dumps({key: result.get(key) for key in ("status", "task", "episode", "wall_seconds")}))
    if result["status"] == "infrastructure_error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
