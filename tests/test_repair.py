import copy

import pytest

from infinidojo.interaction import build_pickup_program, build_program
from infinidojo.repair import (
    event_dependencies,
    initial_positions,
    position_choices,
    rank_repairs,
    task_positions,
    validate_position_options,
    with_positions,
)


def fixture():
    layout = {
        "Rigid": {
            "cube": [
                {"label": f"block_{i}", "default_pos": [0.2 + 0.1 * i, 0, 0.78], "default_ori": [1, 0, 0, 0]}
                for i in range(3)
            ]
        },
        "robot": {"fixed_base": [0, 0, 0]},
    }
    request = {"task_xy_options_m": {label: [xy] for label, xy in task_positions(layout).items()}}
    request["task_xy_options_m"]["block_0"] += [[0.14, 0], [0.26, 0]]
    program = build_program(
        [
            {"object": "block_1", "source": "Table", "destination": "block_0", "arm": "right"},
            {"object": "block_2", "source": "Table", "destination": "block_1", "arm": "right"},
        ],
        {f"block_{i}": "Table" for i in range(3)},
    )
    robots = {"robot0": {"arm": "left"}, "robot1": {"arm": "right"}}
    return layout, request, program, robots


def test_released_support_propagates_to_second_transfer_and_return():
    _, _, program, robots = fixture()
    deps = event_dependencies(program, robots)
    assert deps["00/lift"]["robot1"] == {"block_1"}
    assert deps["00/transport"]["robot1"] == {"block_0", "block_1"}
    assert deps["01/approach"]["block_1"] == {"block_0"}
    assert deps["01/place"]["robot1"] == {"block_0"}
    assert deps["settle"]["block_2"] == {"block_0"}
    assert deps["return"]["robot1"] == {"block_0"}
    assert "robot0" not in deps["01/place"]


def test_event_repair_moves_allowed_anchor_away_from_conflicting_cluster():
    layout, request, program, robots = fixture()
    proposal = {
        "rejection": "full_reference_occupancy",
        "poses": [[0.27, 0, 0.8, 1, 0, 0, 0], [0.36, 0, 0.8, 1, 0, 0, 0]],
        "blockers": [{"event_id": "01/place", "phase": "place", "body": "robot1/link7"}],
    }
    ranked = rank_repairs(request, layout, {"proposals": [proposal]}, program, robots)
    assert ranked[0]["anchor"] == "block_0" and ranked[0]["after_xy_m"] == [0.14, 0]
    assert ranked[0]["blocking_cluster_proposals"] == 1
    duplicate = copy.deepcopy(proposal)
    duplicate["blockers"] *= 10
    assert rank_repairs(request, layout, {"proposals": [duplicate]}, program, robots) == ranked
    for body in ("robot1/base_link", "robot0/link7", "camera_stand"):
        duplicate["blockers"] = [{"event_id": "01/place", "phase": "place", "body": body}]
        assert rank_repairs(request, layout, {"proposals": [duplicate]}, program, robots) == []
    # A fixed task request is never loosened by the repair algorithm.
    assert rank_repairs({}, layout, {"proposals": [proposal]}, program, robots) == []


def test_position_changes_preserve_geometry_robot_and_initial_common_layout():
    layout, request, _, _ = fixture()
    original = copy.deepcopy(layout)
    positions = initial_positions(request, layout, 1200)
    assert positions == task_positions(layout)
    assert len(position_choices(request, layout)) == 3
    positions["block_0"] = [0.14, 0]
    changed = with_positions(layout, positions)
    assert layout == original
    assert changed["robot"] == original["robot"]
    for a, b in zip(changed["Rigid"]["cube"], original["Rigid"]["cube"], strict=True):
        assert a["default_pos"][2:] == b["default_pos"][2:]
        assert a["default_ori"] == b["default_ori"]
    request["task_xy_options_m"]["block_0"] = [[float("nan"), 0]]
    with pytest.raises(ValueError, match="Invalid finite"):
        validate_position_options(request)


def test_task_position_identity_omission_is_rejected():
    _, request, _, _ = fixture()
    del request["task_xy_options_m"]["block_2"]
    with pytest.raises(ValueError, match="identities"):
        validate_position_options(request)


def test_pickup_repair_uses_lift_and_hold_without_inventing_release():
    request = {"task": "general_pickup", "task_xy_options_m": {"target": [[0.22, -0.05], [0.16, -0.05]]}}
    layout = {
        "Rigid": {"can": [{"label": "target", "default_pos": [0.22, -0.05, 0.765], "scale": [1, 1, 1]}]}
    }
    robots = {"robot0": {"arm": "left"}, "robot1": {"arm": "right"}}
    program = build_pickup_program("target", "Table", "right")
    dependencies = event_dependencies(program, robots)
    assert dependencies["00/hold"]["target"] == dependencies["00/lift"]["robot1"] == {"target"}
    assert "return" not in dependencies and "settle" not in dependencies
    assert "robot0" not in dependencies["00/hold"]
    assert initial_positions(request, layout, 1200) == {"target": [0.22, -0.05]}
    assert len(position_choices(request, layout)) == 2
    generation = {
        "proposals": [
            {
                "rejection": "full_reference_occupancy",
                "poses": [[0.3, 0, 0.8, 1, 0, 0, 0], [0.39, 0, 0.8, 1, 0, 0, 0]],
                "blockers": [{"event_id": "00/hold", "phase": "hold", "body": "robot1/link7"}],
            }
        ]
    }
    repair = rank_repairs(request, layout, generation, program, robots)[0]
    assert repair["anchor"] == "target" and repair["after_xy_m"] == [0.16, -0.05]
    changed = with_positions(layout, {"target": repair["after_xy_m"]})
    assert changed["Rigid"]["can"][0]["default_pos"] == [0.16, -0.05, 0.765]
    assert changed["Rigid"]["can"][0]["scale"] == [1, 1, 1]
