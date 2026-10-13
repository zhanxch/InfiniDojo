"""Complete native pickup reference, with physical contact and hold evidence.

The right arm uses the existing planner. This is a privileged generation
witness, not a policy; no task object is welded or teleported during execution.
"""

from __future__ import annotations

import json
from itertools import groupby

import numpy as np
from scipy.spatial.transform import Rotation

from infinidojo.geometry import rotation
from infinidojo.interaction import build_pickup_program, validate_event_trace
from infinidojo.io import write_json
from infinidojo.native import initialize_reward
from infinidojo.native_stack import EvidenceRecorder


def run_reference(env, output, max_steps, reference=None, video=True, planning_root=None):
    rm = env.robot_manager
    robot = rm.robot_list[1]
    initialize_reward(env)
    env.run_reward()
    program = build_pickup_program("target", "Table", robot.arm_name)
    if reference is not None:
        recorded = json.loads((reference / "interaction-program.json").read_text())
        if recorded != json.loads(json.dumps(program)):
            raise ValueError("Reference does not use the pickup event contract")
    write_json(output / "interaction-program.json", program)
    rec = EvidenceRecorder(env, output, max_steps, video=video)
    rec.sample()
    initial = rec.object_pose("target").copy()
    objects = json.loads((output / "scene-objects.json").read_text())
    center = initial[:3] + rotation(initial) @ np.asarray(objects["target"]["bounds"]).mean(0)
    # Upright native cans extend above a midpoint pinch. A downward approach
    # puts their upper half into the palm/link6. A front/down 45-degree approach
    # keeps the palm behind the object without a low pregrasp near the base.
    orientation = Rotation.from_euler("ZY", [90, 45], degrees=True)
    direction = orientation.as_matrix()[:, 0]
    flange = center - direction * robot.gripper_bias
    quat = orientation.as_quat()[[3, 0, 1, 2]].tolist()
    planner, plans = None, []

    def move(position, phase):
        event_id = f"00/{phase}"
        if planner is not None:
            planner.update(next(e for e in program["events"] if e["id"] == event_id))
            result = planner.plan(target=[*position, *quat])
        else:
            result = rm.planner[robot.robot_name].plan_path(
                rm.get_joint(robot, [0])[0], [*position, *quat], robot.entity_origin_pose
            )
        plans.append(
            {"event_id": event_id, "target_flange_pose": [*position, *quat], "status": result["status"]}
        )
        print(json.dumps(plans[-1]), flush=True)
        rec.execute(rm.plan_ee(0, robot.arm_name, result), phase, event_id)
        rec.hold(50, phase, event_id)

    status, error = "completed", None
    try:
        if planning_root is not None:
            from infinidojo.planning import ScenePlanningContext

            planner = ScenePlanningContext(env, rec, planning_root, output)
        rec.capture()
        if reference is not None:
            commands = json.loads((reference / "controls.json").read_text())
            for (phase, event_id), items in groupby(
                commands, key=lambda row: (row["phase"], row.get("event_id"))
            ):
                rec.execute([item["control"] for item in items], phase, event_id)
        else:
            rec.hold(250, "initial")
            move(flange - direction * 0.10, "approach")
            move(flange, "descend")
            rec.execute(
                rm.plan_endeffector_joint(0, robot.arm_name, target_val=0, control_step_num=200),
                "grasp",
                "00/grasp",
            )
            rec.hold(150, "grasp", "00/grasp")
            move(flange + [0, 0, 0.15], "lift")
            rec.hold(250, "hold", "00/hold")
    except RuntimeError as exc:
        status, error = "controller_failed", str(exc)
    finally:
        rec.sample()
        rec.close()
    reward = env.reward_manager.get_reward()
    holding = [f for f in rec.samples if f["event_id"] == "00/hold"]
    return {
        "status": status,
        "error": error,
        "native_task_success": bool(reward[0]),
        # Pickup declares no intermediate process scores. The official policy
        # evaluator maps terminal success to its score; this reference is not
        # that evaluator, so do not label its raw zero as a native policy score.
        "native_process_score": None,
        "reward_manager_raw_score": env.reward_manager.get_score(reward)[0],
        "event_trace": validate_event_trace(program, rec.samples),
        "initial_object_poses": rec.samples[0]["objects"],
        "final_object_poses": rec.samples[-1]["objects"],
        "final_origin_lift_m": float(rec.samples[-1]["objects"]["target"][2] - initial[2]),
        "minimum_hold_origin_lift_m": min(
            (f["objects"]["target"][2] - initial[2] for f in holding), default=None
        ),
        "hold_duration_s": holding[-1]["time_s"] - holding[0]["time_s"] if holding else 0,
        "contact_reporting_errors": rec.contact_errors,
        "steps": rec.steps,
        "physics_dt": env.dt,
        "video_frames": rec.video_frames,
        "plans": plans,
        "grasp_profile": "upright_can_front_down_45_pinch; no arbitrary-object grasp synthesis claim",
        "execution_mode": "fixed_reference_replay" if reference is not None else "online_reference_planning",
        "planning_collision_world": "observed_scene"
        if planner is not None
        else "native_table_only_or_replay",
        "strict_instance_validation": "not evaluated by executor; requires independent pickup/hold validation",
        "controller": "privileged existing CuRobo; physical grasp and sustained hold, no weld or teleport",
        "budget_note": "Raw physics steps differ from the native 200-action policy budget",
    }


if __name__ == "__main__":
    from infinidojo.native_stack import main

    main(default_task="general_pickup")
