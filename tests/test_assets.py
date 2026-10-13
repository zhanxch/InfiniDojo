import json

import numpy as np
import pytest

from infinidojo.assets import merge_asset, pickup_layout
from infinidojo.completion import metadata_bounds
from infinidojo.io import sha256, write_json


def asset(folder, *, seed=1203, height=0.07):
    folder.mkdir()
    bounds = [[-0.022, -0.022, 0], [0.022, 0.022, height]]
    write_json(
        folder / "metadata.json",
        {
            "geometry": {"aligned_bbox": {"vertices": bounds}},
            "physics": {"mass": 0.08, "static_friction": 1.0, "dynamic_friction": 0.8},
        },
    )
    write_json(
        folder / "provenance.json",
        {
            "seed": seed,
            "generator": "infinigen.assets.objects.tableware.can.CanFactory",
            "parameters": {"x_length": 0.022, "z_length": height},
            "bounds": bounds,
            "files": {"metadata.json": sha256(folder / "metadata.json")},
        },
    )
    return folder


def test_generated_target_uses_generated_geometry_and_preserves_fixture(tmp_path):
    folder = asset(tmp_path / "asset")
    base = {
        "Rigid": {"can": [{"label": "target"}], "fan": [{"type": "cluttered"}]},
        "Table": {"default_pos": [0, 0, 0.75], "scale": [1, 1, 0.03]},
    }
    layout = pickup_layout(base, folder, [0.22, -0.05])
    target = layout["Rigid"]["infinigen_can"][0]
    assert target["label"] == "target" and target["default_pos"][2] == pytest.approx(0.765)
    assert layout["Table"] == base["Table"] and "can" in base["Rigid"]
    mapping = {}
    merge_asset(mapping, folder)
    assert np.allclose(
        metadata_bounds(tmp_path, target, asset_map=mapping), [[-0.022, -0.022, 0], [0.022, 0.022, 0.07]]
    )
    with pytest.raises(ValueError, match="missing"):
        metadata_bounds(tmp_path, target)


def test_target_and_clutter_cannot_silently_reuse_an_id_for_different_assets(tmp_path):
    mapping = {}
    first = asset(tmp_path / "first")
    merge_asset(mapping, first)
    merge_asset(mapping, first)
    with pytest.raises(ValueError, match="Conflicting"):
        merge_asset(mapping, asset(tmp_path / "different", height=0.09))
    metadata = json.loads((first / "metadata.json").read_text())
    metadata["physics"]["mass"] = 5
    write_json(first / "metadata.json", metadata)
    with pytest.raises(ValueError, match="changed"):
        merge_asset({}, first)
