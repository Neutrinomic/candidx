"""Shared behavioral tests, wire perturbations, and explicit negative cases."""
import random
from reference import Node, emit_header, leb, parse

EQUAL_GROUPS = [
    ["evolution-record-old", "evolution-record-new", "evolution-record-newer", "evolution-nested-none"],
    ["evolution-variant-old", "evolution-variant-new"],
    ["evolution-array-old", "evolution-array-new"],
    ["evolution-array-mixed", "evolution-array-mixed-newer"],
    ["evolution-variant-array-old", "evolution-variant-array-new"],
    ["evolution-outer-old", "evolution-outer-new"],
    ["evolution-empty-nat-vector", "evolution-empty-text-vector"],
    ["evolution-empty-byte-vector", "empty-blob"],
    ["evolution-option-none-nat", "evolution-option-none-text"],
    ["variant-a", "variant-narrow"],
    ["aliases", "arrays"],
    ["recursive-same", "recursive-distinct"],
]
DISTINCT_PAIRS = [
    ("evolution-record-old", "evolution-present-empty"),
    ("evolution-record-old", "evolution-present-text"),
    ("evolution-nested-none", "evolution-nested-some-none"),
    ("evolution-nested-some-none", "evolution-nested-some-some"),
    ("evolution-variant-old", "evolution-variant-used-new"),
    ("evolution-array-new", "evolution-array-mixed"),
    ("evolution-option-none-nat", "evolution-option-some-none"),
    ("evolution-option-some-none", "evolution-option-some-some"),
    ("evolution-empty-nat-vector", "evolution-empty-byte-vector"),
    ("record-wide", "record-narrow"),
]


def negative_cases():
    cases = {
        "empty-message": b"", "truncated-magic": b"DIDL",
        "unknown-constructor": bytes.fromhex("4449444c01000100"),
        "invalid-reference": emit_header((Node(-19, (), (2,)),), (0,)),
        "duplicate-fields": emit_header((Node(-20, (1, 1), (-3, -3)),), (0,)),
        "unordered-fields": emit_header((Node(-20, (2, 1), (-3, -3)),), (0,)),
        "field-id-overflow": emit_header((Node(-20, (2**32,), (-3,)),), (0,)) + b"\x00",
        "bool-tag": emit_header((), (-2,)) + b"\x02",
        "invalid-utf8": emit_header((), (-15,)) + b"\x01\xff",
        "surrogate-utf8": emit_header((), (-15,)) + b"\x03\xed\xa0\x80",
        "overlong-utf8": emit_header((), (-15,)) + b"\x02\xc0\x80",
        "option-tag": emit_header((Node(-18, (), (-3,)),), (0,)) + b"\x02",
        "variant-index": emit_header((Node(-21, (1,), (-1,)),), (0,)) + b"\x01",
        "opaque-principal": emit_header((), (-24,)) + b"\x00",
        "principal-too-long": emit_header((), (-24,)) + b"\x01\x1e" + bytes(30),
        "trailing-payload": emit_header((), (-3,)) + b"\x01\x00",
        "truncated-float": emit_header((), (-14,)) + b"\x00",
        "uninhabited-empty": emit_header((), (-17,)),
        "uninhabited-variant": emit_header((Node(-21, (1,), (-17,)),), (0,)) + b"\x00",
        "unterminated-integer": emit_header((), (-3,)) + b"\x80",
        "collection-limit": emit_header((Node(-19, (), (-1,)),), (0,)) + leb(2_000_001),
        "type-table-limit": b"DIDL" + leb(1025),
        "value-depth-limit": emit_header((Node(-18, (), (0,)),), (0,)) + b"\x01" * 258 + b"\x00",
        "recursive-no-inhabitant": emit_header((Node(-20, (1,), (0,)),), (0,)),
        "unsupported-function": emit_header((Node(-22, (0, 0, ()), ()),), (0,)) + b"\x01\x01\x00\x00",
        "unsupported-service": emit_header((Node(-23, (), ()),), (0,)) + b"\x01\x00",
        "invalid-function-mode": bytes.fromhex("4449444c016a000001040100"),
        "oneway-with-result": emit_header((Node(-22, (0, 1, (2,)), (-3,)),), ()) ,
        "service-non-function": emit_header((Node(-23, (b"foo",), (-3,)),), ()),
        "service-order": emit_header((Node(-22, (0, 0, ()), ()), Node(-23, (b"z", b"a"), (0, 0))), ()),
    }
    return [{"id": name, "raw": data.hex(), "error": True} for name, data in cases.items()]


