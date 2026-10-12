import json
from copy import deepcopy

import numpy as np
import pytest
from PIL import Image

from infinidojo.policy_eval import CAMERAS, check_observation, record_policy_io


def observation():
    rgb = np.array([[[255, 0, 0], [0, 0, 255]]], dtype=np.uint8)
    return {
        "vision": {name: {"color": rgb.copy(), "shape": rgb.shape} for name in CAMERAS},
        "state": {"right_ee_pose": [0, 0, 0, 1, 0, 0, 0]},
        "action": {"right_ee_joint_state": [1]},
        "instruction": "Stack the three blocks with different textures.",
        "data_format_version": "v1.0",
        "additional_info": {"frequency": 25},
        "env_idx": 0,
    }


def test_privileged_channels_and_nonfinite_state_fail_closed():
    obs = observation()
    check_observation(obs)
    changes = [
        lambda x: x.update(reference_trajectory=[]),
        lambda x: x["state"].update(object_pose=[0, 0, 0]),
        lambda x: x["vision"]["cam_head"].update(depth=np.zeros((1, 2))),
        lambda x: x["additional_info"].update(scene_constraints={}),
        lambda x: x["state"].update(right_ee_joint_state=[float("nan")]),
    ]
    for change in changes:
        altered = deepcopy(obs)
        change(altered)
        with pytest.raises(ValueError):
            check_observation(altered)


def test_recording_preserves_rgb_calls_return_values_and_cleanup(tmp_path):
    obs = observation()

    class Environment:
        def __init__(self):
            self.take_action_cnt = [0]
            self.calls = []

        def get_obs_batch(self):
            self.calls.append("observe")
            return [obs]

        def take_action_batch(self, actions):
            self.calls.append("act")
            self.take_action_cnt[0] += 1
            return "original return"

    env = Environment()
    with pytest.raises(RuntimeError, match="interrupted"), record_policy_io(env, tmp_path):
        assert env.get_obs_batch()[0] is obs
        assert env.take_action_batch([{"right_ee_joint_state": [1]}]) == "original return"
        raise RuntimeError("interrupted")
    assert env.calls == ["observe", "act"] and env.take_action_cnt == [1]
    assert "get_obs_batch" not in env.__dict__ and "take_action_batch" not in env.__dict__
    records = [json.loads(line) for line in (tmp_path / "policy-io.jsonl").read_text().splitlines()]
    assert [r["event"] for r in records] == ["observation", "action_requested", "action_applied"]
    assert records[-1]["action_count"] == [1]
    image_path = tmp_path / records[0]["observation"]["vision"]["cam_head"]["file"]
    np.testing.assert_array_equal(np.asarray(Image.open(image_path)), obs["vision"]["cam_head"]["color"])
    assert len(list((tmp_path / "rgb").glob("*.png"))) == 1
