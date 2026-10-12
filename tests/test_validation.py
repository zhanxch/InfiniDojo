from infinidojo.validation import ValidationThresholds, allowed_pairs, audit_contacts, stack_contact_contract


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
