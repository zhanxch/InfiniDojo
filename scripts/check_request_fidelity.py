"""Post-run count, support-region, provenance and cost checks for the frozen pilot."""

import argparse
import json
from pathlib import Path

import numpy as np

from infinidojo.geometry import aabb, transformed_box
from infinidojo.io import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    cases = json.loads((run / "evaluation_cases.json").read_text())
    assets = {
        int(p.name): json.loads((p / "provenance.json").read_text()) for p in (run / "assets").iterdir()
    }
    checks = []
    for case in cases:
        folder = run / "evaluation" / case["id"]
        result = json.loads((folder / "result.json").read_text())
        initial = json.loads((folder / "trajectory.json").read_text())[0]
        generated = json.loads((run / "generated" / case["id"] / "generation.json").read_text())
        layout = json.loads(Path(case["layout"]).read_text())
        objects = {obj["label"]: obj for obj in layout["Rigid"]["infinigen_can"]}
        count = generated["requested_count"]
        spec = json.loads((run / "llm" / f"count{count}-wording0/specification.json").read_text())
        region_lo = np.array([spec["clutter"]["x_bounds_m"][0], spec["clutter"]["y_bounds_m"][0]])
        region_hi = np.array([spec["clutter"]["x_bounds_m"][1], spec["clutter"]["y_bounds_m"][1]])

        def in_region(box, region_lo=region_lo, region_hi=region_hi):
            return bool(np.all(box[0, :2] >= region_lo - 1e-6) and np.all(box[1, :2] <= region_hi + 1e-6))

        requested_bounds_ok = all(
            in_region(aabb(transformed_box(item["bounds"], item["pose"]))) for item in generated["clutter"]
        )
        initial_bounds = {}
        for name, label in result["object_labels"].items():
            if label == "target":
                continue
            box = aabb(
                transformed_box(assets[objects[label]["category_idx"]]["bounds"], initial["objects"][name])
            )
            initial_bounds[label] = in_region(box)
        checks.append(
            {
                "id": case["id"],
                "requested_clutter": count,
                "generated_clutter": len(generated["clutter"]),
                "native_clutter": len(initial_bounds),
                "count_preserved": count == len(generated["clutter"]) == len(initial_bounds),
                "generated_bounds_satisfied": requested_bounds_ok,
                "settled_bounds_satisfied": all(initial_bounds.values()),
                "settled_out_of_bounds": [name for name, valid in initial_bounds.items() if not valid],
            }
        )
    usage = json.loads((run / "llm_usage.json").read_text())
    tokens = {
        key: sum(u.get(key, 0) for record in usage for u in record["usage"])
        for key in ["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"]
    }
    llm_seconds = sum(
        row["wall_seconds"] for p in (run / "llm").glob("*/history.json") for row in json.loads(p.read_text())
    )
    costs = {
        "reference_batch": json.loads((run / "references/batch.json").read_text()),
        "evaluation_batch": json.loads((run / "evaluation/batch.json").read_text()),
        "llm_wall_seconds": llm_seconds,
        "llm_tokens": tokens,
        "note": "CLI token totals include its harness context. Layout generation excludes reference acquisition; report both.",
    }
    write_json(
        run / "request_fidelity.json",
        {
            "instances": checks,
            "count_preserved": sum(x["count_preserved"] for x in checks),
            "generated_bounds_satisfied": sum(x["generated_bounds_satisfied"] for x in checks),
            "settled_bounds_satisfied": sum(x["settled_bounds_satisfied"] for x in checks),
        },
    )
    write_json(run / "costs.json", costs)


if __name__ == "__main__":
    main()
