import copy

import pytest
from test_stack_frontend import backend

from infinidojo.frontend import compile_request
from infinidojo.io import write_json
from infinidojo.scene_specification import (
    TASK_LABELS,
    load_compilation,
    make_context,
    minimal_layout,
    validate,
)


@pytest.fixture(autouse=True)
def fixture_revision(monkeypatch):
    monkeypatch.setattr("infinidojo.scene_specification.git_revision", lambda path: "fixture-revision")


def context(tmp_path):
    for task, labels in TASK_LABELS.items():
        path = tmp_path / f"Assets/Eval_Layout/RoboDojo/arx_x5/0/{task}_0.json"
        write_json(
            path,
            {
                "Rigid": {
                    "task": [{"label": label, "default_pos": [0.22, -0.05, 0.8]} for label in labels],
                    "other": [{"type": "cluttered", "default_pos": [0, 0, 1]}],
                }
            },
        )
    return make_context(tmp_path)


def proposal(task="general_pickup"):
    can = {"radius_m": 0.022, "height_m": 0.07, "shape": "circle", "color": "red"}
    return {
        "status": "ready",
        "reason": "",
        "task": task,
        "target": {"asset": can, "xy_m": [0.22, -0.05]} if task == "general_pickup" else None,
        "clutter": {
            "count": 6,
            "region_xy_m": [[-0.35, -0.28], [0.5, 0.28]],
            "clearance_m": 0.003,
            "pair_distance_m": 0.09,
            "anchor_region": None,
            "asset": {**can, "height_m": 0.05, "color": "blue"},
        },
    }


@pytest.mark.parametrize("task", list(TASK_LABELS))
def test_shared_frontend_preserves_each_native_task_and_rejects_changed_runtime(tmp_path, monkeypatch, task):
    ctx, good = context(tmp_path), proposal(task)
    backend(monkeypatch, [good])
    result = compile_request("A test request", tmp_path / "compiled", profile="scene-v2", context=ctx)
    assert result["accepted"]
    restored, _ = load_compilation(tmp_path / "compiled", tmp_path)
    assert restored == good
    path = tmp_path / f"Assets/Eval_Layout/RoboDojo/arx_x5/0/{task}_0.json"
    path.write_text("{}")
    with pytest.raises((ValueError, KeyError)):
        load_compilation(tmp_path / "compiled", tmp_path)


def test_request_cannot_hide_missing_task_objects_or_unsupported_appearance(tmp_path):
    ctx, spec = context(tmp_path), proposal()
    assert validate(spec, ctx) == []
    spec["clutter"]["asset"] = copy.deepcopy(spec["target"]["asset"])
    assert any("distinguishing" in error for error in validate(spec, ctx))
    spec["clutter"]["asset"]["color"] = "transparent"
    assert validate(spec, ctx)
    with pytest.raises(ValueError, match="complete task"):
        minimal_layout({"Rigid": {"cube": [{"label": "block_0"}]}}, "stack_blocks")


def test_cross_task_relation_and_impossible_pair_count_are_rejected(tmp_path):
    ctx, spec = context(tmp_path), proposal()
    spec["clutter"]["anchor_region"] = {"anchor": "cup0", "offset_region_xy_m": [[0.1, 0], [0.3, 0.2]]}
    assert any("Anchor" in error for error in validate(spec, ctx))
    spec["clutter"]["anchor_region"] = None
    spec["clutter"]["count"] = 5
    assert any("even count" in error for error in validate(spec, ctx))
    spec["clutter"]["count"] = 6
    spec["target"]["asset"]["height_m"] = 0.3
    assert any("target.height_m" in error for error in validate(spec, ctx))


def test_unsupported_request_has_no_executable_artifact(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    backend(
        monkeypatch,
        [
            {
                "status": "unsupported",
                "reason": "Open containers are unsupported",
                "task": None,
                "target": None,
                "clutter": None,
            }
        ],
    )
    result = compile_request("Pick up an open mug", tmp_path / "compiled", profile="scene-v2", context=ctx)
    assert result["status"] == "unsupported"
    assert not (tmp_path / "compiled/compilation.json").exists()
