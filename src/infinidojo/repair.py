"""One event-conditioned task-anchor repair, followed by real replanning.

The ranking is a directional heuristic, not a kinematic model or feasibility
certificate. It never translates robot poses or controls. Task positions must
come from the request's finite options; all other layout fields stay unchanged.
"""

from __future__ import annotations

import copy
from itertools import product

import numpy as np

LABELS = ("block_0", "block_1", "block_2")


def validate_position_options(request):
    options = request.get("task_xy_options_m")
    if options is None:
        return
    if set(options) != set(LABELS):
        raise ValueError("Task position options must specify all three block identities")
    for label, values in options.items():
        points = np.asarray(values, dtype=float)
        if (
            points.ndim != 2
            or points.shape[1] != 2
            or not 1 <= len(points) <= 9
            or not np.isfinite(points).all()
            or np.any(points < [-0.55, -0.35])
            or np.any(points > [0.55, 0.35])
            or len(np.unique(points, axis=0)) != len(points)
        ):
            raise ValueError(f"Invalid finite XY options for {label}")


def task_positions(layout):
    entries = {item["label"]: item for group in layout["Rigid"].values() for item in group}
    return {label: list(entries[label]["default_pos"][:2]) for label in LABELS}


def with_positions(layout, positions):
    updated = copy.deepcopy(layout)
    for group in updated["Rigid"].values():
        for item in group:
            if item["label"] in positions:
                item["default_pos"][:2] = positions[item["label"]]
    return updated


def position_choices(request, base):
    validate_position_options(request)
    options = request.get("task_xy_options_m")
    if options is None:
        return [task_positions(base)]
    return [dict(zip(LABELS, points, strict=True)) for points in product(*(options[k] for k in LABELS))]


def initial_positions(request, base, seed):
    choices = position_choices(request, base)
    original = task_positions(base)
    # Preserve a supplied allowed layout as the common starting point.
    return (
        original if original in choices else choices[int(np.random.default_rng(seed).integers(len(choices)))]
    )


def event_dependencies(program, robots):
    """Endpoint/support dependencies, including objects released in earlier events.

    These dependencies guide which anchor to adjust; they do not justify reusing
    unaffected controls. The implementation replans and executes the entire task.
    """
    roots = {label: label for label in program["initial_support"]}
    groups = {}
    for event in program["events"]:
        groups.setdefault(event["id"].split("/")[0], []).append(event)
    result = {}
    for events in groups.values():
        obj = events[0]["object"]
        destination = next(e["support"] for e in events if e["phase"] == "release")
        source_root, destination_root = roots[obj], roots[destination]
        for event in events:
            phase = event["phase"]
            active = (
                {source_root}
                if phase in ("approach", "descend", "grasp", "lift")
                else {source_root, destination_root}
                if phase == "transport"
                else {destination_root}
            )
            mapping = {label: {root} for label, root in roots.items()}
            mapping[obj] = active
            for robot, identity in robots.items():
                if identity["arm"] == event["arm"]:
                    mapping[robot] = active
            result[event["id"]] = mapping
            if phase == "release":
                roots[obj] = destination_root
    result["initial"] = {label: {label} for label in program["initial_support"]}
    result["settle"] = {label: {root} for label, root in roots.items()}
    result["return"] = {**result["settle"]}
    for robot, identity in robots.items():
        if identity["arm"] == program["events"][-1]["arm"]:
            result["return"][robot] = {roots[program["events"][-1]["object"]]}
    return result


def rank_repairs(request, layout, generation, program, robots):
    """Rank one-anchor changes away from event-attributed rejected cluster centers.

    Each rejected clutter-cluster proposal votes at most once per anchor. Robot
    base, inactive arm and fixed fixtures do not vote for a task-position repair.
    The score is only a search order; a changed reference needs physical execution.
    """
    validate_position_options(request)
    if "task_xy_options_m" not in request:
        return []
    dependencies = event_dependencies(program, robots)
    points, causes = {label: [] for label in LABELS}, {label: set() for label in LABELS}
    for proposal in generation["proposals"]:
        if proposal["rejection"] != "full_reference_occupancy":
            continue
        voters = set()
        for blocker in proposal.get("blockers", []):
            body = blocker["body"]
            if body.endswith("/base_link"):
                continue
            event = blocker["event_id"] or blocker["phase"]
            for anchor in dependencies.get(event, {}).get(body.split("/")[0], set()):
                voters.add(anchor)
                causes[anchor].add(event)
        center = np.mean([pose[:2] for pose in proposal["poses"]], axis=0)
        for anchor in voters:
            points[anchor].append(center)
    current = task_positions(layout)
    ranked = []
    for label in LABELS:
        if not points[label]:
            continue
        centers = np.asarray(points[label])
        baseline = np.linalg.norm(centers - current[label], axis=1)
        for candidate in request["task_xy_options_m"][label]:
            if candidate == current[label]:
                continue
            score = float(np.mean(np.linalg.norm(centers - candidate, axis=1) - baseline))
            if score <= 1e-9:
                continue
            ranked.append(
                {
                    "anchor": label,
                    "before_xy_m": current[label],
                    "after_xy_m": candidate,
                    "mean_distance_gain_m": score,
                    "blocking_cluster_proposals": len(centers),
                    "blocking_events": sorted(causes[label]),
                    "dependent_events": sorted(
                        event
                        for event, bodies in dependencies.items()
                        if any(label in x for x in bodies.values())
                    ),
                }
            )
    return sorted(ranked, key=lambda r: (-r["mean_distance_gain_m"], r["anchor"], r["after_xy_m"]))
