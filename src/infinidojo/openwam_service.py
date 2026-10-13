"""Pinned OpenWAM service with explicit CPU text-encoder placement.

The weight values, precision, action representation and denoising configuration
stay upstream. This deployment variant is recorded, not assumed numerically
equivalent to all-GPU inference. Upstream files are never modified.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

from infinidojo.io import git_revision, sha256, write_json

XPL_REVISION = "f6e309e8ece20bfa33c0e5d7b05a5f3287522757"
PATCH_INPUTS = {
    "openwam/model/video_backbone/base.py": "6cfec7ef3404816f96984d72afba9c5aa12f6d403f6723cce1027eb4f85d9756",
    "openwam/model/video_backbone/wan_backbone.py": "b1f10e03fcae3ba95a1e49ab17979806828026fba68a65754ffbb2cbf9d72c3d",
    "openwam/model/video_backbone/wan/encode.py": "c3239d921fbf3e78c4c4253b0db8141e3c45d593b6e9195ad540e82e9f3e89c3",
}


def move_with_cpu_text(backbone, dtype, device, original):
    """Exclude the encoder before GPU movement, avoiding a transient full load."""
    encoder = backbone.text_encoder
    backbone.text_encoder = None
    try:
        original(backbone, dtype, device)
    finally:
        # Restore module registration even if another component cannot move.
        backbone.text_encoder = encoder
    encoder.to(dtype=dtype, device="cpu")


def encode_on_encoder_device(original, prompts, *, tokenizer, text_encoder, device):
    context, lengths = original(
        prompts,
        tokenizer=tokenizer,
        text_encoder=text_encoder,
        device=next(text_encoder.parameters()).device,
    )
    return context.to(device), lengths.to(device)


def install_cpu_text(root):
    for name, digest in PATCH_INPUTS.items():
        if sha256(root / name) != digest:
            raise ValueError(f"OpenWAM deployment patch needs the pinned source: {name}")
    from openwam.model.video_backbone.wan import encode
    from openwam.model.video_backbone.wan_backbone import WanBase

    if Path(inspect.getfile(WanBase)).resolve() != root / "openwam/model/video_backbone/wan_backbone.py":
        raise ValueError("Imported OpenWAM is not the selected checkout")
    move, text = WanBase.set_dtype_device, encode.encode_text

    def patched_move(self, dtype, device):
        move_with_cpu_text(self, dtype, device, move)

    def patched_encode(prompts, *, tokenizer, text_encoder, device):
        return encode_on_encoder_device(
            text, prompts, tokenizer=tokenizer, text_encoder=text_encoder, device=device
        )

    WanBase.set_dtype_device = patched_move
    encode.encode_text = patched_encode


def load_recorded_observation(path):
    """Load one audited native RGB observation for an unscored inference probe."""
    from PIL import Image

    from infinidojo.policy_eval import check_observation

    with path.open() as stream:
        record = next(json.loads(line) for line in stream if json.loads(line)["event"] == "observation")
    obs = record["observation"]
    for name, image in obs["vision"].items():
        image_path = (path.parent / image["file"]).resolve()
        if not image_path.is_relative_to(path.parent.resolve()):
            raise ValueError("Recorded observation image is outside its run directory")
        rgb = np.array(Image.open(image_path))
        digest = hashlib.sha256(str(rgb.shape).encode() + rgb.tobytes()).hexdigest()
        if digest != image["rgb_sha256"]:
            raise ValueError("Recorded observation RGB content changed")
        obs["vision"][name] = {"color": rgb, "shape": rgb.shape}
    check_observation(obs)
    return obs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xpolicylab-root", type=Path, required=True)
    parser.add_argument("--robodojo-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=6124)
    parser.add_argument("--task", choices=("stack_blocks", "general_pickup"), default="stack_blocks")
    parser.add_argument(
        "--seed",
        type=int,
        choices=(42,),
        default=42,
        help="Pinned to the upstream adapter's default inference seed",
    )
    parser.add_argument("--probe-observation", type=Path, help="One real inference, no simulation score")
    args = parser.parse_args()
    xpl, root, ckpt = args.xpolicylab_root.resolve(), args.robodojo_root.resolve(), args.checkpoint.resolve()
    output = args.output.resolve()
    if git_revision(xpl) != XPL_REVISION:
        raise ValueError("OpenWAM service requires the declared XPolicyLab revision")
    weights = sorted(ckpt.glob("checkpoint_step_*.safetensors"))
    if len(weights) != 1 or weights[0].name != "checkpoint_step_60000.safetensors":
        raise ValueError("Expected the single released step-60000 RoboDojo checkpoint")
    output.mkdir(parents=True, exist_ok=False)
    # Upstream resolves env_cfg next to XPolicyLab. A local read-only overlay
    # selects this RoboDojo revision without editing either dependency tree.
    runtime = output / "runtime"
    runtime.mkdir()
    (runtime / "XPolicyLab").symlink_to(xpl, target_is_directory=True)
    (runtime / "env_cfg").symlink_to(root / "env_cfg", target_is_directory=True)
    openwam = xpl / "policy/OpenWAM/OpenWAM"
    sys.path[:0] = [str(runtime), str(runtime / "XPolicyLab"), str(openwam)]
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import torch
    import yaml

    torch.set_num_threads(8)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    config = yaml.safe_load((xpl / "policy/OpenWAM/deploy.yml").read_text())
    config.update(
        {
            "host": "localhost",
            "port": args.port,
            "env_cfg_type": "arx_x5",
            "bench_name": "RoboDojo",
            "task_name": args.task,
            "seed": args.seed,
            "ckpt_dir": str(ckpt),
            "openwam_root": str(openwam),
            "device": "cuda",
            "action_type": "ee",
            "replan_steps": None,
            "allow_dummy_policy": False,
        }
    )
    record = {
        "status": "loading",
        "variant": "OpenWAM-step60000-cpu-text-bf16",
        "xpolicylab_commit": git_revision(xpl),
        "robodojo_commit": git_revision(root),
        "deployment_source_sha256": sha256(Path(__file__)),
        "patch_inputs": PATCH_INPUTS,
        "adapter_source_sha256": {
            name: sha256(xpl / name)
            for name in (
                "policy/OpenWAM/model.py",
                "policy/OpenWAM/deploy.py",
                "policy/OpenWAM/deploy.yml",
                "utils/process_data.py",
            )
        },
        "robot_configuration_sha256": {
            name: sha256(root / "env_cfg" / name) for name in ("arx_x5.yml", "robot/_robot_info.json")
        },
        "checkpoint_sha256": {
            str(p.relative_to(ckpt)): sha256(p) for p in sorted(ckpt.rglob("*")) if p.is_file()
        },
        "adapter_config": config,
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "cuda_device": torch.cuda.get_device_name(),
        "cpu_threads": torch.get_num_threads(),
        "note": "CPU text encoder; unchanged BF16 checkpoint and upstream action/denoising configuration.",
    }
    write_json(output / "service.json", record)
    started = time.monotonic()
    try:
        install_cpu_text(openwam)
        from client_server.ws.model_server import PolicyServer, PolicyServerConfig
        from XPolicyLab.policy.OpenWAM.model import Model

        model = Model(config)
        from omegaconf import OmegaConf

        write_json(
            # The checkpoint retains an unused Hydra training-output expression
            # (${now:...}). Resolving it requires a training-only resolver and
            # must not prevent inference or invent a deployment-time value.
            output / "engine-config.json", OmegaConf.to_container(model._engine.cfg, resolve=False)
        )
        architecture = model._engine.architecture
        parameter_bytes = Counter()
        for name, value in architecture.named_parameters():
            component = "text_encoder" if ".text_encoder." in name else "other"
            parameter_bytes[f"{component}:{value.device}:{value.dtype}"] += (
                value.numel() * value.element_size()
            )
        if any(p.device.type != "cpu" for p in architecture.video_backbone.text_encoder.parameters()):
            raise RuntimeError("Text encoder did not stay on CPU")
        record.update(
            {
                "status": "ready",
                "load_seconds": time.monotonic() - started,
                "parameter_bytes": dict(parameter_bytes),
                "cuda_allocated_bytes": torch.cuda.memory_allocated(),
                "cuda_peak_bytes": torch.cuda.max_memory_allocated(),
            }
        )
        write_json(output / "service.json", record)
        print(
            json.dumps({k: record[k] for k in ("status", "parameter_bytes", "cuda_peak_bytes")}), flush=True
        )
        if args.probe_observation:
            probe = args.probe_observation.resolve()
            model.reset()
            model.update_obs(load_recorded_observation(probe))
            before = time.monotonic()
            actions = model.get_action()
            from infinidojo.policy_eval import jsonable

            write_json(
                output / "probe.json",
                {
                    "status": "unscored_real_inference",
                    "input_sha256": sha256(probe),
                    "actions": jsonable(actions),
                    "seconds": time.monotonic() - before,
                    "cuda_peak_bytes": torch.cuda.max_memory_allocated(),
                },
            )
        else:
            server = PolicyServer(model, PolicyServerConfig(host="localhost", port=args.port))
            asyncio.run(server.serve_forever())
    except BaseException as exc:
        write_json(output / "error.json", {"type": type(exc).__name__, "message": str(exc)})
        raise


if __name__ == "__main__":
    main()
