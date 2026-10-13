import numpy as np
from scipy.spatial.transform import Rotation

from infinidojo.planning import (
    box_obstacle,
    covering_spheres,
    proxy_capacities,
    relative_pose,
    sphere_box_penetration,
    transform_spheres,
)


def test_scene_and_payload_use_the_same_rotated_base_frame():
    base = [0.3, -0.45, 0.765, 2**-0.5, 0, 0, 2**-0.5]
    object_pose = [0.4, -0.15, 0.8, 1, 0, 0, 0]
    # Off-center bounds exercise generated assets whose origin is at the bottom.
    bounds = [[-0.02, -0.03, 0], [0.02, 0.03, 0.1]]
    world_spheres = transform_spheres(covering_spheres(bounds), object_pose)
    object_in_base = relative_pose(object_pose, base)
    local_spheres = transform_spheres(covering_spheres(bounds), object_in_base)
    recovered = transform_spheres(local_spheres, base)
    np.testing.assert_allclose(recovered, world_spheres, atol=1e-12)
    box = box_obstacle("can", bounds, object_pose, base)
    np.testing.assert_allclose(box["pose"][:3], [0.3, -0.1, 0.085], atol=1e-12)
    np.testing.assert_allclose(box["dims"], [0.04, 0.06, 0.1])
    np.testing.assert_allclose(
        sphere_box_penetration(local_spheres, box),
        sphere_box_penetration(
            world_spheres, box_obstacle("can", bounds, object_pose, [0, 0, 0, 1, 0, 0, 0])
        ),
        atol=1e-12,
    )


def test_covering_spheres_include_box_corners_and_surface():
    bounds = np.array([[-0.015, -0.0245, -0.0304], [0.071, 0.0129, 0.0307]])
    spheres = covering_spheres(bounds)
    grid = np.stack(
        np.meshgrid(*(np.linspace(a, b, 15) for a, b in zip(*bounds)), indexing="ij"), axis=-1
    ).reshape(-1, 3)
    distance = np.linalg.norm(grid[:, None] - spheres[None, :, :3], axis=-1) - spheres[:, 3]
    assert np.max(distance.min(axis=1)) < 1e-12


def test_rotated_obstacle_distinguishes_contact_gap_and_penetration():
    quat = Rotation.from_euler("z", 90, degrees=True).as_quat()[[3, 0, 1, 2]]
    obstacle = {"pose": [1, 2, 3, *quat], "dims": [0.2, 0.4, 0.6]}
    # Rotating swaps the X/Y half-extents: the world X face is at 1.2.
    spheres = np.array([[1.25, 2, 3, 0.05], [1.26, 2, 3, 0.05], [1.24, 2, 3, 0.05]])
    np.testing.assert_allclose(sphere_box_penetration(spheres, obstacle), [0, -0.01, 0.01], atol=1e-12)


def test_tall_payload_capacity_grows_without_coarsening_or_counting_static_clutter():
    can = [[-0.0247, -0.0247, 0.0005], [0.0247, 0.0247, 0.1385]]
    objects = {
        "target": {"bounds": can},
        "clutter_0": {"bounds": [[-1, -1, -1], [1, 1, 1]]},
    }
    program = {"events": [{"object": "target", "phase": "lift"}]}
    capacities = proxy_capacities(objects, program)
    assert capacities["attached_object"] == 350 == len(covering_spheres(can))
    assert capacities["gripper_proxy"] == 512
    objects["target"]["bounds"] = [[-0.0125] * 3, [0.0125] * 3]
    assert proxy_capacities(objects, program)["attached_object"] == 125
