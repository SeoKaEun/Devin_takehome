"""Contract layer: spec matching, prompt assembly, schema shape."""
import unittest

from orchestrator import contracts


class SpecForTest(unittest.TestCase):
    def test_known_specs_match_by_title(self):
        self.assertIn("superset-frontend/",
                      contracts.spec_for("Replace react-loadable")["allowed_paths"])
        self.assertFalse(contracts.spec_for("paramiko 3.5.1 advisory")["expect_pr"])

    def test_unknown_issue_gets_bounded_default_not_everything(self):
        spec = contracts.spec_for("[security] somepkg 1.0 vulnerable - auto-detected")
        self.assertNotIn("", spec["allowed_paths"])
        self.assertIn("requirements/", spec["allowed_paths"])

    def test_ci_paths_are_globally_denied(self):
        self.assertIn(".github/", contracts.DENIED_PATHS)


class PromptTest(unittest.TestCase):
    ISSUE = {"number": 7, "title": "Upgrade x", "body": "## Task\ndo x"}

    def test_work_prompt_pins_branch_and_forbids_upstream(self):
        p = contracts.build_work_prompt(self.ISSUE)
        self.assertIn("devin/issue-7", p)
        self.assertIn("NEVER open a PR against apache/superset", p)
        self.assertIn("## Task", p)  # the issue body IS the contract

    def test_review_prompt_forbids_writing(self):
        p = contracts.build_review_prompt(self.ISSUE, {"pr_url": "http://x", "summary": "s"})
        self.assertIn("Do NOT push commits", p)


class SchemaTest(unittest.TestCase):
    def test_work_schema_outcomes_are_closed_set(self):
        self.assertEqual(
            contracts.WORK_SCHEMA["properties"]["outcome"]["enum"],
            ["fixed", "blocked", "partial"])

    def test_review_schema_verdicts_are_closed_set(self):
        self.assertEqual(
            contracts.REVIEW_SCHEMA["properties"]["verdict"]["enum"],
            ["approve", "request_changes"])


if __name__ == "__main__":
    unittest.main()
