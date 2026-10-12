"""Reproducible v0 study stages. Large assets and rollouts stay outside Git."""

from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

from infinidojo.io import git_revision, sha256, write_json

SEEDS = list(range(1100, 1106))
COUNTS = [0, 6, 12]
METHODS = ["independent", "static", "interaction"]


def request_for(count, paraphrase=0):
    if paraphrase == 0:
        return (
            f"Create a pickup scene with exactly {count} clutter objects. The target is an Infinigen can "
            "with radius 2.5 cm and height 7 cm at x=-23 cm, y=-5 cm. "
            "Keep complete clutter footprints in x=[-45,25] cm and y=[-25,20] cm, "
            "with 5 mm clearance. Use the left X5 arm to lift the target 15 cm."
        )
    return (
        f"Let the left X5 arm pick up a can by 0.15 metres. Place {count} other cans on the table; "
        "their full footprints must lie between -0.45 and 0.25 m in x, and -0.25 and 0.20 m in y. "
        "Allow 0.005 m clearance. The target stands at (-0.23,-0.05) m in XY; "
        "its height is 0.07 m and radius is 0.025 m. Include the interaction events."
    )


def expected_for(count):
    return {
        "target": {"radius_m": 0.025, "height_m": 0.07, "xy_m": [-0.23, -0.05]},
        "clutter": {
            "count": count,
            "x_bounds_m": [-0.45, 0.25],
            "y_bounds_m": [-0.25, 0.2],
            "clearance_m": 0.005,
        },
        "lift_m": 0.15,
    }


def llm_study(output, executable, model):
    from infinidojo.frontend import compile_request

    results = []
    for count in COUNTS:
        for paraphrase in (0, 1):
            case = output / "llm" / f"count{count}-wording{paraphrase}"
            result = compile_request(
                request_for(count, paraphrase),
                case,
                expected=expected_for(count),
                executable=executable,
                model=model,
            )
            results.append({"count": count, "wording": paraphrase, **result})
            write_json(case / "result.json", result)
            print(f"LLM count={count} wording={paraphrase}: accepted={result['accepted']}", flush=True)
    write_json(output / "llm_results.json", results)


def prepare_assets(output, infinigen_root):
    from infinidojo.assets import generate_can

    for seed in SEEDS:
        generate_can(seed, output / "assets" / f"{seed:05d}", infinigen_root, radius=0.025, height=0.07)
        print(f"Generated Infinigen asset {seed}", flush=True)


def prepare_references(output, robodojo_root):
    from infinidojo.geometry import extract_robot_bounds
    from infinidojo.native import build_layout

    write_json(output / "robot_bounds.json", extract_robot_bounds(robodojo_root / "Assets/Robots/x5/ARX.usd"))
    asset_map = {str(seed): str(output / "assets" / f"{seed:05d}") for seed in SEEDS}
    cases = []
    for seed in SEEDS:
        layout = build_layout(
            robodojo_root, Path(asset_map[str(seed)]), output / "prepare_overlay", seed, [-0.23, -0.05]
        )
        path = output / "reference_layouts" / f"{seed}.json"
        write_json(path, layout)
        cases.append(
            {
                "id": str(seed),
                "seed": seed,
                "layout": str(path),
                "asset": asset_map[str(seed)],
                "asset_map": asset_map,
            }
        )
    write_json(output / "reference_cases.json", cases)


