"""Cross-module guards found during integration review."""
import hashlib
from pathlib import Path

import pytest
from scipy.stats import binom

from scripts import analyze_recovery_costs as costs, run_recovery_replay as replay
from src import budgeted_recovery as recovery, recovery_calibration as calibration
from scripts import measure_workflow_context as context


def test_replay_and_cost_reports_cannot_cover_inputs():
    for path in (replay.ROOT, replay.ROOT.parent, replay.ROOT / "experiments/recovery-development", replay.ROOT / "src"):
        with pytest.raises(ValueError):
            replay.paths_for(path)
        with pytest.raises(ValueError):
            costs.validate_output_dir(path)


def test_calibration_digest_pins_actual_module():
    assert calibration.CALIBRATION_CODE_DIGEST == hashlib.sha256(Path(calibration.__file__).read_bytes()).hexdigest()
    assert calibration.exact_binomial_tail_p_value(successes=3000, n=10000, null_p=0.35) == pytest.approx(binom.cdf(3000, 10000, 0.35))


def test_holdout_rejects_same_group_even_with_different_trajectory_ids():
    candidate = calibration.StoppingPolicyCandidate("policy-v1", 0.5)
    rows = [calibration.TrajectoryScore("new-id", "same-template", 0.1, False, "policy-v1")]
    with pytest.raises(ValueError, match="partition groups overlap"):
        calibration.holdout_calibration_audit(
            candidate, [candidate], rows, target_risk=0.1, family_error=0.05,
            learn_trajectory_ids=frozenset({"old-id"}), learn_partition_groups=frozenset({"same-template"}),
        )


def test_uncertain_supervisor_failure_keeps_conservative_charge():
    class FailingSupervisor:
        def reserve_token_credits(self):
            return 0.2

        def decide(self, observation, *, ledger):
            raise TimeoutError("request may have been billed")

    manifest, key = recovery.build_manifest("wf", [("r", "measurement", "42")])
    archive = recovery.TrustedArchive.build("wf", manifest, {"r": "42"}, signing_key=key)
    case = recovery.EvidenceCase("wf", "Check measurement", "Publish", (), manifest)
    result = recovery.run_trajectory(case, archive, recovery.AcquisitionPolicyName.FULL, recovery.RecoveryBudget(token_credits=1), FailingSupervisor())
    assert result.terminal == "error"
    assert result.costs["token_credits"] == pytest.approx(0.2)
    assert result.costs["pending_token_reserve"] == 0


def test_zero_holdout_count_keeps_all_groups_in_learning():
    assert set(replay.deterministic_group_split(["a", "b"], seed=0, holdout_count=0).values()) == {"learn"}


def test_context_lower_bound_retains_uncertainty_with_all_successes():
    assert context.clopper_pearson_lower_bound(successes=1, n=1, delta=0.05) == pytest.approx(0.05)
    assert context.clopper_pearson_lower_bound(successes=10, n=10, delta=0.05) == pytest.approx(0.05 ** 0.1)


def test_trace_extraction_does_not_duplicate_history_and_trajectory():
    raw = b'{"history":[{"content":"duplicate"}],"trajectory":[{"action":"ls","observation":"a.py","response":"duplicate"}]}'
    text = context.extract_transcript_text(raw=raw, suffix=".traj", extraction={"strategy": "trajectory_fields", "fields": ["action", "observation"]}, extraction_path=None)
    assert "duplicate" not in text
    assert "ls" in text and "a.py" in text


def test_hf_context_count_requests_token_ids_instead_of_encoding_dict():
    class FakeHF:
        def apply_chat_template(self, messages, **kwargs):
            assert kwargs["return_dict"] is False
            return [1, 2, 3, 4, 5]

    assert context.count_input_tokens(context.HFChatTokenizer(FakeHF()), "one prompt") == 5
