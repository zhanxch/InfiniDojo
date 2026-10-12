"""Portable native episode bundles; reference controls remain outside the bundle."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from infinidojo.io import sha256, write_json


def export_episode(execution, generation, validation_path, destination):
    audit = json.loads(validation_path.read_text())
    if (
        audit["status"] != "validated_under_development_checks"
        or not audit["checks"]
        or not all(audit["checks"].values())
    ):
        raise ValueError("Only an episode passing all declared checks can be exported")
    for name, digest in audit["input_sha256"].items():
        if sha256(execution / name) != digest:
            raise ValueError(f"Execution changed since validation: {name}")
    for name, digest in audit["generation_input_sha256"].items():
        if sha256(generation / name) != digest:
            raise ValueError(f"Generation changed since validation: {name}")
    provenance = json.loads((execution / "provenance.json").read_text())
    mapping = json.loads((generation / "asset-map.json").read_text())
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(execution / "layout.json", destination / "layout.json")
    shutil.copyfile(generation / "request.json", destination / "request.json")
    portable = {}
    for asset_id, path in mapping.items():
        source = (generation / path).resolve()
        record = json.loads((source / "provenance.json").read_text())
        target = destination / "assets" / asset_id
        target.mkdir(parents=True)
        for name, digest in record["files"].items():
            if sha256(source / name) != digest:
                raise ValueError(f"Asset changed since generation: {asset_id}/{name}")
            shutil.copyfile(source / name, target / name)
        shutil.copyfile(source / "provenance.json", target / "provenance.json")
        portable[asset_id] = f"assets/{asset_id}"
    write_json(destination / "asset-map.json", portable)
    files = {
        str(p.relative_to(destination)): sha256(p) for p in sorted(destination.rglob("*")) if p.is_file()
    }
    manifest = {
        "format": "infinidojo.episode.v1",
        "task": "stack_blocks",
        "robot": "arx_x5",
        "seed": provenance["seed"],
        "instruction": "Stack the three blocks with different textures.",
        "robodojo_commit": provenance["robodojo_commit"],
        "layout": "layout.json",
        "asset_map": "asset-map.json",
        "request": "request.json",
        "files_sha256": files,
        "validation": {
            "status": audit["status"],
            "threshold_status": audit["threshold_status"],
            "thresholds": audit["thresholds"],
            "report_sha256": sha256(validation_path),
        },
        "policy_observation_contract": "Native cameras and proprioception only; scene metadata stays in harness.",
        "native_asset_requirement": "Licensed native RoboDojo assets must be installed separately.",
    }
    write_json(destination / "episode.json", manifest)
    return manifest


def load_episode(folder):
    folder = folder.resolve()
    manifest = json.loads((folder / "episode.json").read_text())
    if (
        manifest["format"] != "infinidojo.episode.v1"
        or manifest["task"] != "stack_blocks"
        or manifest["robot"] != "arx_x5"
    ):
        raise ValueError("Unsupported episode format or task")

    def within(relative):
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder):
            raise ValueError("Episode contains an external path")
        return path

    for name, digest in manifest["files_sha256"].items():
        if sha256(within(name)) != digest:
            raise ValueError(f"Episode content changed: {name}")
    if any(manifest[key] not in manifest["files_sha256"] for key in ("layout", "asset_map", "request")):
        raise ValueError("Episode is missing required file hashes")
    layout = within(manifest["layout"])
    mapping = json.loads(within(manifest["asset_map"]).read_text())
    asset_map = {key: str(within(value)) for key, value in mapping.items()}
    return manifest, layout, asset_map


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = export_episode(
        args.execution.resolve(), args.generation.resolve(), args.validation.resolve(), args.output
    )
    print(json.dumps({key: manifest[key] for key in ("format", "task", "seed", "validation")}))


if __name__ == "__main__":
    main()
