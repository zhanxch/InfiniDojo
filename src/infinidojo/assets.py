"""Generate actual Infinigen meshes and export native RoboDojo rigid assets.

Run in the Blender/tooling Python, not in an already-running Isaac process.
The v0 can family is closed and approximately convex: convex collision is
appropriate for this task, but this exporter must not be used for open mugs,
containers, insertion sockets or articulated assets without a new adapter.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

from infinidojo.io import git_revision, sha256, write_json


def generate_can(seed: int, output: Path, infinigen_root: Path, *, radius: float, height: float) -> dict:
    sys.path.insert(0, str(infinigen_root.resolve()))
    import bpy
    from infinigen.assets.objects.tableware.can import CanFactory
    from infinigen.core.util.math import FixedSeed

    output.mkdir(parents=True, exist_ok=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with FixedSeed(seed):
        factory = CanFactory(seed)
        factory.x_length = radius
        factory.z_length = height
        factory.skewness = 1.0
        obj = factory.create_asset()
        obj.name = "procedural_can"
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    mesh.calc_loop_triangles()
    vertices = np.array([list(evaluated.matrix_world @ v.co) for v in mesh.vertices], dtype=float)
    faces = np.array([list(t.vertices) for t in mesh.loop_triangles], dtype=int)
    evaluated.to_mesh_clear()
    # Canonical frame: XY centered, support plane z=0. Keep actual mesh geometry.
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    offset = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    vertices -= offset
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    np.savez_compressed(output / "mesh.npz", vertices=vertices, faces=faces)
    # Preserve the procedural source for inspection; do not claim its shaders
    # have been baked into the v0 USD PreviewSurface material.
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "source.blend"))
    color = np.random.default_rng(seed).uniform(0.2, 0.8, 3).tolist()
    export_usd(output / "object.usd", vertices, faces, color)
    corners = list(itertools.product(*zip(lo.tolist(), hi.tolist())))
    box = {"vertices": corners, "extents": (hi - lo).tolist()}
    grasp_z = float((hi[2] - lo[2]) / 2)
    metadata = {
        "geometry": {
            "vertices": len(vertices),
            "faces": len(faces),
            "aligned_bbox": box,
            "oriented_bbox": box,
            "radius": float(np.linalg.norm(hi - lo) / 2),
        },
        "physics": {"mass": 0.08, "static_friction": 1.0, "dynamic_friction": 0.8},
        "visual": {},
        "active": {"grasp_center": [0.0, 0.0, grasp_z, 1.0, 0.0, 0.0, 0.0]},
        "passive": {},
    }
    write_json(output / "metadata.json", metadata)
    write_json(
        output / "description.json",
        {"description": ["procedurally generated can"], "caption": "procedurally generated can"},
    )
    provenance = {
        "generator": "infinigen.assets.objects.tableware.can.CanFactory",
        "seed": seed,
        "infinigen_commit": git_revision(infinigen_root),
        "blender_version": bpy.app.version_string,
        "parameters": {"x_length": radius, "z_length": height, "skewness": 1.0, "shape": factory.shape},
        "canonical_offset": offset.tolist(),
        "bounds": [lo.tolist(), hi.tolist()],
        "material": "seeded USD PreviewSurface; original procedural shaders retained only in source.blend",
        "collision": "convex hull, closed can family only",
        "files": {
            name: sha256(output / name)
            for name in ["mesh.npz", "object.usd", "metadata.json", "description.json"]
        },
    }
    write_json(output / "provenance.json", provenance)
    return provenance


def export_usd(path: Path, vertices: np.ndarray, faces: np.ndarray, color: list[float]) -> None:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, "/Asset/mesh")
    mesh.CreatePointsAttr([Gf.Vec3f(*p) for p in vertices.tolist()])
    mesh.CreateFaceVertexCountsAttr([3] * len(faces))
    mesh.CreateFaceVertexIndicesAttr(faces.reshape(-1).tolist())
    mesh.CreateSubdivisionSchemeAttr("none")
    mesh.CreateDoubleSidedAttr(True)
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr("convexHull")
    material = UsdShade.Material.Define(stage, "/Asset/Material")
    shader = UsdShade.Shader.Define(stage, "/Asset/Material/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.4)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.25)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    stage.GetRootLayer().Save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--infinigen-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--radius", type=float, default=0.025)
    parser.add_argument("--height", type=float, default=0.07)
    args = parser.parse_args()
    if not 0.015 <= args.radius <= 0.1 or not 0.03 <= args.height <= 0.3:
        parser.error("unsupported can dimensions")
    print(
        json.dumps(
            generate_can(
                args.seed, args.output.resolve(), args.infinigen_root, radius=args.radius, height=args.height
            )
        )
    )


if __name__ == "__main__":
    main()
