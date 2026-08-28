"""End-to-end pipeline test against the simulation clients.

Drives pipeline.tick() until every fixture issue reaches a terminal state and
asserts the four demo narratives:
  #1 review rejects once -> bounded rework -> re-review approves -> fixed
  #2 clean fix -> review approves -> fixed
  #3 blocked report -> escalated (no PR forced)
  #4 session goes idle once -> nudged -> recovers -> fixed
"""
import tempfile
import unittest
from pathlib import Path

from orchestrator import config, pipeline
from orchestrator import state as st
from orchestrator.sim import SimDevin, SimGithub


class PipelineE2ETest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_state_path = config.STATE_PATH
        config.STATE_PATH = Path(self._tmp.name) / "state.json"
        self.gh, self.devin = SimGithub(), SimDevin()
        self.state = {"issues": {}, "meta": {"created_at": st.utcnow(), "ticks": 0}}

    def tearDown(self):
        config.STATE_PATH = self._old_state_path
        self._tmp.cleanup()

    def _run_to_completion(self, max_ticks=80):
        for _ in range(max_ticks):
            pipeline.tick(self.gh, self.devin, self.state, log=lambda *_: None)
            issues = self.state["issues"]
            if issues and all(e["state"] in st.TERMINAL_STATES
                              for e in issues.values()):
                return
        self.fail("pipeline did not reach terminal state within tick budget")

    def test_all_fixture_issues_reach_expected_terminal_states(self):
        self._run_to_completion()
        got = {k: e["state"] for k, e in self.state["issues"].items()}
        self.assertEqual(got, {
            "1": st.DONE_FIXED, "2": st.DONE_FIXED,
            "3": st.DONE_ESCALATED, "4": st.DONE_FIXED,
        })

    def test_rework_loop_is_exercised_and_bounded(self):
        self._run_to_completion()
        e1 = self.state["issues"]["1"]
        self.assertEqual(e1["rework_count"], 1)
        self.assertLessEqual(e1["rework_count"], config.POLICY["max_reworks"])
        # rework findings were actually routed back to the work session
        self.assertTrue(any("independent review" in m.lower()
                            for _, m in self.devin.messages))

    def test_blocked_session_gets_nudged_not_abandoned(self):
        self._run_to_completion()
        self.assertTrue(any("appears blocked" in m
                            for _, m in self.devin.messages))
        self.assertEqual(self.state["issues"]["4"]["state"], st.DONE_FIXED)

    def test_escalation_carries_its_evidence(self):
        self._run_to_completion()
        out = self.state["issues"]["3"]["work_output"]
        self.assertEqual(out["outcome"], "blocked")
        self.assertTrue(out["blocking_reason"])
        self.assertTrue(out["mitigation"])

    def test_every_terminal_issue_was_reported_to_github(self):
        self._run_to_completion()
        commented_on = {n for n, _ in self.gh.comments}
        self.assertEqual(commented_on, {1, 2, 3, 4})

    def test_concurrency_cap_is_respected_every_tick(self):
        for _ in range(80):
            pipeline.tick(self.gh, self.devin, self.state, log=lambda *_: None)
            live = sum(1 for e in self.state["issues"].values()
                       if e["state"] in st.ACTIVE_STATES)
            self.assertLessEqual(live, config.MAX_CONCURRENT_SESSIONS)
            if self.state["issues"] and all(
                    e["state"] in st.TERMINAL_STATES
                    for e in self.state["issues"].values()):
                break


class GateInvariantTest(unittest.TestCase):
    """Suspicion-lane checks: wrong-but-not-erroring values must be caught."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_state_path = config.STATE_PATH
        config.STATE_PATH = Path(self._tmp.name) / "state.json"
        self.gh, self.devin = SimGithub(), SimDevin()
        self.state = {"issues": {}, "meta": {"created_at": st.utcnow(), "ticks": 0}}

    def tearDown(self):
        config.STATE_PATH = self._old_state_path
        self._tmp.cleanup()

    def _entry(self):
        pipeline.tick(self.gh, self.devin, self.state, log=lambda *_: None)
        key = sorted(self.state["issues"])[0]
        return key, self.state["issues"][key]

    def test_fixed_claim_without_pr_goes_to_attention(self):
        key, entry = self._entry()
        detail = {"structured_output": {"outcome": "fixed", "summary": "s",
                                        "pr_url": None}}
        pipeline._gate1(self.gh, self.devin, self.state, key, entry, detail,
                        log=lambda *_: None)
        self.assertEqual(entry["state"], st.NEEDS_ATTENTION)

    def test_nonexistent_pr_goes_to_attention(self):
        key, entry = self._entry()
        detail = {"structured_output": {"outcome": "fixed", "summary": "s",
                                        "pr_url": "sim://pr/does-not-exist",
                                        "tests_run": ["x -> ok"]}}
        pipeline._gate1(self.gh, self.devin, self.state, key, entry, detail,
                        log=lambda *_: None)
        self.assertEqual(entry["state"], st.NEEDS_ATTENTION)

    def test_blocked_claim_without_evidence_goes_to_attention(self):
        key, entry = self._entry()
        detail = {"structured_output": {"outcome": "blocked", "summary": "s",
                                        "blocking_reason": None, "mitigation": None}}
        pipeline._gate1(self.gh, self.devin, self.state, key, entry, detail,
                        log=lambda *_: None)
        self.assertEqual(entry["state"], st.NEEDS_ATTENTION)


if __name__ == "__main__":
    unittest.main()
