"""Independent development checks on executed stack, pickup and cover episodes.

Uses native task success, actual PhysX contacts, actual motion and an independent
OBB screen. Thresholds are explicit development values, not a frozen paper
protocol. A failed reference is unverified; it does not prove unsolvability.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path

import numpy as np

from infinidojo.geometry import (
    aabb,
    extract_robot_bounds,
    overlap_aabb,
    overlap_obb,
    rotation,
    transformed_box,
)
from infinidojo.interaction import (
    build_cover_program,
    build_pickup_program,
    build_program,
    validate_event_trace,
)
from infinidojo.io import sha256, write_json
from infinidojo.repair import validate_position_options


@dataclass(frozen=True)
class ValidationThresholds:
    penetration_m: float = 0.001
    contact_distance_m: float = 0.001
    initialization_translation_m: float = 0.005
    initialization_rotation_deg: float = 2.0
    clutter_motion_m: float = 0.002
    clutter_rotation_deg: float = 2.0
    settle_motion_m: float = 0.002
    settle_rotation_deg: float = 2.0
    attachment_translation_m: float = 0.003
    attachment_rotation_deg: float = 5.0
    pair_distance_error_m: float = 0.003
    support_gap_m: float = 0.001
    minimum_settle_s: float = 1.0


def angle_degrees(a, b):
    return float(np.degrees(np.arccos(np.clip((np.trace(a.T @ b) - 1) / 2, -1, 1))))


def allowed_pairs(program, event_id, objects, phase=None):
    event = next((e for e in program["events"] if e["id"] == event_id), None)
    if event is not None:
        pairs = [*event["allowed_contacts"], *event["support_relations"].items()]
    else:
        support = program["final_support"] if phase in ("return", "settle") else program["initial_support"]
        pairs = list(support.items())
    pairs.extend((label, "Table") for label in objects if label.startswith("clutter_"))
    return {tuple(sorted(pair)) for pair in pairs}


def actor_name(path, objects, robots):
    for label, item in objects.items():
        if path == item["prim_path"] or path.startswith(item["prim_path"] + "/"):
            return label
    if "/Table/" in path:
        return "Table"
    for name, robot in robots.items():
        prefix = f"/World/envs/env_0/{name}/"
        if path.startswith(prefix):
            body = path[len(prefix) :].split("/")[0]
            return f"{robot['arm']}/gripper" if body in robot["gripper_bodies"] else f"{name}/{body}"
    return f"unmapped:{path}"


def stack_contact_contract(arm):
    """Verifier-owned contract for the currently supported two-transfer order.

    Do not accept a generator's expanded legal-contact set as validation policy.
    Additional native-valid orders need a separately reviewed contract.
    """
    return json.loads(
        json.dumps(
            build_program(
                [
                    {"object": "block_1", "source": "Table", "destination": "block_0", "arm": arm},
                    {"object": "block_2", "source": "Table", "destination": "block_1", "arm": arm},
                ],
                {label: "Table" for label in ("block_0", "block_1", "block_2")},
            )
        )
    )


def pickup_contact_contract(arm):
    """Verifier-owned pickup contract ends in an actual sustained grasp."""
    return json.loads(json.dumps(build_pickup_program("target", "Table", arm)))


def cover_contact_contract(initial, robots):
    order = sorted(("red", "green", "blue"), key=lambda label: initial[label][0])
    arms = {"cup0": robots["robot0"]["arm"], "cup1": robots["robot1"]["arm"], "cup2": robots["robot1"]["arm"]}
    return json.loads(json.dumps(build_cover_program(order, arms)))


def audit_contacts(records, program, objects, robots, thresholds):
    violations, contacts, raw_count, point_count = {}, {}, 0, 0
    for record in records:
        raw_count += 1
        if not record["points"]:
            continue
        point_count += len(record["points"])
        pair = tuple(sorted(actor_name(path, objects, robots) for path in record["actors"]))
        minimum = min(p["separation"] for p in record["points"])
        event = record.get("event_id") or record["phase"]
        if minimum <= thresholds.contact_distance_m:
            key = (event, pair)
            contacts.setdefault(key, {"event": event, "pair": list(pair), "steps": set()})["steps"].add(
                record["step"]
            )
        reasons = []
        if minimum < -thresholds.penetration_m:
            reasons.append("excessive_reported_penetration")
        if minimum <= thresholds.contact_distance_m and pair not in allowed_pairs(
            program, record.get("event_id"), objects, record["phase"]
        ):
            reasons.append("undeclared_contact")
        for reason in reasons:
            key = (event, pair, reason)
            violations.setdefault(
                key,
                {
                    "event": event,
                    "pair": list(pair),
                    "reason": reason,
                    "first_step": record["step"],
                    "separation_m": minimum,
                },
            )
    evidence = [{**item, "steps": sorted(item["steps"])} for item in contacts.values()]
    return {
        "clear": not violations,
        "raw_records": raw_count,
        "point_count": point_count,
        "violations": list(violations.values()),
        "evidence": evidence,
    }


def audit_observed_geometry(frames, objects, robot_bounds):
    """Conservative OBB screen using measured clutter, robot and task poses."""
    intersections = {}
    for frame in frames:
        boxes = {
            label: transformed_box(item["bounds"], frame["objects"][label]) for label, item in objects.items()
        }
        for index, robot in enumerate(frame["robots"]):
            for name, pose in zip(robot["body_names"], robot["body_poses"], strict=True):
                boxes[f"robot{index}/{name}"] = transformed_box(robot_bounds[name], pose)
        broad = {name: aabb(box) for name, box in boxes.items()}
        clutter = [name for name in objects if name.startswith("clutter_")]
        for label in clutter:
            for other, box in boxes.items():
                if other == label or (other in clutter and other < label):
                    continue
                if overlap_aabb(broad[label], broad[other]) and overlap_obb(boxes[label], box):
                    intersections.setdefault(
                        (label, other), {"pair": [label, other], "first_step": frame["step"]}
                    )
    return {
        "clear": not intersections,
        "intersections": list(intersections.values()),
        "scope": "clutter versus each robot link, task object and other clutter; sampled conservative OBBs",
        "interpretation": "Overlap requires review; it is not proof of a mesh collision",
    }


def audit_episode(execution, generation, robot_usd, thresholds=None):
    thresholds = thresholds or ValidationThresholds()

    def read(path):
        return json.loads(path.read_text())

    result = read(execution / "result.json")
    frames = read(execution / "trajectory.json")
    objects = read(execution / "scene-objects.json")
    robots = read(execution / "robot-identities.json")
    recorded_program = read(execution / "interaction-program.json")
    sensor = read(execution / "contact-summary.json")
    request = read(generation / "request.json")
    generated = read(generation / "generation.json")
    provenance = read(execution / "provenance.json")
    task = provenance["task"]
    if task not in ("stack_blocks", "general_pickup", "cover_blocks") or request["task"] != task:
        raise ValueError("Execution and generation must use the same supported task")
    pickup = task == "general_pickup"
    cover = task == "cover_blocks"
    if cover:
        program = cover_contact_contract(frames[0]["objects"], robots)
    else:
        contract = pickup_contact_contract if pickup else stack_contact_contract
        program = contract(robots["robot1"]["arm"])
    robot_hash = sha256(robot_usd)
    if provenance.get("robot_usd_sha256", robot_hash) != robot_hash:
        raise ValueError("Robot geometry changed since execution")
    initial, final = frames[0]["objects"], frames[-1]["objects"]
    checks, diagnostics = {}, {}
    extra_inputs = []
    mapping = read(generation / "asset-map.json")
    generated_records = {}
    if mapping:
        generated_records = read(execution / "asset-provenance.json")
        checks["generated_assets_match_execution"] = set(generated_records) == set(mapping) and all(
            record == read((generation / mapping[key]).resolve() / "provenance.json")
            and all(
                sha256((generation / mapping[key]).resolve() / name) == digest
                for name, digest in record["files"].items()
            )
            for key, record in generated_records.items()
            if key in mapping
        )
        extra_inputs.append("asset-provenance.json")
    if cover:
        from infinidojo.cover_geometry import audit_cover_states, collision_mesh

        recorded_assets = (
            read(execution / "cup-collision-assets.json")
            if (execution / "cup-collision-assets.json").exists()
            else {}
        )
        meshes, asset_checks, asset_diagnostics = {}, [], {}
        layout = read(execution / "layout.json")
        items = {item["label"]: item for group in layout["Rigid"].values() for item in group}
        for cup in ("cup0", "cup1", "cup2"):
            item = items[cup]
            asset = (
                robot_usd.parents[2]
                / f"Object/RoboDojo/Rigid/{item['category']}/{item['category_idx']:05d}/object.usdz"
            )
            mesh, approximation = collision_mesh(asset, item["scale"])
            meshes[cup] = mesh
            actual_record = {
                "sha256": sha256(asset),
                "scale": item["scale"],
                "category": item["category"],
                "category_idx": item["category_idx"],
            }
            asset_checks.append(
                recorded_assets.get(cup) == actual_record and approximation == "convexDecomposition"
            )
            asset_diagnostics[cup] = {
                **actual_record,
                "approximation": approximation,
                "metadata_bounds_m": objects[cup]["bounds"],
                "source_mesh_bounds_m": (mesh.bounds / 1000).tolist(),
            }
            # Use source geometry for cup support and clutter envelope checks.
            objects[cup] = {**objects[cup], "bounds": (mesh.bounds / 1000).tolist()}
        checks["cup_collision_assets_recorded_and_unchanged"] = all(asset_checks)
        if recorded_assets:
            extra_inputs.append("cup-collision-assets.json")
        diagnostics["cup_collision_assets"] = asset_diagnostics
        cavity = audit_cover_states(frames, program, objects, meshes)
        diagnostics["cup_cavity_audit"] = cavity
        checks["cup_source_mesh_clear_of_blocks"] = cavity["clear"]
        checks["cover_and_uncover_relations"] = cavity["relations_match"]
        stages = [frame.get("native_remaining_stages") for frame in frames]
        progression = [stage for i, stage in enumerate(stages) if i == 0 or stage != stages[i - 1]]
        diagnostics["native_stage_progression"] = progression
        checks["six_native_stages_completed"] = progression == [6, 5, 4, 3, 2, 1, 0]
        diagnostics["maximum_block_displacement_m"] = {
            label: max(
                float(np.linalg.norm(np.array(f["objects"][label][:3]) - initial[label][:3])) for f in frames
            )
            for label in ("red", "green", "blue")
        }
        checks["native_block_displacement_limit"] = all(
            v <= 0.05 for v in diagnostics["maximum_block_displacement_m"].values()
        )
    checks["verifier_owned_contact_contract"] = recorded_program == program
    checks["native_task_success"] = result["native_task_success"]
    checks["complete_event_order"] = validate_event_trace(program, frames)["complete"]
    checks["complete_execution"] = result["status"] == "completed"
    diagnostics["initial_translation_error_m"] = {
        label: float(np.linalg.norm(np.array(initial[label][:3]) - item["requested_pose"][:3]))
        for label, item in objects.items()
    }
    checks["initialization_preserved"] = all(
        v <= thresholds.initialization_translation_m
        for v in diagnostics["initial_translation_error_m"].values()
    )
    diagnostics["initial_rotation_error_deg"] = {
        label: angle_degrees(rotation(initial[label]), rotation(item["requested_pose"]))
        for label, item in objects.items()
    }
    checks["initial_rotations_preserved"] = all(
        v <= thresholds.initialization_rotation_deg
        for v in diagnostics["initial_rotation_error_deg"].values()
    )
    clutter = [label for label in objects if label.startswith("clutter_")]
    checks["requested_count"] = generated["accepted"] and len(clutter) == request["clutter"]["count"]
    if "task_xy_options_m" in request:
        validate_position_options(request)
        diagnostics["task_position_option_error_m"] = {
            label: min(float(np.linalg.norm(np.array(initial[label][:2]) - point)) for point in options)
            for label, options in request["task_xy_options_m"].items()
        }
        checks["requested_task_positions"] = all(
            error <= thresholds.initialization_translation_m
            for error in diagnostics["task_position_option_error_m"].values()
        )
    checks["requested_asset_identities"] = all(
        objects[item["id"]]["category_idx"] == item["asset_id"] for item in generated["clutter"]
    )
    region = np.asarray(request["clutter"]["region_xy_m"])
    bounds = [aabb(transformed_box(objects[label]["bounds"], initial[label])) for label in clutter]
    checks["initial_footprints_inside_region"] = all(
        np.all(box[0, :2] >= region[0]) and np.all(box[1, :2] <= region[1]) for box in bounds
    )
    if request["clutter"].get("anchor_region") is not None:
        relation = request["clutter"]["anchor_region"]
        anchor = relation["anchor"]
        center = aabb(transformed_box(objects[anchor]["bounds"], initial[anchor])).mean(axis=0)[:2]
        relative_region = center + np.asarray(relation["offset_region_xy_m"])
        checks["requested_anchor_region"] = all(
            np.all(box[0, :2] >= relative_region[0]) and np.all(box[1, :2] <= relative_region[1])
            for box in bounds
        )
    if "asset_requirements" in request:
        from infinidojo.assets import matches_parameters

        checks["requested_procedural_assets"] = all(
            label in objects
            and objects[label]["category"] == "infinigen_can"
            and matches_parameters(generated_records[str(objects[label]["category_idx"])], expected)
            for role, expected in request["asset_requirements"].items()
            for label in (clutter if role == "clutter" else [role])
        )
    checks["requested_initial_clearance"] = not any(
        overlap_aabb(a, b, request["clutter"]["clearance_m"]) for a, b in combinations(bounds, 2)
    )
    pairs = {}
    for item in generated["clutter"]:
        pairs.setdefault(item["cluster"], []).append(item["id"])
    distance = request["clutter"].get("pair_distance_m")
    pair_groups_valid = distance is None or (
        len(pairs) * 2 == len(clutter) and all(len(members) == 2 for members in pairs.values())
    )
    errors = (
        [
            abs(float(np.linalg.norm(np.array(initial[members[0]][:2]) - initial[members[1]][:2])) - distance)
            for members in pairs.values()
        ]
        if distance is not None and pair_groups_valid
        else []
    )
    diagnostics["pair_distance_errors_m"] = errors
    checks["pair_relation_preserved"] = pair_groups_valid and all(
        error <= thresholds.pair_distance_error_m for error in errors
    )
    diagnostics["clutter_motion_m"] = {
        label: max(
            float(np.linalg.norm(np.array(f["objects"][label][:3]) - initial[label][:3])) for f in frames
        )
        for label in clutter
    }
    checks["clutter_remains_stationary"] = all(
        value <= thresholds.clutter_motion_m for value in diagnostics["clutter_motion_m"].values()
    )
    diagnostics["clutter_rotation_deg"] = {
        label: max(angle_degrees(rotation(initial[label]), rotation(f["objects"][label])) for f in frames)
        for label in clutter
    }
    checks["clutter_rotation_stable"] = all(
        v <= thresholds.clutter_rotation_deg for v in diagnostics["clutter_rotation_deg"].values()
    )
    with (execution / "contacts.jsonl").open() as stream:
        contact = audit_contacts((json.loads(line) for line in stream), program, objects, robots, thresholds)
    checks["contact_reporting_available"] = contact["point_count"] > 0 and not sensor["errors"]
    checks["all_recorded_bodies_instrumented"] = all(
        item["prim_path"] in sensor["report_bodies"] for item in objects.values()
    ) and all(
        f"/World/envs/env_0/robot{index}/{name}" in sensor["report_bodies"]
        for index, robot in enumerate(frames[0]["robots"])
        for name in robot["body_names"]
    )
    checks["contacts_legal_and_shallow"] = contact["clear"]
    settled = [frame for frame in frames if frame["phase"] == ("hold" if pickup else "settle")]
    checks["settle_duration"] = (
        bool(settled) and settled[-1]["time_s"] - settled[0]["time_s"] >= thresholds.minimum_settle_s
    )
    diagnostics["settle_translation_m"] = {
        label: max(
            (
                float(
                    np.linalg.norm(np.array(frame["objects"][label][:3]) - settled[0]["objects"][label][:3])
                )
                for frame in settled
            ),
            default=None,
        )
        for label in objects
    }
    checks["settle_motion"] = all(
        value is not None and value <= thresholds.settle_motion_m
        for value in diagnostics["settle_translation_m"].values()
    )
    diagnostics["settle_rotation_deg"] = {
        label: max(
            (
                angle_degrees(rotation(settled[0]["objects"][label]), rotation(f["objects"][label]))
                for f in settled
            ),
            default=None,
        )
        for label in objects
    }
    checks["settle_rotation_stable"] = all(
        v is not None and v <= thresholds.settle_rotation_deg
        for v in diagnostics["settle_rotation_deg"].values()
    )
    observed = {(item["event"], tuple(item["pair"])) for item in contact["evidence"]}
    # Task-object support contacts are mandatory; the fixed table may be asleep
    # before contact instrumentation begins, so its base support uses geometry.
    support_pairs = [
        tuple(sorted((obj, support)))
        for obj, support in program["final_support"].items()
        if support in objects
    ]
    checks["final_task_support_contacts"] = all(("settle", pair) in observed for pair in support_pairs)
    support_gaps = {}
    table = read(execution / "layout.json")["Table"]
    table_z = table["default_pos"][2] + table["scale"][2] / 2
    for label, support in program["final_support"].items():
        bottom = aabb(transformed_box(objects[label]["bounds"], final[label]))[0, 2]
        top = (
            table_z
            if support == "Table"
            else aabb(transformed_box(objects[support]["bounds"], final[support]))[1, 2]
        )
        support_gaps[label] = float(bottom - top)
    diagnostics["final_support_gaps_m"] = support_gaps
    checks["final_support_geometry"] = all(
        abs(gap) <= thresholds.support_gap_m for gap in support_gaps.values()
    )
    if pickup:
        # Empty final_support means still held, not vacuously valid support.
        del checks["final_task_support_contacts"], checks["final_support_geometry"]
        diagnostics.pop("final_support_gaps_m")
        for old, new in (
            ("settle_duration", "hold_duration"),
            ("settle_motion", "hold_motion"),
            ("settle_rotation_stable", "hold_rotation_stable"),
        ):
            checks[new] = checks.pop(old)
        for old, new in (
            ("settle_translation_m", "hold_translation_m"),
            ("settle_rotation_deg", "hold_rotation_deg"),
        ):
            diagnostics[new] = diagnostics.pop(old)
        diagnostics["minimum_hold_origin_lift_m"] = min(
            (frame["objects"]["target"][2] - initial["target"][2] for frame in settled), default=None
        )
        checks["sustained_native_lift"] = bool(settled) and diagnostics["minimum_hold_origin_lift_m"] > 0.1
        resting = [frame for frame in frames if frame["phase"] == "initial"]
        checks["initial_rest_duration"] = bool(resting) and (
            resting[-1]["time_s"] - resting[0]["time_s"] >= thresholds.minimum_settle_s
        )
        diagnostics["initial_target_support_gap_m"] = float(
            aabb(transformed_box(objects["target"]["bounds"], initial["target"]))[0, 2] - table_z
        )
        checks["initial_target_support_geometry"] = (
            abs(diagnostics["initial_target_support_gap_m"]) <= thresholds.support_gap_m
        )
        checks["initial_target_stable"] = bool(resting) and all(
            np.linalg.norm(np.array(frame["objects"]["target"][:3]) - initial["target"][:3])
            <= thresholds.settle_motion_m
            and angle_degrees(rotation(initial["target"]), rotation(frame["objects"]["target"]))
            <= thresholds.settle_rotation_deg
            for frame in resting
        )
    attached_errors, attached_angles, held_contacts = {}, {}, []
    for event in program["events"]:
        if event["phase"] not in ("lift", "transport", "hold"):
            continue
        label = event["object"]
        robot_id = next(
            int(name.removeprefix("robot")) for name, value in robots.items() if value["arm"] == event["arm"]
        )
        grasp_id = event["id"].split("/")[0] + "/grasp"
        grasp = [f for f in frames if f["event_id"] == grasp_id]
        held = [f for f in frames if f["event_id"] == event["id"]]
        if not grasp or not held:
            attached_errors[event["id"]] = None
            held_contacts.append(False)
            continue

        def relative(frame, robot_index=robot_id, object_label=label):
            robot = frame["robots"][robot_index]
            flange = robot["body_poses"][robot["body_names"].index("link6")]
            return (
                rotation(flange).T @ (np.array(frame["objects"][object_label][:3]) - flange[:3]),
                rotation(flange).T @ rotation(frame["objects"][object_label]),
            )

        offset, orientation = relative(grasp[-1])
        attached_errors[event["id"]] = max(
            float(np.linalg.norm(relative(frame)[0] - offset)) for frame in held
        )
        attached_angles[event["id"]] = max(angle_degrees(relative(frame)[1], orientation) for frame in held)
        held_contacts.append((event["id"], tuple(sorted((label, f"{event['arm']}/gripper")))) in observed)
    diagnostics["held_relative_translation_error_m"] = attached_errors
    diagnostics["held_relative_rotation_error_deg"] = attached_angles
    checks["held_relation_observed"] = all(
        error is not None and error <= thresholds.attachment_translation_m
        for error in attached_errors.values()
    ) and all(held_contacts)
    checks["held_rotation_observed"] = len(attached_angles) == len(attached_errors) and all(
        v <= thresholds.attachment_rotation_deg for v in attached_angles.values()
    )
    geometry = audit_observed_geometry(frames, objects, extract_robot_bounds(robot_usd))
    checks["independent_clutter_geometry_screen"] = geometry["clear"]
    checks = {key: bool(value) for key, value in checks.items()}
    return {
        "status": "validated_under_development_checks" if all(checks.values()) else "unverified",
        "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
        "threshold_status": "development values, not frozen held-out protocol",
        "thresholds": asdict(thresholds),
        "diagnostics": diagnostics,
        "contact_audit": contact,
        "geometry_audit": geometry,
        "limitations": [
            "Recorded time resolution does not prove continuous collision freedom.",
            "OBBs may reject valid near-contact placements.",
            "Contact evidence covers active reported bodies; initial settling is checked by poses and geometry.",
        ],
        "input_sha256": {
            name: sha256(execution / name)
            for name in [
                "result.json",
                "trajectory.json",
                "contacts.jsonl",
                "scene-objects.json",
                "layout.json",
                "provenance.json",
                "interaction-program.json",
                "contact-summary.json",
                "robot-identities.json",
            ]
            + extra_inputs
        },
        "generation_input_sha256": {
            name: sha256(generation / name) for name in ["request.json", "generation.json", "asset-map.json"]
        },
        "robot_geometry_sha256": robot_hash,
        "robot_geometry_hash_recorded_at_execution": "robot_usd_sha256" in provenance,
        "validator_sha256": sha256(Path(__file__)),
        "validator_component_sha256": {
            name: sha256(Path(__file__).with_name(name))
            for name in ["geometry.py", "interaction.py"] + (["cover_geometry.py"] if cover else [])
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--robodojo-root", type=Path, required=True)
    args = parser.parse_args()
    result = audit_episode(args.execution, args.generation, args.robodojo_root / "Assets/Robots/x5/ARX.usd")
    write_json(args.output, result)
    print(json.dumps({key: result[key] for key in ["status", "checks", "failed_checks"]}))


if __name__ == "__main__":
    main()
