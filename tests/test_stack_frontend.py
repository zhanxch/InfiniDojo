import copy
import json
import subprocess
from pathlib import Path

import pytest

from infinidojo.frontend import compile_request
from infinidojo.interaction import validate_event_trace
from infinidojo.io import sha256, write_json
from infinidojo.stack_specification import TRANSFERS, load_compilation, make_context, validate


def fixtures(tmp_path):
    layout = tmp_path / "layout.json"
    write_json(
        layout,
        {
            "Rigid": {
                "cube": [
                    {"label": f"block_{i}", "default_pos": [0.2 + 0.05 * i, -0.1, 0.78]} for i in range(3)
                ]
            }
        },
    )
    asset = tmp_path / "asset"
    asset.mkdir()
    (asset / "mesh").write_bytes(b"test-mesh")
    write_json(
        asset / "provenance.json",
        {
            "generator": "infinigen.assets.objects.tableware.can.CanFactory",
            "seed": 1,
            "parameters": {"x_length": 0.02, "z_length": 0.05, "shape": "circle"},
            "bounds": [[-0.02, -0.02, 0], [0.02, 0.02, 0.05]],
            "files": {"mesh": sha256(asset / "mesh")},
        },
    )
    context = make_context(layout, [asset])
    proposal = {
        "status": "ready",
        "reason": "",
        "request": {
            "task": "stack_blocks",
            "clutter": {
                "count": 6,
                "region_xy_m": [[0.1, -0.3], [0.48, 0.18]],
                "clearance_m": 0.005,
                "pair_distance_m": 0.09,
            },
            "task_xy_options_m": None,
        },
        "asset_pool_ids": [1],
        "transfers": copy.deepcopy(TRANSFERS),
        "return_to_initial": True,
    }
    return context, proposal, asset


def backend(monkeypatch, proposals, *, tool_item=False):
    prompts = []
    original_popen = subprocess.Popen

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            self.response = Path(command[command.index("--output-last-message") + 1])
            if tool_item:
                kwargs["stdout"].write(json.dumps({"item": {"type": "command_execution"}}) + "\n")

        def communicate(self, prompt, timeout):
            prompts.append(prompt)
            write_json(self.response, proposals[len(prompts) - 1])

    monkeypatch.setattr(
        "infinidojo.frontend.subprocess.Popen",
        lambda command, **kwargs: (
            Process(command, **kwargs)
            if "--output-last-message" in command
            else original_popen(command, **kwargs)
        ),
    )
    return prompts


def test_intrinsic_feedback_repairs_task_order_and_exports_executable_request(tmp_path, monkeypatch):
    context, good, asset = fixtures(tmp_path)
    bad = copy.deepcopy(good)
    bad["transfers"] = bad["transfers"][:1]
    prompts = backend(monkeypatch, [bad, good])
    folder = tmp_path / "compiled"
    result = compile_request("stack with six cans", folder, profile="stack-v1", context=context, attempts=2)
    assert result["accepted"] and result["attempts"] == 2
    assert "complete two-transfer task" in prompts[1]
    request, selected, _, manifest = load_compilation(folder, [asset])
    assert request["clutter"]["count"] == 6 and selected == [asset]
    assert "task_xy_options_m" not in request
    program = json.loads((folder / "interaction-program.json").read_text())
    assert len(program["events"]) == 16
    assert program["final_support"] == {"block_0": "Table", "block_1": "block_0", "block_2": "block_1"}
    assert not validate_event_trace(program, [])["complete"]  # Compilation alone is no execution evidence.
    assert manifest["files_sha256"]["generator-request.json"] == sha256(folder / "generator-request.json")
    (folder / "generator-request.json").write_text("{}")
    with pytest.raises(ValueError, match="changed since acceptance"):
        load_compilation(folder, [asset])


def test_unsupported_request_never_exports_or_silently_reduces_count(tmp_path, monkeypatch):
    context, _, _ = fixtures(tmp_path)
    declined = {
        "status": "unsupported",
        "reason": "Thirty objects exceed the supported count",
        "request": None,
        "transfers": [],
        "asset_pool_ids": [],
        "return_to_initial": False,
    }
    backend(monkeypatch, [declined])
    folder = tmp_path / "declined"
    result = compile_request("thirty objects", folder, profile="stack-v1", context=context)
    assert not result["accepted"] and result["status"] == "unsupported"
    assert not (folder / "generator-request.json").exists()
    assert not (folder / "compilation.json").exists()


def test_heldout_answers_cannot_enter_feedback_and_assets_cannot_be_invented(tmp_path):
    context, proposal, _ = fixtures(tmp_path)
    with pytest.raises(ValueError, match="scored separately"):
        compile_request(
            "six objects",
            tmp_path / "wrong",
            profile="stack-v1",
            context=context,
            expected={"clutter": {"count": 6}},
        )
    assert validate(proposal, context) == []
    proposal["asset_pool_ids"] = [999]
    assert any("Asset IDs" in e for e in validate(proposal, context))
    proposal["asset_pool_ids"] = [1]
    proposal["return_to_initial"] = False
    assert any("complete two-transfer task" in e for e in validate(proposal, context))


def test_backend_tool_use_is_not_accepted_as_plain_compilation(tmp_path, monkeypatch):
    context, proposal, _ = fixtures(tmp_path)
    backend(monkeypatch, [proposal], tool_item=True)
    with pytest.raises(RuntimeError, match="unexpected tool"):
        compile_request("six objects", tmp_path / "tool-use", profile="stack-v1", context=context)
    assert not (tmp_path / "tool-use/compilation.json").exists()


def test_compilation_cannot_be_reused_with_different_asset_metadata(tmp_path, monkeypatch):
    context, proposal, asset = fixtures(tmp_path)
    backend(monkeypatch, [proposal])
    folder = tmp_path / "compiled"
    compile_request("six objects", folder, profile="stack-v1", context=context)
    provenance = json.loads((asset / "provenance.json").read_text())
    provenance["parameters"]["z_length"] = 0.09
    write_json(asset / "provenance.json", provenance)
    with pytest.raises(ValueError, match="Runtime asset pool differs"):
        load_compilation(folder, [asset])
