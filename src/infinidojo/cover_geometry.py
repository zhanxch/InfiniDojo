"""Independent source-mesh checks for the native inverted-cup task.

Mesh queries use millimetres to avoid ray tolerances swallowing the tiny faces
of the native cup. These checks supplement actual PhysX reports: source meshes
are not the simulator's cooked convex decomposition and checks are discrete.
"""

from __future__ import annotations

from itertools import product

import numpy as np

from infinidojo.geometry import rotation


def collision_mesh(path, scale=(1, 1, 1)):
    """Read the single rigid body's enabled triangle/quad collision surfaces."""
    import trimesh
    from pxr import Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.Open(str(path))
    bodies = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    if len(bodies) != 1:
        raise ValueError("Cup audit requires exactly one rigid body")
    cache = UsdGeom.XformCache()
    body_inverse = cache.GetLocalToWorldTransform(bodies[0]).GetInverse()
    meshes, approximations = [], []
    for prim in Usd.PrimRange(bodies[0], Usd.TraverseInstanceProxies()):
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        if not UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get():
            continue
        if not prim.IsA(UsdGeom.Mesh):
            raise ValueError("Cup audit only supports mesh colliders")
        source = UsdGeom.Mesh(prim)
        if source.GetHoleIndicesAttr().Get():
            raise ValueError("Mesh face holes are not supported")
        matrix = np.asarray(cache.GetLocalToWorldTransform(prim) * body_inverse)
        points = np.asarray(source.GetPointsAttr().Get(), dtype=float)
        points = (np.c_[points, np.ones(len(points))] @ matrix)[:, :3] * scale
        indices = np.asarray(source.GetFaceVertexIndicesAttr().Get())
        faces, offset = [], 0
        for count in source.GetFaceVertexCountsAttr().Get():
            if count not in (3, 4):
                raise ValueError("Only native triangle/quad faces are supported")
            face = indices[offset : offset + count]
            faces.extend((face[0], face[i], face[i + 1]) for i in range(1, count - 1))
            offset += count
        mesh = trimesh.Trimesh(vertices=points * 1000, faces=faces, process=True)
        if not mesh.is_watertight or not mesh.is_winding_consistent:
            raise ValueError("Cup collision source must be a closed consistently oriented solid")
        meshes.append(mesh)
        approximations.append(UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get())
    if len(meshes) != 1:
        raise ValueError("Native cup audit requires one collision mesh")
    return meshes[0], approximations[0]


def intersecting_triangles(triangles, bounds):
    """Triangle/AABB separating-axis test, retaining touching surfaces."""
    bounds = np.asarray(bounds)
    center, half = bounds.mean(0), (bounds[1] - bounds[0]) / 2
    candidates = np.all(triangles.max(1) >= bounds[0], axis=1) & np.all(triangles.min(1) <= bounds[1], axis=1)
    indices = np.flatnonzero(candidates)
    tri = triangles[indices] - center
    if not len(tri):
        return indices
    edges = np.roll(tri, -1, axis=1) - tri
    axes = [np.cross(edges[:, 0], edges[:, 1])]
    axes.extend(np.cross(edges[:, edge], basis) for edge in range(3) for basis in np.eye(3))
    hit = np.ones(len(tri), dtype=bool)
    for axis in axes:
        projection = np.einsum("nvi,ni->nv", tri, axis)
        radius = np.abs(axis) @ half
        nonzero = np.linalg.norm(axis, axis=1) > 1e-12
        separated = (projection.min(1) > radius) | (projection.max(1) < -radius)
        hit &= ~(nonzero & separated)
    return indices[hit]


def box_clear_of_mesh(mesh_mm, mesh_pose, box_bounds, box_pose):
    """Test the whole box volume, including fully enclosed solid material."""
    mr, br = rotation(mesh_pose), rotation(box_pose)
    # Row-vector convention; all queries and mesh vertices have the same units.
    vertices = (mesh_mm.vertices @ mr.T + (np.asarray(mesh_pose[:3]) - box_pose[:3]) * 1000) @ br
    intersections = intersecting_triangles(vertices[mesh_mm.faces], np.asarray(box_bounds) * 1000)
    box_center_world = np.asarray(box_bounds).mean(0) @ br.T + box_pose[:3]
    center_local_mm = (box_center_world - mesh_pose[:3]) @ mr * 1000
    center_in_solid = bool(mesh_mm.contains([center_local_mm])[0])
    return {
        "clear": not len(intersections) and not center_in_solid,
        "intersecting_source_triangles": len(intersections),
        "box_center_inside_solid": center_in_solid,
    }


def audit_cover_states(frames, program, objects, meshes):
    """Check each transfer's end state against the independently built program."""
    endpoints, pairs = [], []
    for index, transfer in enumerate(program["transfers"]):
        event_id = f"{index:02d}/retreat"
        observed = [f for f in frames if f.get("event_id") == event_id]
        if not observed:
            endpoints.append({"event_id": event_id, "present": False, "relations_match": False})
            continue
        frame = observed[-1]
        expected = program["events"][index * 8 + 7]["containment_goal_after_transfer"]
        covered = {}
        for cup, mesh in meshes.items():
            cup_pose = frame["objects"][cup]
            for block in ("red", "green", "blue"):
                block_pose = frame["objects"][block]
                bounds = np.asarray(objects[block]["bounds"])
                corners = np.array(list(product(*zip(*bounds, strict=True))))
                world = corners @ rotation(block_pose).T + block_pose[:3]
                local_mm = (world - cup_pose[:3]) @ rotation(cup_pose) * 1000
                inside_envelope = bool(
                    np.all(local_mm >= mesh.bounds[0] - 0.001) and np.all(local_mm <= mesh.bounds[1] + 0.001)
                )
                if inside_envelope:
                    covered.setdefault(cup, []).append(block)
                pairs.append(
                    {
                        "event_id": event_id,
                        "step": frame["step"],
                        "cup": cup,
                        "block": block,
                        **box_clear_of_mesh(mesh, cup_pose, bounds, block_pose),
                    }
                )
        upright = all(-rotation(frame["objects"][cup])[2, 2] > np.cos(np.radians(10)) for cup in meshes)
        endpoints.append(
            {
                "event_id": event_id,
                "present": True,
                "observed_containment": covered,
                "expected_containment": expected,
                "relations_match": covered == {cup: [block] for cup, block in expected.items()},
                "cups_inverted": upright,
            }
        )
    return {
        "clear": all(p["clear"] for p in pairs) and all(e["present"] for e in endpoints),
        "relations_match": all(e["relations_match"] and e.get("cups_inverted", False) for e in endpoints),
        "endpoints": endpoints,
        "pairs": pairs,
        "scope": "Whole block boxes versus cup source collision surfaces at six transfer endpoints",
        "limitations": "Not cooked PhysX convex parts or a continuous-time proof; actual contact audit is also required",
    }