def positive_edges():
    nested = emit_header((Node(-18, (), (1,)), Node(-18, (), (-15,))), (0,))
    return [
        ("padded-nat", bytes.fromhex("4449444c00017d8100")),
        ("padded-int", bytes.fromhex("4449444c00017cff7f")),
        ("padded-header", bytes.fromhex("4449444c81006d7b81007d01")),
        ("record-null-retained", emit_header((Node(-20, (97, 98), (-15, -1)),), (0,)) + b"\x03abc"),
        ("record-reserved-retained", emit_header((Node(-20, (97, 98), (-15, -16)),), (0,)) + b"\x03abc"),
        ("padded-field-id", bytes.fromhex("4449444c016c01e1808080800071010003616263")),
        ("vec-float32-nans", emit_header((Node(-19, (), (-13,)),), (0,)) + b"\x02" + bytes.fromhex("0100807f0200c0ff")),
        ("vec-float64-nan", emit_header((Node(-19, (), (-14,)),), (0,)) + b"\x01" + bytes.fromhex("010000000000f0ff")),
        ("variant-array-both-payloads", emit_header((Node(-19, (), (1,)), Node(-21, (97, 98), (-3, -3))), (0,)) + b"\x02\x00\x01\x01\x02"),
        ("nullable-some-text", nested + b"\x01\x01\x03abc"),
        ("nullable-some-none", nested + b"\x01\x00"),
        ("none-optional-function", emit_header((Node(-18, (), (1,)), Node(-22, (0, 0, ()), ())), (0,)) + b"\x00"),
        ("unused-function-type", emit_header((Node(-22, (0, 0, ()), ()),), (-3,)) + b"\x07"),
        ("all-absent-array", emit_header((Node(-19, (), (1,)), Node(-18, (), (-3,))), (0,)) + b"\x03\x00\x00\x00"),
        ("array-reversed", emit_header((Node(-19, (), (-3,)),), (0,)) + b"\x02\x02\x01"),
        ("array-forward", emit_header((Node(-19, (), (-3,)),), (0,)) + b"\x02\x01\x02"),
        ("float-positive-zero", emit_header((), (-14,)) + bytes(8)),
        ("float-negative-zero", emit_header((), (-14,)) + bytes.fromhex("0000000000000080")),
        ("blob-contains-nan-bits", emit_header((Node(-19, (), (-5,)),), (0,)) + b"\x08" + bytes.fromhex("010000000000f0ff")),
    ]


def perturbed_vectors(vectors):
    rng = random.Random(20261004)
    cases = []
    for vector in vectors:
        data = bytes.fromhex(next(iter(vector["raw"].values())))
        nodes, roots, offset = parse(data)
        for repetition in range(3):
            # Shuffle, duplicate every declaration, and add a dead vec bool.
            expanded = nodes + nodes + (Node(-19, (), (-2,)),)
            order = list(range(len(expanded)))
            rng.shuffle(order)
            inverse = {old: new for new, old in enumerate(order)}
            def remap(e):
                if e < 0:
                    return e
                return inverse[e + (len(nodes) if rng.randrange(2) else 0)]
            changed = tuple(Node(expanded[i].opcode, expanded[i].labels,
                                 tuple(remap(e) for e in expanded[i].edges)) for i in order)
            wire = emit_header(changed, tuple(remap(e) for e in roots)) + data[offset:]
            cases.append({"id": f"table-permutation-{vector['id']}-{repetition}",
                          "raw": wire.hex(), "canonical": vector["canonical"]})
    return cases
