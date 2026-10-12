"""GPU diagnostic: a blocker at the carried object must affect planning.

This is a synthetic planning-model test, not a generated episode or a physical
experiment. Disabling the payload is solely a negative control in this probe.
Run with RoboDojo's CuRobo Python; an Isaac application is not required.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from infinidojo.io import sha256, write_json
from infinidojo.planning import make_scene_planner, sphere_box_penetration


def run_probe(root, execution):
    import torch
    from curobo.scene import Cuboid, Scene

    event_id = "00/transport"
    record = next(
        x for x in json.loads((execution / "planner-scenes.json").read_text()) if x["event_id"] == event_id
    )
    frame = next(
        x for x in json.loads((execution / "trajectory.json").read_text()) if x["event_id"] == event_id
    )
    target = next(
        x["target_flange_pose"]
        for x in json.loads((execution / "result.json").read_text())["plans"]
        if x.get("event_id") == event_id
    )
    robot = SimpleNamespace(
        curobo_yml_path=str(root / "Assets/Robots/x5/curobo.yml"),
        arm_joints_name=[f"joint{i}" for i in range(1, 7)],
        entity_origin_pose=record["base_pose"],
    )
    wrapper = make_scene_planner(robot, 0.004)
    planner = wrapper.motion_planner
    params = planner.kinematics.config.kinematics_config
    for name, spheres in record["proxy_spheres"].items():
        indices = params.get_sphere_index_from_link_name(name)
        values = torch.zeros((len(indices), 4), device=params.link_spheres.device)
        values[:, 3] = -100
        if spheres:
            values[: len(spheres)] = torch.tensor(spheres, device=values.device)
        params.link_spheres[:, indices] = values
    current = np.array(frame["robots"][1]["joint_positions"][:6])
    world = [Cuboid(**obj) for obj in record["world"] if obj["name"] not in record["optimizer_omissions"]]
    cases = []

    def plan(name, obstacles):
        planner.update_world(Scene(cuboid=obstacles))
        planner.reset_seed()
        start = time.monotonic()
        result = wrapper.plan_path(current, target, record["base_pose"])
        cases.append({"case": name, "status": result["status"], "seconds": time.monotonic() - start})
        print(json.dumps(cases[-1]), flush=True)
        return result

    result = plan("clear_world", world)
    if result["status"] != "Success":
        return {"passed": False, "reason": "baseline_transport_unavailable", "cases": cases}
    state = planner.compute_kinematics(wrapper._build_joint_state(result["position"][-1]))
    spheres = state.robot_spheres.cpu().numpy().reshape(-1, 4)
    indices = params.get_sphere_index_from_link_name("attached_object")
    payload = spheres[indices.cpu().numpy()]
    payload = payload[payload[:, 3] > 0]
    center = (payload[:, :3].min(0) + payload[:, :3].max(0)) / 2
    blocker = {
        "name": "diagnostic_payload_blocker",
        "pose": [*center.astype(float).tolist(), 1, 0, 0, 0],
        "dims": [0.014] * 3,
    }
    other = spheres[:, 3] > 0
    other[indices.cpu().numpy()] = False
    overlap = float(sphere_box_penetration(spheres[other], blocker).max())
    plan("payload_blocked", [*world, Cuboid(**blocker)])
    saved = params.link_spheres[:, indices].clone()
    try:
        params.link_spheres[:, indices, 3] = -100
        plan("same_blocker_payload_disabled", [*world, Cuboid(**blocker)])
    finally:
        params.link_spheres[:, indices] = saved
    plan("blocker_removed_payload_restored", world)
    return {
        "passed": [r["status"] for r in cases] == ["Success", "Fail", "Success", "Success"] and overlap < 0,
        "cases": cases,
        "blocker": blocker,
        "non_payload_robot_overlap_m": overlap,
        "event_id": event_id,
        "description": __doc__,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robodojo-root", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, execution, output = args.robodojo_root.resolve(), args.execution.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(root))
    write_json(
        output / "provenance.json",
        {
            "input_sha256": {
                name: sha256(execution / name)
                for name in ("planner-scenes.json", "trajectory.json", "result.json")
            },
            "source_sha256": {
                name: sha256(Path(__file__).parent / name) for name in ("planning.py", "planning_probe.py")
            },
            "native_curobo_config_sha256": sha256(root / "Assets/Robots/x5/curobo.yml"),
        },
    )
    try:
        result = run_probe(root, execution)
        write_json(output / "result.json", result)
    except Exception as exc:
        write_json(output / "error.json", {"type": type(exc).__name__, "message": str(exc)})
        raise
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
