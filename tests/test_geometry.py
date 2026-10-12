import numpy as np
from scipy.spatial.transform import Rotation

from infinidojo.geometry import aabb, overlap_aabb, overlap_obb


def test_audit_rejects_false_positive_from_axis_aligned_bound():
    # A diagonal thin bar's world AABB covers empty corner space.
    bar = (np.zeros(3), np.array([1.0, 0.05, 0.05]), Rotation.from_euler("z", 45, degrees=True).as_matrix())
    corner = (np.array([0.6, -0.6, 0]), np.array([0.04] * 3), np.eye(3))
    assert overlap_aabb(aabb(bar), aabb(corner))
    assert not overlap_obb(bar, corner)
    on_bar = (np.array([0.4, 0.4, 0]), np.array([0.04] * 3), np.eye(3))
    assert overlap_obb(bar, on_bar)


def test_clearance_and_touching_are_distinct_from_penetration():
    first = (np.zeros(3), np.ones(3), np.eye(3))
    second = (np.array([2, 0, 0]), np.ones(3), np.eye(3))
    assert not overlap_obb(first, second)
    assert overlap_obb(first, second, margin=0.01)
