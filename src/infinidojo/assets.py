"""Generate actual Infinigen meshes and export native RoboDojo rigid assets.

Run in the Blender/tooling Python, not in an already-running Isaac process.
The v0 can family is closed and approximately convex: convex collision is
appropriate for this task, but this exporter must not be used for open mugs,
containers, insertion sockets or articulated assets without a new adapter.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import sys
from pathlib import Path

import numpy as np

from infinidojo.io import git_revision, sha256, write_json

COLORS = {
    "red": [0.8, 0.05, 0.05],
    "blue": [0.05, 0.15, 0.8],
    "green": [0.05, 0.65, 0.1],
    "yellow": [0.85, 0.7, 0.05],
    "gray": [0.45, 0.45, 0.45],
}


def verified_asset(folder: Path) -> dict:
    record = json.loads((folder / "provenance.json").read_text())
    if record["generator"] != "infinigen.assets.objects.tableware.can.CanFactory":
        raise ValueError("Only the closed CanFactory adapter is supported")
    for name, digest in record["files"].items():
        if sha256(folder / name) != digest:
            raise ValueError(f"Asset content changed: {folder.name}/{name}")
    return record


def matches_parameters(record: dict, requested: dict) -> bool:
    params = record["parameters"]
    return (
        params["x_length"] == requested["radius_m"]
        and params["z_length"] == requested["height_m"]
        and params["shape"] == requested["shape"]
        and record.get("appearance", {}).get("color_name") == requested["color"]
    )


def merge_asset(mapping: dict, folder: Path) -> dict:
    """Keep task and clutter assets together without silently replacing an ID."""
    folder = folder.resolve()
    record = verified_asset(folder)
    key = str(record["seed"])
    if key in mapping and sha256(Path(mapping[key]) / "provenance.json") != sha256(
        folder / "provenance.json"
    ):
        raise ValueError(f"Conflicting generated asset identity: {key}")
    mapping[key] = str(folder)
    return record


def pickup_layout(base: dict, folder: Path, xy: list[float]) -> dict:
    """Replace the pickup template's target and clutter with a minimal task scene.

    The requested clutter is generated separately around the new interaction.
    Native unlabeled clutter entries are not part of the pickup task contract.
    """
    record = verified_asset(folder)
    params = record["parameters"]
    if not 0.018 <= params["x_length"] <= 0.03 or not 0.05 <= params["z_length"] <= 0.09:
        raise ValueError("Target dimensions outside the declared small-can grasp profile")
    if len(xy) != 2 or not np.isfinite(xy).all() or not (0.12 <= xy[0] <= 0.35 and -0.14 <= xy[1] <= 0.1):
        raise ValueError("Target outside the declared right-arm pickup region")
    task_items = [
        item for group in base["Rigid"].values() for item in group if item.get("type") != "cluttered"
    ]
    if len(task_items) != 1 or task_items[0].get("label") != "target":
        raise ValueError("Procedural target replacement requires a single-target pickup layout")
    metadata = json.loads((folder / "metadata.json").read_text())
    layout = copy.deepcopy(base)
    tabletop = layout["Table"]["default_pos"][2] + layout["Table"]["scale"][2] / 2
    layout["Rigid"] = {
        "infinigen_can": [
            {
                "category": "infinigen_can",
                "category_idx": record["seed"],
                "label": "target",
                "default_pos": [*xy, tabletop - record["bounds"][0][2]],
                "default_ori": [1, 0, 0, 0],
                "scale": [1, 1, 1],
                "physics": {"type": "rigid", **metadata["physics"]},
                "visual": {},
                "relative_plane": "Table",
            }
        ]
    }
    return layout


def generate_can(
    seed: int,
    output: Path,
    infinigen_root: Path,
    *,
    radius: float,
    height: float,
    shape: str | None = None,
    color_name: str | None = None,
) -> dict:
    if shape not in (None, "circle", "rectangle") or color_name not in (None, *COLORS):
        raise ValueError("Unsupported can shape or appearance")
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
        if shape is not None:
            factory.shape = shape
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
    color = COLORS[color_name] if color_name else np.random.default_rng(seed).uniform(0.2, 0.8, 3).tolist()
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
    caption = (
        f"{color_name} {factory.shape} can, {height * 100:g} cm tall and {radius * 200:g} cm wide"
        if color_name
        else "procedurally generated can"
    )
    write_json(
        output / "description.json",
        {"description": [caption], "caption": caption},
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
        "appearance": {"color_name": color_name, "diffuse_color": color, "roughness": 0.4, "metallic": 0.25},
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
    parser.add_argument("--shape", choices=["circle", "rectangle"])
    parser.add_argument("--color", choices=list(COLORS))
    args = parser.parse_args()
    if not 0.015 <= args.radius <= 0.1 or not 0.03 <= args.height <= 0.3:
        parser.error("unsupported can dimensions")
    print(
        json.dumps(
            generate_can(
                args.seed,
                args.output.resolve(),
                args.infinigen_root,
                radius=args.radius,
                height=args.height,
                shape=args.shape,
                color_name=args.color,
            )
        )
    )


if __name__ == "__main__":
    main()
