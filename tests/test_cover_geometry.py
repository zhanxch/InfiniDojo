import numpy as np
import pytest

from infinidojo.cover_geometry import (
    audit_cover_states,
    box_clear_of_mesh,
    collision_mesh,
    intersecting_triangles,
)
from infinidojo.interaction import build_cover_program

trimesh = pytest.importorskip("trimesh")
pytest.importorskip("rtree")
IDENTITY = [0, 0, 0, 1, 0, 0, 0]


def test_cavity_and_solid_material_are_distinguished():
    # A watertight annular solid has an empty central opening, like a cup wall.
    mesh = trimesh.creation.annulus(r_min=30, r_max=36, height=72, sections=128)
    box = [[-0.0125] * 3, [0.0125] * 3]
    assert box_clear_of_mesh(mesh, IDENTITY, box, IDENTITY)["clear"]
    touching_wall = box_clear_of_mesh(mesh, IDENTITY, box, [0.025, 0, 0, 1, 0, 0, 0])
    assert not touching_wall["clear"] and touching_wall["intersecting_source_triangles"] > 0
    solid = trimesh.creation.box(extents=[72] * 3)
    embedded = box_clear_of_mesh(solid, IDENTITY, box, IDENTITY)
    assert not embedded["clear"] and embedded["box_center_inside_solid"]


def test_whole_volume_checked_even_when_box_corners_are_outside_material():
    mesh = trimesh.creation.box(extents=[1, 50, 50])
    large_box = [[-0.03] * 3, [0.03] * 3]
    assert not mesh.contains(np.array([[-30, -30, -30], [30, 30, 30]])).any()
    hit = box_clear_of_mesh(mesh, IDENTITY, large_box, IDENTITY)
    assert not hit["clear"] and hit["intersecting_source_triangles"] > 0


def test_triangle_box_sat_rejects_broadphase_false_overlap():
    triangles = np.array([[[0, 2, 0], [2, 0, 0], [2, 2, 0]], [[0, 0, 0], [1, 0, 0], [0, 1, 0]]])
    assert intersecting_triangles(triangles, [[-0.1] * 3, [0.1] * 3]).tolist() == [1]


def test_mesh_pose_rotation_and_translation_preserve_query():
    mesh = trimesh.creation.annulus(r_min=30, r_max=36, height=72, sections=64)
    pose = [0.2, -0.13, 0.8, 2**-0.5, 2**-0.5, 0, 0]
    assert box_clear_of_mesh(mesh, pose, [[-0.0125] * 3, [0.0125] * 3], pose)["clear"]


def test_full_cover_sequence_requires_empty_cavities_and_complete_uncovering():
    from copy import deepcopy

    mesh = trimesh.creation.annulus(r_min=30, r_max=36, height=72, sections=32)
    meshes = {cup: mesh for cup in ("cup0", "cup1", "cup2")}
    program = build_cover_program(["red", "blue", "green"], {cup: "arm" for cup in meshes})
    poses = {
        color: [x, 0, 0.05, 1, 0, 0, 0]
        for color, x in zip(("red", "blue", "green"), (-0.2, 0, 0.2), strict=True)
    }
    poses.update({cup: [x, -0.1, 0.05, 0, 1, 0, 0] for cup, x in zip(meshes, (-0.2, 0, 0.2), strict=True)})
    initial = deepcopy(poses)
    objects = {label: {"bounds": [[-0.0125] * 3, [0.0125] * 3]} for label in poses}
    frames = []
    for i, transfer in enumerate(program["transfers"]):
        cup, block = transfer["object"], transfer["block"]
        poses[cup][:3] = poses[block][:3] if transfer["operation"] == "cover" else initial[cup][:3]
        frames.append({"step": i, "event_id": f"{i:02d}/retreat", "objects": deepcopy(poses)})
    good = audit_cover_states(frames, program, objects, meshes)
    assert good["clear"] and good["relations_match"]
    incomplete = audit_cover_states(frames[:3], program, objects, meshes)
    assert not incomplete["clear"] and not incomplete["relations_match"]
    embedded = deepcopy(frames)
    embedded[2]["objects"]["red"][0] += 0.025
    assert not audit_cover_states(embedded, program, objects, meshes)["clear"]


def test_usd_collision_mesh_uses_body_relative_transform_and_instance_scale(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    path = tmp_path / "cup.usda"
    stage = Usd.Stage.CreateNew(str(path))
    body = UsdGeom.Xform.Define(stage, "/body")
    body.AddTranslateOp().Set(Gf.Vec3d(10, 20, 30))
    UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
    shape = UsdGeom.Mesh.Define(stage, "/body/collision")
    source = trimesh.creation.box(extents=[0.01, 0.02, 0.03])
    shape.CreatePointsAttr(source.vertices.tolist())
    shape.CreateFaceVertexCountsAttr([3] * len(source.faces))
    shape.CreateFaceVertexIndicesAttr(source.faces.flatten().tolist())
    shape.AddTranslateOp().Set(Gf.Vec3d(0.001, 0, 0))
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim()).CreateCollisionEnabledAttr(True)
    UsdPhysics.MeshCollisionAPI.Apply(shape.GetPrim()).CreateApproximationAttr("convexDecomposition")
    stage.GetRootLayer().Save()
    mesh, approximation = collision_mesh(path, [2, 1, 1])
    assert approximation == "convexDecomposition"
    assert np.allclose(mesh.bounds, [[-8, -10, -15], [12, 10, 15]])
