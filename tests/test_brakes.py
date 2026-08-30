"""Brakes: three ways to stop spending without killing the process.

Each brake must (a) stop NEW sessions, (b) leave running sessions to finish
through the normal gates, and (c) be visible in state so the dashboard can
show it. Driven against the simulation clients, offline.
"""
import tempfile
import unittest
from pathlib import Path

from orchestrator import config, pipeline
from orchestrator import state as st
from orchestrator.sim import SimDevin, SimGithub


def _fresh_state():
    return {"issues": {}, "meta": {"created_at": st.utcnow(), "ticks": 0}}


class BrakeTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = {k: getattr(config, k) for k in
                       ("STATE_PATH", "ACU_BUDGET", "MAX_CONSECUTIVE_ATTENTION")}
        config.STATE_PATH = Path(self._tmp.name) / "state.json"
        config.ACU_BUDGET = 0
        config.MAX_CONSECUTIVE_ATTENTION = 0
        self.gh, self.devin = SimGithub(), SimDevin()
        self.state = _fresh_state()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(config, k, v)
        self._tmp.cleanup()

    def _tick(self, n=1):
        for _ in range(n):
            pipeline.tick(self.gh, self.devin, self.state, log=lambda *_: None)

    def _states(self):
        return {k: e["state"] for k, e in self.state["issues"].items()}

    def _all_terminal(self):
        issues = self.state["issues"]
        return bool(issues) and all(e["state"] in st.TERMINAL_STATES for e in issues.values())


class PauseFileTest(BrakeTestBase):
    def test_pause_halts_new_dispatch_and_resume_lifts_it(self):
        config.pause_file().parent.mkdir(parents=True, exist_ok=True)
        config.pause_file().write_text("test")
        self._tick(5)
        # issues were discovered but nothing was dispatched
        self.assertEqual(set(self._states().values()), {st.QUEUED})
        self.assertFalse(self.devin._sessions)
        self.assertIn("paused", self.state["meta"]["dispatch_halted"])

        config.pause_file().unlink()
        self._tick(80)
        self.assertTrue(self._all_terminal())
        self.assertEqual(self.state["meta"]["dispatch_halted"], "")

    def test_pause_lets_running_sessions_finish(self):
        self._tick(1)  # dispatches the first two work sessions
        running = [k for k, s in self._states().items() if s == st.WORKING]
        self.assertEqual(len(running), config.MAX_CONCURRENT_SESSIONS)

        config.pause_file().parent.mkdir(parents=True, exist_ok=True)
        config.pause_file().write_text("test")
        self._tick(40)
        # the two that were running keep being monitored... but their reviews
        # need a NEW session, which the pause forbids -> they park before Gate 2
        for k in running:
            self.assertIn(self._states()[k],
                          (st.REVIEW_PENDING, st.DONE_ESCALATED, st.DONE_FIXED))
        # nothing queued was started
        queued = [k for k, s in self._states().items() if k not in running]
        self.assertTrue(all(self._states()[k] == st.QUEUED for k in queued))


class AcuBudgetTest(BrakeTestBase):
    def test_budget_stops_new_work_but_not_reviews(self):
        # two work sessions (10 each) fit; a third would exceed 25.
        # reviews (5 each) of the started work are NOT budget-gated.
        config.ACU_BUDGET = 25
        self._tick(80)
        states = self._states()
        started = [k for k, e in self.state["issues"].items() if e.get("work_session")]
        self.assertEqual(len(started), 2)
        for k in started:
            self.assertIn(states[k], st.TERMINAL_STATES)   # finished through Gate 2
        waiting = [k for k in states if k not in started]
        self.assertEqual(len(waiting), 2)
        self.assertTrue(all(states[k] == st.QUEUED for k in waiting))
        self.assertTrue(self.state["meta"]["budget_exhausted"])
        # committed = 2 work * 10 + 2 reviews * 5 (+ the re-review on issue #1)
        self.assertGreaterEqual(self.state["meta"]["acu_committed"], 30)

    def test_raising_the_budget_resumes(self):
        config.ACU_BUDGET = 25
        self._tick(80)
        self.assertFalse(self._all_terminal())
        config.ACU_BUDGET = 0
        self._tick(80)
        self.assertTrue(self._all_terminal())
        self.assertFalse(self.state["meta"]["budget_exhausted"])


class CircuitBreakerTest(BrakeTestBase):
    def _force_attention(self, key):
        entry = self.state["issues"][key]
        bad = {"structured_output": {"outcome": "fixed", "summary": "s", "pr_url": None}}
        pipeline._gate1(self.gh, self.devin, self.state, key, entry, bad,
                        log=lambda *_: None)
        self.assertEqual(entry["state"], st.NEEDS_ATTENTION)

    def test_consecutive_attention_opens_the_circuit(self):
        config.MAX_CONSECUTIVE_ATTENTION = 2
        self._tick(1)
        working = [k for k, s in self._states().items() if s == st.WORKING]
        self.assertEqual(len(working), 2)
        for k in working:
            self._force_attention(k)
        self.assertEqual(self.state["meta"]["consecutive_attention"], 2)

        self._tick(10)
        self.assertIn("circuit open", self.state["meta"]["dispatch_halted"])
        rest = [k for k in self._states() if k not in working]
        self.assertTrue(all(self._states()[k] == st.QUEUED for k in rest))

        # a human investigates and resumes (what `orchestrator resume` does)
        self.state["meta"]["consecutive_attention"] = 0
        self._tick(80)
        self.assertTrue(all(self._states()[k] in st.TERMINAL_STATES for k in rest))

    def test_a_success_resets_the_streak(self):
        config.MAX_CONSECUTIVE_ATTENTION = 2
        self._tick(1)
        working = [k for k, s in self._states().items() if s == st.WORKING]
        self._force_attention(working[0])
        self.assertEqual(self.state["meta"]["consecutive_attention"], 1)
        self._tick(80)  # the other running issue completes normally
        self.assertEqual(self.state["meta"]["consecutive_attention"], 0)
        self.assertEqual(self.state["meta"]["dispatch_halted"], "")


class BackfillTest(BrakeTestBase):
    def test_pre_existing_state_gets_counters_from_timeline(self):
        self._tick(80)
        self.assertTrue(self._all_terminal())
        expected = self.state["meta"]["acu_committed"]
        # simulate a state.json written before the counters existed
        del self.state["meta"]["acu_committed"]
        self._tick(1)
        self.assertEqual(self.state["meta"]["acu_committed"], expected)


if __name__ == "__main__":
    unittest.main()
