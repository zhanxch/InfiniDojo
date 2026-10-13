"""Audit completed policy runs without turning missing evidence into failures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from infinidojo.io import sha256, write_json


def read_json(path):
    return json.loads(path.read_text()) if path.is_file() else None


def audit_io(folder, *, allow_partial=False):
    rows = [json.loads(line) for line in (folder / "policy-io.jsonl").read_text().splitlines()]
    applied, requested, observations, ignored = 0, 0, 0, 0
    pending = None
    images = {}
    instructions = set()
    for row in rows:
        count = row["action_count"]
        if len(count) != 1 or not isinstance(count[0], int) or count[0] < 0:
            raise ValueError("Invalid single-environment action count")
        count = count[0]
        if row["event"] == "action_requested":
            if pending is not None or count != applied:
                raise ValueError("Action requests are not contiguous")
            pending = count
            requested += 1
        elif row["event"] == "action_applied":
            if pending is None or count - pending not in (0, 1):
                raise ValueError("Missing request or invalid action-count transition")
            ignored += count == pending
            applied, pending = count, None
        elif row["event"] == "observation":
            # Native terminal capture can occur inside take_action_batch, before
            # the action_applied audit event, after the counter has advanced.
            if count not in ({applied, applied + 1} if pending is not None else {applied}):
                raise ValueError("Observation count does not match execution")
            observations += 1
            obs = row["observation"]
            instructions.add(obs["instruction"])
            for camera in obs["vision"].values():
                name = camera["file"]
                item = (camera["rgb_sha256"], tuple(camera["shape"]))
                if name in images and images[name] != item:
                    raise ValueError("Conflicting image identities")
                images[name] = item
        else:
            raise ValueError("Unknown policy audit event")
    if pending is not None and not allow_partial:
        raise ValueError("Final action did not return")
    for name, (digest, shape) in images.items():
        path = (folder / name).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError("Observation image escapes run directory")
        rgb = np.asarray(Image.open(path))
        actual = hashlib.sha256(str(rgb.shape).encode() + rgb.tobytes()).hexdigest()
        if rgb.dtype != np.uint8 or rgb.shape != shape or actual != digest:
            raise ValueError(f"Observation content changed: {name}")
    return {
        "actions_executed": applied,
        "action_requests": requested,
        "ignored_after_end": ignored,
        "observations": observations,
        "unique_rgb_images": len(images),
        "instructions": sorted(instructions),
        "unfinished_action_request": pending is not None,
    }


def audit_codex_calls(folder, trace, *, allow_partial=False):
    calls = sorted((folder / "decisions").glob("call-*"))
    latency, tokens = 0.0, {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
    successful, failures = 0, []
    for call in calls:
        execution = read_json(call / "execution.json")
        invocation = read_json(call / "invocation.json")
        if not invocation or invocation["requested_model"] != "gpt-6-astra":
            raise ValueError("Unexpected requested model")
        for image in invocation["images"]:
            # Stored invocation paths are absolute; portable audit uses the
            # archived filename inside its own call directory.
            if sha256(call / Path(image["path"]).name) != image["sha256"]:
                raise ValueError("Model input JPEG changed")
        events = [json.loads(line) for line in (call / "events.jsonl").read_text().splitlines()]
        completed = [x for x in events if x.get("type") == "turn.completed"]
        if any(x.get("item", {}).get("type") not in (None, "agent_message", "reasoning") for x in events):
            raise ValueError("Model call invoked an extra tool")
        errors = [x for x in events if x.get("type") in ("turn.failed", "error")]
        ok = (
            execution is not None
            and execution["returncode"] == 0
            and (call / "completion.json").is_file()
            and len(completed) == 1
            and not errors
        )
        if not ok and not allow_partial:
            raise ValueError("A recorded model call did not complete")
        successful += bool(ok)
        if not ok:
            failures.append({"call": call.name, "execution": execution, "events": errors})
        if execution:
            latency += execution["latency_s"]
        for event in completed:
            for key in tokens:
                tokens[key] += event.get("usage", {}).get(key, 0)
    if successful != trace["llm_calls"]:
        raise ValueError("Transcript and successful model-call count differ")
    return {
        "model_calls": successful,
        "model_call_attempts": len(calls),
        "unsuccessful_calls": failures,
        "model_latency_seconds": latency,
        "cli_tokens": tokens,
        "token_scope": "Usage reported by completed CLI turns; failed-turn billing is unknown.",
        "model_identity": "Requested gpt-6-astra; serving snapshot is not exposed by this transport.",
    }


def audit_run(folder, returncode):
    result = read_json(folder / "result.json")
    error = read_json(folder / "error.json")
    provenance = read_json(folder / "provenance.json")
    report = {"status": "incomplete", "returncode": returncode, "native_success": None, "native_score": None}
    if provenance:
        report.update({k: provenance[k] for k in ("policy", "task", "instance_source", "reset_seed")})
    if error:
        report.update(status="execution_error", error=error)
        # Preserve incurred work without assigning a task outcome to an aborted
        # episode. A zero process code is insufficient: Kit shutdown can exit
        # before Python propagates an evaluation exception.
        diagnostics = {}
        if (folder / "policy-io.jsonl").is_file():
            try:
                diagnostics["io"] = audit_io(folder, allow_partial=True)
            except (ValueError, KeyError, OSError) as exc:
                diagnostics["io_audit_error"] = str(exc)
        traces = list(folder.glob("traces/*/*/l4_inspect_transcript.json"))
        if len(traces) == 1 and (folder / "decisions").is_dir():
            try:
                diagnostics["model"] = audit_codex_calls(folder, read_json(traces[0]), allow_partial=True)
            except (ValueError, KeyError, OSError) as exc:
                diagnostics["model_audit_error"] = str(exc)
        report["partial_execution"] = diagnostics
        return report
    if not result:
        return report
    if result["status"] == "observation_diagnostic":
        report["status"] = "diagnostic_not_policy_evaluation"
        return report
    if result["status"] != "evaluated":
        raise ValueError("Unknown evaluation result status")
    if not provenance:
        raise ValueError("Completed score is missing provenance")
    for name, digest in provenance["source_sha256"].items():
        path = (folder / name).resolve()
        if not path.is_relative_to(folder.resolve()) or sha256(path) != digest:
            raise ValueError("Archived evaluation source changed")
    if sha256(folder / "layout.json") != provenance["layout_sha256"]:
        raise ValueError("Run layout digest does not match its recorded input")
    if provenance["policy"] == "OpenWAM" and (
        not provenance.get("policy_service_sha256")
        or sha256(folder / "service-provenance.json") != provenance["policy_service_sha256"]
    ):
        raise ValueError("OpenWAM score needs the recorded weight/deployment provenance")
    native = read_json(folder / "native/_result.json")
    if native is None or native != result["native_result"] or native["eval_time"] != 1:
        raise ValueError("Harness result differs from the complete native score")
    detail = list(native["details"].values())
    if len(detail) != 1 or detail[0]["layout_id"] != provenance["reset_seed"]:
        raise ValueError("Native result identifies a different episode")
    io = audit_io(folder)
    if (
        io["actions_executed"] != result["actions"]
        or result["actions"] > result["native_policy_action_limit"]
    ):
        raise ValueError("Action evidence does not match the result/budget")
    traces = list(folder.glob("traces/*/*/l4_inspect_transcript.json"))
    llm = {}
    if provenance["policy"] == "GPT_6_Astra_Codex_EEF":
        if len(traces) != 1:
            raise ValueError("Astra evaluation needs exactly one completed transcript")
        trace = read_json(traces[0])
        if trace["in_progress"] or trace["failure_kind"] == "infrastructure":
            raise ValueError("An incomplete/infrastructure transcript is not a policy score")
        if trace["official_success"] != [detail[0]["success"]]:
            raise ValueError("Transcript outcome disagrees with native result")
        llm = audit_codex_calls(folder, trace)
        cap = int(provenance["policy_environment"]["L4_INSPECT_MAX_LLM_CALLS"])
        if llm["model_calls"] > cap:
            raise ValueError("Model-call budget exceeded")
        llm.update(termination_reason=trace["termination_reason"], failure_kind=trace["failure_kind"])
    report.update(
        status="evaluated" if returncode == 0 else "score_available_process_exit_unverified",
        native_success=detail[0]["success"],
        native_score=native["score"],
        native_score_scale=100,
        evaluator_wall_seconds=result["wall_seconds"],
        io=io,
        **llm,
    )
    return report


def compare_run_conditions(first, second):
    """Check shared evaluation settings; instance layouts intentionally differ."""
    provenance = [read_json(folder / "provenance.json") for folder in (first, second)]
    configs = [read_json(folder / "config.json") for folder in (first, second)]
    if any(value is None for value in (*provenance, *configs)):
        return {"status": "missing_condition_metadata", "checks": {}}
    a, b = provenance
    checks = {
        key: a[key] == b[key]
        for key in ("policy", "task", "robodojo_commit", "xpolicylab_commit", "policy_environment")
    }
    checks["instance_sources"] = a["instance_source"] == "native" and b["instance_source"] == "generated"
    if a["policy"] == "OpenWAM":
        checks["policy_deployment"] = bool(a.get("policy_service_sha256")) and (
            a.get("policy_service_sha256") == b.get("policy_service_sha256")
        )
    for config in configs:
        # Output/run identity has no policy or simulation semantics.
        for key in ("evaluation_id", "trial_id"):
            config.get("deploy_cfg", {}).pop(key, None)
    checks["evaluation_configuration"] = configs[0] == configs[1]
    required = {
        f"source/infinidojo/src/infinidojo/{name}.py"
        for name in ("policy_eval", "episode", "native", "native_stack")
    }
    required |= {name for p in provenance for name in p["source_sha256"] if name.startswith("source/policy/")}
    checks["runner_and_adapter_sources"] = all(
        name in a["source_sha256"]
        and name in b["source_sha256"]
        and a["source_sha256"][name] == b["source_sha256"][name]
        for name in required
    )
    return {
        "status": "matched" if all(checks.values()) else "mismatch",
        "checks": checks,
        "scope": "Same policy/evaluator settings; different instance distributions, no causal attribution.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pair = args.pair.resolve()
    manifest = read_json(pair / "pair.json")
    reports = []
    runs = {run["condition"]: run for run in manifest["runs"]}
    for condition in manifest["conditions"]:
        run = runs.get(condition, {})
        # A saved 'running' state is not proof a process is alive. This reader
        # simply withholds a finalized report until the launcher records exit.
        if run.get("status") != "process_exited":
            reports.append({"condition": condition, "status": "not_finalized", "native_success": None})
            continue
        try:
            report = audit_run(pair / condition, run["returncode"])
        except (ValueError, KeyError, OSError) as exc:
            report = {"status": "audit_error", "error": str(exc), "native_success": None}
        reports.append({"condition": condition, **report})
    write_json(
        args.output,
        {
            "development_only": True,
            "requested": len(manifest["conditions"]),
            "pair_sha256": sha256(pair / "pair.json"),
            "condition_comparison": compare_run_conditions(pair / "native", pair / "generated"),
            "runs": reports,
        },
    )


if __name__ == "__main__":
    main()
