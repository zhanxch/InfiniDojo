"""Scene-aware reference planning with explicit, approximate collision geometry.

Only the planning model attaches objects. Physics continues to grasp and release
normally. Cuboids and covering spheres are proposal constraints, not a substitute
for the independent execution validator. No upstream dependency is modified.
"""

from __future__ import annotations

import copy
import json
import time

import numpy as np
from scipy.spatial.transform import Rotation

from infinidojo.geometry import extract_robot_bounds, rotation
from infinidojo.io import git_revision, sha256, write_json

PROXY_SLOTS = {"gripper_proxy": 512, "attached_object": 125}


def relative_pose(pose, base):
    """World pose expressed in a base frame; quaternions are wxyz."""
    rot = rotation(base).T
    quat = Rotation.from_matrix(rot @ rotation(pose)).as_quat()[[3, 0, 1, 2]]
    return np.r_[rot @ (np.asarray(pose[:3]) - base[:3]), quat]


def box_obstacle(name, bounds, pose, base):
    bounds = np.asarray(bounds, dtype=float)
    center = np.asarray(pose[:3]) + rotation(pose) @ bounds.mean(0)
    return {
        "name": name,
        "dims": (bounds[1] - bounds[0]).tolist(),
        "pose": relative_pose(np.r_[center, pose[3:]], base).tolist(),
    }


def covering_spheres(bounds, max_cell=0.01):
    """Circumscribe each grid cell; the union covers the entire bounding box."""
    bounds = np.asarray(bounds, dtype=float)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        raise ValueError("Expected positive finite box bounds")
    dims = bounds[1] - bounds[0]
    if np.any(dims <= 0) or max_cell <= 0:
        raise ValueError("Expected positive finite box bounds")
    counts = np.maximum(1, np.ceil(dims / max_cell).astype(int))
    cell = dims / counts
    grid = np.stack(np.meshgrid(*(np.arange(n) for n in counts), indexing="ij"), axis=-1)
    centers = bounds[0] + (grid.reshape(-1, 3) + 0.5) * cell
    return np.c_[centers, np.full(len(centers), np.linalg.norm(cell) / 2)]


def transform_spheres(spheres, pose):
    return np.c_[spheres[:, :3] @ rotation(pose).T + pose[:3], spheres[:, 3]]


def proxy_capacities(objects, program):
    """Allocate once before CUDA capture; keep the same geometric resolution."""
    manipulated = {event["object"] for event in program["events"]}
    counts = [len(covering_spheres(objects[label]["bounds"])) for label in manipulated]
    return {**PROXY_SLOTS, "attached_object": max([PROXY_SLOTS["attached_object"], *counts])}


def sphere_box_penetration(spheres, obstacle):
    """Positive values mean overlap of a sphere with an oriented cuboid."""
    pose, half = obstacle["pose"], np.asarray(obstacle["dims"]) / 2
    local = (spheres[..., :3] - pose[:3]) @ rotation(pose)
    delta = np.abs(local) - half
    signed_distance = np.linalg.norm(np.maximum(delta, 0), axis=-1) + np.minimum(delta.max(-1), 0)
    return spheres[..., 3] - signed_distance


def configure_robot(robot_cfg, proxy_slots=None):
    """Add fixed flange-relative proxy slots; finger opening comes from physics."""
    cfg = copy.deepcopy(robot_cfg)
    kin = cfg["robot_cfg"]["kinematics"]
    for name, slots in (proxy_slots or PROXY_SLOTS).items():
        kin.setdefault("extra_links", {})[name] = {
            "link_name": name,
            "joint_name": f"{name}_joint",
            "joint_type": "FIXED",
            "parent_link_name": kin["tool_frames"][0],
            "fixed_transform": [0, 0, 0, 1, 0, 0, 0],
        }
        kin["collision_link_names"].append(name)
        kin["collision_spheres"][name] = [{"center": [0, 0, 0], "radius": -100.0}] * slots
    ignores = kin.setdefault("self_collision_ignore", {})
    # Finger geometry is rigid with link6 during arm motion. Adjacent finger,
    # flange and camera geometry overlaps at its mounting joints.
    ignores["gripper_proxy"] = ["link6", "camera_base", "camera", "attached_object"]
    ignores["attached_object"] = ["gripper_proxy"]
    # Native fitted base spheres extend below the supporting table. Clip only
    # their lower extent to the fixed base plane; moving links are untouched.
    for sphere in kin["collision_spheres"]["base_link"]:
        sphere["radius"] = min(sphere["radius"], max(0.0, sphere["center"][2] - 0.0001))
    return cfg


