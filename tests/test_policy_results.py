import json

import numpy as np
import pytest
from PIL import Image
from test_policy_eval import observation

from infinidojo.io import sha256, write_json
from infinidojo.policy_eval import record_policy_io
from infinidojo.policy_results import audit_codex_calls, audit_io, audit_run, compare_run_conditions


class Environment:
    def __init__(self):
        self.take_action_cnt = [0]

    def get_obs_batch(self):
        return [observation()]

    def take_action_batch(self, actions):
        if self.take_action_cnt == [0]:
            self.take_action_cnt = [1]
            # Native termination captures the final observation inside action.
            self.get_obs_batch()


def recorded_io(folder):
    env = Environment()
    with record_policy_io(env, folder):
        env.get_obs_batch()
        env.take_action_batch([{"right_ee_joint_state": [1]}])
        env.take_action_batch([{"right_ee_joint_state": [1]}])


def test_terminal_capture_and_ignored_requests_do_not_inflate_action_count(tmp_path):
    recorded_io(tmp_path)
    result = audit_io(tmp_path)
    assert result["actions_executed"] == 1
    assert result["action_requests"] == 2 and result["ignored_after_end"] == 1
    assert result["observations"] == 2
    path = next((tmp_path / "rgb").glob("*.png"))
    Image.fromarray(np.zeros((1, 2, 3), dtype=np.uint8)).save(path)
    with pytest.raises(ValueError, match="content changed"):
        audit_io(tmp_path)


def test_incomplete_diagnostic_and_infrastructure_are_not_task_failures(tmp_path):
    assert audit_run(tmp_path, None)["native_success"] is None
    write_json(tmp_path / "result.json", {"status": "observation_diagnostic"})
    assert audit_run(tmp_path, 0)["status"] == "diagnostic_not_policy_evaluation"
    write_json(tmp_path / "error.json", {"type": "InfrastructureFailure", "message": "service timeout"})
    result = audit_run(tmp_path, 1)
    assert result["status"] == "execution_error" and result["native_success"] is None


def test_failed_episode_retains_work_and_unreported_billing_is_not_zero(tmp_path):
    recorded_io(tmp_path)
    trace = {"llm_calls": 1}
    write_json(tmp_path / "traces/task/episode/l4_inspect_transcript.json", trace)
    write_json(tmp_path / "error.json", {"type": "InfrastructureFailure", "message": "usage limit"})
    for index, succeeded in enumerate((True, False), start=1):
        call = tmp_path / "decisions" / f"call-{index:04d}"
        write_json(call / "invocation.json", {"requested_model": "gpt-6-astra", "images": []})
        write_json(call / "execution.json", {"returncode": 0 if succeeded else 1, "latency_s": 2})
        if succeeded:
            write_json(call / "completion.json", {"action": "move_eef"})
            event = {"type": "turn.completed", "usage": {"input_tokens": 40, "output_tokens": 10}}
        else:
            event = {"type": "turn.failed", "error": {"message": "usage limit"}}
        (call / "events.jsonl").write_text(json.dumps(event) + "\n")
    report = audit_run(tmp_path, 0)
    assert report["native_success"] is None and report["native_score"] is None
    assert report["partial_execution"]["io"]["actions_executed"] == 1
    model = report["partial_execution"]["model"]
    assert model["model_calls"] == 1 and model["model_call_attempts"] == 2
    assert model["model_latency_seconds"] == 4 and model["cli_tokens"]["input_tokens"] == 40
    assert "unknown" in model["token_scope"]
    assert model["unsuccessful_calls"][0]["call"] == "call-0002"
    with pytest.raises(ValueError, match="did not complete"):
        audit_codex_calls(tmp_path, trace)


def test_aborted_action_is_preserved_but_cannot_certify_completed_run(tmp_path):
    rows = [{"event": "action_requested", "action_count": [0]}]
    (tmp_path / "policy-io.jsonl").write_text(json.dumps(rows[0]) + "\n")
    partial = audit_io(tmp_path, allow_partial=True)
    assert partial["actions_executed"] == 0 and partial["unfinished_action_request"]
    with pytest.raises(ValueError, match="did not return"):
        audit_io(tmp_path)


def test_completed_native_failure_counts_and_score_disagreement_fails(tmp_path):
    recorded_io(tmp_path)
    write_json(tmp_path / "layout.json", {"fixture": "test"})
    write_json(tmp_path / "service-provenance.json", {"fixture": "fixed deployment"})
    (tmp_path / "frozen.py").write_text("# archived test source\n")
    write_json(
        tmp_path / "provenance.json",
        {
            "policy": "OpenWAM",
            "policy_service_sha256": sha256(tmp_path / "service-provenance.json"),
            "task": "stack_blocks",
            "instance_source": "native",
            "reset_seed": 0,
            "layout_sha256": sha256(tmp_path / "layout.json"),
            "source_sha256": {"frozen.py": sha256(tmp_path / "frozen.py")},
        },
    )
    native = {
        "eval_time": 1,
        "score": 15,
        "success_rate": 0,
        "details": {"0": {"layout_id": 0, "success": False, "score": 0.15}},
    }
    write_json(tmp_path / "native/_result.json", native)
    result = {
        "status": "evaluated",
        "actions": 1,
        "native_policy_action_limit": 550,
        "native_result": native,
        "wall_seconds": 2,
    }
    write_json(tmp_path / "result.json", result)
    report = audit_run(tmp_path, 0)
    assert report["status"] == "evaluated" and report["native_success"] is False
    assert report["native_score"] == 15
    assert audit_run(tmp_path, -6)["status"] == "score_available_process_exit_unverified"
    result["native_result"]["details"]["0"]["success"] = True
    write_json(tmp_path / "result.json", result)
    with pytest.raises(ValueError, match="differs"):
        audit_run(tmp_path, 0)
    # Preserve a valid JSON artifact for failure diagnosis, not just an exception.
    assert json.loads((tmp_path / "native/_result.json").read_text())["success_rate"] == 0


def test_pair_comparison_allows_run_identity_but_rejects_changed_control_settings(tmp_path):
    first, second = tmp_path / "native", tmp_path / "generated"
    for folder in (first, second):
        write_json(
            folder / "provenance.json",
            {
                "policy": "test",
                "task": "stack_blocks",
                "robodojo_commit": "fixed",
                "xpolicylab_commit": "fixed",
                "policy_environment": {},
                "instance_source": folder.name,
                "source_sha256": {
                    f"source/infinidojo/src/infinidojo/{name}.py": "same"
                    for name in ("policy_eval", "episode", "native", "native_stack")
                },
            },
        )
        write_json(folder / "config.json", {"deploy_cfg": {"trial_id": folder.name}, "frequency": 25})
    assert compare_run_conditions(first, second)["status"] == "matched"
    write_json(second / "config.json", {"deploy_cfg": {"trial_id": second.name}, "frequency": 50})
    result = compare_run_conditions(first, second)
    assert result["status"] == "mismatch" and not result["checks"]["evaluation_configuration"]
