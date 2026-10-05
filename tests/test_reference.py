import json
from pathlib import Path
import unittest

from cases import perturbed_vectors
from reference import canonicalize, values


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads(Path(__file__).with_name("vectors.json").read_text())
        cls.entries = {e["id"]: e for e in cls.corpus["vectors"]}

    def test_all_three_upstream_encodings(self):
        for entry in self.corpus["vectors"]:
            for raw in entry["raw"].values():
                with self.subTest(id=entry["id"]):
                    self.assertEqual(canonicalize(bytes.fromhex(raw)).hex(), entry["canonical"])

    def test_canonical_messages_are_idempotent(self):
        for entry in self.corpus["vectors"] + self.corpus["positive_edges"]:
            expected = bytes.fromhex(entry["canonical"])
            self.assertEqual(canonicalize(expected), expected)

    def test_absent_fields_and_unselected_cases(self):
        for group in self.corpus["equal_groups"]:
            self.assertEqual(len({self.entries[k]["canonical"] for k in group}), 1, group)

    def test_present_data_and_optional_presence(self):
        for a, b in self.corpus["distinct_pairs"]:
            self.assertNotEqual(self.entries[a]["canonical"], self.entries[b]["canonical"])

    def test_hand_written_record_example(self):
        self.assertEqual(self.entries["evolution-record-old"]["canonical"],
                         "4449444c016c016171010003616263")

    def test_type_order_duplication_and_unreachable_declarations(self):
        for entry in perturbed_vectors(self.corpus["vectors"]):
            self.assertEqual(canonicalize(bytes.fromhex(entry["raw"])).hex(), entry["canonical"], entry["id"])

    def test_padded_integers_are_rewritten(self):
        for entry in self.corpus["positive_edges"]:
            self.assertEqual(canonicalize(bytes.fromhex(entry["raw"])).hex(), entry["canonical"])

    def test_malformed_unsupported_and_limited_input(self):
        for entry in self.corpus["negative_cases"]:
            with self.subTest(id=entry["id"]), self.assertRaises((ValueError, UnicodeError)):
                canonicalize(bytes.fromhex(entry["raw"]))

    def test_normalized_data_is_preserved(self):
        for entry in self.corpus["vectors"]:
            # Binary metadata is inferred from an empty byte vector too.
            raw = bytes.fromhex(next(iter(entry["raw"].values())))
            self.assertEqual(values(raw), values(bytes.fromhex(entry["canonical"])), entry["id"])


if __name__ == "__main__":
    unittest.main()
