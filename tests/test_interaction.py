import pytest

from infinidojo.interaction import (
    build_cover_program,
    build_pickup_program,
    build_program,
    validate_event_trace,
)


def program():
    return build_program(
        [
            {"object": "b1", "source": "table", "destination": "b0", "arm": "right"},
            {"object": "b2", "source": "table", "destination": "b1", "arm": "right"},
        ],
        {"b0": "table", "b1": "table", "b2": "table"},
    )


def test_release_keeps_placed_object_in_later_interactions():
    p = program()
    transport = next(e for e in p["events"] if e["id"] == "01/transport")
    assert transport["support_relations"] == {"b0": "table", "b1": "b0"}
    assert transport["attachment"] == "held"
    retreat = p["events"][-1]
    assert retreat["attachment"] == "free"
    assert retreat["support_relations"]["b2"] == "b1"
    assert ("b2", "right/gripper") not in retreat["allowed_contacts"]


def test_repeated_transfer_must_use_updated_support_and_avoid_cycles():
    with pytest.raises(ValueError, match="source"):
        build_program(
            [{"object": "b1", "source": "table", "destination": "b2", "arm": "right"}],
            {"b1": "b0", "b0": "table", "b2": "table"},
        )
    with pytest.raises(ValueError, match="Cyclic"):
        build_program(
            [{"object": "b0", "source": "table", "destination": "b1", "arm": "right"}],
            {"b0": "table", "b1": "b0"},
        )


def test_skipped_or_reordered_events_do_not_count_as_complete():
    p = program()
    trace = [{"step": i, "event_id": e["id"]} for i, e in enumerate(p["events"])]
    assert validate_event_trace(p, trace)["complete"]
    assert not validate_event_trace(p, trace[1:])["complete"]
    with pytest.raises(ValueError, match="backwards"):
        validate_event_trace(p, trace[::-1])


def test_pickup_ends_held_without_invented_release_or_support():
    p = build_pickup_program("target", "Table", "arm1")
    hold = p["events"][-1]
    assert hold["phase"] == "hold" and hold["attachment"] == "held"
    assert hold["support_relations"] == {} and p["final_support"] == {}
    assert p["final_attachment"] == {"target": "arm1/gripper"}
    assert ("target", "Table") not in hold["allowed_contacts"]
    trace = [{"step": i, "event_id": e["id"]} for i, e in enumerate(p["events"])]
    assert validate_event_trace(p, trace)["complete"]
    assert not validate_event_trace(p, trace[:-1])["complete"]


def test_cover_preserves_table_support_and_uncover_color_order():
    p = build_cover_program(["green", "blue", "red"], {"cup0": "left", "cup1": "right", "cup2": "right"})
    assert [t["block"] for t in p["transfers"]] == ["green", "blue", "red", "red", "green", "blue"]
    assert [t["object"] for t in p["transfers"][3:]] == ["cup2", "cup0", "cup1"]
    assert p["events"][23]["containment_goal_after_transfer"] == {
        "cup0": "green",
        "cup1": "blue",
        "cup2": "red",
    }
    assert p["events"][-1]["containment_goal_after_transfer"] == {}
    assert all(support == "Table" for support in p["final_support"].values())
    for event in p["events"]:
        assert (event["object"], event["target_block"]) not in event["allowed_contacts"]
        assert event["support_relations"][event["target_block"]] == "Table"
    trace = [{"step": i, "event_id": e["id"]} for i, e in enumerate(p["events"])]
    assert validate_event_trace(p, trace)["complete"]
    assert not validate_event_trace(p, trace[:24])["complete"]
