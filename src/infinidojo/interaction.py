"""Event-dependent contact and support relations for a complete manipulation.

These are planning constraints, not assertions that a physical grasp succeeded.
Execution evidence must establish grasp, release and stable support separately.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class InteractionEvent:
    id: str
    phase: str
    object: str
    arm: str
    attachment: str
    support: str | None
    allowed_contacts: tuple[tuple[str, str], ...]

    def as_dict(self):
        return asdict(self)


def transfer_events(index: int, obj: str, source: str, destination: str, arm: str):
    """A transfer preserves object identity across grasp, transport and release."""
    if obj in (source, destination):
        raise ValueError("The manipulated object cannot be its own support")
    gripper = f"{arm}/gripper"
    phases = [
        ("approach", "free", source, [(obj, source)]),
        ("descend", "free", source, [(obj, source)]),
        ("grasp", "contact", source, [(obj, source), (obj, gripper)]),
        ("lift", "held", None, [(obj, source), (obj, gripper)]),
        ("transport", "held", None, [(obj, gripper)]),
        ("place", "held", None, [(obj, gripper), (obj, destination)]),
        ("release", "contact", destination, [(obj, gripper), (obj, destination)]),
        ("retreat", "free", destination, [(obj, destination)]),
    ]
    return [
        InteractionEvent(f"{index:02d}/{phase}", phase, obj, arm, attachment, support, tuple(contacts))
        for phase, attachment, support, contacts in phases
    ]


def build_program(transfers: list[dict], initial_support: dict[str, str]):
    """Compile shared support state; completed transfers remain in later events."""
    supports = dict(initial_support)
    frames = []
    for index, transfer in enumerate(transfers):
        obj, source, destination, arm = (transfer[k] for k in ("object", "source", "destination", "arm"))
        if supports.get(obj) != source:
            raise ValueError(f"{obj} is not supported by requested source {source}")
        node = destination
        seen = {obj}
        while node in supports:
            if node in seen:
                raise ValueError("Cyclic support relation")
            seen.add(node)
            node = supports[node]
        for event in transfer_events(index, obj, source, destination, arm):
            passive = {key: value for key, value in supports.items() if key != obj}
            if event.support is not None:
                passive[obj] = event.support
            frames.append({**event.as_dict(), "support_relations": passive})
        supports[obj] = destination
    return {"events": frames, "initial_support": initial_support, "final_support": supports}


def build_pickup_program(obj: str, source: str, arm: str):
    """A pickup ends held, without inventing a release or return requirement."""
    if obj == source:
        raise ValueError("Object and initial support must differ")
    gripper = f"{arm}/gripper"
    phases = [
        ("approach", "free", source, [(obj, source)]),
        ("descend", "free", source, [(obj, source)]),
        ("grasp", "contact", source, [(obj, source), (obj, gripper)]),
        ("lift", "held", None, [(obj, source), (obj, gripper)]),
        ("hold", "held", None, [(obj, gripper)]),
    ]
    events = []
    for phase, attachment, support, contacts in phases:
        event = InteractionEvent(f"00/{phase}", phase, obj, arm, attachment, support, tuple(contacts))
        events.append({**event.as_dict(), "support_relations": {obj: support} if support else {}})
    return {
        "events": events,
        "initial_support": {obj: source},
        "final_support": {},
        "final_attachment": {obj: gripper},
    }


def build_cover_program(block_order, cup_arms):
    """Six table-to-table transfers; coverage is distinct from support/contact."""
    colors = ("red", "green", "blue")
    cups = ("cup0", "cup1", "cup2")
    if len(block_order) != 3 or set(block_order) != set(colors) or set(cup_arms) != set(cups):
        raise ValueError("Cover task needs the three colors and three assigned cups")
    paired = dict(zip(block_order, cups, strict=True))
    transfers = []
    for operation, order in (("cover", block_order), ("uncover", colors)):
        for block in order:
            cup = paired[block]
            transfers.append(
                {
                    "object": cup,
                    "source": "Table",
                    "destination": "Table",
                    "arm": cup_arms[cup],
                    "operation": operation,
                    "block": block,
                }
            )
    program = build_program(transfers, {label: "Table" for label in (*colors, *cups)})
    program["transfers"] = transfers
    program["cover_order"] = list(block_order)
    covered = {}
    for index, transfer in enumerate(transfers):
        before = dict(covered)
        cup = transfer["object"]
        if transfer["operation"] == "cover":
            covered[cup] = transfer["block"]
        else:
            del covered[cup]
        for event in program["events"][index * 8 : (index + 1) * 8]:
            event.update(
                operation=transfer["operation"],
                target_block=transfer["block"],
                containment_before_transfer=before,
                containment_goal_after_transfer=dict(covered),
            )
    return program


def validate_event_trace(program, trajectory):
    """Check actual recorded ordering and boundaries, without claiming feasibility."""
    expected = [event["id"] for event in program["events"]]
    observed = []
    last_step = -1
    for frame in trajectory:
        if frame["step"] < last_step:
            raise ValueError("Trajectory time moved backwards")
        last_step = frame["step"]
        event = frame.get("event_id")
        if event is not None and (not observed or observed[-1] != event):
            observed.append(event)
    return {"complete": observed == expected, "expected": expected, "observed": observed}
