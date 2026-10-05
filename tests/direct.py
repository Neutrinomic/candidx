"""Shared direct-encoder cases and an independent typed-value test oracle.

Expected bytes are derived from the declared values, never from an ordinary
Candid message. The parser-based oracle remains a separate inbound check.
This module is test infrastructure and is not part of any shipped package.
"""
from hashlib import sha256
import json
from pathlib import Path
import struct

from reference import NULL, Value, encode_values, leb

ROOT = Path(__file__).resolve().parent.parent
SCALARS = {"null": -1, "bool": -2, "nat": -3, "int": -4,
           "nat8": -5, "nat16": -6, "nat32": -7, "nat64": -8,
           "int8": -9, "int16": -10, "int32": -11, "int64": -12,
           "float32": -13, "float64": -14, "text": -15, "reserved": -16,
           "principal": -24}


def field_id(label):
    if isinstance(label, int):
        return label
    result = 0
    for byte in label.encode("utf-8"):
        result = (result * 223 + byte) & 0xffffffff
    return result


def float_payload(kind, value):
    width = 32 if kind.startswith("float32") else 64
    if kind.endswith("bits"):
        bits = int(value, 0) if value.startswith("0x") else int(value)
        if not 0 <= bits < 1 << width:
            raise ValueError("float bit witness out of range")
    else:
        number = {"nan": float("nan"), "inf": float("inf"), "-inf": -float("inf")}.get(value)
        number = float(value) if number is None else number
        bits = int.from_bytes(struct.pack("<f" if width == 32 else "<d", number), "little")
    fraction, exponent = (23, 8) if width == 32 else (52, 11)
    if bits >> fraction & ((1 << exponent) - 1) == (1 << exponent) - 1 and bits & ((1 << fraction) - 1):
        bits = 0x7fc00000 if width == 32 else 0x7ff8000000000000
    return bits.to_bytes(width // 8, "little")


def typed_value(schema, value):
    """Interpret a source witness/value pair into the fixed cx value algebra."""
    kind = schema[0]
    if kind == "opt":
        return Value(-18, typed_value(schema[1], value[0])) if value else NULL
    if kind in ("vec", "blob"):
        binary = kind == "blob" or schema[1][0] == "nat8"
        child = ["nat8"] if kind == "blob" else schema[1]
        return Value(-19, tuple(typed_value(child, item) for item in value), binary)
    if kind == "record":
        fields = []
        for label, child in schema[1:]:
            field = typed_value(child, value[str(label)] if isinstance(label, int) else value[label])
            if child[0] not in ("opt", "list") or field.op != -1:
                fields.append((field_id(label), field))
        return Value(-20, tuple(sorted(fields)))
    if kind == "tuple":
        return typed_value(["record", *[[i, child] for i, child in enumerate(schema[1:])]],
                           {str(i): item for i, item in enumerate(value)})
    if kind == "variant":
        label, item = next(iter(value.items()))
        child = next(child for name, child in schema[1:] if name == label)
        return Value(-21, ((field_id(label), typed_value(child, item)),))
    if kind == "list":
        if not value:
            return NULL
        fields = [["item", schema[1]], ["next", schema]]
        if schema[2]:
            fields.append(["extra", ["opt", ["text"]]])
        return Value(-18, typed_value(["record", *fields], value[0]))
    if kind == "chain":
        return typed_value(["record", ["head", ["nat"]], ["tail", ["opt", schema]]], value)
    if kind in ("null", "reserved"):
        return Value(SCALARS[kind])
    if kind == "bool":
        raw = bytes([value])
    elif kind in ("nat", "int"):
        raw = leb(int(value), signed=kind == "int")
    elif kind.startswith(("nat", "int")):
        signed = kind.startswith("int")
        bits = int(kind[3:])
        raw = int(value).to_bytes(bits // 8, "little", signed=signed)
    elif kind.startswith("float"):
        return Value(-13 if kind.startswith("float32") else -14, float_payload(kind, value))
    elif kind == "text":
        data = value.encode("utf-8", errors="strict")
        raw = leb(len(data)) + data
    elif kind == "principal":
        data = bytes(value)
        if len(data) > 29:
            raise ValueError("principal length exceeds 29")
        raw = b"\x01" + leb(len(data)) + data
    else:
        raise ValueError(f"unknown source witness {kind}")
    return Value(SCALARS[kind], raw)


def arguments(case):
    return (case["schemas"], case["values"]) if "schemas" in case else ([case["schema"]], [case["value"]])


def normalized_values(case):
    schemas, values = arguments(case)
    if len(schemas) != len(values):
        raise ValueError("argument count mismatch")
    return tuple(typed_value(schema, value) for schema, value in zip(schemas, values))


def expected_bytes(case):
    return encode_values(normalized_values(case))


def native_tree(schema, value):
    """Convert source values to the standalone Wasm typed-memory ABI.

    Source optional fields and binary vector metadata are retained. This
    transport deliberately does not use the normalized-data oracle above.
    """
    kind = schema[0]
    if kind == "opt":
        return {"kind": "opt", "value": native_tree(schema[1], value[0]) if value else None}
    if kind == "vec":
        return {"kind": "vec", "values": [native_tree(schema[1], child) for child in value],
                "binary": schema[1][0] == "nat8"}
    if kind == "blob":
        return {"kind": kind, "value": bytes(value)}
    if kind == "record":
        return {"kind": kind, "fields": sorted([
            {"id": field_id(label), "value": native_tree(child, value[str(label)] if isinstance(label, int) else value[label]),
             "optional": child[0] in ("opt", "list")}
            for label, child in schema[1:]], key=lambda field: field["id"])}
    if kind == "tuple":
        return native_tree(["record", *[[i, child] for i, child in enumerate(schema[1:])]],
                           {str(i): child for i, child in enumerate(value)})
    if kind == "variant":
        label, child_value = next(iter(value.items()))
        child = next(child for name, child in schema[1:] if name == label)
        return {"kind": kind, "id": field_id(label), "value": native_tree(child, child_value)}
    if kind == "list":
        fields = [["item", schema[1]], ["next", schema]]
        if schema[2]:
            fields.append(["extra", ["opt", ["text"]]])
        return {"kind": "opt", "value": native_tree(["record", *fields], value[0]) if value else None}
    if kind == "chain":
        return native_tree(["record", ["head", ["nat"]], ["tail", ["opt", schema]]], value)
    if kind.startswith(("nat", "int")):
        return {"kind": kind, "value": int(value)}
    if kind.endswith("bits"):
        return {"kind": kind, "value": int(value, 0) if value.startswith("0x") else int(value)}
    if kind.startswith("float"):
        number = {"nan": float("nan"), "inf": float("inf"), "-inf": -float("inf")}.get(value)
        return {"kind": kind, "value": float(value) if number is None else number}
    if kind == "principal":
        return {"kind": kind, "value": bytes(value)}
    return {"kind": kind, "value": value}


def native_trees(case):
    schemas, values = arguments(case)
    if len(schemas) != len(values):
        raise ValueError("argument count mismatch")
    return [native_tree(schema, value) for schema, value in zip(schemas, values)]


def frozen_cases():
    """Explicit original schemas/values for the unchanged 61 frozen goldens."""
    cases = []

    def add(name, schemas, values):
        cases.append({"id": name, "schemas": schemas, "values": values, "frozen_id": name})

    old = ["record", ["a", ["text"]]]
    new = ["record", ["a", ["text"]], ["b", ["opt", ["text"]]]]
    newer = ["record", *new[1:], ["c", ["opt", ["vec", ["nat"]]]]]
    nested = ["record", ["a", ["text"]], ["b", ["opt", ["opt", ["text"]]]]]
    choice = ["variant", ["a", ["null"]], ["b", ["nat"]]]
    more = ["variant", *choice[1:], ["c", ["text"]]]
    ov = {"a": "abc"}
    nv = {"a": "abc", "b": []}
    nrv = {"a": "abc", "b": [], "c": []}
    add("evolution-record-old", [old], [ov])
    add("evolution-record-new", [new], [nv])
    add("evolution-record-newer", [newer], [nrv])
    add("evolution-present-empty", [new], [{"a": "abc", "b": [""]}])
    add("evolution-present-text", [new], [{"a": "abc", "b": ["new data"]}])
    for name, value in [("none", []), ("some-none", [[]]), ("some-some", [["x"]])]:
        add("evolution-nested-" + name, [nested], [{"a": "abc", "b": value}])
    add("evolution-variant-old", [choice], [{"b": "128"}])
    add("evolution-variant-new", [more], [{"b": "128"}])
    add("evolution-variant-used-new", [more], [{"c": "new"}])
    add("evolution-array-old", [["vec", old]], [[ov, {"a": "xyz"}]])
    add("evolution-array-new", [["vec", new]], [[nv, {"a": "xyz", "b": []}]])
    add("evolution-array-mixed", [["vec", new]], [[nv, {"a": "xyz", "b": ["present"]}]])
    add("evolution-array-mixed-newer", [["vec", newer]], [[nrv, {"a": "xyz", "b": ["present"], "c": []}]])
    for name, schema in [("old", choice), ("new", more)]:
        add("evolution-variant-array-" + name, [["vec", schema]], [[{"a": None}, {"b": "5"}]])
    add("evolution-outer-old", [["record", ["inner", old], ["items", ["vec", choice]]]],
        [{"inner": ov, "items": [{"b": "7"}]}])
    add("evolution-outer-new", [["record", ["inner", new], ["items", ["vec", more]], ["extra", ["opt", ["nat"]]]]],
        [{"inner": nv, "items": [{"b": "7"}], "extra": []}])
    for name, child in [("nat", ["nat"]), ("text", ["text"]), ("byte", ["nat8"])]:
        add("evolution-empty-" + name + "-vector", [["vec", child]], [[]])
    for kind in ("nat", "text"):
        add("evolution-option-none-" + kind, [["opt", [kind]]], [[]])
    add("evolution-option-some-none", [["opt", ["opt", ["nat"]]]], [[[]]])
    add("evolution-option-some-some", [["opt", ["opt", ["nat"]]]], [[["7"]]])
    add("evolution-nullable-array", [["vec", ["opt", ["opt", ["nat"]]]]], [[[], [[]], [["7"]]]])

    huge = str(2 ** 200 + 63)
    binary = [0, 255] * 65
    alias = ["record", ["a", ["blob"]], ["b", ["vec", ["nat8"]]]]
    for name in ("aliases", "arrays"):
        add(name, [alias], [{"a": [1, 2], "b": [1, 2]}])
    add("char-aliases", [["record", ["a", ["vec", ["nat32"]]], ["b", ["vec", ["nat32"]]]]], [{"a": [65], "b": [65]}])
    add("empty-args", [], [])
    add("empty-blob", [["blob"]], [[]])
    add("blob-130", [["blob"]], [binary])
    add("empty-record", [["record"]], [{}])
    add("nested", [["record", ["aa", ["opt", ["nat"]]], ["b", ["vec", ["nat"]]]]], [{"aa": ["7"], "b": ["1", "2"]}])
    leaf = {"head": "1", "tail": []}
    for name in ("recursive-same", "recursive-distinct"):
        add(name, [["chain"], ["chain"]], [leaf, leaf])
    add("recursive-deep", [["chain"]], [{"head": "128", "tail": [{"head": "1", "tail": [{"head": "0", "tail": []}]}]}])
    add("nat-boundaries", [["nat"]] * 4, ["0", "127", "128", huge])
    add("int-boundaries", [["int"]] * 4, ["-1", "-64", "-65", "-" + huge])
    add("fixed-integers", [[kind] for kind in ("nat8", "nat16", "nat32", "nat64", "int8", "int16", "int32", "int64")],
        [255, 65535, 4294967295, "18446744073709551615", -128, -32768, -2147483648, "-9223372036854775808"])
    add("bool-null", [["bool"], ["bool"], ["null"]], [True, False, None])
    add("text-utf8", [["text"]] * 3, ["é", "e\u0301", "🦀\0"])
    add("options", [["opt", ["nat"]], ["opt", ["nat"]], ["opt", ["opt", ["nat"]]]], [[], ["128"], [[]]])
    add("variant-a", [choice], [{"a": None}])
    add("variant-b", [choice], [{"b": "128"}])
    add("variant-narrow", [["variant", ["a", ["null"]]]], [{"a": None}])
    add("nested-arrays", [["vec", ["vec", ["nat"]]]], [[[], ["1", "128"]]])
    add("tuple", [["tuple", ["nat"], ["text"], ["bool"]]], [["1", "tuple", True]])
    tx = ["record", ["from", ["principal"]], ["to", ["principal"]], ["amount", ["nat"]], ["memo", ["opt", ["blob"]]]]
    block = ["record", ["parentHash", ["opt", ["blob"]]], ["timestamp", ["nat64"]], ["transactions", ["vec", tx]]]
    add("block", [block], [{"parentHash": [[1, 2, 3]], "timestamp": "1234567890", "transactions": [
        {"from": [], "to": [1, 2], "amount": huge, "memo": [binary]},
        {"from": [1, 2], "to": [], "amount": "0", "memo": []}]}])
    add("float64-values", [["float64"]] * 5, ["0", "-0", "1.5", "inf", "-inf"])
    add("float32-values", [["float32"]] * 3, ["0", "-0", "1.5"])
    add("principal", [["principal"]], [[1, 2]])
    add("record-wide", [["record", ["a", ["nat"]], ["b", ["nat"]]]], [{"a": "1", "b": "2"}])
    add("record-narrow", [["record", ["a", ["nat"]]]], [{"a": "1"}])
    for width, patterns in [(32, [0x7fc00001, 0x7f800001, 0xffc00002]),
                            (64, [0x7ff8000000000001, 0x7ff0000000000001, 0xfff8000000000002])]:
        for bits in patterns:
            add(f"nan-{width}-{bits:x}", [[f"float{width}bits"]], [hex(bits)])
    return sorted(cases, key=lambda case: case["id"])


def directed_cases():
    cases = frozen_cases()
    for width, patterns in [(32, [0, 0x80000000, 1, 0x80000001, 0x00800000, 0x80800000,
                                  0x7f7fffff, 0xff7fffff, 0x7f800000, 0xff800000,
                                  0x7f800001, 0xff800001, 0x7fc12345, 0xffdabcde]),
                            (64, [0, 0x8000000000000000, 1, 0x8000000000000001,
                                  0x0010000000000000, 0x8010000000000000,
                                  0x7fefffffffffffff, 0xffefffffffffffff,
                                  0x7ff0000000000000, 0xfff0000000000000,
                                  0x7ff0000000000001, 0xfff0000000000001,
                                  0x7ff812345678abcd, 0xfffdabcdef123456])]:
        for bits in patterns:
            cases.append({"id": f"direct-float{width}-{bits:0{width // 4}x}",
                          "schema": [f"float{width}bits"], "value": hex(bits)})
        cases.append({"id": f"direct-float{width}-mixed-vector", "schema": ["vec", [f"float{width}bits"]],
                      "value": [hex(bits) for bits in patterns]})
    cases.append({"id": "direct-root-order", "schemas": [["nat"], ["text"], ["bool"], ["opt", ["nat"]]],
                  "values": ["128", "é\0🧪", False, ["7"]]})
    cases.append({"id": "direct-root-order-reversed", "schemas": [["opt", ["nat"]], ["bool"], ["text"], ["nat"]],
                  "values": [["7"], False, "é\0🧪", "128"]})
    return cases


def check_frozen_sources():
    fixture = json.loads((ROOT / "tests/vectors.json").read_text())
    lookup = {entry["id"]: entry for entry in fixture["vectors"]}
    cases = frozen_cases()
    assert {case["id"] for case in cases} == set(lookup), "typed source corpus must cover every frozen case"
    for case in cases:
        expected = expected_bytes(case)
        assert expected.hex() == lookup[case["id"]]["canonical"], case["id"]
        assert sha256(expected).hexdigest() == lookup[case["id"]]["application_sha256"], case["id"]
    return lookup


def verify_outputs(cases, sources):
    """Compare direct output to source values, frozen goldens, and external SHA."""
    from reference import canonicalize, values
    lookup = check_frozen_sources()
    ids = {case["id"] for case in cases}
    for language, outputs in sources.items():
        assert set(outputs) == ids, (language, "case ID mismatch")
    rows = []
    for case in cases:
        expected = expected_bytes(case)
        data = normalized_values(case)
        outputs = {language: source[case["id"]] for language, source in sources.items()}
        for language, output in outputs.items():
            assert not output.get("error"), (language, case, output)
            assert output.get("direct_path") == "typed-values", (language, case["id"], "direct path was not used")
            actual = bytes.fromhex(output["canonical"])
            assert actual == expected, (language, case, output, expected.hex())
            assert sha256(actual).digest() == sha256(expected).digest(), (language, case["id"], "hash disagreement")
            assert values(actual) == data, (language, case["id"], "direct data changed")
            assert canonicalize(actual) == expected, (language, case["id"], "not canonical")
            if output.get("raw") is not None:
                raw = bytes.fromhex(output["raw"])
                assert canonicalize(raw) == expected, (language, case["id"], "ordinary control differs")
                assert values(raw) == data, (language, case["id"], "ordinary control data differs")
            decoding = output.get("decoding")
            if language in ("ocaml", "wasm"):
                assert decoding == "not-available", (language, case["id"], "no upstream decoder is supplied")
            elif language == "ts":
                assert output.get("compatible_decoding") == "exact", (language, case["id"], "typed recovery failed")
                assert output.get("canonical_roundtrip") is True, (language, case["id"], "original input equality was not checked")
                assert decoding in ("exact", "upstream-decoder-loss", "sdk-recursive-optional-field-loss"), (language, case, output)
            else:
                assert decoding == "exact", (language, case, output)
                assert output.get("canonical_roundtrip") is True, (language, case["id"], "original input equality was not checked")
        if case.get("frozen_id"):
            assert expected.hex() == lookup[case["frozen_id"]]["canonical"], case["id"]
        rows.append({"id": case["id"], "raw": {language: output["raw"] for language, output in outputs.items() if "raw" in output},
                     "direct": {language: output["canonical"] for language, output in outputs.items()},
                     "canonical": expected.hex(), "application_sha256": sha256(expected).hexdigest(),
                     "frozen_id": case.get("frozen_id")})
    return rows
