"""Complete native cover/uncover reference with six physical cup transfers.

Uses unchanged native task predicates, cup assets and existing arm planners.
Cup/block containment does not imply support or permission for wall penetration.
"""

from __future__ import annotations

import json
from itertools import groupby
from pathlib import Path

import numpy as np

from infinidojo.geometry import rotation
from infinidojo.interaction import build_cover_program, validate_event_trace
from infinidojo.io import sha256, write_json
from infinidojo.native import initialize_reward
from infinidojo.native_stack import EvidenceRecorder

COLORS = ("red", "green", "blue")
CUPS = ("cup0", "cup1", "cup2")


class CoverRecorder(EvidenceRecorder):
    def sample(self):
        super().sample()
        self.samples[-1]["native_remaining_stages"] = len(self.env.reward_manager.check_list[0])


def run_reference(env, output, max_steps, reference=None, video=True, planning_root=None):
    if planning_root is not None:
        raise ValueError("Cover reference needs cavity-aware planning before scene-aware mode can be enabled")
    rm = env.robot_manager
    initialize_reward(env)
    env.run_reward()
    env.get_score()
    arm_indices = {"cup0": 0, "cup1": 1, "cup2": 1}
    lm = env.scene_manager.layout_manager
    write_json(
        output / "cup-collision-assets.json",
        {
            item["label"]: {
                "sha256": sha256(Path(item["usd_path"])),
                "scale": item["scale"],
                "category": item["category"],
                "category_idx": item["category_idx"],
            }
            for item in lm.get_layout_records(0, "Rigid")
            if item["label"] in CUPS
        },
    )
    block_order = sorted(COLORS, key=lambda label: lm.get_instance_pose(0, label=label)[0][0])
    program = build_cover_program(
        block_order, {cup: rm.robot_list[i].arm_name for cup, i in arm_indices.items()}
    )
    if reference is not None and json.loads(
        (reference / "interaction-program.json").read_text()
    ) != json.loads(json.dumps(program)):
        raise ValueError("Reference cover order or arm assignments differ from this reset")
    write_json(output / "interaction-program.json", program)
    rec = CoverRecorder(env, output, max_steps, video=video)
    rec.sample()
    initial_joints = [np.array(rm.get_joint(robot, [0])[0], copy=True) for robot in rm.robot_list]
    initial_cups = {label: rec.object_pose(label).copy() for label in CUPS}
    objects = json.loads((output / "scene-objects.json").read_text())
    quat = [2**-0.5, 0, 2**-0.5, 0]
    plans, transfers = [], []

    def move(robot, position, phase, event_id):
        result = rm.planner[robot.robot_name].plan_path(
            rm.get_joint(robot, [0])[0], [*position, *quat], robot.entity_origin_pose
        )
        plans.append(
            {
                "arm": robot.arm_name,
                "event_id": event_id,
                "phase": phase,
                "target_flange_pose": [*position, *quat],
                "status": result["status"],
            }
        )
        print(json.dumps(plans[-1]), flush=True)
        rec.execute(rm.plan_ee(0, robot.arm_name, result), phase, event_id)
        rec.hold(25, phase, event_id)

    def gripper(robot, value, phase, event_id):
        rec.execute(
            rm.plan_endeffector_joint(0, robot.arm_name, target_val=value, control_step_num=200),
            phase,
            event_id,
        )
        rec.hold(100, phase, event_id)

    def park(index, phase):
        robot = rm.robot_list[index]
        result = rm.planner[robot.robot_name].plan_joint(rm.get_joint(robot, [0])[0], initial_joints[index])
        plans.append({"arm": robot.arm_name, "phase": phase, "status": result["status"]})
        rec.execute(rm.plan_ee(0, robot.arm_name, result), phase)
        rec.hold(50, phase)

    status, error = "completed", None
    try:
        rec.capture()
        if reference is not None:
            commands = json.loads((reference / "controls.json").read_text())
            for (phase, event_id), items in groupby(
                commands, key=lambda row: (row["phase"], row.get("event_id"))
            ):
                rec.execute([item["control"] for item in items], phase, event_id)
        else:
            for index, transfer in enumerate(program["transfers"]):
                cup, block = transfer["object"], transfer["block"]
                robot_index = arm_indices[cup]
                robot = rm.robot_list[robot_index]

                def eid(phase, transfer_index=index):
                    return f"{transfer_index:02d}/{phase}"

                pose = rec.object_pose(cup)
                center = pose[:3] + rotation(pose) @ np.asarray(objects[cup]["bounds"]).mean(0)
                # Inverted cups taper toward their closed top. Grip above the
                # mid-height, retaining clearance between the palm and top.
                flange = center + [0, 0, robot.gripper_bias + 0.012]
                move(robot, flange + [0, 0, 0.10], "approach", eid("approach"))
                move(robot, flange, "descend", eid("descend"))
                gripper(robot, 0, "grasp", eid("grasp"))
                move(robot, flange + [0, 0, 0.08], "lift", eid("lift"))
                lifted = rec.object_pose(cup)[:3]
                if lifted[2] - pose[2] < 0.04:
                    raise RuntimeError(f"{cup}: physical grasp/lift failed")
                offset = rm.get_real_endpose(robot, [0], is_relative=False)[0][:3] - lifted
                goal = initial_cups[cup][:3].copy()
                if transfer["operation"] == "cover":
                    goal[:2] = rec.object_pose(block)[:2]
                place_flange = goal + offset
                move(robot, place_flange + [0, 0, 0.08], "transport", eid("transport"))
                move(robot, place_flange + [0, 0, 0.001], "place", eid("place"))
                gripper(robot, 1, "release", eid("release"))
                move(robot, place_flange + [0, 0, 0.10], "retreat", eid("retreat"))
                rec.hold(150, "retreat", eid("retreat"))
                park(robot_index, "park")
                transfers.append(
                    {
                        "index": index,
                        "operation": transfer["operation"],
                        "cup": cup,
                        "block": block,
                        "cup_after_release": rec.object_pose(cup).tolist(),
                        "native_remaining_stages": len(env.reward_manager.check_list[0]),
                    }
                )
                print(json.dumps(transfers[-1]), flush=True)
            for index in range(len(rm.robot_list)):
                park(index, "return")
            rec.hold(500, "settle")
    except RuntimeError as exc:
        status, error = "controller_failed", str(exc)
    finally:
        rec.sample()
        rec.close()
    reward = env.reward_manager.get_reward()
    return {
        "status": status,
        "error": error,
        "native_task_success": bool(reward[0]),
        "native_score": env.reward_manager.get_score(reward)[0],
        "native_remaining_stages": len(env.reward_manager.check_list[0]),
        "event_trace": validate_event_trace(program, rec.samples),
        "initial_object_poses": rec.samples[0]["objects"],
        "final_object_poses": rec.samples[-1]["objects"],
        "max_block_displacement_m": {
            label: max(
                float(
                    np.linalg.norm(np.array(f["objects"][label][:3]) - rec.samples[0]["objects"][label][:3])
                )
                for f in rec.samples
            )
            for label in COLORS
        },
        "contact_reporting_errors": rec.contact_errors,
        "steps": rec.steps,
        "physics_dt": env.dt,
        "video_frames": rec.video_frames,
        "plans": plans,
        "transfers": transfers,
        "execution_mode": "fixed_reference_replay" if reference is not None else "online_reference_planning",
        "planning_collision_world": "native_table_only_or_replay",
        "strict_instance_validation": "not evaluated by executor; needs independent contact and cup-cavity audit",
        "controller": "privileged existing CuRobo; real six cup transfers, no object welding or teleport",
        "budget_note": "Reference physics steps differ from native 800-action policy budget",
    }


if __name__ == "__main__":
    from infinidojo.native_stack import main

    main(default_task="cover_blocks")
