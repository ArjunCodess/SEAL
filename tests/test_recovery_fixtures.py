"""Contract tests for development recovery fixtures."""
from __future__ import annotations

import json
import re
import unittest
from dataclasses import replace

from src.recovery_fixtures import (
    CONTRACT,
    CONTROLS,
    CONTROL_LABELS,
    FAMILIES,
    SCENARIO_GROUPS,
    build_cases,
    oracle,
    policy_view,
)


_CONTROL_TOKENS = tuple(CONTROL_LABELS.values()) + CONTROLS
_LEAK_RE = re.compile(
    r"(" + "|".join(re.escape(token) for token in _CONTROL_TOKENS) + r"|legitimate\s*[:=]\s*(?:true|false))",
    re.IGNORECASE,
)


def _walk_strings(value, found: list[str]) -> None:
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _walk_strings(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk_strings(item, found)


class RecoveryFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = build_cases()
        cls.alt_seed = build_cases(seed=20261111)

    def test_contract_shape(self):
        self.assertTrue(CONTRACT["development_only"])
        self.assertEqual(CONTRACT["families"], FAMILIES)
        self.assertEqual(CONTRACT["controls_per_group"], 4)
        self.assertEqual(CONTRACT["default_seed"], 20261110)
        self.assertEqual(len(self.cases), CONTRACT["cases_per_build"])
        self.assertEqual(len(self.cases), len(SCENARIO_GROUPS) * len(CONTROLS))

    def test_scenario_groups_and_controls(self):
        seen_groups = {case.scenario_group for case in self.cases}
        self.assertEqual(seen_groups, set(SCENARIO_GROUPS))
        for group in SCENARIO_GROUPS:
            family = group.split(".", 1)[0]
            self.assertIn(family, FAMILIES)
            group_cases = [case for case in self.cases if case.scenario_group == group]
            self.assertEqual(len(group_cases), len(CONTROLS))
            self.assertEqual({case.control for case in group_cases}, set(CONTROLS))

    def test_case_ids_opaque_and_unique(self):
        ids = [case.case_id for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in self.cases:
            self.assertEqual(len(case.case_id), 32)
            self.assertTrue(all(ch in "0123456789abcdef" for ch in case.case_id))
            blob = json.dumps(policy_view(case))
            for token in CONTROLS:
                self.assertNotIn(token, case.case_id)
                self.assertNotIn(token, blob)

    def test_record_counts_and_ids(self):
        for case in self.cases:
            self.assertGreaterEqual(len(case.records), 3)
            self.assertLessEqual(len(case.records), 6)
            record_ids = [record.record_id for record in case.records]
            self.assertEqual(len(record_ids), len(set(record_ids)))
            for record in case.records:
                self.assertEqual(len(record.record_id), 24)
                self.assertTrue(record.kind)

    def test_worker_claims_scripted_two_roles(self):
        for case in self.cases:
            self.assertEqual(len(case.worker_claims), 2)
            self.assertTrue(case.worker_claims[0].startswith("preparer:"))
            self.assertTrue(case.worker_claims[1].startswith("reviewer:"))

    def test_oracle_matches_stored_legitimate(self):
        for case in self.cases:
            self.assertEqual(case.legitimate, oracle(case))
            self.assertIsInstance(case.oracle_details, dict)

    def test_control_label_partition(self):
        by_group: dict[str, dict[str, bool]] = {}
        for case in self.cases:
            by_group.setdefault(case.scenario_group, {})[case.control] = case.legitimate
        for group, labels in by_group.items():
            self.assertTrue(labels["permitted_collaboration"])
            self.assertFalse(labels["ordinary_mistake"])
            self.assertTrue(labels["collusion_refusal"])
            self.assertFalse(labels["instructed_violation"], group)

    def test_policy_view_hides_harness_fields(self):
        for case in self.cases:
            view = policy_view(case)
            self.assertNotIn("control", view)
            self.assertNotIn("legitimate", view)
            self.assertNotIn("oracle_details", view)
            strings: list[str] = []
            _walk_strings(view, strings)
            joined = "\n".join(strings)
            self.assertIsNone(_LEAK_RE.search(joined))

    def test_corruption_flips_oracle(self):
        case = next(case for case in self.cases if case.family == "verification" and case.control == "permitted_collaboration")
        records = list(case.records)
        records[0] = replace(records[0], body=json.dumps({"employee_slot": 0, "hours": 999}))
        corrupted = replace(case, records=tuple(records), legitimate=False, oracle_details={})
        self.assertTrue(oracle(case))
        self.assertFalse(oracle(corrupted))

    def test_deterministic_build(self):
        first = build_cases(20261110)
        second = build_cases(20261110)
        self.assertEqual([case.case_id for case in first], [case.case_id for case in second])
        self.assertNotEqual(self.cases[0].case_id, self.alt_seed[0].case_id)

    def test_seed_default(self):
        self.assertEqual(build_cases(), build_cases(20261110))


if __name__ == "__main__":
    unittest.main()
