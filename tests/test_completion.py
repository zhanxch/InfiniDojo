import numpy as np
import pytest

from infinidojo.completion import complete_scene, reference_boxes


def request(count=2, distance=0.09):
    return {
        "task": "stack_blocks",
        "clutter": {
            "count": count,
            "region_xy_m": [[0.1, -0.1], [0.5, 0.2]],
            "clearance_m": 0.005,
            "pair_distance_m": distance,
        },
    }


def test_released_and_passive_objects_reserve_later_scene_space():
    bounds = {
        "b0": [[-0.02, -0.02, -0.02], [0.02, 0.02, 0.02]],
        "b1": [[-0.02, -0.02, -0.02], [0.02, 0.02, 0.02]],
    }
    frames = [
        {
            "step": 10,
            "phase": "transport",
            "time_s": 0.04,
            "robots": [],
            "objects": {"b0": [0.3, 0, 0.79, 1, 0, 0, 0], "b1": [0.3, 0, 0.83, 1, 0, 0, 0]},
        }
    ]
    boxes = reference_boxes(frames, {}, bounds)
    assert [label for label, _ in boxes[0]["bodies"]] == ["b0", "b1"]
    assert np.allclose(boxes[0]["bodies"][1][1][0], [0.3, 0, 0.83])


def test_paired_clutter_keeps_relation_and_does_not_shrink_failed_request():
    pool = [{"asset_id": 1, "bounds": [[-0.02, -0.02, 0], [0.02, 0.02, 0.06]]}]
    far = {"bodies": [("arm", (np.array([-1.0, -1.0, 1.0]), np.ones(3) * 0.1, np.eye(3)))]}
    generated = complete_scene(request(), pool, [far], seed=1200, tabletop_z=0.765, attempt_budget=20)
    assert generated["accepted"]
    a, b = [np.array(item["pose"][:2]) for item in generated["clutter"]]
    assert np.linalg.norm(a - b) == pytest.approx(0.09)
    blocked = {"bodies": [("later_object", (np.array([0.3, 0, 0.8]), np.ones(3), np.eye(3)))]}
    failed = complete_scene(request(), pool, [far, blocked], seed=1200, tabletop_z=0.765, attempt_budget=20)
    assert not failed["accepted"] and failed["requested_count"] == 2
    assert failed["generated_count"] == 0 and failed["attempts"] == 20


def test_unsupported_relation_is_rejected_before_generation():
    with pytest.raises(ValueError, match="even count"):
        complete_scene(request(count=3), [], [], seed=1, tabletop_z=0.765)
