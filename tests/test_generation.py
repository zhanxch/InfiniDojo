import copy

import numpy as np

from infinidojo.generation import generate_layout
from infinidojo.specification import validate


def spec():
    return {
        "task": "general_pickup",
        "instruction": "Lift the can by 15 cm.",
        "target": {"family": "infinigen_can", "radius_m": 0.025, "height_m": 0.07, "xy_m": [-0.23, -0.05]},
        "clutter": {"count": 1, "x_bounds_m": [0.0, 0.1], "y_bounds_m": [0.0, 0.1], "clearance_m": 0.005},
        "events": [
            {"name": name, "attachment": attach, "support": support}
            for name, attach, support in [
                ("approach", "free", "table"),
                ("descend", "free", "table"),
                ("grasp", "contact", "table"),
                ("lift", "held", "gripper"),
                ("hold", "held", "gripper"),
            ]
        ],
        "lift_m": 0.15,
    }


def test_future_collision_prevents_placement_without_weakening_count():
    initial = (np.array([-1, -1, 1.0]), np.array([0.1] * 3), np.eye(3))
    future = (np.array([0.05, 0.05, 0.8]), np.array([0.2] * 3), np.eye(3))
    frames = [{"bodies": [("robot0/link6", box)]} for box in (initial, future)]
    pool = [{"asset_id": 0, "bounds": [[-0.025, -0.025, 0], [0.025, 0.025, 0.07]]}]
    static = generate_layout(spec(), pool, frames, seed=0, method="static", attempt_budget=5)
    interaction = generate_layout(spec(), pool, frames, seed=0, method="interaction", attempt_budget=5)
    assert static["accepted"]
    assert not interaction["accepted"]
    assert interaction["requested_count"] == 1 and interaction["generated_count"] == 0
    assert interaction["attempts"] == 5


def test_request_fidelity_and_contact_transition_validation():
    proposal = spec()
    assert validate(proposal) == []
    proposal["clutter"]["count"] = 0
    assert validate(proposal, {"clutter": {"count": 1}})
    proposal = copy.deepcopy(spec())
    proposal["events"][0]["attachment"] = "held"
    assert validate(proposal)
