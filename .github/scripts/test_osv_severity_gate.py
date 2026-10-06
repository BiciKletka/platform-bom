import datetime
import json
import os
import tempfile
import unittest

import osv_severity_gate as gate


def results(*groups):
    return {"results": [{"packages": [{"package": {"name": "x", "version": "1"}, "groups": list(groups)}]}]}


class FindingsTest(unittest.TestCase):
    def test_high_and_critical_fail(self):
        self.assertEqual(1, len(gate.findings(results({"ids": ["A"], "max_severity": "7.0"}), 7.0)))
        self.assertEqual(1, len(gate.findings(results({"ids": ["A"], "max_severity": "9.8"}), 7.0)))

    def test_medium_passes(self):
        self.assertEqual([], gate.findings(results({"ids": ["A"], "max_severity": "6.9"}), 7.0))

    def test_unknown_severity_fails(self):
        self.assertEqual(1, len(gate.findings(results({"ids": ["A"], "max_severity": ""}), 7.0)))
        self.assertEqual(1, len(gate.findings(results({"ids": ["A"]}), 7.0)))

    def test_no_results_passes(self):
        self.assertEqual([], gate.findings({}, 7.0))

    def test_group_from_the_sbom_is_printed(self):
        coordinates = {("jackson-core", "3.1.5"): "tools.jackson.core:jackson-core"}
        found = gate.findings({"results": [{"packages": [{"package": {"name": "jackson-core", "version": "3.1.5"},
                                                           "groups": [{"ids": ["A"], "max_severity": "7.5"}]}]}]},
                              7.0, coordinates)
        self.assertEqual("tools.jackson.core:jackson-core@3.1.5", found[0][0])
        self.assertEqual("x@1", gate.findings(results({"ids": ["A"], "max_severity": "9"}), 7.0, coordinates)[0][0])


def sbom(count, group="g"):
    return {"components": [{"group": group, "name": f"c{i}", "version": "1"} for i in range(count)]}


class SbomTest(unittest.TestCase):
    def load(self, payload):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
        try:
            return gate.read_sbom(handle.name)
        finally:
            os.unlink(handle.name)

    def test_components_are_counted_and_keyed_by_name_and_version(self):
        count, coordinates = self.load(sbom(3, "org.x"))
        self.assertEqual(3, count)
        self.assertEqual("org.x:c1", coordinates[("c1", "1")])

    def test_group_is_optional(self):
        count, coordinates = self.load({"components": [{"name": "solo", "version": "2"}]})
        self.assertEqual((1, "solo"), (count, coordinates[("solo", "2")]))

    def test_no_components_key_counts_zero(self):
        self.assertEqual(0, self.load({})[0])


class AllowlistTest(unittest.TestCase):
    today = datetime.date(2026, 10, 5)

    def problems(self, toml):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(toml)
        try:
            return gate.allowlist_problems(handle.name, self.today)
        finally:
            os.unlink(handle.name)

    def test_missing_file_is_fine(self):
        self.assertEqual([], gate.allowlist_problems("/nonexistent.toml", self.today))

    def test_valid_entry(self):
        self.assertEqual([], self.problems(
            '[[IgnoredVulns]]\nid = "GHSA-x"\nignoreUntil = 2026-12-01\nreason = "not reachable"\n'))

    def test_missing_reason_and_expiry_and_expired_and_too_far(self):
        self.assertEqual(1, len(self.problems('[[IgnoredVulns]]\nid = "A"\nignoreUntil = 2026-12-01\n')))
        self.assertEqual(1, len(self.problems('[[IgnoredVulns]]\nid = "A"\nreason = "r"\n')))
        self.assertEqual(1, len(self.problems('[[IgnoredVulns]]\nid = "A"\nignoreUntil = 2026-10-01\nreason = "r"\n')))
        self.assertEqual(1, len(self.problems('[[IgnoredVulns]]\nid = "A"\nignoreUntil = 2027-06-01\nreason = "r"\n')))

    def test_package_overrides_are_refused_in_every_form(self):
        # osv-scanner would drop the findings without a reason or an expiry: the gate's one hole.
        for toml in (
                '[[PackageOverrides]]\necosystem = "Maven"\nignore = true\n',
                '[[PackageOverrides]]\nname = "tomcat-embed-core"\necosystem = "Maven"\nignore = true\n',
                '[[PackageOverrides]]\nname = "x"\nvulnerability.ignore = true\n',
                '[[PackageOverrides]]\nname = "x"\nlicense.ignore = true\n'):
            problems = self.problems(toml)
            self.assertEqual(1, len(problems), toml)
            self.assertIn("PackageOverrides", problems[0])
            self.assertIn("only [[IgnoredVulns]]", problems[0])

    def test_any_other_top_level_key_is_refused(self):
        self.assertEqual(1, len(self.problems('GoVersionOverride = "1.22"\n')))
        self.assertEqual(1, len(self.problems('[[ExemptVulns]]\nid = "A"\n')))

    def test_unknown_key_inside_an_entry_is_refused(self):
        self.assertEqual(1, len(self.problems(
            '[[IgnoredVulns]]\nid = "A"\nignoreUntil = 2026-12-01\nreason = "r"\nignore = true\n')))

    def test_malformed_ignored_vulns_shapes_give_a_message(self):
        for text in ('[IgnoredVulns]\nid = "A"\n',
                     'IgnoredVulns = [{id = "A", ignoreUntil = 2026-12-01, reason = "r"}, 5]\n',
                     'IgnoredVulns = "A"\n'):
            problems = self.problems(text)
            self.assertEqual(1, len(problems), text)
            self.assertIn("IgnoredVulns must be an array of tables", problems[0])

    def test_quoted_ignore_until_is_refused_with_a_message(self):
        problems = self.problems('[[IgnoredVulns]]\nid = "A"\nignoreUntil = "2026-12-01"\nreason = "r"\n')
        self.assertEqual(1, len(problems))
        self.assertIn("unquoted", problems[0])

    def test_unparsable_config_is_a_problem_not_a_traceback(self):
        problems = self.problems('[[IgnoredVulns\n')
        self.assertEqual(1, len(problems))
        self.assertIn("cannot parse", problems[0])


class MainTest(unittest.TestCase):
    def run_main(self, payload, *extra):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
        try:
            return gate.main([handle.name, "--config", "/nonexistent.toml", *extra])
        finally:
            os.unlink(handle.name)

    def run_with_sbom(self, sbom_payload, *extra):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(sbom_payload, handle)
        try:
            return self.run_main({"results": []}, "--sbom", handle.name, *extra)
        finally:
            os.unlink(handle.name)

    def test_a_shrunken_sbom_fails_even_without_findings(self):
        self.assertEqual(0, self.run_with_sbom(sbom(50), "--min-components", "40"))
        self.assertEqual(1, self.run_with_sbom(sbom(5), "--min-components", "40"))
        self.assertEqual(1, self.run_with_sbom({}, "--min-components", "40"))

    def test_unreadable_sbom_fails_closed(self):
        self.assertEqual(2, self.run_main({"results": []}, "--sbom", "/nonexistent-bom.json"))

    def test_min_components_without_sbom_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            self.run_main({"results": []}, "--min-components", "40")

    def test_exit_codes(self):
        self.assertEqual(0, self.run_main({"results": []}))
        self.assertEqual(1, self.run_main(results({"ids": ["A"], "max_severity": "9.1"})))
        self.assertEqual(2, gate.main(["/nonexistent.json"]))


if __name__ == "__main__":
    unittest.main()
