"""Scanner: pin parsing and contract templating (no network)."""
import unittest

from orchestrator import scanner


class ParsePinsTest(unittest.TestCase):
    def test_parses_exact_pins_only(self):
        text = "\n".join([
            "# comment", "flask==2.3.3", "  paramiko==3.5.1",
            "loose>=1.0", "-e ./superset-core", "name == spaced.ok",
        ])
        pins = scanner._parse_pins(text)
        self.assertEqual(pins["flask"], "2.3.3")
        self.assertEqual(pins["paramiko"], "3.5.1")
        self.assertNotIn("loose", pins)


class BuildBodyTest(unittest.TestCase):
    DETAILS = [{"id": "GHSA-x", "summary": "bad thing", "severity": "HIGH",
                "fixed_versions": ["2.0"]}]

    def test_fixable_advisory_yields_upgrade_contract(self):
        body = scanner._build_body("pkg", "1.0", self.DETAILS, "requirements/base.txt")
        self.assertIn("Upgrade the `pkg` pin", body)
        self.assertIn("If you get blocked", body)  # blocked protocol always present

    def test_unfixable_advisory_yields_investigation_contract(self):
        details = [{**self.DETAILS[0], "fixed_versions": []}]
        body = scanner._build_body("pkg", "1.0", details, "requirements/base.txt")
        self.assertIn("investigation", body)
        normalized = " ".join(body.split())
        self.assertIn("do not close this issue yourself", normalized)


if __name__ == "__main__":
    unittest.main()
