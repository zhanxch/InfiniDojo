"""Native RoboDojo reference execution. Run with its Isaac Python runtime.

The controller is privileged. Its trajectory is a generation witness, not a
policy input or an official RoboDojo leaderboard submission.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from itertools import groupby
from pathlib import Path

import numpy as np

from infinidojo.io import git_revision, write_json


def build_layout(root: Path, asset: Path, store: Path, seed: int, xy: list[float]) -> dict:
    """Use native fixtures; reference the generated asset through a local overlay."""
    source = root / "Assets/Eval_Layout/RoboDojo/arx_x5/0/general_pickup_0.json"
    layout = json.loads(source.read_text())
    category = store / "Rigid/infinigen_can"
    category.mkdir(parents=True, exist_ok=True)
    link = category / f"{seed:05d}"
    if not link.exists():
        link.symlink_to(asset.resolve(), target_is_directory=True)
    elif link.resolve() != asset.resolve():
        raise ValueError(f"Asset overlay already points elsewhere: {link}")
    geom = store / "Geometry"
    if not geom.exists():
        geom.symlink_to((root / "Assets/Object/RoboDojo/Geometry").resolve(), target_is_directory=True)
    layout["Rigid"] = {
        "infinigen_can": [
            {
                "category": "infinigen_can",
                "category_idx": seed,
                "label": "target",
                "default_pos": [*xy, 0.767],
                "default_ori": [1, 0, 0, 0],
                "scale": [1, 1, 1],
                "physics": {"type": "rigid", "mass": 0.08, "static_friction": 1.0, "dynamic_friction": 0.8},
                "visual": {},
                "relative_plane": "Table",
            }
        ]
    }
    return layout


def make_config(root: Path, seed: int):
    from omegaconf import OmegaConf
    from utils.pipeline_utils import process_config

    mapping = {"sim": "sim_config", "scene": "default", "robot": "dual_x5", "camera": "camera_config"}
    config = OmegaConf.create({k: OmegaConf.load(root / f"env_cfg/{k}/{v}.yml") for k, v in mapping.items()})
    config.task_env = OmegaConf.load(root / "task/RoboDojo/config/general_pickup.yml")
    config.sim.scene.num_envs = 1
    config.sim.seed = [seed]
    config.camera.default_frequency = 25
    config, _ = process_config(config, "general_pickup")
    return config


class Recorder:
    def __init__(self, env):
        self.env = env
        self.samples = []
        self.controls = []
        self.phase = "initial"
        self.steps = 0

    def sample(self):
        env = self.env
        rm = env.robot_manager
        pos, quat = env.scene_manager.layout_manager.get_instance_pose(0, label="target")
        self.samples.append(
            {
                "step": self.steps,
                "time_s": self.steps * env.dt,
                "phase": self.phase,
                "target_pose": np.r_[pos, quat].tolist(),
                "objects": {
                    name: np.r_[obj.get_world_pose()[0], obj.get_world_pose()[1]].tolist()
                    for name, obj in env.scene_manager.get_objects([0], object_type="rigid").items()
                },
                "robots": [
                    {
                        "body_names": list(key.body_names),
                        "body_poses": key.data.body_link_pose_w[0].cpu().numpy().tolist(),
                        "joint_positions": key.data.joint_pos[0].cpu().numpy().tolist(),
                    }
                    for key in rm.robot_key
                ],
            }
        )

    def execute(self, controls, phase: str):
        if controls is None:
            raise RuntimeError(f"Planner failed during {phase}")
        self.phase = phase
        rm = self.env.robot_manager
        rm.control_manager.push([0], [controls])
        while not rm.control_manager.control_queue[0].is_empty():
            meta = rm.control_manager.pop([0])
            self.controls.append(
                {
                    "phase": phase,
                    "control": {
                        k: {s: np.asarray(v).tolist() for s, v in value.items()}
                        for k, value in meta[0].control_info_dict.items()
                    },
                }
            )
            self.env.step(meta)
            self.env.sim_step(render=False)
            self.steps += 1
            if self.steps % 5 == 0:
                self.sample()

    def hold(self, n: int, phase: str):
        self.execute([{} for _ in range(n)], phase)


def initialize_reward(env):
    env.success = [True]
    env.robot_manager.set_origin_endpose()
    env.robot_manager.set_robot_init_state()
    env.reward_manager.init_state()


def install_reset_cleanup(env):
    """Disable descendant colliders when native CPU reset retires an object.

    The native wrapper disables collision on its root. Exported assets may have
    CollisionAPI on descendant meshes instead. Retired hidden meshes must not
    collide with the next episode. This changes only retired scene instances.
    """
    from pxr import Usd, UsdPhysics

    manager = env.scene_manager
    original = manager.delete_scene_object

    def delete_retired_object(obj):
        path = getattr(obj, "prim_path", None)
        if path:
            prim = env.stage.GetPrimAtPath(path)
            if prim.IsValid():
                for child in Usd.PrimRange(prim, Usd.TraverseInstanceProxies()):
                    if child.HasAPI(UsdPhysics.CollisionAPI):
                        UsdPhysics.CollisionAPI(child).GetCollisionEnabledAttr().Set(False)
        original(obj)

    manager.delete_scene_object = delete_retired_object


def native_reward(env):
    # Execute exactly the native task's registered predicate and reward path.
    env.run_reward()
    env.reward_manager.step()
    return float(env.reward_manager.get_reward()[0])


def run_reference(env, asset: Path, output: Path) -> dict:
    rm = env.robot_manager
    robot = rm.robot_list[0]
    lm = env.scene_manager.layout_manager
    initialize_reward(env)
    initial, _ = lm.get_instance_pose(0, label="target")
    initial = np.array(initial, copy=True)
    rec = Recorder(env)
    rec.sample()
    bounds = json.loads((asset / "provenance.json").read_text())["bounds"]
    height = bounds[1][2] - bounds[0][2]
    quat = [2**-0.5, 0, 2**-0.5, 0]  # local +X gripper approach points down
    center = initial + [0, 0, height / 2]
    flange = center + [0, 0, robot.gripper_bias]
    plans = []

    def move(pos, phase):
        result = rm.planner[robot.robot_name].plan_path(
            rm.get_joint(robot, [0])[0], [*pos, *quat], robot.entity_origin_pose
        )
        plans.append({"phase": phase, "target_flange_pose": [*pos, *quat], "status": result["status"]})
        print(json.dumps(plans[-1]), flush=True)
        rec.execute(rm.plan_ee(0, robot.arm_name, result), phase)
        rec.hold(50, phase)

    status, error = "completed", None
    try:
        move(flange + [0, 0, 0.10], "approach")
        move(flange, "descend")
        rec.execute(rm.plan_endeffector_joint(0, robot.arm_name, target_val=0, control_step_num=200), "grasp")
        rec.hold(150, "grasp")
        move(flange + [0, 0, 0.15], "lift")
        rec.hold(250, "hold")
    except RuntimeError as exc:
        status, error = "controller_failed", str(exc)
    rec.sample()
    final, _ = lm.get_instance_pose(0, label="target")
    native_success = native_reward(env)
    result = {
        "status": status,
        "error": error,
        "native_lift_success": bool(native_success),
        "initial_target_position": initial.tolist(),
        "final_target_position": list(map(float, final)),
        "lift_m": float(final[2] - initial[2]),
        "steps": rec.steps,
        "physics_dt": env.dt,
        "controller": "privileged native CuRobo reference; no object welding or teleport during action",
        "plans": plans,
    }
    write_json(output / "trajectory.json", rec.samples)
    write_json(output / "controls.json", rec.controls)
    write_json(output / "result.json", result)
    return result


def replay_reference(env, reference: Path, output: Path) -> dict:
    initialize_reward(env)
    rec = Recorder(env)
    rec.sample()
    initial = rec.samples[0]["target_pose"][:3]
    layout_records = env.scene_manager.layout_manager.get_layout_records(0, "Rigid")
    requested = {f"env0_rigid_{item['inst_name']}": item for item in layout_records}
    initial_errors = {
        name: float(np.linalg.norm(np.array(pose[:2]) - requested[name]["default_pos"][:2]))
        for name, pose in rec.samples[0]["objects"].items()
    }
    controls = json.loads((reference / "controls.json").read_text())
    for phase, items in groupby(controls, key=lambda item: item["phase"]):
        rec.execute([item["control"] for item in items], phase)
    rec.sample()
    final = rec.samples[-1]["target_pose"][:3]
    shifts = {
        name: float(np.linalg.norm(np.array(pose[:3]) - rec.samples[0]["objects"][name][:3]))
        for name, pose in rec.samples[-1]["objects"].items()
    }
    result = {
        "status": "completed",
        "native_lift_success": bool(native_reward(env)),
        "initial_target_position": initial,
        "final_target_position": final,
        "lift_m": final[2] - initial[2],
        "object_displacement_m": shifts,
        "initial_xy_displacement_m": initial_errors,
        "initial_layout_preserved_1cm": all(value <= 0.01 for value in initial_errors.values()),
        "object_labels": {name: item.get("label") for name, item in requested.items()},
        "steps": rec.steps,
        "physics_dt": env.dt,
        "controller": "frozen native reference controls replayed in physical simulation",
    }
    write_json(output / "trajectory.json", rec.samples)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robodojo-root", type=Path, required=True)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--target-xy", type=float, nargs=2, default=[-0.23, -0.05])
    parser.add_argument("--batch", type=Path, help="JSON list of id/layout/reference/asset cases")
    from isaaclab.app import AppLauncher

    AppLauncher.add_app_launcher_args(parser)
    args = parser.parse_args()
    root, asset, output = (p.resolve() for p in (args.robodojo_root, args.asset, args.output))
    cases = json.loads(args.batch.resolve().read_text()) if args.batch else None
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(root))
    os.chdir(root)
    start = time.monotonic()
    app = AppLauncher(args).app
    env = None
    try:
        import env.scene_manager.layout_manager as layout_module
        from omegaconf import OmegaConf
        from task.RoboDojo.tasks.general_pickup import general_pickup

        store = output / "asset_overlay"
        layout = build_layout(root, asset, store, args.seed, args.target_xy)
        layout_module.OBJECTS_PATH = str(store)
        config = make_config(root, args.seed)
        if cases and all("reference" in case for case in cases):
            for robot in config.robot.robots:
                robot.need_planner = False
        write_json(output / "config.json", OmegaConf.to_container(config, resolve=True))
        write_json(output / "layout.json", layout)
        env = general_pickup(config, app)
        install_reset_cleanup(env)
        if cases is None:
            cases = [{"id": ".", "seed": args.seed, "layout_data": layout, "asset": str(asset)}]
        for case in cases:
            case_output = output / case["id"]
            case_output.mkdir(parents=True, exist_ok=True)
            case_start = time.monotonic()
            case_layout = case.get("layout_data") or json.loads(Path(case["layout"]).read_text())
            # All case assets are explicit mappings: native loading resolves
            # category indices under this isolated overlay.
            for asset_id, folder in case.get("asset_map", {}).items():
                build_layout(root, Path(folder), store, int(asset_id), args.target_xy)
            env.scene_manager.layout_manager.set_saved_layout(0, case_layout)
            env.reset(seed=[case["seed"]])
            # Native reset sets PD targets but may leave motion from the last
            # episode. Restore actual joint state while all objects are offscreen.
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
            if "reference" in case:
                result = replay_reference(env, Path(case["reference"]), case_output)
            else:
                result = run_reference(env, Path(case["asset"]), case_output)
            result.update(robodojo_commit=git_revision(root), wall_seconds=time.monotonic() - case_start)
            write_json(case_output / "result.json", result)
            print(json.dumps({"case": case["id"], **result}), flush=True)
        write_json(output / "batch.json", {"completed": len(cases), "wall_seconds": time.monotonic() - start})
    except BaseException as exc:
        write_json(output / "error.json", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        if env is not None:
            env.close()
        app.close()


if __name__ == "__main__":
    main()
