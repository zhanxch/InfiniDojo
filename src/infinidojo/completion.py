"""Fixed-reference scene completion for complete native manipulation tasks.

This is G2, not a new joint optimizer. Reserve every recorded robot and task
object pose, including released objects and the final return. Small rigid
clutter pairs can preserve a requested spatial relation during placement.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import time
from pathlib import Path

import numpy as np

from infinidojo.geometry import aabb, extract_robot_bounds, overlap_aabb, transformed_box
from infinidojo.io import git_revision, sha256, write_json
from infinidojo.repair import validate_position_options


def validate_request(request):
    validate_position_options(request)
    if request["task"] not in ("stack_blocks", "general_pickup", "cover_blocks"):
        raise ValueError("Unsupported complete native task contract")
    if request["task"] != "stack_blocks" and "task_xy_options_m" in request:
        raise ValueError("Task-position repair is only connected for stacking")
    clutter = request["clutter"]
    region = np.asarray(clutter["region_xy_m"], dtype=float)
    count = clutter["count"]
    if not isinstance(count, int) or not 0 <= count <= 20:
        raise ValueError("Clutter count must be an integer in [0, 20]")
    if region.shape != (2, 2) or not np.isfinite(region).all() or np.any(region[0] >= region[1]):
        raise ValueError("Invalid XY region")
    if np.any(region[0] < [-0.55, -0.35]) or np.any(region[1] > [0.55, 0.35]):
        raise ValueError("Region outside supported tabletop")
    if not 0 <= clutter["clearance_m"] <= 0.03:
        raise ValueError("Unsupported clearance")
    distance = clutter.get("pair_distance_m")
    if distance is not None and (not 0.05 <= distance <= 0.2 or count % 2):
        raise ValueError("Paired clutter needs an even count and separation in [0.05, 0.2] m")


def reference_boxes(trajectory, robot_bounds, object_bounds, fixtures=()):
    """Use all measured task-object poses, including passive and released ones."""
    frames = []
    for frame in trajectory:
        bodies = []
        for index, robot in enumerate(frame["robots"]):
            for name, pose in zip(robot["body_names"], robot["body_poses"], strict=True):
                bodies.append((f"robot{index}/{name}", transformed_box(robot_bounds[name], pose)))
        for label, bounds in object_bounds.items():
            bodies.append((label, transformed_box(bounds, frame["objects"][label])))
        bodies.extend(fixtures)
        frames.append(
            {
                "step": frame["step"],
                "event_id": frame.get("event_id"),
                "phase": frame["phase"],
                "time_s": frame["time_s"],
                "bodies": bodies,
            }
        )
    return frames


def complete_scene(
    request,
    pool,
    frames,
    *,
    seed,
    tabletop_z,
    attempt_budget=1000,
    cluster_budget=128,
    occupancy="full_reference",
    selected_indices=None,
):
    validate_request(request)
    if occupancy not in ("full_reference", "initial_state"):
        raise ValueError("Unknown occupancy scope")
    if occupancy == "initial_state" and len(frames) != 1:
        raise ValueError("Initial-state sampling must receive exactly one observed state")
    if not pool or not frames or attempt_budget < 1 or cluster_budget < 1:
        raise ValueError("Asset pool, reference and positive attempt budget required")
    started = time.monotonic()
    clutter = request["clutter"]
    count = clutter["count"]
    region = np.asarray(clutter["region_xy_m"])
    margin = clutter["clearance_m"]
    distance = clutter.get("pair_distance_m")
    rng = np.random.default_rng(seed)
    asset_indices = rng.integers(0, len(pool), count)
    if selected_indices is not None:
        if len(selected_indices) != count or any(
            not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(pool) for i in selected_indices
        ):
            raise ValueError("Fixed asset selection must match the request count and pool")
        # Consume the same RNG draw as the legacy entry point, while preserving
        # identities across layout retries with different sampling seeds.
        asset_indices = np.asarray(selected_indices, dtype=int)
    reserved = np.asarray([aabb(box) for frame in frames for _, box in frame["bodies"]])
    identities = [
        (frame.get("event_id"), frame.get("phase", "initial"), body)
        for frame in frames
        for body, _ in frame["bodies"]
    ]
    placed, occupied, proposals = [], [], []
    best_partial, restarts = [], 0
    group_size = 2 if distance is not None else 1
    while len(placed) < count and len(proposals) < attempt_budget:
        first = len(placed)
        group_start = len(proposals)
        while len(proposals) < attempt_budget and len(proposals) - group_start < cluster_budget:
            center = rng.uniform(region[0], region[1])
            yaw = float(rng.uniform(-np.pi, np.pi))
            axis = np.array([np.cos(yaw), np.sin(yaw)])
            members, boxes = [], []
            for member in range(group_size):
                asset = pool[int(asset_indices[first + member])]
                offset = (member - 0.5) * distance * axis if distance is not None else np.zeros(2)
                bounds = np.asarray(asset["bounds"])
                pose = [*(center + offset), tabletop_z - bounds[0, 2], np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)]
                box = aabb(transformed_box(bounds, pose))
                boxes.append(box)
                members.append(
                    {
                        "id": f"clutter_{first + member}",
                        "asset_id": asset["asset_id"],
                        "bounds": asset["bounds"],
                        "pose": pose,
                        "cluster": first // group_size,
                    }
                )
            reason, blockers = None, []
            if any(np.any(box[0, :2] < region[0]) or np.any(box[1, :2] > region[1]) for box in boxes):
                reason = "footprint_outside_request"
            elif any(
                overlap_aabb(box, other, margin)
                for index, box in enumerate(boxes)
                for other in [*occupied, *boxes[:index]]
            ):
                reason = "clutter_overlap"
            elif len(reserved):
                hits = np.zeros(len(reserved), dtype=bool)
                for box in boxes:
                    hits |= np.all(box[0] < reserved[:, 1] + margin, axis=1) & np.all(
                        reserved[:, 0] < box[1] + margin, axis=1
                    )
                if hits.any():
                    reason = f"{occupancy}_occupancy"
                    blockers = [
                        {"event_id": event, "phase": phase, "body": body}
                        for event, phase, body in sorted(
                            {identities[i] for i in np.flatnonzero(hits)}, key=str
                        )
                    ]
            proposals.append(
                {
                    "restart": restarts,
                    "cluster": first // group_size,
                    "poses": [m["pose"] for m in members],
                    "rejection": reason,
                    "blockers": blockers,
                }
            )
            if reason is None:
                placed.extend(members)
                occupied.extend(boxes)
                break
        else:
            if len(placed) > len(best_partial):
                best_partial = list(placed)
            # Avoid spending the entire budget behind an unlucky early packing.
            # Asset identities and request constraints remain fixed on restart.
            placed, occupied = [], []
            restarts += 1
    if len(best_partial) > len(placed):
        placed = best_partial
    return {
        "method": "G2_fixed_reference_completion" if occupancy == "full_reference" else "G1_static_candidate",
        "occupancy": occupancy,
        "accepted": len(placed) == count,
        "requested_count": count,
        "generated_count": len(placed),
        "seed": seed,
        "asset_indices": asset_indices.tolist(),
        "clutter": placed,
        "proposals": proposals,
        "attempt_budget": attempt_budget,
        "cluster_budget": cluster_budget,
        "restarts": restarts,
        "attempts": len(proposals),
        "reason": None if len(placed) == count else "candidate_budget_exhausted",
        "wall_seconds": time.monotonic() - started,
    }


def add_clutter(base, clutter):
    layout = copy.deepcopy(base)
    entries = layout["Rigid"].setdefault("infinigen_can", [])
    for item in clutter:
        entries.append(
            {
                "category": "infinigen_can",
                "category_idx": item["asset_id"],
                "label": item["id"],
                "default_pos": item["pose"][:3],
                "default_ori": item["pose"][3:],
                "scale": [1, 1, 1],
                "physics": {"type": "rigid", "mass": 0.08, "static_friction": 1.0, "dynamic_friction": 0.8},
                "visual": {},
                "relative_plane": "Table",
            }
        )
    return layout


def metadata_bounds(root, item, kind="Rigid"):
    path = root / f"Assets/Object/RoboDojo/{kind}/{item['category']}/{item['category_idx']:05d}/metadata.json"
    points = np.array(json.loads(path.read_text())["geometry"]["aligned_bbox"]["vertices"])
    points *= item["scale"]
    return [points.min(0).tolist(), points.max(0).tolist()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robodojo-root", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--assets", type=Path, nargs="+", required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--asset-selection", type=Path, help="JSON list of fixed asset pool indices")
    parser.add_argument(
        "--initial-state-only",
        action="store_true",
        help="Sample a G1 static candidate using only the initial observed state; requires planning",
    )
    parser.add_argument("--attempt-budget", type=int, default=1000)
    parser.add_argument(
        "--cluster-budget",
        type=int,
        default=128,
        help="Restart packing when one cluster exhausts this many proposals",
    )
    args = parser.parse_args()
    root, reference, output = args.robodojo_root.resolve(), args.reference.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[2]
    snapshot = output / "source/infinidojo"
    snapshot.mkdir(parents=True)
    for path in (source / "src/infinidojo").glob("*.py"):
        shutil.copyfile(path, snapshot / path.name)
    request = json.loads(args.request.read_text())
    if request["task"] != json.loads((reference / "provenance.json").read_text())["task"]:
        raise ValueError("Request and reference tasks differ")
    if not args.initial_state_only:
        result = json.loads((reference / "result.json").read_text())
        if not result["native_task_success"] or not result["event_trace"]["complete"]:
            raise ValueError("Reference did not complete the native task and event sequence")
    base = json.loads((reference / "layout.json").read_text())
    trajectory = json.loads((reference / "trajectory.json").read_text())
    if args.initial_state_only:
        trajectory = trajectory[:1]
        if not trajectory or trajectory[0]["phase"] != "initial" or trajectory[0]["step"] != 0:
            raise ValueError("G1 needs an observed reset state before any reference motion")
        write_json(output / "initial-state.json", trajectory[0])
    object_bounds = {
        item["label"]: metadata_bounds(root, item) for group in base["Rigid"].values() for item in group
    }
    if request["task"] == "cover_blocks":
        from infinidojo.cover_geometry import collision_mesh

        # Reserve the actual cup envelope; native metadata has a small offset.
        # The envelope applies to new clutter, not the block inside the cavity.
        for group in base["Rigid"].values():
            for item in group:
                if item["label"] not in ("cup0", "cup1", "cup2"):
                    continue
                asset = (
                    root
                    / f"Assets/Object/RoboDojo/Rigid/{item['category']}/{item['category_idx']:05d}/object.usdz"
                )
                mesh, _ = collision_mesh(asset, item["scale"])
                object_bounds[item["label"]] = (mesh.bounds / 1000).tolist()
    fixtures = [
        (
            item["label"],
            transformed_box(
                metadata_bounds(root, item, "Geometry"), [*item["default_pos"], *item["default_ori"]]
            ),
        )
        for group in base.get("Geometry", {}).values()
        for item in group
    ]
    robot_bounds = extract_robot_bounds(root / "Assets/Robots/x5/ARX.usd")
    frames = reference_boxes(trajectory, robot_bounds, object_bounds, fixtures)
    pool, asset_map = [], {}
    for folder in args.assets:
        provenance = json.loads((folder / "provenance.json").read_text())
        pool.append({"asset_id": provenance["seed"], "bounds": provenance["bounds"]})
        asset_map[str(provenance["seed"])] = str(folder.resolve())
    table = base["Table"]
    generated = complete_scene(
        request,
        pool,
        frames,
        seed=args.seed,
        tabletop_z=table["default_pos"][2] + table["scale"][2] / 2,
        attempt_budget=args.attempt_budget,
        cluster_budget=args.cluster_budget,
        occupancy="initial_state" if args.initial_state_only else "full_reference",
        selected_indices=json.loads(args.asset_selection.read_text()) if args.asset_selection else None,
    )
    write_json(output / "request.json", request)
    write_json(output / "generation.json", generated)
    write_json(output / "asset-map.json", asset_map)
    write_json(output / "robot-bounds.json", robot_bounds)
    write_json(
        output / "provenance.json",
        {
            "infinidojo_commit": git_revision(source),
            "source_sha256": {
                str(p.relative_to(source)): sha256(p) for p in (source / "src/infinidojo").glob("*.py")
            },
            "reference_trajectory_sha256": sha256(reference / "trajectory.json"),
            "uses_reference_motion": not args.initial_state_only,
            "asset_provenance_sha256": {str(a.resolve()): sha256(a / "provenance.json") for a in args.assets},
        },
    )
    if generated["accepted"]:
        write_json(output / "layout.json", add_clutter(base, generated["clutter"]))
    print(json.dumps({k: v for k, v in generated.items() if k not in ("clutter", "proposals")}))


if __name__ == "__main__":
    main()
