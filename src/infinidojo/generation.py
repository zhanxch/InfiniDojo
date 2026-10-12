"""Matched layout generators under an event-conditioned reference interaction."""

from __future__ import annotations

import time

import numpy as np

from infinidojo.geometry import aabb, overlap_aabb, transformed_box
from infinidojo.specification import validate

METHODS = ("independent", "static", "interaction")


def generate_layout(spec, asset_pool, frames, *, seed, method, attempt_budget=500):
    errors = validate(spec)
    if errors:
        raise ValueError(errors)
    if method not in METHODS or attempt_budget < 1 or not asset_pool or not frames:
        raise ValueError("Unsupported generator configuration")
    start = time.monotonic()
    rng = np.random.default_rng(seed)
    # Asset identity is fixed before rejection sampling, so rejection cannot
    # replace a difficult object by a smaller one.
    ids = np.random.default_rng(seed).integers(0, len(asset_pool), spec["clutter"]["count"])
    clearance = spec["clutter"]["clearance_m"]
    reserved = []
    if method == "static":
        reserved = [aabb(b) for _, b in frames[0]["bodies"]]
    elif method == "interaction":
        reserved = [aabb(b) for frame in frames for _, b in frame["bodies"]]
    reserved = np.asarray(reserved)
    occupied, accepted, proposals = [], [], []
    for object_idx, asset_idx in enumerate(ids):
        asset = asset_pool[int(asset_idx)]
        bounds = np.asarray(asset["bounds"])
        xmin, xmax = spec["clutter"]["x_bounds_m"]
        ymin, ymax = spec["clutter"]["y_bounds_m"]
        lo = np.array([xmin, ymin]) - bounds[0, :2]
        hi = np.array([xmax, ymax]) - bounds[1, :2]
        if np.any(lo > hi):
            return {
                "accepted": False,
                "reason": "asset_does_not_fit_region",
                "clutter": accepted,
                "proposals": proposals,
                "wall_seconds": time.monotonic() - start,
            }
        placed = False
        while len(proposals) < attempt_budget:
            xy = rng.uniform(lo, hi)
            pose = [*xy, 0.765, 1, 0, 0, 0]
            box = aabb(transformed_box(bounds, pose))
            reason = None
            if method != "independent":
                if any(overlap_aabb(box, other, clearance) for other in occupied):
                    reason = "clutter_overlap"
                elif len(reserved) and np.any(
                    np.all(box[0] < reserved[:, 1] + clearance, axis=1)
                    & np.all(reserved[:, 0] < box[1] + clearance, axis=1)
                ):
                    reason = "interaction_occupancy" if method == "interaction" else "initial_occupancy"
            proposals.append({"object": object_idx, "pose": pose, "rejection": reason})
            if reason is None:
                accepted.append(
                    {
                        "id": f"clutter_{object_idx}",
                        "asset_id": asset["asset_id"],
                        "bounds": bounds.tolist(),
                        "pose": pose,
                    }
                )
                occupied.append(box)
                placed = True
                break
        if not placed:
            break
    return {
        "accepted": len(accepted) == len(ids),
        "method": method,
        "seed": seed,
        "requested_count": len(ids),
        "generated_count": len(accepted),
        "clutter": accepted,
        "proposals": proposals,
        "attempts": len(proposals),
        "attempt_budget": attempt_budget,
        "wall_seconds": time.monotonic() - start,
        "reason": None if len(accepted) == len(ids) else "candidate_budget_exhausted",
    }