def make_scene_planner(robot, dt, proxy_slots=None):
    """Reuse native single-arm plan methods without unused batch/IK solvers."""
    import yaml
    from curobo.motion_planner import MotionPlanner, MotionPlannerCfg
    from curobo.types import DeviceCfg
    from env.planner_manager.curobo_planner import CuroboPlanner

    class ConfiguredPlanner(CuroboPlanner):
        def __init__(self):
            self.yml_path = robot.curobo_yml_path
            self.dt = dt
            self.robot_origin_pose = [0, 0, 0, 1, 0, 0, 0]
            self.active_joints_name = list(robot.arm_joints_name)
            self.all_joints = self.active_joints_name
            with open(self.yml_path) as stream:
                data = yaml.safe_load(stream)
            self.frame_bias = data["planner"]["frame_bias"]
            self.robot_cfg, self.scene_model = self._build_robot_and_scene_cfg(
                data, 0.74 - robot.entity_origin_pose[2]
            )
            cspace = self.robot_cfg["robot_cfg"]["kinematics"]["cspace"]
            defaults = dict(zip(cspace["joint_names"], cspace["default_joint_position"], strict=True))
            self.device_cfg = DeviceCfg()
            self.use_cuda_graph, self.use_graph_planner = True, False
            self.motion_planner = MotionPlanner(self._build_motion_planner_cfg(1))
            self.cspace_joint_names = list(self.motion_planner.joint_names)
            self.cspace_retract_config = np.array(
                [defaults[n] for n in self.cspace_joint_names], dtype=np.float32
            )
            self._active_to_cspace_idx = [self.cspace_joint_names.index(n) for n in self.active_joints_name]
            self.tool_frames = list(self.motion_planner.tool_frames)
            self.ee_link = self.tool_frames[0]
            self.motion_planner.warmup(enable_graph=False, num_warmup_iterations=5)

        def _build_robot_and_scene_cfg(self, data, table_height):
            cfg, world = super()._build_robot_and_scene_cfg(data, table_height)
            return configure_robot(cfg, proxy_slots), world

        def _build_motion_planner_cfg(self, max_batch_size):
            cfg = MotionPlannerCfg.create(
                robot=self.robot_cfg,
                scene_model=self.scene_model,
                collision_cache={"cuboid": 64},
                device_cfg=self.device_cfg,
                num_ik_seeds=32,
                num_trajopt_seeds=4,
                self_collision_check=True,
                use_cuda_graph=self.use_cuda_graph,
                max_batch_size=max_batch_size,
                multi_env=False,
                max_goalset=1,
            )
            if self.dt is not None:
                cfg.trajopt_solver_config.interpolation_dt = float(self.dt)
            if not self.use_graph_planner:
                cfg.graph_planner_config = None
            return cfg

    return ConfiguredPlanner()


