"""Reconstruct the declared pickup development layouts without copying native assets."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from infinidojo.io import git_revision, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robodojo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.robodojo_root.resolve(), args.output.resolve()
    if git_revision(root) != "726e9aabfaa642203722eb126f5eaf0f37f3e1ad":
        raise ValueError("Development layouts require the declared RoboDojo revision")
    source = root / "Assets/Eval_Layout/RoboDojo/arx_x5/0/general_pickup_0.json"
    asset = root / "Assets/Object/RoboDojo/Rigid/can/00000"
    metadata = json.loads((asset / "metadata.json").read_text())
    base = json.loads(source.read_text())
    bottom = min(v[2] for v in metadata["geometry"]["aligned_bbox"]["vertices"])
    output.mkdir(parents=True, exist_ok=False)
    positions = {1200: [0.22, -0.05], 1201: [0.28, 0.02]}
    for seed, xy in positions.items():
        layout = copy.deepcopy(base)
        layout["Rigid"] = {
            "can": [
                {
                    "category": "can",
                    "category_idx": 0,
                    "label": "target",
                    "default_pos": [
                        *xy,
                        base["Table"]["default_pos"][2] + base["Table"]["scale"][2] / 2 - bottom,
                    ],
                    "default_ori": [1, 0, 0, 0],
                    "scale": [1, 1, 1],
                    "physics": {"type": "rigid", **metadata["physics"]},
                    "visual": {},
                    "relative_plane": "Table",
                }
            ]
        }
        write_json(output / f"layout-{seed}.json", layout)
    write_json(
        output / "request-6.json",
        {
            "task": "general_pickup",
            "clutter": {
                "count": 6,
                "region_xy_m": [[0.08, -0.14], [0.5, 0.28]],
                "clearance_m": 0.003,
                "pair_distance_m": 0.09,
            },
        },
    )
    write_json(
        output / "inputs.json",
        {
            "development_only": True,
            "task": "general_pickup",
            "positions": positions,
            "robodojo_commit": git_revision(root),
            "fixture_layout_sha256": sha256(source),
            "native_target_sha256": {
                name: sha256(asset / name) for name in ("metadata.json", "description.json", "object.usdz")
            },
            "selection": "Two previously declared right-arm positions; native upright can; not all pickup assets.",
            "files_sha256": {p.name: sha256(p) for p in output.glob("*.json")},
        },
    )


if __name__ == "__main__":
    main()
