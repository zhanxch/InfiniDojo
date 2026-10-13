import json

import pytest

from infinidojo.episode import export_episode, load_episode
from infinidojo.io import sha256, write_json


@pytest.mark.parametrize("task", ["stack_blocks", "general_pickup"])
def test_portable_bundle_rejects_changed_or_external_files(tmp_path, task):
    folder = tmp_path / "episode"
    folder.mkdir()
    for name in ("layout.json", "request.json", "asset-map.json"):
        write_json(folder / name, {})
    manifest = {
        "format": "infinidojo.episode.v1",
        "task": task,
        "robot": "arx_x5",
        "layout": "layout.json",
        "request": "request.json",
        "asset_map": "asset-map.json",
        "files_sha256": {p.name: sha256(p) for p in folder.iterdir()},
    }
    write_json(folder / "episode.json", manifest)
    _, layout, assets = load_episode(folder)
    assert layout == folder / "layout.json" and assets == {}
    write_json(layout, {"changed": True})
    with pytest.raises(ValueError, match="content changed"):
        load_episode(folder)
    manifest["files_sha256"] = {"../outside": "x"}
    write_json(folder / "episode.json", manifest)
    with pytest.raises(ValueError, match="external path"):
        load_episode(folder)


def test_unverified_or_post_validation_changed_episode_is_not_exported(tmp_path):
    audit = tmp_path / "audit.json"
    output = tmp_path / "bundle"
    write_json(audit, {"status": "unverified", "checks": {"native_success": True}})
    with pytest.raises(ValueError, match="passing all"):
        export_episode(tmp_path, tmp_path, audit, output)
    assert not output.exists()
    write_json(tmp_path / "layout.json", {})
    write_json(
        audit,
        {
            "status": "validated_under_development_checks",
            "checks": {"all": True},
            "input_sha256": {"layout.json": sha256(tmp_path / "layout.json")},
        },
    )
    (tmp_path / "layout.json").write_text(json.dumps({"changed": True}))
    with pytest.raises(ValueError, match="changed since validation"):
        export_episode(tmp_path, tmp_path, audit, output)
    assert not output.exists()
