import unittest

from ui_refinement_rules import audit_ui_source, summarize_findings


class UiRefinementRulesTests(unittest.TestCase):
    def test_audit_flags_large_radius_and_font(self):
        findings = audit_ui_source(
            """
            button.setStyleSheet("font-size: 22px; border-radius: 18px; padding: 24px;")
            label.setFixedHeight(44)
            """
        )
        rules = {finding.rule for finding in findings}

        self.assertIn("font_scale", rules)
        self.assertIn("radius", rules)
        self.assertIn("spacing", rules)

    def test_summary_counts_severity(self):
        findings = audit_ui_source('x.setStyleSheet("font-size: 9px;")')
        summary = summarize_findings(findings)

        self.assertGreaterEqual(summary["medium"], 1)


if __name__ == "__main__":
    unittest.main()
