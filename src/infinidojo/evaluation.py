"""Aggregate every requested instance, including rejection and infrastructure failure."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

from infinidojo.geometry import aabb, overlap_aabb, overlap_obb, transformed_box
from infinidojo.io import sha256, write_json
from infinidojo.study import METHODS, SEEDS


def wilson(successes, total, z=1.959963984540054):
    if not total:
        return None
    p = successes / total
    d = 1 + z * z / total
    center = (p + z * z / (2 * total)) / d
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return [max(0, center - half), min(1, center + half)]


def audit_execution(trajectory, robot_bounds, objects):
    """Use observed object poses rather than the generator's rigid attachment."""
    collisions = {}
    for frame in trajectory:
        bodies = []
        for ri, robot in enumerate(frame["robots"]):
            for name, pose in zip(robot["body_names"], robot["body_poses"], strict=True):
                bodies.append((f"robot{ri}/{name}", transformed_box(robot_bounds[name], pose)))
        obstacles = []
        for name, pose in frame["objects"].items():
            info = objects[name]
            box = transformed_box(info["bounds"], pose)
            if info["label"] == "target":
                bodies.append(("target", box))
            else:
                obstacles.append((info["label"], box, aabb(box)))
        for name, body in bodies:
            body_aabb = aabb(body)
            for label, obstacle, obstacle_aabb in obstacles:
                key = (name, label, frame["phase"])
                if key in collisions:
                    continue
                if overlap_aabb(body_aabb, obstacle_aabb) and overlap_obb(body, obstacle):
                    collisions[key] = {
                        "body": name,
                        "clutter_id": label,
                        "phase": frame["phase"],
                        "time_s": frame["time_s"],
                    }
    return {
        "clear": not collisions,
        "intersections": list(collisions.values()),
        "frames": len(trajectory),
        "sample_period_s": 0.02,
        "method": "observed poses, native collider bounding boxes, oriented-box SAT",
    }


def summarize(output: Path):
    rows = json.loads((output / "generation_rows.json").read_text())
    robot_bounds = json.loads((output / "robot_bounds.json").read_text())
    assets = {
        seed: json.loads((output / "assets" / f"{seed:05d}" / "provenance.json").read_text())
        for seed in SEEDS
    }
    for row in rows:
        folder = output / "evaluation" / row["id"]
        result_path = folder / "result.json"
        row["execution_available"] = result_path.exists()
        row["native_success"] = False
        row["usable"] = False
        if result_path.exists():
            result = json.loads(result_path.read_text())
            row["native_success"] = bool(result["native_lift_success"])
            row["lift_m"] = result["lift_m"]
            row["initial_layout_preserved"] = result["initial_layout_preserved_1cm"]
            row["execution_seconds"] = result["wall_seconds"]
            layout = json.loads((output / "generated" / row["id"] / "layout.json").read_text())
            by_label = {v["label"]: v for v in layout["Rigid"]["infinigen_can"]}
            objects = {
                name: {"label": label, "bounds": assets[by_label[label]["category_idx"]]["bounds"]}
                for name, label in result["object_labels"].items()
            }
            trajectory_path = folder / "trajectory.json"
            trace = json.loads(trajectory_path.read_text())
            audit = audit_execution(trace, robot_bounds, objects)
            write_json(folder / "execution_audit.json", audit)
            row["execution_audit_clear"] = audit["clear"]
            row["execution_intersections"] = len(audit["intersections"])
            row["usable"] = bool(
                row["accepted"]
                and row["reference_audit_clear"]
                and row["native_success"]
                and row["initial_layout_preserved"]
                and audit["clear"]
            )
            row["result_sha256"] = sha256(result_path)
            row["trajectory_sha256"] = sha256(trajectory_path)
        write_json(output / "results" / "instances.json", rows)
        print(
            f"Audited {row['id']}: available={row['execution_available']} usable={row['usable']}", flush=True
        )
    summary = {"requested": len(rows), "by_method": {}, "paired_seed_differences": {}}
    for method in METHODS:
        selected = [r for r in rows if r["method"] == method]
        totals = {
            key: sum(bool(r.get(key, False)) for r in selected)
            for key in [
                "accepted",
                "execution_available",
                "native_success",
                "reference_audit_clear",
                "initial_layout_preserved",
                "execution_audit_clear",
                "usable",
            ]
        }
        summary["by_method"][method] = {
            "requested": len(selected),
            **totals,
            "usable_wilson95": wilson(totals["usable"], len(selected)),
            "native_success_wilson95": wilson(totals["native_success"], len(selected)),
            "mean_generation_seconds": float(np.mean([r.get("wall_seconds", 0) for r in selected])),
            "mean_candidate_attempts": float(np.mean([r.get("attempts", 0) for r in selected])),
            "by_clutter_count": {
                str(count): {
                    "requested": sum(r["count"] == count for r in selected),
                    "usable": sum(r["count"] == count and r["usable"] for r in selected),
                    "native_success": sum(r["count"] == count and r["native_success"] for r in selected),
                }
                for count in [0, 6, 12]
            },
        }
    for baseline in ["independent", "static"]:
        differences = []
        for seed in SEEDS:
            target = [r for r in rows if r["seed"] == seed and r["method"] == "interaction"]
            base = [r for r in rows if r["seed"] == seed and r["method"] == baseline]
            differences.append(
                sum(r["usable"] for r in target) / len(target) - sum(r["usable"] for r in base) / len(base)
            )
        summary["paired_seed_differences"][baseline] = {
            "seeds": SEEDS,
            "differences": differences,
            "mean": float(np.mean(differences)),
        }
    llm = json.loads((output / "llm_results.json").read_text())
    summary["llm"] = {
        "requests": len(llm),
        "one_shot_accepted": sum(r["accepted"] and r["attempts"] == 1 for r in llm),
        "refinement_accepted": sum(r["accepted"] for r in llm),
        "calls": sum(r["attempts"] for r in llm),
    }
    summary["assets"] = {
        "seeds": SEEDS,
        "unique_mesh_hashes": len({a["files"]["mesh.npz"] for a in assets.values()}),
        "shape_counts": dict(Counter(a["parameters"]["shape"] for a in assets.values())),
    }
    summary["interpretation"] = (
        "Descriptive v0 pilot. Frozen privileged controls, one pickup task and one asset family. "
        "Usable requires accepted generation, reference/observed collision-box audits, preserved initial XY "
        "within 1 cm and native lift success. Bounding-box audits can reject physically non-colliding scenes; "
        "20 ms sampling cannot prove continuous collision freedom. Wilson intervals are descriptive; "
        "counts from the same six seeds are correlated. No generalist policy accuracy is measured."
    )
    write_json(output / "results" / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(summarize(args.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