class ScenePlanningContext:
    """Refresh a single active arm's model before each complete-task motion."""

    def __init__(self, env, rec, root, output):
        from pathlib import Path

        import curobo

        self.env, self.rec, self.output = env, rec, output
        self.rm = env.robot_manager
        self.robot = self.rm.robot_list[1]
        self.objects = json.loads((output / "scene-objects.json").read_text())
        program = json.loads((output / "interaction-program.json").read_text())
        self.proxy_slots = proxy_capacities(self.objects, program)
        self.wrapper = make_scene_planner(self.robot, env.dt, self.proxy_slots)
        self.rm.planner[self.robot.robot_name] = self.wrapper
        self.planner = self.wrapper.motion_planner
        self.bounds = extract_robot_bounds(root / "Assets/Robots/x5/ARX.usd")
        self.table = json.loads((output / "layout.json").read_text())["Table"]
        lm = env.scene_manager.layout_manager
        self.fixtures = {}
        for item in lm.get_layout_records(0, "Geometry"):
            points = (
                np.asarray(
                    lm.get_instance_metadata(0, label=item["label"])["geometry"]["aligned_bbox"]["vertices"]
                )
                * item["scale"]
            )
            self.fixtures[item["label"]] = [points.min(0).tolist(), points.max(0).tolist()]
        self.log = []
        self.proxies = {}
        self.base = None
        self.world = []
        self.omitted = []
        self.held = None
        self.event = None
        self.sphere_links = {}
        params = self.planner.kinematics.config.kinematics_config
        for name in self.wrapper.robot_cfg["robot_cfg"]["kinematics"]["collision_link_names"]:
            self.sphere_links[name] = params.get_sphere_index_from_link_name(name).cpu().numpy()
        write_json(
            output / "planner-model.json",
            {
                "world": "observed object/fixture OBBs, configured table, inactive-arm OBBs",
                "robot": "native fitted spheres; fixed-base lower extent clipped 0.1 mm above support plane",
                "gripper": "covering spheres of measured finger OBBs, fixed during arm motions",
                "held_object": "covering spheres of measured object OBB, flange-relative in planning only",
                "contact_handling": "omit legal payload support from optimizer, screen all other pairs before execution",
                "collision_cache_cuboids": 64,
                "proxy_slots": self.proxy_slots,
                "proxy_capacity_rule": "Preallocate enough 1-cm-cell covering spheres for every manipulated object",
                "candidate_screen": "all interpolated arm controls; sphere-OBB overlap, tolerance 1e-5 m",
                "validation": "requires separate physical validation; approximations can reject valid motions",
                "curobo_commit": git_revision(Path(curobo.__file__).resolve().parents[1]),
                "native_curobo_config_sha256": sha256(self.robot.curobo_yml_path),
                "native_urdf_sha256": sha256(root / "Assets/Robots/x5/X5A.urdf"),
            },
        )

    def _set_proxy(self, name, spheres):
        import torch

        params = self.planner.kinematics.config.kinematics_config
        indices = params.get_sphere_index_from_link_name(name)
        if len(spheres) > len(indices):
            raise RuntimeError(f"{name} exceeds configured sphere capacity")
        values = np.zeros((len(indices), 4), dtype=np.float32)
        values[:, 3] = -100
        values[: len(spheres)] = spheres
        params.link_spheres[:, indices] = torch.as_tensor(
            values, device=params.link_spheres.device, dtype=params.link_spheres.dtype
        )
        self.proxies[name] = spheres.tolist()

    def update(self, event=None):
        from curobo.scene import Cuboid, Scene

        self.event = event
        key = self.rm.robot_key[1]
        poses = dict(zip(key.body_names, key.data.body_link_pose_w[0].cpu().numpy(), strict=True))
        self.base = poses["base_link"]
        flange = poses["link6"]
        fingers = np.concatenate(
            [
                transform_spheres(covering_spheres(self.bounds[name]), relative_pose(poses[name], flange))
                for name in ("link7", "link8")
            ]
        )
        self._set_proxy("gripper_proxy", fingers)
        self.held = event["object"] if event and event["attachment"] == "held" else None
        if self.held:
            spheres = covering_spheres(self.objects[self.held]["bounds"])
            self._set_proxy(
                "attached_object",
                transform_spheres(spheres, relative_pose(self.rec.object_pose(self.held), flange)),
            )
        else:
            self._set_proxy("attached_object", np.empty((0, 4)))
        self.world = [
            box_obstacle(label, obj["bounds"], self.rec.object_pose(label), self.base)
            for label, obj in self.objects.items()
            if label != self.held
        ]
        half = np.asarray(self.table["scale"]) / 2
        self.world.append(
            box_obstacle(
                "Table", [-half, half], [*self.table["default_pos"], *self.table["default_ori"]], self.base
            )
        )
        self.world.extend(
            box_obstacle(label, bounds, self.rec.object_pose(label), self.base)
            for label, bounds in self.fixtures.items()
        )
        other = self.rm.robot_key[0]
        self.world.extend(
            box_obstacle(f"robot0/{name}", self.bounds[name], pose, self.base)
            for name, pose in zip(other.body_names, other.data.body_link_pose_w[0].cpu().numpy(), strict=True)
        )
        self.omitted = []
        if self.held:
            self.omitted = [
                b if a == self.held else a
                for a, b in event["allowed_contacts"]
                if self.held in (a, b) and self.robot.arm_name + "/gripper" not in (a, b)
            ]
        obstacles = [Cuboid(**obj) for obj in self.world if obj["name"] not in self.omitted]
        if len(obstacles) > 64:
            raise RuntimeError("World exceeds preallocated collision cache")
        self.planner.update_world(Scene(cuboid=obstacles))
        # CUDA graph rollouts and IK must observe the same in-place sphere edits.
        params = self.planner.kinematics.config.kinematics_config
        ik_params = self.planner.ik_solver.kinematics.config.kinematics_config
        if params.link_spheres.data_ptr() != ik_params.link_spheres.data_ptr():
            raise RuntimeError("IK and trajectory optimization do not share updated collision geometry")
        state = self.wrapper._build_joint_state(self.rm.get_joint(self.robot, [0])[0])
        fk = np.asarray(self.planner.compute_kinematics(state).tool_poses.get_link_pose("link6").tolist())
        observed = relative_pose(flange, self.base)
        frame_error = float(np.linalg.norm(fk[:3] - observed[:3]))
        rotation_error = float(
            np.degrees(np.arccos(np.clip((np.trace(rotation(fk).T @ rotation(observed)) - 1) / 2, -1, 1)))
        )
        if frame_error > 0.001 or rotation_error > 0.5:
            raise RuntimeError("Planner FK and observed flange disagree")
        self.log.append(
            {
                "event_id": event["id"] if event else None,
                "world": self.world,
                "base_pose": self.base.tolist(),
                "held_object": self.held,
                "optimizer_omissions": self.omitted,
                "proxy_spheres": copy.deepcopy(self.proxies),
                "flange_position_error_m": frame_error,
                "flange_rotation_error_deg": rotation_error,
            }
        )
        self.save()

    def screen(self, result):
        """Recheck all world obstacles, with only pair-specific contact exemptions."""
        import torch
        from curobo.types import JointState

        if result["status"] != "Success":
            return []
        q = np.stack([self.wrapper._build_cspace_joint_values(row) for row in result["position"]])
        state = JointState.from_position(
            torch.as_tensor(q[None], dtype=torch.float32, device=self.wrapper.device_cfg.device),
            joint_names=self.wrapper.cspace_joint_names,
        )
        spheres = self.planner.compute_kinematics(state).robot_spheres.detach().cpu().numpy()[0]
        hits = []
        for obstacle in self.world:
            penetration = sphere_box_penetration(spheres, obstacle)
            for link, ids in self.sphere_links.items():
                if not len(ids):
                    continue
                if link == "attached_object" and obstacle["name"] in self.omitted:
                    continue
                if link == "base_link" and obstacle["name"] == "Table":
                    continue
                overlap = penetration[:, ids]
                mask = (overlap > 1e-5) & (spheres[:, ids, 3] > 0)
                if mask.any():
                    first = int(np.argwhere(mask)[0, 0])
                    hits.append(
                        {
                            "link": link,
                            "obstacle": obstacle["name"],
                            "first_sample": first,
                            "max_overlap_m": float(overlap[mask].max()),
                        }
                    )
        return hits

    def plan(self, target=None, initial_joints=None):
        started = time.monotonic()
        current = self.rm.get_joint(self.robot, [0])[0]
        if target is not None:
            result = self.wrapper.plan_path(current, target, self.base)
        else:
            result = self.wrapper.plan_joint(current, initial_joints)
        self.log[-1]["native_planner_status"] = result["status"]
        hits = self.screen(result)
        if hits:
            result = {"status": "Fail"}
        self.log[-1].update(
            {
                "candidate_intersections": hits,
                "status": result["status"],
                "planning_and_screen_seconds": time.monotonic() - started,
            }
        )
        self.save()
        return result

    def save(self):
        write_json(self.output / "planner-scenes.json", self.log)
