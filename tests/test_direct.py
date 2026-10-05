import unittest
from unittest.mock import patch

import direct
from reference import values


class DirectOracleTests(unittest.TestCase):
    def test_source_values_match_every_frozen_golden(self):
        self.assertEqual(len(direct.check_frozen_sources()), 61)

    def test_expected_bytes_do_not_parse_wire_input(self):
        with patch("reference.parse", side_effect=AssertionError("wire parser used")), \
                patch("reference.values", side_effect=AssertionError("wire values used")), \
                patch("reference.canonicalize", side_effect=AssertionError("wire normalizer used")):
            for case in direct.directed_cases():
                self.assertTrue(direct.expected_bytes(case).startswith(b"DIDL"), case["id"])

    def test_typed_value_data_matches_canonical_decode(self):
        for case in direct.directed_cases():
            with self.subTest(id=case["id"]):
                self.assertEqual(values(direct.expected_bytes(case)), direct.normalized_values(case))

    def test_wasm_transport_does_not_use_normalization(self):
        with patch("direct.typed_value", side_effect=AssertionError("normalized oracle used")), \
                patch("reference.canonicalize", side_effect=AssertionError("wire normalizer used")), \
                patch("reference.parse", side_effect=AssertionError("wire parser used")):
            for case in direct.directed_cases():
                self.assertEqual(len(direct.native_trees(case)), len(direct.arguments(case)[0]))

    def test_wasm_transport_retains_source_presence_metadata(self):
        case = next(case for case in direct.frozen_cases() if case["id"] == "evolution-record-new")
        fields = direct.native_trees(case)[0]["fields"]
        self.assertEqual(len(fields), 2)
        optional = next(field for field in fields if field["optional"])
        self.assertEqual(optional["value"], {"kind": "opt", "value": None})
        case = next(case for case in direct.frozen_cases() if case["id"] == "evolution-empty-byte-vector")
        self.assertTrue(direct.native_trees(case)[0]["binary"])

    def test_schema_extensions_do_not_change_source_data(self):
        import fuzz
        corpus = fuzz.corpus(0xcad1d, 5)
        outputs = {case["id"]: direct.expected_bytes(case) for case in corpus["cases"]}
        for group in corpus["equal_groups"]:
            self.assertEqual(len({outputs[name] for name in group}), 1, group)
        for a, b in corpus["distinct_pairs"]:
            self.assertNotEqual(outputs[a], outputs[b], (a, b))


if __name__ == "__main__":
    unittest.main()
