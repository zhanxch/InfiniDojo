"""Package completed numeric evidence without copying licensed simulator assets."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from infinidojo.io import sha256, write_json


def archive(source: Path, destination: Path):
    summary = json.loads((source / "results/summary.json").read_text())
    rows = json.loads((source / "results/instances.json").read_text())
    if len(rows) != summary["requested"] or len(rows) != 54:
        raise ValueError("The preregistered 54 requests are not accounted for")
    if any(r["accepted"] and not r["execution_available"] for r in rows):
        raise ValueError("Accepted scenes still lack native execution evidence")
    freeze = json.loads((source / "freeze.json").read_text())
    repo = Path(__file__).resolve().parents[1]
    changed = [name for name, digest in freeze["source_files"].items() if sha256(repo / name) != digest]
    if changed:
        raise ValueError(f"Evaluated source changed after freeze: {changed}")
    destination.mkdir(parents=True, exist_ok=False)
    files = [
        "freeze.json",
        "runtime.json",
        "llm_results.json",
        "llm_usage.json",
        "robot_bounds.json",
        "mesh_content_hashes.json",
        "request_fidelity.json",
        "costs.json",
        "references/batch.json",
        "evaluation/batch.json",
        "results/summary.json",
        "results/instances.json",
        "generation_rows.json",
    ]
    for path in (source / "assets").glob("*/provenance.json"):
        files.append(str(path.relative_to(source)))
    for path in (source / "llm").rglob("*"):
        if path.is_file() and path.name in {
            "request.txt",
            "history.json",
            "response.json",
            "prompt.txt",
            "schema.json",
            "specification.json",
            "result.json",
            "events.jsonl",
        }:
            files.append(str(path.relative_to(source)))
    for directory, patterns in {
        "references": ["*/result.json"],
        "generated": ["*/generation.json", "*/layout.json", "*/reference_audit.json"],
        "evaluation": ["*/result.json", "*/execution_audit.json"],
    }.items():
        for pattern in patterns:
            files += [str(p.relative_to(source)) for p in (source / directory).glob(pattern)]
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    raw_index = {}
    for subdir in ["references", "evaluation"]:
        for p in (source / subdir).rglob("*.json"):
            if "asset_overlay" not in p.parts:
                raw_index[str(p.relative_to(source))] = {"sha256": sha256(p), "bytes": p.stat().st_size}
    write_json(destination / "raw_artifact_index.json", raw_index)
    write_json(
        destination / "archive_check.json",
        {"requested": len(rows), "source_unchanged": True, "packaged_files": len(files)},
    )
    print(f"Archived {len(files)} numeric evidence files in {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    archive(args.source.resolve(), args.destination.resolve())


if __name__ == "__main__":
    main()
