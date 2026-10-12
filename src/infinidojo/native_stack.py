"""Full native stack_blocks reference with event, state and contact evidence.

Run using RoboDojo's Isaac Python. This privileged development controller is
neither a tested policy nor a complete instance validator. It uses unchanged
native assets, task predicates and physical grasp/release actions.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from itertools import groupby
from pathlib import Path

import numpy as np

from infinidojo.interaction import build_program, validate_event_trace
from infinidojo.io import git_revision, sha256, write_json
from infinidojo.native import initialize_reward, install_reset_cleanup

LABELS = ("block_0", "block_1", "block_2")


def make_config(root, seed):
    from omegaconf import OmegaConf
    from utils.pipeline_utils import process_config

    mapping = {"sim": "sim_config", "scene": "default", "robot": "dual_x5", "camera": "camera_config"}
    config = OmegaConf.create({k: OmegaConf.load(root / f"env_cfg/{k}/{v}.yml") for k, v in mapping.items()})
    config.task_env = OmegaConf.load(root / "task/RoboDojo/config/stack_blocks.yml")
    config.sim.scene.num_envs = 1
    config.sim.seed = [seed]
    config.camera.default_frequency = 25
    return process_config(config, "stack_blocks")[0]


class EvidenceRecorder:
    def __init__(self, env, output, max_steps, video=True):
        self.env, self.output, self.max_steps = env, output, max_steps
        self.video = video
        self.steps, self.event_id, self.phase = 0, None, "initial"
        self.samples, self.controls, self.boundaries = [], [], []
        self.contact_counts, self.contact_errors = {}, []
        self.contact_file = (output / "contacts.jsonl").open("w")
        self.writer = None
        self.video_frames = 0
        self.subscription = None
        lm = env.scene_manager.layout_manager
        self.records = lm.get_layout_records(0, "Rigid")
        self.labels = [item["label"] for item in self.records]
        objects = {}
        for item in self.records:
            vertices = (
                np.asarray(
                    lm.get_instance_metadata(0, label=item["label"])["geometry"]["aligned_bbox"]["vertices"]
                )
                * item["scale"]
            )
            objects[item["label"]] = {
                "prim_path": item["prim_path"],
                "category": item["category"],
                "category_idx": item["category_idx"],
                "requested_pose": [*item["default_pos"], *item["default_ori"]],
                "bounds": [vertices.min(0).tolist(), vertices.max(0).tolist()],
            }
        write_json(output / "scene-objects.json", objects)
        write_json(
            output / "robot-identities.json",
            {
                f"robot{i}": {"arm": robot.arm_name, "gripper_bodies": ["link7", "link8"]}
                for i, robot in enumerate(env.robot_manager.robot_list)
            },
        )
        self.start_contacts()

    def start_contacts(self):
        import carb.settings
        from omni.physx import get_physx_simulation_interface
        from pxr import PhysxSchema, Usd, UsdPhysics

        # IsaacLab disables report processing by default when no sensor exists.
        carb.settings.get_settings().set_bool("/physics/disableContactProcessing", False)
        self.report_bodies = []
        for prim in Usd.PrimRange(self.env.stage.GetPseudoRoot(), Usd.TraverseInstanceProxies()):
            if prim.HasAPI(UsdPhysics.RigidBodyAPI) and not prim.IsInstanceProxy():
                api = PhysxSchema.PhysxContactReportAPI.Apply(prim)
                api.CreateThresholdAttr().Set(0)
                self.report_bodies.append(str(prim.GetPath()))
        self.subscription = get_physx_simulation_interface().subscribe_contact_report_events(self.on_contacts)

    def on_contacts(self, headers, data):
        from pxr import PhysicsSchemaTools

        try:
            for header in headers:
                actors = [
                    str(PhysicsSchemaTools.intToSdfPath(getattr(header, f"actor{i}"))) for i in range(2)
                ]
                colliders = [
                    str(PhysicsSchemaTools.intToSdfPath(getattr(header, f"collider{i}"))) for i in range(2)
                ]
                points = []
                for i in range(
                    header.contact_data_offset, header.contact_data_offset + header.num_contact_data
                ):
                    point = data[i]
                    points.append(
                        {
                            "position": list(point.position),
                            "normal": list(point.normal),
                            "impulse": list(point.impulse),
                            "separation": float(point.separation),
                        }
                    )
                record = {
                    "step": self.steps,
                    "event_id": self.event_id,
                    "phase": self.phase,
                    "type": str(header.type),
                    "actors": actors,
                    "colliders": colliders,
                    "points": points,
                }
                self.contact_file.write(json.dumps(record, allow_nan=False) + "\n")
                if not points:
                    continue
                pair = " | ".join(sorted(actors))
                key = f"{self.event_id or self.phase}: {pair}"
                summary = self.contact_counts.setdefault(
                    key,
                    {
                        "phase": self.phase,
                        "event_id": self.event_id,
                        "actors": sorted(actors),
                        "reports": 0,
                        "first_step": self.steps,
                        "last_step": self.steps,
                        "min_separation_m": 0.0,
                    },
                )
                summary["reports"] += 1
                summary["last_step"] = self.steps
                summary["min_separation_m"] = min(
                    summary["min_separation_m"], *(p["separation"] for p in points)
                )
        except Exception as exc:  # noqa: BLE001 -- callback errors must survive simulator dispatch
            # PhysX callback exceptions otherwise disappear into the simulator log.
            self.contact_errors.append(f"{type(exc).__name__}: {exc}")

    def object_pose(self, label):
        pos, quat = self.env.scene_manager.layout_manager.get_instance_pose(0, label=label)
        return np.r_[pos, quat]

    def sample(self):
        self.samples.append(
            {
                "step": self.steps,
                "time_s": self.steps * self.env.dt,
                "event_id": self.event_id,
                "phase": self.phase,
                "objects": {label: self.object_pose(label).tolist() for label in self.labels},
                "robots": [
                    {
                        "body_names": list(key.body_names),
                        "body_poses": key.data.body_link_pose_w[0].cpu().numpy().tolist(),
                        "joint_positions": key.data.joint_pos[0].cpu().numpy().tolist(),
                    }
                    for key in self.env.robot_manager.robot_key
                ],
            }
        )

    def capture(self):
        if not self.video:
            return
        import cv2
        from env.camera_manager.capture.render_sync import wait_for_latest_cameras

        wait_for_latest_cameras(self.env, capture_manager=self.env.capture_manager)
        data = self.env.capture_manager.step(env_ids=[0], cam_ids=[0])
        frame = np.asarray(data[0]["rgb"][0]["data"])[..., :3]
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        if self.writer is None:
            self.writer = cv2.VideoWriter(
                str(self.output / "reference.mp4"),
                cv2.VideoWriter_fourcc(*"mp4v"),
                25,
                (frame.shape[1], frame.shape[0]),
            )
            if not self.writer.isOpened():
                raise RuntimeError("Could not open reference video writer")
            cv2.imwrite(str(self.output / "initial.png"), frame)
        self.writer.write(frame)
        self.video_frames += 1
        cv2.imwrite(str(self.output / "latest.png"), frame)

    def execute(self, controls, phase, event_id=None):
        if controls is None:
            raise RuntimeError(f"Planner failed during {phase}")
        if self.phase != phase or self.event_id != event_id:
            self.phase, self.event_id = phase, event_id
            self.boundaries.append({"step": self.steps, "phase": phase, "event_id": event_id})
            self.sample()
        rm = self.env.robot_manager
        rm.control_manager.push([0], [controls])
        while not rm.control_manager.control_queue[0].is_empty():
            if self.steps >= self.max_steps:
                raise RuntimeError("Reference physics step budget exhausted")
            meta = rm.control_manager.pop([0])
            self.controls.append(
                {
                    "step": self.steps + 1,
                    "phase": phase,
                    "event_id": event_id,
                    "control": {
                        k: {s: np.asarray(v).tolist() for s, v in value.items()}
                        for k, value in meta[0].control_info_dict.items()
                    },
                }
            )
            self.steps += 1
            self.env.step(meta)
            self.env.sim_step(render=False)
            self.env.reward_manager.step()
            if self.steps % 5 == 0:
                self.sample()
            if self.steps % 10 == 0:
                self.capture()
        self.sample()

    def hold(self, n, phase, event_id=None):
        self.execute([{} for _ in range(n)], phase, event_id)

    def close(self):
        self.subscription = None
        self.contact_file.close()
        if self.writer is not None:
            self.writer.release()
        write_json(self.output / "trajectory.json", self.samples)
        write_json(self.output / "controls.json", self.controls)
        write_json(self.output / "event-boundaries.json", self.boundaries)
        write_json(
            self.output / "contact-summary.json",
            {
                "report_bodies": self.report_bodies,
                "errors": self.contact_errors,
                "pairs_by_phase": list(self.contact_counts.values()),
                "note": "Sleeping objects need not emit persistent reports. This is evidence, not a full validator.",
            },
        )


def run_reference(env, output, max_steps, reference=None, video=True, planning_root=None):
    rm = env.robot_manager
    robot = rm.robot_list[1]  # native layout 0 places its three blocks on the right
    initialize_reward(env)
    env.run_reward()
    env.get_score()
    initial_joints = np.array(rm.get_joint(robot, [0])[0], copy=True)
    program = build_program(
        [
            {"object": "block_1", "source": "Table", "destination": "block_0", "arm": robot.arm_name},
            {"object": "block_2", "source": "Table", "destination": "block_1", "arm": robot.arm_name},
        ],
        {label: "Table" for label in LABELS},
    )
    if reference is not None:
        program = json.loads((reference / "interaction-program.json").read_text())
    write_json(output / "interaction-program.json", program)
    rec = EvidenceRecorder(env, output, max_steps, video=video)
    rec.sample()
    scene_planning = None
    plans, transfers = [], []
    quat = [2**-0.5, 0, 2**-0.5, 0]
    heights = {
        label: env.scene_manager.layout_manager.get_instance_metadata(0, label=label)["geometry"][
            "aligned_bbox"
        ]["extents"][2]
        for label in LABELS
    }

    def move(pos, phase, event_id):
        if scene_planning:
            event = next(e for e in program["events"] if e["id"] == event_id)
            scene_planning.update(event)
            result = scene_planning.plan(target=[*pos, *quat])
        else:
            result = rm.planner[robot.robot_name].plan_path(
                rm.get_joint(robot, [0])[0], [*pos, *quat], robot.entity_origin_pose
            )
        plans.append(
            {
                "event_id": event_id,
                "phase": phase,
                "target_flange_pose": [*pos, *quat],
                "status": result["status"],
            }
        )
        print(json.dumps(plans[-1]), flush=True)
        rec.execute(rm.plan_ee(0, robot.arm_name, result), phase, event_id)
        rec.hold(50, phase, event_id)

    def gripper(value, phase, event_id):
        rec.execute(
            rm.plan_endeffector_joint(0, robot.arm_name, target_val=value, control_step_num=200),
            phase,
            event_id,
        )
        rec.hold(100, phase, event_id)

    status, error = "completed", None
    try:
        if planning_root is not None:
            from infinidojo.planning import ScenePlanningContext

            scene_planning = ScenePlanningContext(env, rec, planning_root, output)
        rec.capture()
        if reference is not None:
            commands = json.loads((reference / "controls.json").read_text())
            for (phase, event_id), items in groupby(
                commands, key=lambda row: (row["phase"], row.get("event_id"))
            ):
                rec.execute([item["control"] for item in items], phase, event_id)
        for index, (label, destination) in (
            enumerate(zip(LABELS[1:], LABELS[:-1])) if reference is None else []
        ):

            def eid(phase, event_index=index):
                return f"{event_index:02d}/{phase}"

            start = rec.object_pose(label)[:3].copy()
            flange = start + [0, 0, robot.gripper_bias]
            move(flange + [0, 0, 0.10], "approach", eid("approach"))
            move(flange, "descend", eid("descend"))
            gripper(0, "grasp", eid("grasp"))
            move(flange + [0, 0, 0.15], "lift", eid("lift"))
            lifted = rec.object_pose(label)[:3]
            if lifted[2] - start[2] < 0.08:
                raise RuntimeError(f"{label}: physical grasp/lift failed")
            # Measured offset is used only by this privileged reference controller.
            offset = rm.get_real_endpose(robot, [0], is_relative=False)[0][:3] - lifted
            goal = rec.object_pose(destination)[:3] + [0, 0, (heights[label] + heights[destination]) / 2]
            place_flange = goal + offset
            move(place_flange + [0, 0, 0.10], "transport", eid("transport"))
            move(place_flange + [0, 0, 0.001], "place", eid("place"))
            gripper(1, "release", eid("release"))
            move(place_flange + [0, 0, 0.10], "retreat", eid("retreat"))
            rec.hold(250, "retreat", eid("retreat"))
            transfers.append(
                {
                    "object": label,
                    "destination": destination,
                    "initial_position": start.tolist(),
                    "after_release_pose": rec.object_pose(label).tolist(),
                    "destination_pose": rec.object_pose(destination).tolist(),
                }
            )
            print(json.dumps(transfers[-1]), flush=True)
        if reference is None:
            if scene_planning:
                scene_planning.update()
                result = scene_planning.plan(initial_joints=initial_joints)
            else:
                result = rm.planner[robot.robot_name].plan_joint(rm.get_joint(robot, [0])[0], initial_joints)
            plans.append({"phase": "return", "status": result["status"]})
            rec.execute(rm.plan_ee(0, robot.arm_name, result), "return")
            rec.hold(500, "settle")
    except RuntimeError as exc:
        status, error = "controller_failed", str(exc)
    finally:
        rec.sample()
        rec.close()
    reward = env.reward_manager.get_reward()
    settle = [s for s in rec.samples if s["phase"] == "settle"]
    displacement = {
        label: max(
            (
                float(
                    np.linalg.norm(
                        np.array(s["objects"][label][:3]) - np.array(settle[0]["objects"][label][:3])
                    )
                )
                for s in settle
            ),
            default=None,
        )
        for label in rec.labels
    }
    return {
        "status": status,
        "error": error,
        "native_task_success": bool(reward[0]),
        "native_score": env.reward_manager.get_score(reward)[0],
        "event_trace": validate_event_trace(program, rec.samples),
        "initial_object_poses": rec.samples[0]["objects"],
        "final_object_poses": rec.samples[-1]["objects"],
        "final_settle_displacement_m": displacement,
        "contact_reporting_errors": rec.contact_errors,
        "video_frames": rec.video_frames,
        "steps": rec.steps,
        "physics_dt": env.dt,
        "plans": plans,
        "transfers": transfers,
        "execution_mode": "fixed_reference_replay" if reference is not None else "online_reference_planning",
        "planning_collision_world": "observed_scene" if scene_planning else "native_table_only_or_replay",
        "strict_instance_validation": "not evaluated by executor; see separate independent validation report",
        "controller": "privileged CuRobo reference, physical grasp/release, no object welding or teleport",
        "budget_note": "Raw physics steps are not native policy actions; no official leaderboard claim",
    }


def install_asset_overlay(root, output, asset_map):
    """Resolve generated assets without modifying the upstream asset tree."""
    import env.scene_manager.layout_manager as layout_module

    native = root / "Assets/Object/RoboDojo"
    overlay = output / "asset_overlay"
    overlay.mkdir()
    for path in native.iterdir():
        if path.name != "Rigid":
            (overlay / path.name).symlink_to(path.resolve(), target_is_directory=path.is_dir())
    rigid = overlay / "Rigid"
    rigid.mkdir()
    for path in (native / "Rigid").iterdir():
        (rigid / path.name).symlink_to(path.resolve(), target_is_directory=path.is_dir())
    category = rigid / "infinigen_can"
    category.mkdir()
    provenance = {}
    for asset_id, path in asset_map.items():
        folder = Path(path).resolve()
        record = json.loads((folder / "provenance.json").read_text())
        for name, digest in record["files"].items():
            if sha256(folder / name) != digest:
                raise ValueError(f"Generated asset content changed: {asset_id}/{name}")
        (category / f"{int(asset_id):05d}").symlink_to(folder, target_is_directory=True)
        provenance[asset_id] = record
    write_json(output / "asset-provenance.json", provenance)
    layout_module.OBJECTS_PATH = str(overlay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robodojo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--layout", type=Path, help="Native stack layout; default is saved layout 0")
    parser.add_argument("--episode", type=Path, help="Portable episode bundle to load")
    parser.add_argument("--max-steps", type=int, default=20000)
    parser.add_argument("--asset-map", type=Path, help="JSON mapping generated can IDs to asset folders")
    parser.add_argument("--reference", type=Path, help="Replay fixed controls from this reference directory")
    parser.add_argument("--no-video", action="store_true", help="Keep physical evidence without RGB encoding")
    parser.add_argument(
        "--scene-aware", action="store_true", help="Development scene-aware reference planner"
    )
    from isaaclab.app import AppLauncher

    AppLauncher.add_app_launcher_args(parser)
    args = parser.parse_args()
    root, output = args.robodojo_root.resolve(), args.output.resolve()
    source = Path(__file__).resolve().parents[2]
    asset_map = None
    if args.episode:
        from infinidojo.episode import load_episode

        if args.layout or args.asset_map or args.seed is not None:
            parser.error("--episode provides its own layout, asset map and seed")
        manifest, args.layout, asset_map = load_episode(args.episode)
        if manifest["robodojo_commit"] != git_revision(root):
            raise ValueError("Episode requires a different RoboDojo revision")
        args.seed = manifest["seed"]
    elif args.asset_map:
        map_path = args.asset_map.resolve()
        asset_map = {
            key: str((map_path.parent / value).resolve())
            for key, value in json.loads(map_path.read_text()).items()
        }
    args.seed = 1200 if args.seed is None else args.seed
    layout_path = (
        args.layout.resolve()
        if args.layout
        else root / "Assets/Eval_Layout/RoboDojo/arx_x5/0/stack_blocks_0.json"
    )
    layout = json.loads(layout_path.read_text())
    reference = args.reference.resolve() if args.reference else None
    if reference is not None and args.scene_aware:
        parser.error("--scene-aware plans online and cannot be combined with --reference replay")
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "source/infinidojo"
    snapshot.mkdir(parents=True)
    for path in (source / "src/infinidojo").glob("*.py"):
        shutil.copyfile(path, snapshot / path.name)
    write_json(
        output / "provenance.json",
        {
            "infinidojo_commit": git_revision(source),
            "robodojo_commit": git_revision(root),
            "robot_usd_sha256": sha256(root / "Assets/Robots/x5/ARX.usd"),
            "source_sha256": {
                str(p.relative_to(source)): sha256(p) for p in (source / "src/infinidojo").glob("*.py")
            },
            "task": "stack_blocks",
            "robot": "arx_x5",
            "seed": args.seed,
            "native_layout_sha256": sha256(layout_path),
            "max_reference_physics_steps": args.max_steps,
            "reference_controls_sha256": sha256(reference / "controls.json") if reference else None,
            "scene_aware_planning": args.scene_aware,
        },
    )
    write_json(output / "layout.json", layout)
    sys.path.insert(0, str(root))
    os.chdir(root)
    start = time.monotonic()
    app = AppLauncher(args).app
    env = None
    try:
        from omegaconf import OmegaConf
        from task.RoboDojo.tasks.stack_blocks import stack_blocks

        config = make_config(root, args.seed)
        if reference is not None or args.scene_aware:
            for robot_config in config.robot.robots:
                robot_config.need_planner = False
        if asset_map is not None:
            install_asset_overlay(root, output, asset_map)
        write_json(output / "config.json", OmegaConf.to_container(config, resolve=True))
        env = stack_blocks(config, app)
        install_reset_cleanup(env)
        env.scene_manager.layout_manager.set_saved_layout(0, layout)
        env.reset(seed=[args.seed])
        for key in env.robot_manager.robot_key:
            key.write_joint_state_to_sim(
                key.data.default_joint_pos.clone(), key.data.default_joint_vel.clone()
            )
            key.set_joint_position_target(key.data.default_joint_pos.clone())
            key.set_joint_velocity_target(key.data.default_joint_vel.clone())
        for _ in range(50):
            env.sim_step(render=False)
        env.scene_manager.apply_saved_poses([0])
        for _ in range(200):
            env.sim_step(render=False)
        write_json(
            output / "loaded-layout.json", env.scene_manager.layout_manager.get_layout_records(0, "Rigid")
        )
        result = run_reference(
            env,
            output,
            args.max_steps,
            reference,
            video=not args.no_video,
            planning_root=root if args.scene_aware else None,
        )
        result["wall_seconds"] = time.monotonic() - start
        write_json(output / "result.json", result)
        print(json.dumps(result), flush=True)
    except BaseException as exc:
        write_json(output / "error.json", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        if env is not None:
            env.close()
        app.close()


if __name__ == "__main__":
    main()
