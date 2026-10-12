"""Conservative body boxes and an independent oriented-box collision audit.

Generation uses world AABBs. Auditing uses a 15-axis OBB separating-axis test.
Both bound actual USD collision geometry; neither is a contact-force measurement
or a continuous-time guarantee. Intended finger–target contacts are not tested
as obstacles: obstacle checks cover each full robot and the carried object.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation


def rotation(pose):
    return Rotation.from_quat(np.asarray(pose)[[4, 5, 6, 3]]).as_matrix()


def transformed_box(bounds, pose):
    bounds = np.asarray(bounds)
    rot = rotation(pose)
    return rot @ bounds.mean(axis=0) + np.asarray(pose)[:3], (bounds[1] - bounds[0]) / 2, rot


def aabb(box):
    center, half, rot = box
    extent = np.abs(rot) @ half
    return np.array([center - extent, center + extent])


def overlap_aabb(a, b, margin=0.0):
    a, b = np.asarray(a), np.asarray(b)
    return bool(np.all(a[0] < b[1] + margin) and np.all(b[0] < a[1] + margin))


def overlap_obb(a, b, margin=0.0):
    ca, ha, ra = a
    cb, hb, rb = b
    axes = [*ra.T, *rb.T, *(np.cross(x, y) for x in ra.T for y in rb.T)]
    for axis in axes:
        length = np.linalg.norm(axis)
        if length < 1e-10:
            continue
        axis = axis / length
        distance = abs(np.dot(cb - ca, axis))
        reach = np.sum(ha * np.abs(ra.T @ axis)) + np.sum(hb * np.abs(rb.T @ axis))
        if distance >= reach + margin:
            return False
    return True


def extract_robot_bounds(usd_path):
    """Read the native robot's collision shapes in each rigid-link frame."""
    from pxr import Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.Open(str(usd_path))
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        ["default", "render", "proxy", "guide"],
        useExtentsHint=False,
        ignoreVisibility=True,
    )
    bounds = {}
    for body in stage.Traverse():
        if not body.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        boxes = []
        for prim in Usd.PrimRange(body, Usd.TraverseInstanceProxies()):
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                box = cache.ComputeRelativeBound(prim, body).ComputeAlignedRange()
                if not box.IsEmpty():
                    boxes.append([list(box.GetMin()), list(box.GetMax())])
        if not boxes:
            raise ValueError(f"No collision shape for {body.GetPath()}")
        boxes = np.asarray(boxes)
        bounds[body.GetName()] = [boxes[:, 0].min(0).tolist(), boxes[:, 1].max(0).tolist()]
    if not bounds:
        raise ValueError("Robot has no rigid collision bodies")
    return bounds


def cluster_frames(trajectory, robot_bounds, target_bounds):
    """Use one attachment transform after grasp; keep the arm articulated."""
    grasp = [frame for frame in trajectory if frame["phase"] == "grasp"]
    if not grasp:
        raise ValueError("Reference has no grasp event")
    attach = grasp[-1]
    robot = attach["robots"][0]
    flange = robot["body_poses"][robot["body_names"].index("link6")]
    relative_pos = rotation(flange).T @ (np.asarray(attach["target_pose"][:3]) - flange[:3])
    relative_rot = rotation(flange).T @ rotation(attach["target_pose"])
    frames = []
    for frame in trajectory:
        bodies = []
        for ri, robot in enumerate(frame["robots"]):
            for name, pose in zip(robot["body_names"], robot["body_poses"], strict=True):
                if name not in robot_bounds:
                    raise ValueError(f"Missing collider for native body {name}")
                box = transformed_box(robot_bounds[name], pose)
                bodies.append((f"robot{ri}/{name}", box))
        if frame["phase"] in ("lift", "hold"):
            robot = frame["robots"][0]
            flange = robot["body_poses"][robot["body_names"].index("link6")]
            rot = rotation(flange)
            pos = np.asarray(flange[:3]) + rot @ relative_pos
            quat = Rotation.from_matrix(rot @ relative_rot).as_quat()[[3, 0, 1, 2]]
            target_pose = np.r_[pos, quat]
        else:
            target_pose = frame["target_pose"]
        bodies.append(("target", transformed_box(target_bounds, target_pose)))
        frames.append({"phase": frame["phase"], "time_s": frame["time_s"], "bodies": bodies})
    return frames


def audit_clutter(frames, clutter, clearance=0.0):
    """Audit at every recorded frame and retain first pair/phase intersections."""
    collisions = {}
    obstacles = [(item["id"], transformed_box(item["bounds"], item["pose"])) for item in clutter]
    for frame in frames:
        for name, body in frame["bodies"]:
            broad = aabb(body)
            for item_id, obstacle in obstacles:
                if overlap_aabb(broad, aabb(obstacle), clearance) and overlap_obb(body, obstacle, clearance):
                    key = (name, item_id, frame["phase"])
                    collisions.setdefault(
                        key,
                        {
                            "body": name,
                            "clutter_id": item_id,
                            "phase": frame["phase"],
                            "time_s": frame["time_s"],
                        },
                    )
    return {
        "clear": not collisions,
        "intersections": list(collisions.values()),
        "frames": len(frames),
        "method": "oriented collision-geometry bounding boxes; sampled, conservative",
    }
