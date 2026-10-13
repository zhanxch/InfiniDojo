"""Run one native or exported episode through unchanged RoboDojo evaluation.

Run with the Isaac Python runtime and an already running XPolicyLab server.
The generator's reference controller is never imported into the policy adapter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import numpy as np

from infinidojo.episode import load_episode
from infinidojo.io import git_revision, sha256, write_json

POLICIES = ("OpenWAM", "GPT_6_Astra_Direct_EEF", "GPT_6_Astra_Codex_EEF")
CAMERAS = {"cam_head", "cam_left_wrist", "cam_right_wrist"}
STATE_KEYS = {
    f"{arm}_{field}"
    for arm in ("left", "right")
    for field in ("arm_joint_state", "ee_joint_state", "ee_pose")
}


def jsonable(value):
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value.tolist() if hasattr(value, "tolist") else value


def check_observation(obs):
    """Reject unexpected channels instead of silently leaking or dropping them."""
    expected = {
        "vision",
        "state",
        "action",
        "instruction",
        "data_format_version",
        "additional_info",
        "env_idx",
    }
    if set(obs) != expected or set(obs["vision"]) != CAMERAS:
        raise ValueError("Unexpected native observation/camera keys")
    if set(obs["additional_info"]) != {"frequency"}:
        raise ValueError("Unexpected observation metadata")
    for name in ("state", "action"):
        if set(obs[name]) - STATE_KEYS:
            raise ValueError("Unexpected privileged state in policy observation")
        for value in obs[name].values():
            if not np.isfinite(np.asarray(value)).all():
                raise ValueError("Nonfinite robot state")
    for camera in obs["vision"].values():
        if set(camera) != {"color", "shape"}:
            raise ValueError("Policy cameras must contain RGB and shape only")
        rgb = camera["color"]
        if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("Expected native RGB uint8 array")
        if tuple(camera["shape"]) != rgb.shape:
            raise ValueError("Camera shape disagrees with RGB")


@contextmanager
def record_policy_io(env, output):
    """Archive existing calls, without adding observations or physics steps."""
    from PIL import Image

    originals = {name: getattr(env, name) for name in ("get_obs_batch", "take_action_batch")}
    saved = {name: env.__dict__.get(name) for name in originals}
    present = {name: name in env.__dict__ for name in originals}
    images = output / "rgb"
    images.mkdir()
    started = time.monotonic()
    with (output / "policy-io.jsonl").open("x", buffering=1) as stream:

        def emit(record):
            record["wall_elapsed_s"] = time.monotonic() - started
            stream.write(json.dumps(jsonable(record), allow_nan=False) + "\n")

        def observations(*args, **kwargs):
            result = originals["get_obs_batch"](*args, **kwargs)
            for obs in result:
                check_observation(obs)
                entry = {k: v for k, v in obs.items() if k != "vision"}
                entry["vision"] = {}
                for name, camera in obs["vision"].items():
                    rgb = np.ascontiguousarray(camera["color"])
                    digest = hashlib.sha256(str(rgb.shape).encode() + rgb.tobytes()).hexdigest()
                    path = images / f"{digest}.png"
                    if not path.exists():
                        Image.fromarray(rgb).save(path)
                    entry["vision"][name] = {
                        "file": f"rgb/{path.name}",
                        "rgb_sha256": digest,
                        "shape": rgb.shape,
                    }
                emit(
                    {"event": "observation", "action_count": list(env.take_action_cnt), "observation": entry}
                )
            return result

        def actions(actions_list, *args, **kwargs):
            emit(
                {
                    "event": "action_requested",
                    "action_count": list(env.take_action_cnt),
                    "actions": deepcopy(actions_list),
                }
            )
            result = originals["take_action_batch"](actions_list, *args, **kwargs)
            emit({"event": "action_applied", "action_count": list(env.take_action_cnt)})
            return result

        env.get_obs_batch, env.take_action_batch = observations, actions
        try:
            yield
        finally:
            for name in originals:
                if present[name]:
                    setattr(env, name, saved[name])
                else:
                    delattr(env, name)


def make_eval_config(root, policy, port, run_id, task="stack_blocks"):
    """Use the official single-environment configuration and processing order."""
    from omegaconf import OmegaConf
    from utils.pipeline_utils import process_config, process_randomization

    evaluation = OmegaConf.load(root / "env_cfg/arx_x5.yml")
    evaluation.update(
        {
            "task_name": task,
            "num_envs": 1,
            "device_id": 0,
            "eval_batch": False,
            "policy_name": policy,
            "additional_info": "infinidojo-development",
            "seed": 0,
            "eval_num": 1,
            "physx_monitor_enabled": False,
        }
    )
    config = OmegaConf.create(
        {k: OmegaConf.load(root / f"env_cfg/{k}/{v}.yml") for k, v in evaluation.config.items()}
    )
    config.task_env = OmegaConf.load(root / f"task/RoboDojo/config/{task}.yml")
    config.eval_cfg = evaluation
    config.deploy_cfg = {
        "policy_name": policy,
        "host": "localhost",
        "port": port,
        "protocol": "ws",
        "evaluation_id": run_id,
        "trial_id": run_id,
        "action_case_id": f"{task}_case",
    }
    config.sim.scene.num_envs = 1
    config = process_randomization(config)
    config = process_config(config, task)[0]
    config.camera.default_frequency = evaluation.observation.collect_freq
    config.sim.seed = [0]
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robodojo-root", type=Path, required=True)
    parser.add_argument("--xpolicylab-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", choices=POLICIES, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--task", choices=("stack_blocks", "general_pickup"))
    parser.add_argument("--policy-service-record", type=Path, help="Ready OpenWAM deployment provenance")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--episode", type=Path)
    inputs.add_argument("--native-layout", type=int)
    parser.add_argument(
        "--observe-only", action="store_true", help="Reset/camera diagnostic; never a policy score"
    )
    from isaaclab.app import AppLauncher

    AppLauncher.add_app_launcher_args(parser)
    args = parser.parse_args()
    root, xpl, output = args.robodojo_root.resolve(), args.xpolicylab_root.resolve(), args.output.resolve()
    source = Path(__file__).resolve().parents[2]
    service = None
    if args.policy == "OpenWAM":
        if args.policy_service_record is None:
            parser.error("OpenWAM needs --policy-service-record from the running service")
        service = json.loads(args.policy_service_record.read_text())
        if (
            service["status"] != "ready"
            or service["robodojo_commit"] != git_revision(root)
            or service["xpolicylab_commit"] != git_revision(xpl)
            or service["adapter_config"]["port"] != args.port
            or service["adapter_config"]["allow_dummy_policy"]
        ):
            raise ValueError("OpenWAM service does not match this evaluation")
    manifest, assets = None, None
    if args.episode:
        manifest, layout, assets = load_episode(args.episode)
        task = manifest["task"]
        if args.task is not None and args.task != task:
            parser.error("Episode task differs from --task")
        if manifest["robodojo_commit"] != git_revision(root):
            raise ValueError("Episode requires a different RoboDojo revision")
        seed = manifest["seed"]
    else:
        task = args.task or "stack_blocks"
        if args.native_layout < 0:
            parser.error("Native layout must be nonnegative")
        seed = args.native_layout
        layout = root / f"Assets/Eval_Layout/RoboDojo/arx_x5/0/{task}_{seed}.json"
    if service is not None and service["adapter_config"]["task_name"] != task:
        raise ValueError("OpenWAM service task differs from this episode")
    layout_data = json.loads(layout.read_text())
    output.mkdir(parents=True, exist_ok=False)
    if service is not None:
        write_json(output / "service-provenance.json", service)
    write_json(output / "layout.json", layout_data)
    run_id = output.name
    os.environ["ROBODOJO_RUN_ID"] = run_id
    os.environ["ROBODOJO_ROOT"] = str(root)
    # Explicit paths select the pinned checkout, not the older host installation.
    sys.path[:0] = [str(root), str(xpl.parent), str(xpl)]
    os.chdir(root)
    files = list((source / "src/infinidojo").glob("*.py"))
    upstream_files = list((xpl / "policy" / args.policy).rglob("*.py"))
    if args.policy == "GPT_6_Astra_Codex_EEF":
        upstream_files += list((xpl / "policy/GPT_6_Astra_Direct_EEF").rglob("*.py"))
        upstream_files += [xpl.parent / name for name in ("codex_bridge.py", "trajectory_recorder.py")]
    for prefix, base, paths in (("infinidojo", source, files), ("policy", xpl.parent, upstream_files)):
        for path in paths:
            target = output / "source" / prefix / path.relative_to(base)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    write_json(
        output / "provenance.json",
        {
            "format": "infinidojo.policy-evaluation.v1",
            "development_only": True,
            "infinidojo_commit": git_revision(source),
            "robodojo_commit": git_revision(root),
            "xpolicylab_commit": git_revision(xpl),
            "policy": args.policy,
            "policy_service_sha256": sha256(output / "service-provenance.json") if service else None,
            "task": task,
            "instance_source": "generated" if manifest else "native",
            "reset_seed": seed,
            "episode_manifest": manifest,
            "layout_sha256": sha256(layout),
            "observe_only": args.observe_only,
            "official_leaderboard_reproduction": False,
            "source_sha256": {
                str(p.relative_to(output)): sha256(p) for p in (output / "source").rglob("*.py")
            },
            "policy_environment": {
                k: v
                for k, v in os.environ.items()
                if k
                in {
                    "L4_INSPECT_MODEL",
                    "L4_INSPECT_PLANNER",
                    "L4_INSPECT_REASONING_EFFORT",
                    "L4_INSPECT_DEPTH",
                    "L4_INSPECT_IMAGE_HORIZON",
                    "L4_INSPECT_KEEP_ALL_IMAGES",
                    "L4_INSPECT_MAX_LLM_CALLS",
                    "CODEX_PLANNER_TIMEOUT_S",
                }
            },
            "reference_inputs_to_policy": False,
        },
    )
    from env.camera_manager.capture.render_sync import add_zero_delay_kit_args

    add_zero_delay_kit_args(args)
    app = AppLauncher(args).app
    env = None
    started = time.monotonic()
    try:
        from omegaconf import OmegaConf
        from src.eval_client.eval_env import create_eval_env

        from infinidojo.native import install_reset_cleanup
        from infinidojo.native_stack import install_asset_overlay

        config = make_eval_config(root, args.policy, args.port, run_id, task)
        write_json(output / "config.json", OmegaConf.to_container(config, resolve=True))
        if assets is not None:
            install_asset_overlay(root, output, assets)
        env = create_eval_env(config, app, resume_state={"save_dir": str(output / "native")})
        # Redirect output and the selected input only. Native reset, action
        # interpolation, observation, reward and episode termination stay intact.
        env.resume_manifest_path = lambda: str(output / "native-resume.json")
        env.seed_manager.seed_info[seed] = {"scene_layout": str(output / "layout.json")}
        install_reset_cleanup(env)
        env.reset(seed=[seed])
        write_json(
            output / "loaded-layout.json", env.scene_manager.layout_manager.get_layout_records(0, "Rigid")
        )
        with record_policy_io(env, output):
            if args.observe_only:
                env.get_obs()
            else:
                env.run_eval()
        result = {
            "status": "observation_diagnostic" if args.observe_only else "evaluated",
            "native_policy_action_limit": env.step_lim,
            "actions": env.take_action_cnt[0],
            "physics_dt_s": env.dt,
            "observation_frequency_hz": env.obs_manager.collect_freq,
            "native_result": None if args.observe_only else env.eval_result,
            "wall_seconds": time.monotonic() - started,
        }
        if not args.observe_only and (env.eval_result["eval_time"] != 1 or not all(env.end_flag)):
            raise RuntimeError("Native evaluator did not finish exactly one episode")
        write_json(output / "result.json", result)
        print(json.dumps(result), flush=True)
    except BaseException as exc:
        write_json(
            output / "error.json",
            {"type": type(exc).__name__, "message": str(exc), "status": "execution_error_not_a_policy_score"},
        )
        raise
    finally:
        if env is not None:
            env.model_client.close()
            env.close()
        app.close()


if __name__ == "__main__":
    main()
