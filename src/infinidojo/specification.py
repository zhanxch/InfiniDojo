"""Supported v0 scene constraints and event semantics, independent of the LLM."""

from __future__ import annotations

import jsonschema


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


PAIR = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
EVENTS = ["approach", "descend", "grasp", "lift", "hold"]
ATTACHMENTS = ["free", "free", "contact", "held", "held"]
SCHEMA = object_schema(
    {
        "task": {"type": "string", "enum": ["general_pickup"]},
        "instruction": {"type": "string"},
        "target": object_schema(
            {
                "family": {"type": "string", "enum": ["infinigen_can"]},
                "radius_m": {"type": "number"},
                "height_m": {"type": "number"},
                "xy_m": PAIR,
            }
        ),
        "clutter": object_schema(
            {
                "count": {"type": "integer"},
                "x_bounds_m": PAIR,
                "y_bounds_m": PAIR,
                "clearance_m": {"type": "number"},
            }
        ),
        "events": {
            "type": "array",
            "items": object_schema(
                {
                    "name": {"type": "string", "enum": EVENTS},
                    "attachment": {"type": "string", "enum": ["free", "contact", "held"]},
                    "support": {"type": "string", "enum": ["table", "gripper"]},
                }
            ),
        },
        "lift_m": {"type": "number"},
    }
)

CAPABILITY = """Generate JSON procedural constraints for RoboDojo general_pickup.
Units: metres. A closed Infinigen can is lifted vertically by the left X5 arm.
The native goal is a lift strictly exceeding 0.10 m. Use a 0.15 m reference lift.
The instruction field must be a short task instruction for a robot operator;
keep generation constraints and implementation details in their structured fields.
Supported target radius 0.018–0.030 m, height 0.05–0.09 m.
Target XY must be inside x=[-0.35,-0.10], y=[-0.12,0.05].
Clutter is drawn from the same can asset family; count 0–20. XY bounds must be
inside x=[-0.55,0.50], y=[-0.35,0.35]. Bounds constrain complete asset footprints.
Use 0.005 m clearance unless requested otherwise (supported 0–0.03 m).
Exact chronological events: approach/free/table, descend/free/table,
grasp/contact/table, lift/held/gripper, hold/held/gripper.
These express contact constraints, not a simulator weld. Arm joints remain free.
During grasp, finger–target contact is intended; arm–clutter contact is not.
Preserve the requested count, dimensions, target location and bounds on repairs.
Never silently reduce clutter or expand the specified region.
"""


def validate(spec: dict, expected: dict | None = None) -> list[str]:
    errors = [
        f"{list(e.path)}: {e.message}" for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(spec)
    ]
    if errors:
        return errors
    target, clutter = spec["target"], spec["clutter"]
    if not 0.018 <= target["radius_m"] <= 0.03 or not 0.05 <= target["height_m"] <= 0.09:
        errors.append("Unsupported can dimensions")
    if not -0.35 <= target["xy_m"][0] <= -0.10 or not -0.12 <= target["xy_m"][1] <= 0.05:
        errors.append("Target outside supported workspace")
    if not 0 <= clutter["count"] <= 20 or not 0 <= clutter["clearance_m"] <= 0.03:
        errors.append("Unsupported clutter count or clearance")
    for key, bounds in [("x_bounds_m", [-0.55, 0.50]), ("y_bounds_m", [-0.35, 0.35])]:
        lo, hi = clutter[key]
        if not bounds[0] <= lo < hi <= bounds[1]:
            errors.append(f"Invalid support interval {key}")
    if [e["name"] for e in spec["events"]] != EVENTS:
        errors.append("Missing or reordered task events")
    if [e["attachment"] for e in spec["events"]] != ATTACHMENTS:
        errors.append("Incorrect attachment transitions")
    if [e["support"] for e in spec["events"]] != ["table"] * 3 + ["gripper"] * 2:
        errors.append("Incorrect support transitions")
    if not 0.10 < spec["lift_m"] <= 0.18:
        errors.append("Reference lift must exceed native goal and remain in workspace")
    if expected:

        def check(actual, want, path=""):
            for key, value in want.items():
                if isinstance(value, dict):
                    check(actual[key], value, path + key + ".")
                elif actual[key] != value:
                    errors.append(f"Request mismatch: {path}{key} must equal {value!r}")

        check(spec, expected)
    return errors
