from copy import deepcopy

from infinidojo.io import write_json
from infinidojo.validation import (
    ValidationThresholds,
    allowed_pairs,
    audit_contacts,
    audit_episode,
    pickup_contact_contract,
    stack_contact_contract,
)


def test_release_changes_contact_permissions_and_preserves_support():
    program = stack_contact_contract("arm1")
    grasp = allowed_pairs(program, "00/grasp", {})
    assert tuple(sorted(("block_1", "arm1/gripper"))) in grasp
    later = allowed_pairs(program, "01/transport", {})
    assert ("block_0", "block_1") in later
    assert tuple(sorted(("block_1", "arm1/gripper"))) not in later


def test_contact_report_rejects_unexpected_touch_even_when_shallow():
    objects = {"block_1": {"prim_path": "/objects/target"}, "clutter_0": {"prim_path": "/objects/clutter"}}
    robots = {"robot1": {"arm": "arm1", "gripper_bodies": ["link7", "link8"]}}

    def contact(obj, penetration):
        return {
            "actors": ["/World/envs/env_0/robot1/link7", obj],
            "event_id": "00/grasp",
            "phase": "grasp",
            "step": 1,
            "points": [{"separation": penetration}],
        }

    program = stack_contact_contract("arm1")
    accepted = audit_contacts(
        [contact("/objects/target", -0.0001)], program, objects, robots, ValidationThresholds()
    )
    assert accepted["clear"]
    wrong = audit_contacts(
        [contact("/objects/clutter", -0.0001)], program, objects, robots, ValidationThresholds()
    )
    assert not wrong["clear"]
    assert wrong["violations"][0]["reason"] == "undeclared_contact"
    deep = audit_contacts(
        [contact("/objects/target", -0.003)], program, objects, robots, ValidationThresholds()
    )
    assert not deep["clear"]
    assert deep["violations"][0]["reason"] == "excessive_reported_penetration"


def test_pickup_hold_allows_gripper_contact_but_rejects_remaining_table_support():
    program = pickup_contact_contract("arm1")
    objects = {"target": {"prim_path": "/objects/target"}}
    robots = {"robot1": {"arm": "arm1", "gripper_bodies": ["link7", "link8"]}}
    record = {
        "actors": ["/objects/target", "/World/envs/env_0/robot1/link7"],
        "event_id": "00/hold",
        "phase": "hold",
        "step": 100,
        "points": [{"separation": -0.0001}],
    }
    assert audit_contacts([record], program, objects, robots, ValidationThresholds())["clear"]
    record["actors"][1] = "/World/envs/env_0/Table/cube"
    bad = audit_contacts([record], program, objects, robots, ValidationThresholds())
    assert not bad["clear"] and bad["violations"][0]["reason"] == "undeclared_contact"


def test_native_lift_flag_cannot_replace_sustained_lift_or_physical_hold(tmp_path, monkeypatch):
    """Exercise task acceptance independently of the controller's success flag."""
    import json

    from infinidojo import validation

    execution, generation = tmp_path / "execution", tmp_path / "generation"
    robot_file = tmp_path / "robot.usd"
    robot_file.write_text("USD loading is outside this contact/hold test")
    monkeypatch.setattr(validation, "extract_robot_bounds", lambda path: {})
    monkeypatch.setattr(validation, "audit_observed_geometry", lambda *args: {"clear": True})
    identities = {f"robot{i}": {"arm": f"arm{i}", "gripper_bodies": ["link7", "link8"]} for i in range(2)}
    initial = [0.2, 0, 0.765, 1, 0, 0, 0]
    frames = []
    for step, phase, height in (
        (0, "initial", 0.765),
        (250, "initial", 0.765),
        (251, "approach", 0.765),
        (252, "descend", 0.765),
        (253, "grasp", 0.765),
        (300, "lift", 0.915),
        (400, "hold", 0.915),
        (650, "hold", 0.915),
    ):
        pose = [0.2, 0, height, 1, 0, 0, 0]
        frames.append(
            {
                "step": step,
                "time_s": step * 0.004,
                "phase": phase,
                "event_id": None if phase == "initial" else f"00/{phase}",
                "objects": {"target": pose},
                "robots": [
                    {"body_names": ["link6", "link7", "link8"], "body_poses": [pose] * 3} for _ in range(2)
                ],
            }
        )
    records = [
        {
            "actors": ["/objects/target", "/World/envs/env_0/robot1/link7"],
            "points": [{"separation": 0}],
            "phase": phase,
            "event_id": f"00/{phase}",
            "step": step,
        }
        for step, phase in ((253, "grasp"), (300, "lift"), (650, "hold"))
    ]
    artifacts = {
        "result.json": {"status": "completed", "native_task_success": True},
        "trajectory.json": frames,
        "provenance.json": {"task": "general_pickup"},
        "robot-identities.json": identities,
        "interaction-program.json": pickup_contact_contract("arm1"),
        "scene-objects.json": {
            "target": {
                "prim_path": "/objects/target",
                "category_idx": 0,
                "requested_pose": initial,
                "bounds": [[-0.025, -0.025, 0], [0.025, 0.025, 0.1]],
            }
        },
        "layout.json": {"Table": {"default_pos": [0, 0, 0.74], "scale": [1.4, 1.1, 0.05]}},
        "contact-summary.json": {
            "errors": [],
            "report_bodies": [
                "/objects/target",
                *[f"/World/envs/env_0/robot{i}/link{j}" for i in range(2) for j in (6, 7, 8)],
            ],
        },
    }
    for name, content in artifacts.items():
        write_json(execution / name, content)
    write_json(
        generation / "request.json",
        {
            "task": "general_pickup",
            "clutter": {
                "count": 0,
                "region_xy_m": [[0, -0.2], [0.5, 0.3]],
                "clearance_m": 0.003,
            },
        },
    )
    write_json(generation / "generation.json", {"accepted": True, "clutter": []})
    write_json(generation / "asset-map.json", {})
    contacts = execution / "contacts.jsonl"
    contacts.write_text("".join(json.dumps(r) + "\n" for r in records))
    assert audit_episode(execution, generation, robot_file)["failed_checks"] == []

    # The object was lifted during motion, then held below the native threshold.
    low_hold = deepcopy(frames)
    for frame in low_hold[-2:]:
        frame["objects"]["target"][2] = 0.81
    write_json(execution / "trajectory.json", low_hold)
    report = audit_episode(execution, generation, robot_file)
    assert report["checks"]["native_task_success"] and not report["checks"]["sustained_native_lift"]
    assert report["status"] == "unverified"

    write_json(execution / "trajectory.json", frames)
    contacts.write_text("".join(json.dumps(r) + "\n" for r in records[:-1]))
    report = audit_episode(execution, generation, robot_file)
    assert report["checks"]["sustained_native_lift"] and not report["checks"]["held_relation_observed"]