def prepare_layouts(output):
    from infinidojo.generation import generate_layout
    from infinidojo.geometry import audit_clutter, cluster_frames

    bounds = json.loads((output / "robot_bounds.json").read_text())
    pool, asset_map = [], {}
    for seed in SEEDS:
        folder = output / "assets" / f"{seed:05d}"
        provenance = json.loads((folder / "provenance.json").read_text())
        pool.append({"asset_id": seed, "bounds": provenance["bounds"]})
        asset_map[str(seed)] = str(folder)
    rows, cases = [], []
    for seed in SEEDS:
        reference = output / "references" / str(seed)
        result = json.loads((reference / "result.json").read_text())
        if not result["native_lift_success"]:
            # Retain all 9 failed requests. Do not replace a difficult seed.
            for count in COUNTS:
                for method in METHODS:
                    rows.append(
                        {
                            "id": f"{seed}-{count}-{method}",
                            "seed": seed,
                            "count": count,
                            "method": method,
                            "accepted": False,
                            "reason": "reference_failed",
                        }
                    )
            continue
        target_bounds = next(p["bounds"] for p in pool if p["asset_id"] == seed)
        trajectory = json.loads((reference / "trajectory.json").read_text())
        frames = cluster_frames(trajectory, bounds, target_bounds)
        base = json.loads((output / "reference_layouts" / f"{seed}.json").read_text())
        for count in COUNTS:
            spec_path = output / "llm" / f"count{count}-wording0/specification.json"
            spec = json.loads(spec_path.read_text())
            for method in METHODS:
                case_id = f"{seed}-{count}-{method}"
                folder = output / "generated" / case_id
                generated = generate_layout(spec, pool, frames, seed=seed, method=method, attempt_budget=500)
                write_json(folder / "generation.json", generated)
                audit = audit_clutter(frames, generated["clutter"])
                write_json(folder / "reference_audit.json", audit)
                row = {
                    "id": case_id,
                    "seed": seed,
                    "count": count,
                    "method": method,
                    **{k: v for k, v in generated.items() if k not in ("clutter", "proposals")},
                    "reference_audit_clear": audit["clear"],
                }
                rows.append(row)
                if generated["accepted"]:
                    layout = copy.deepcopy(base)
                    target = layout["Rigid"]["infinigen_can"][0]
                    for item in generated["clutter"]:
                        obj = copy.deepcopy(target)
                        obj.update(
                            label=item["id"],
                            category_idx=item["asset_id"],
                            default_pos=[*item["pose"][:2], 0.767],
                            default_ori=item["pose"][3:],
                        )
                        layout["Rigid"]["infinigen_can"].append(obj)
                    path = folder / "layout.json"
                    write_json(path, layout)
                    cases.append(
                        {
                            "id": case_id,
                            "seed": seed,
                            "layout": str(path),
                            "reference": str(reference),
                            "asset_map": asset_map,
                        }
                    )
                write_json(output / "generation_rows.json", rows)
                print(f"{case_id}: accepted={generated['accepted']} audit_clear={audit['clear']}", flush=True)
    # Randomize execution order to distribute any simulator history effects.
    import numpy as np

    order = np.random.default_rng(710).permutation(len(cases))
    write_json(output / "evaluation_cases.json", [cases[int(i)] for i in order])


def freeze(output):
    root = Path(__file__).resolve().parents[2]
    manifest = {
        "time_unix": time.time(),
        "source_commit": git_revision(root),
        "seeds": SEEDS,
        "clutter_counts": COUNTS,
        "methods": METHODS,
        "asset_parameters": {"radius": 0.025, "height": 0.07},
        "candidate_budget": 500,
        "physics_dt": 0.004,
        "trajectory_sample_dt": 0.02,
        "initial_layout_xy_tolerance_m": 0.01,
        "protocol": sha256(root / "docs/evaluation.md"),
        "source_files": {str(p.relative_to(root)): sha256(p) for p in sorted((root / "src").rglob("*.py"))},
    }
    path = output / "freeze.json"
    if path.exists():
        raise FileExistsError("Freeze already exists; do not silently replace a frozen study")
    write_json(path, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["freeze", "llm", "assets", "references", "layouts"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--infinigen-root", type=Path)
    parser.add_argument("--robodojo-root", type=Path)
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--model", default="gpt-6-sol")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.stage == "freeze":
        freeze(output)
    elif args.stage == "llm":
        llm_study(output, args.executable, args.model)
    elif args.stage == "assets":
        prepare_assets(output, args.infinigen_root.resolve())
    elif args.stage == "references":
        prepare_references(output, args.robodojo_root.resolve())
    elif args.stage == "layouts":
        prepare_layouts(output)


if __name__ == "__main__":
    main()
