#!/usr/bin/env python3
"""Reproducible SAME-records CandidX direct-encoding fuzzing.

Run using a Python environment containing wasmtime, for example:
  ../motoko_candid/research/candid-canonical/.venv/bin/python tests/fuzz.py
Replay with --seed 0xcad1d --cases 100, or --input <saved corpus.json>.
All hashing here belongs to this application-side test, not the codecs.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import re
import shutil
import shlex
import struct
import sys
import traceback

import verify
import direct
from reference import canonicalize as reference_canonicalize, values as reference_values

ROOT = Path(__file__).resolve().parent.parent
LEAVES = ["null", "reserved", "bool", "nat", "int", "nat8", "nat16", "nat32", "nat64",
          "int8", "int16", "int32", "int64", "float32", "float64", "text", "principal", "blob"]


def generate_schema(rng, depth):
    if not depth or rng.randrange(4) == 0:
        return [rng.choice(LEAVES)]
    kind = rng.choice(["opt", "vec", "record", "variant", "list"])
    if kind in ("opt", "vec"):
        return [kind, generate_schema(rng, depth - 1)]
    if kind == "list":
        return [kind, generate_schema(rng, depth - 1), False]
    return [kind, *[[label, generate_schema(rng, depth - 1)]
                    for label in "abcd"[:rng.randint(1, 3)]]]


def generate_value(rng, schema):
    kind = schema[0]
    if kind == "opt":
        return [] if rng.randrange(3) == 0 else [generate_value(rng, schema[1])]
    if kind == "vec":
        return [generate_value(rng, schema[1]) for _ in range(rng.randrange(5))]
    if kind == "record":
        return {label: generate_value(rng, child) for label, child in schema[1:]}
    if kind == "variant":
        label, child = rng.choice(schema[1:])
        return {label: generate_value(rng, child)}
    if kind == "list":
        value = []
        for _ in range(rng.randrange(6)):
            record = {"item": generate_value(rng, schema[1]), "next": value}
            if schema[2]:
                record["extra"] = []
            value = [record]
        return value
    if kind in ("null", "reserved"):
        return None
    if kind == "bool":
        return bool(rng.getrandbits(1))
    if kind in ("nat", "int"):
        bits = rng.choice([0, 1, 7, 8, 31, 32, 64, 128, 255, 512])
        n = rng.getrandbits(bits)
        if kind == "int" and rng.getrandbits(1):
            n = -n
        return str(n)
    if kind.startswith(("nat", "int")):
        signed = kind.startswith("int")
        bits = int(kind[3:])
        lo, hi = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
        n = rng.choice([lo, hi, 0, rng.randint(lo, hi)])
        return str(n) if bits == 64 else n
    if kind == "float32":
        return rng.choice(["-0", "0", "nan", "inf", "-inf", "0.1", "1.25", "-123.75",
                           "1.401298464324817e-45", "-1.401298464324817e-45", "3.4028234663852886e38"])
    if kind == "float64":
        return rng.choice(["-0", "0", "nan", "inf", "-inf", "1.25", "-123.75",
                           "4.9406564584124654e-324", "-4.9406564584124654e-324", "1.7976931348623157e308"])
    if kind == "text":
        return rng.choice(["", "abc", "a\0z", "é", "e\u0301", "🧪", "\ufeffstart", "\uffff", "東京"])
    if kind == "principal":
        return list(rng.randbytes(rng.choice([0, 1, 10, 29])))
    if kind == "blob":
        return list(rng.randbytes(rng.choice([0, 1, 2, 32])))
    raise ValueError(kind)


def extend(schema, value):
    """Recursively add only absent fields and unselected cases."""
    kind = schema[0]
    if kind == "opt":
        child, _ = extend(schema[1], None)
        return [kind, child], [extend(schema[1], value[0])[1]] if value else [] if value is not None else None
    if kind == "vec":
        child, _ = extend(schema[1], None)
        return [kind, child], [extend(schema[1], x)[1] for x in value] if value is not None else None
    if kind in ("record", "variant"):
        fields = [[label, extend(child, None)[0]] for label, child in schema[1:]]
        fields.append(["unused", ["opt", ["text"]] if kind == "record" else ["text"]])
        if value is None:
            return [kind, *fields], None
        if kind == "record":
            return [kind, *fields], {**{label: extend(child, value[label])[1] for label, child in schema[1:]}, "unused": []}
        label = next(iter(value))
        child = next(t for k, t in schema[1:] if k == label)
        return [kind, *fields], {label: extend(child, value[label])[1]}
    if kind == "list":
        child, _ = extend(schema[1], None)
        if value is None:
            return [kind, child, True], None
        if not value:
            return [kind, child, True], []
        return [kind, child, True], [{"item": extend(schema[1], value[0]["item"])[1],
                                     "next": extend(schema, value[0]["next"])[1], "extra": []}]
    return schema, value


def corpus(seed, count):
    rng = random.Random(seed)
    cases, groups = [], []

    def pair(name, schema, value):
        # Every top-level value is the SAME record across the three encoders.
        top = ["record", ["data", schema]]
        v = {"data": value}
        grown, grown_value = extend(top, v)
        names = [name + "-old", name + "-extended"]
        cases.extend([{ "id": names[0], "schema": top, "value": v },
                      { "id": names[1], "schema": grown, "value": grown_value }])
        groups.append(names)

    # Directed boundary cases are always present, independent of random size.
    for kind in LEAVES:
        if kind.startswith(("nat", "int")) and kind not in ("nat", "int"):
            bits, signed = int(kind[3:]), kind.startswith("int")
            values = [-(1 << (bits - 1)), (1 << (bits - 1)) - 1] if signed else [0, (1 << bits) - 1]
            for n in values:
                pair(f"edge-{kind}-{n}", [kind], str(n) if bits == 64 else n)
        else:
            for i in range(2):
                pair(f"edge-{kind}-{i}", [kind], generate_value(rng, [kind]))
    for kind in ("nat", "text", "null", "blob"):
        pair(f"empty-vector-{kind}", ["vec", [kind]], [])
    pair("binary-empty", ["blob"], [])
    pair("nested-options", ["vec", ["opt", ["opt", ["nat"]]]], [[], [[]], [["7"]], []])
    pair("mixed-record-array", ["vec", ["record", ["a", ["text"]], ["b", ["opt", ["text"]]], ["c", ["opt", ["opt", ["nat"]]]]]],
         [{"a": "abc", "b": [], "c": []}, {"a": "xyz", "b": ["present"], "c": [[]]}, {"a": "z", "b": [""], "c": [["7"]]}])
    pair("variant-array", ["vec", ["variant", ["a", ["null"]], ["b", ["opt", ["nat"]]], ["c", ["text"]]]],
         [{"a": None}, {"b": []}, {"b": ["128"]}, {"c": "é"}])
    pair("variant-two-nats", ["vec", ["variant", ["a", ["nat8"]], ["b", ["nat8"]]]], [{"a": 255}, {"b": 0}])
    pair("variant-two-nats-reversed", ["vec", ["variant", ["a", ["nat8"]], ["b", ["nat8"]]]], [{"b": 0}, {"a": 255}])
    pair("option-none", ["opt", ["opt", ["text"]]], [])
    pair("option-some-none", ["opt", ["opt", ["text"]]], [[]])
    pair("option-some-some-empty", ["opt", ["opt", ["text"]]], [[""]])
    pair("one-nat", ["nat"], "1")
    pair("one-nat64", ["nat64"], "1")
    for text in ["\ufeffstart", "a\0z", "é", "e\u0301", "🧪"]:
        pair(f"text-{text.encode().hex()}", ["text"], text)
    for f in ["nan", "inf", "-inf", "-0", "0", "4.9406564584124654e-324", "-4.9406564584124654e-324", "1.7976931348623157e308"]:
        pair(f"float-{f}", ["float64"], f)
    pair("float32-nested", ["vec", ["record", ["value", ["float32"]], ["optional", ["opt", ["float32"]]]]],
         [{"value": "-0", "optional": []}, {"value": "0.1", "optional": ["nan"]},
          {"value": "1.401298464324817e-45", "optional": ["-inf"]}])
    for i in range(count):
        schema = generate_schema(rng, 4)
        if i % 3 == 0:
            schema = ["vec", schema]
        pair(f"random-{i}", schema, generate_value(rng, schema))
    distinct = [["option-none-old", "option-some-none-old"],
                ["option-some-none-old", "option-some-some-empty-old"],
                ["one-nat-old", "one-nat64-old"],
                ["float--0-old", "float-0-old"],
                ["binary-empty-old", "empty-vector-nat-old"],
                ["text-c3a9-old", "text-65cc81-old"],
                ["variant-two-nats-old", "variant-two-nats-reversed-old"]]
    return {"seed": seed, "random_records": count, "cases": cases, "equal_groups": groups, "distinct_pairs": distinct}


def mo_literal(text):
    return '"' + ''.join(f"\\{byte:02x}" for byte in text.encode()) + '"'


class MotokoSource:
    def __init__(self):
        self.defs, self.counter = [], 0
        self.comparators, self.functions = {}, []

    def ty(self, schema):
        kind = schema[0]
        if kind == "opt":
            return "?" + self.ty(schema[1])
        if kind == "vec":
            return "[" + self.ty(schema[1]) + "]"
        if kind in ("record", "variant"):
            tag = "#" if kind == "variant" else ""
            return "{ " + "; ".join(tag + k + " : " + self.ty(t) for k, t in schema[1:]) + " }"
        if kind == "list":
            name = f"Rec{self.counter}"
            self.counter += 1
            item = self.ty(schema[1])
            extra = "; extra : ?Text" if schema[2] else ""
            self.defs.append(f"type {name} = ?{{ item : {item}; next : {name}{extra} }};")
            return name
        if kind == "chain":
            name = f"Rec{self.counter}"
            self.counter += 1
            self.defs.append(f"type {name} = {{ head : Nat; tail : ?{name} }};")
            return name
        if kind == "tuple":
            return "(" + ", ".join(self.ty(child) for child in schema[1:]) + ")"
        return {"reserved": "Any", "float32": "Float32", "float32bits": "Float32",
                "float64": "Float", "float64bits": "Float", "blob": "Blob"}.get(kind, kind[0].upper() + kind[1:])

    def val(self, schema, value):
        kind = schema[0]
        if kind == "opt":
            return "?(" + self.val(schema[1], value[0]) + ")" if value else "null"
        if kind == "vec":
            return "[" + ", ".join(self.val(schema[1], x) for x in value) + "]"
        if kind == "blob":
            return verify.mo_blob(bytes(value).hex())
        if kind == "record":
            return "{ " + "; ".join(k + " = " + self.val(t, value[k]) for k, t in schema[1:]) + " }"
        if kind == "variant":
            name = next(iter(value))
            child = next(t for k, t in schema[1:] if k == name)
            return "#" + name + "(" + self.val(child, value[name]) + ")"
        if kind == "list":
            if not value:
                return "null"
            extra = "; extra = " + self.val(["opt", ["text"]], value[0]["extra"]) if schema[2] else ""
            return "?{ item = " + self.val(schema[1], value[0]["item"]) + "; next = " + self.val(schema, value[0]["next"]) + extra + " }"
        if kind == "chain":
            tail = "?(" + self.val(schema, value["tail"][0]) + ")" if value["tail"] else "null"
            return "{ head = " + value["head"] + "; tail = " + tail + " }"
        if kind == "tuple":
            return "(" + ", ".join(self.val(child, item) for child, item in zip(schema[1:], value)) + ")"
        if kind in ("null", "reserved"):
            return "null"
        if kind == "bool":
            return "true" if value else "false"
        if kind.startswith("float"):
            if kind.endswith("bits"):
                width = 32 if kind.startswith("float32") else 64
                bits = int(value, 0) if value.startswith("0x") else int(value)
                value = repr(struct.unpack("<f" if width == 32 else "<d", bits.to_bytes(width // 8, "little"))[0])
            literal = {"nan": "(0.0 / 0.0)", "inf": "(1.0 / 0.0)", "-inf": "(-1.0 / 0.0)", "-0": "-0.0", "0": "0.0"}.get(value, value)
            return "Prim.floatToFloat32(" + literal + ")" if kind.startswith("float32") else literal
        if kind == "text":
            return mo_literal(value)
        if kind == "principal":
            return "Prim.principalOfBlob(" + verify.mo_blob(bytes(value).hex()) + ")"
        return str(value)

    def equal(self, schema):
        """Compare native values using their schema, without encoding either."""
        key = json.dumps(schema, separators=(',', ':'))
        if key in self.comparators:
            return self.comparators[key]
        name = f"sameTyped{len(self.comparators)}"
        self.comparators[key] = name
        kind = schema[0]
        typename = self.ty(schema)
        parameters = "a", "b"
        if kind in ("null", "reserved") or (kind in ("record", "tuple") and len(schema) == 1):
            parameters, body = ("_a", "_b"), "true"
        elif kind in ("float32", "float32bits"):
            body = "sameFloat(Prim.float32ToFloat(a), Prim.float32ToFloat(b))"
        elif kind in ("float64", "float64bits"):
            body = "sameFloat(a, b)"
        elif kind == "opt":
            child = self.equal(schema[1])
            body = ("switch (a, b) { case (null, null) true; "
                    f"case (?left, ?right) {child}(left, right); case _ false }}")
        elif kind == "vec":
            child = self.equal(schema[1])
            body = ("if (a.size() != b.size()) return false; var i = 0; "
                    f"while (i < a.size()) {{ if (not {child}(a[i], b[i])) return false; i += 1 }}; true")
        elif kind == "record":
            body = " and ".join(f"{self.equal(child)}(a.{label}, b.{label})" for label, child in schema[1:])
        elif kind == "tuple":
            body = " and ".join(f"{self.equal(child)}(a.{index}, b.{index})"
                                for index, child in enumerate(schema[1:]))
        elif kind == "variant":
            branches = [f"case (#{label}(left), #{label}(right)) {self.equal(child)}(left, right)"
                        for label, child in schema[1:]]
            if len(branches) > 1:
                branches.append("case _ false")
            body = "switch (a, b) { " + "; ".join(branches) + " }"
        elif kind == "list":
            extra = f" and {self.equal(['opt', ['text']])}(left.extra, right.extra)" if schema[2] else ""
            body = ("switch (a, b) { case (null, null) true; case (?left, ?right) { "
                    f"{self.equal(schema[1])}(left.item, right.item) and {name}(left.next, right.next){extra}"
                    " }; case _ false }")
        elif kind == "chain":
            body = (f"{self.equal(['nat'])}(a.head, b.head) and "
                    "(switch (a.tail, b.tail) { case (null, null) true; "
                    f"case (?left, ?right) {name}(left, right); case _ false }})")
        else:
            body = "a == b"
        a, b = parameters
        self.functions.append(f"func {name}({a} : {typename}, {b} : {typename}) : Bool {{ {body} }};")
        return name


PRINT_MO = '''
// IEEE equality fixes every non-NaN bit pattern except signed zero. The
// reciprocal distinguishes its sign; NaNs compare by class as cx specifies.
func sameFloat(a : Float, b : Float) : Bool {
  if (a != a) { b != b } else { a == b and (a != 0.0 or 1.0 / a == 1.0 / b) }
};
func printBlob(name : Text, bytes : Blob) {
  let data = Prim.blobToArray(bytes); var offset = 0; var chunk = 0;
  while (offset < data.size()) {
    let left = Prim.abs((data.size() : Int) - offset);
    let n = if (left > 128) 128 else left;
    let part = Prim.arrayToBlob(Prim.Array_tabulate<Nat8>(n, func(i) { data[offset + i] }));
    Prim.debugPrint(name # ":" # debug_show(chunk) # " " # debug_show(part));
    offset += n; chunk += 1;
  }
};
func normalize(raw : Blob) : Blob {
  switch (CandidX.canonicalize(raw)) { case (#ok(bytes)) bytes; case (#err(e)) Prim.trap(e) }
};
'''


def motoko_encode_source(cases, path, runtime_bridge=False):
    renderer = MotokoSource()
    bodies = []
    for i, c in enumerate(cases):
        typename = f"Case{i}"
        renderer.defs.append(f"type {typename} = {renderer.ty(c['schema'])};")
        compare = renderer.equal(c['schema'])
        direct_code = '  let direct = to_candid_canonical(value);\n' if runtime_bridge else ''
        direct_check = '  assert direct == canonical;\n  printBlob("direct:' + c['id'] + '", direct);\n' if runtime_bridge else ''
        direct_recovery = '  recoverOriginal(direct);\n' if runtime_bridge else ''
        bodies.append(f'''do {{
  let value : {typename} = {renderer.val(c['schema'], c['value'])};
{direct_code}\
  let raw = to_candid(value);
  let canonical = normalize(raw);
{direct_check}\
  assert normalize(canonical) == canonical;
  func recoverOriginal(encoded : Blob) {{
    switch (from_candid(encoded) : ?{typename}) {{
      case (?decoded) {{
        if (not {compare}(value, decoded)) Prim.trap("original value roundtrip loss {c['id']}");
        if (normalize(to_candid(decoded)) != canonical) Prim.trap("decode loss {c['id']}");
      }};
      case (null) Prim.trap("decode failure {c['id']}");
    }};
  }};
  recoverOriginal(canonical);
  recoverOriginal(raw);
{direct_recovery}\
  printBlob("raw:{c['id']}", raw);
  printBlob("canonical:{c['id']}", canonical);
}};''')
    import_path = os.path.relpath(ROOT / "motoko/src/lib", path.parent)
    source = ['import Prim "mo:⛔";', f'import CandidX "{import_path}";', PRINT_MO,
              *renderer.defs, *renderer.functions, *bodies]
    path.write_text('\n'.join(source) + '\n')


def motoko_direct_source(cases, path, compiled_controls=True):
    """Direct compiler calls from typed native values, including multiple roots."""
    renderer, bodies = MotokoSource(), []
    for i, case in enumerate(cases):
        schemas, values = direct.arguments(case)
        names, recovery_names, variables, declarations = [], [], [], []
        for j, (schema, value) in enumerate(zip(schemas, values)):
            name = f"DirectCase{i}Root{j}"
            renderer.defs.append(f"type {name} = {renderer.ty(schema)};")
            names.append(name)
            if schema[0] == 'tuple':
                recovery = f"DirectCase{i}Recovery{j}"
                fields = "; ".join(f"_{index}_ : {renderer.ty(child)}" for index, child in enumerate(schema[1:]))
                renderer.defs.append(f"type {recovery} = {{ {fields} }};")
                recovery_names.append(recovery)
            else:
                recovery_names.append(name)
            variables.append(f"value{j}")
            declarations.append(f"  let value{j} : {name} = {renderer.val(schema, value)};")
            renderer.equal(schema)
        args = ", ".join(variables)
        if not compiled_controls:
            bodies.append(f'''do {{
{chr(10).join(declarations)}
  let canonical = to_candid_canonical({args});
  printBlob("canonical:{case['id']}", canonical);
}};''')
            continue
        recovered_type = recovery_names[0] if len(names) == 1 else "(" + ", ".join(recovery_names) + ")"
        recovered = []
        for j, schema in enumerate(schemas):
            root = 'decoded' if len(names) == 1 else f'decoded.{j}'
            # from_candid interprets a top-level tuple annotation as multiple
            # roots. Recover a single Candid tuple record using numeric fields,
            # then rebuild the native tuple value for direct re-encoding.
            recovered.append('(' + ', '.join(f'{root}._{index}_' for index in range(len(schema) - 1)) + ')'
                             if schema[0] == 'tuple' else root)
        recovered_args = ", ".join(recovered)
        semantic_check = " and ".join(f"{renderer.equal(schema)}({original}, {decoded})"
                                      for schema, original, decoded in zip(schemas, variables, recovered)) or "true"
        recovered_name = "decoded" if names else "_decoded"
        bodies.append(f'''do {{
{chr(10).join(declarations)}
  let canonical = to_candid_canonical({args});
  let raw = to_candid({args});
  printBlob("raw:{case['id']}", raw);
  printBlob("canonical:{case['id']}", canonical);
  if (canonical != normalize(raw)) Prim.trap("direct bytes differ {case['id']}");
  func recoverOriginal(encoded : Blob) {{
    switch (from_candid(encoded) : ?{recovered_type}) {{
      case (?{recovered_name}) {{
        if (not ({semantic_check})) Prim.trap("original direct roundtrip loss {case['id']}");
        assert to_candid_canonical({recovered_args}) == canonical;
      }};
      case (null) Prim.trap("direct decoder failure {case['id']}");
    }};
  }};
  recoverOriginal(canonical);
  recoverOriginal(raw);
}};''')
    import_path = os.path.relpath(ROOT / "motoko/src/lib", path.parent)
    imports = ['import Prim "mo:⛔";'] + ([f'import CandidX "{import_path}";'] if compiled_controls else [])
    printer = PRINT_MO if compiled_controls else PRINT_MO[:PRINT_MO.index('func normalize(')]
    path.write_text('\n'.join([*imports, printer,
                               *renderer.defs, *renderer.functions, *bodies]) + '\n')


def wasm_direct_results(cases, artifact):
    sys.path.insert(0, str(ROOT))
    from wasm.runtime import TypedEncoder
    encoder = TypedEncoder(artifact)
    return {case['id']: {'id': case['id'], 'canonical': encoder.encode_args(direct.native_trees(case)).hex(),
                        'decoding': 'not-available', 'direct_path': 'typed-values'} for case in cases}


def run_direct_corpus(out, name="direct", compiler=None, core=None, batch_size=64, repetitions=2, wasm_artifact=None):
    cases = direct.directed_cases()
    path = out / f"{name}-corpus.json"
    path.write_text(json.dumps({"profile": "cx", "cases": cases}, ensure_ascii=False, indent=2) + '\n')
    sources = {
        'rust': load_results(verify.command([ROOT / 'rust/target/debug/examples/fuzz', path], name + '-rust')),
        'ts': load_results(verify.command(['node', ROOT / 'ts/test/fuzz.mjs', path], name + '-ts')),
        'ocaml': load_results(verify.command([ROOT / 'ocaml/_build/default/direct_probe.exe', path], name + '-ocaml')),
    }
    if wasm_artifact and wasm_artifact.exists():
        sources['wasm'] = wasm_direct_results(cases, wasm_artifact)
    if compiler:
        sources['compiler'] = {}
        moc = [compiler, '--package', 'core', core, '-wasi-system-api']
        for batch, start in enumerate(range(0, len(cases), batch_size)):
            subset = cases[start:start + batch_size]
            source = out / f'{name}-compiler-{batch}.mo'
            motoko_direct_source(subset, source)
            wasm_path = source.with_suffix('.wasm')
            verify.command(moc + ['-o', wasm_path, source], f'{name}-compiler-build-{batch}')
            result = verify.parse_probe(verify.wasm(wasm_path, f'{name}-compiler-{batch}', repetitions), True)
            for case in subset:
                sources['compiler'][case['id']] = {'id': case['id'], 'raw': result['raw:' + case['id']].hex(),
                    'canonical': result['canonical:' + case['id']].hex(), 'decoding': 'exact',
                    'canonical_roundtrip': True, 'ordinary_roundtrip': True, 'direct_path': 'typed-values'}
    rows = direct.verify_outputs(cases, sources)
    for case in cases:
        assert sources['ts'][case['id']].get('client_canonical_checks') == len(direct.arguments(case)[0]) * 20, case['id']
    roundtrips = cross_decode(cases, sources, out, name)
    (out / f'{name}-records.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    return {'status': 'pass', 'typed_cases_per_implementation': len(cases), 'frozen_golden_cases': len(direct.frozen_cases()),
            'implementations': list(sources), 'expected_bytes': 'independent source-value oracle and unchanged frozen goldens',
            'external_hash': 'Python hashlib.sha256 over complete bytes',
            'client_canonical_byte_checks': sum(row['client_canonical_checks'] for row in sources['ts'].values()),
            'ordinary_decoders': {'rust': 'exact', 'ts': 'decodeCompatible exact; upstream caveats recorded',
                                  'ocaml': 'not available', **({'compiler': 'exact'} if compiler else {})},
            'original_value_roundtrips': roundtrips, 'constructor_coverage': constructor_coverage(cases),
            'corpus_sha256': sha256(path.read_bytes()).hexdigest(),
            'records_sha256': sha256((out / f'{name}-records.json').read_bytes()).hexdigest()}


def load_results(text):
    return {c['id']: c for c in map(json.loads, text.splitlines())}


def constructor_coverage(cases):
    declared, visited, observations = Counter(), Counter(), Counter()
    roots = 0

    def schema_visit(schema):
        kind = schema[0].removesuffix('bits')
        declared[kind] += 1
        if kind in ('opt', 'vec', 'list'):
            schema_visit(schema[1])
        elif kind in ('record', 'variant'):
            for _, child in schema[1:]:
                schema_visit(child)
        elif kind == 'tuple':
            for child in schema[1:]:
                schema_visit(child)

    def value_visit(schema, value):
        kind = schema[0].removesuffix('bits')
        visited[kind] += 1
        if kind == 'opt':
            observations['option.some' if value else 'option.none'] += 1
            if value:
                if schema[1][0] == 'opt' and not value[0]:
                    observations['option.some-none'] += 1
                value_visit(schema[1], value[0])
        elif kind in ('vec', 'blob'):
            observations[f'{kind}.nonempty' if value else f'{kind}.empty'] += 1
            if kind == 'blob' or schema[1][0] == 'nat8':
                observations['binary.nonempty' if value else 'binary.empty'] += 1
            if kind == 'vec':
                for item in value:
                    value_visit(schema[1], item)
        elif kind == 'record':
            observations['record.nonempty' if schema[1:] else 'record.empty'] += 1
            for label, child in schema[1:]:
                item = value[str(label) if isinstance(label, int) else label]
                if child[0] in ('opt', 'list') and not item:
                    observations['record.absent-optional-field'] += 1
                value_visit(child, item)
        elif kind == 'variant':
            label = next(iter(value))
            observations['variant.selected-' + label] += 1
            value_visit(next(child for name, child in schema[1:] if name == label), value[label])
        elif kind == 'tuple':
            for child, item in zip(schema[1:], value):
                value_visit(child, item)
        elif kind == 'list':
            observations['recursive-list.some' if value else 'recursive-list.none'] += 1
            if value:
                value_visit(schema[1], value[0]['item'])
                value_visit(schema, value[0]['next'])
        elif kind == 'chain':
            observations['recursive-chain.nonleaf' if value['tail'] else 'recursive-chain.leaf'] += 1
            value_visit(['nat'], value['head'])
            if value['tail']:
                value_visit(schema, value['tail'][0])
        elif kind.startswith('float'):
            bits = int.from_bytes(direct.float_payload(schema[0], value), 'little')
            fraction, exponent = (23, 8) if kind == 'float32' else (52, 11)
            payload, exp = bits & ((1 << fraction) - 1), bits >> fraction & ((1 << exponent) - 1)
            cls = ('nan' if payload else 'infinity') if exp == (1 << exponent) - 1 else \
                  ('subnormal' if payload else ('negative-zero' if bits >> (fraction + exponent) else 'positive-zero')) if not exp else 'finite'
            observations[f'{kind}.{cls}'] += 1
        elif kind == 'text':
            for flag, present in [('empty', not value), ('nul', '\0' in value), ('bom', '\ufeff' in value),
                                  ('non-ascii', any(ord(char) > 127 for char in value))]:
                if present:
                    observations['text.' + flag] += 1
        elif kind == 'principal':
            observations['principal.nonempty' if value else 'principal.empty'] += 1

    for case in cases:
        schemas, values = direct.arguments(case)
        roots += len(schemas)
        observations['arguments.zero' if not schemas else 'arguments.multiple' if len(schemas) > 1 else 'arguments.single'] += 1
        for schema, value in zip(schemas, values):
            schema_visit(schema)
            value_visit(schema, value)
    return {'basis': 'source witness DSL kinds; list and chain are recursive shorthands',
            'cases': len(cases), 'argument_roots': roots, 'declared_kinds': dict(sorted(declared.items())),
            'visited_kinds': dict(sorted(visited.items())), 'observations': dict(sorted(observations.items()))}


def cross_decode(cases, sources, out, name):
    """Decode each actual encoder's bytes against separately built originals."""
    inputs = [{**case, 'decode_inputs': {encoder: source[case['id']]['canonical']
                                      for encoder, source in sources.items()}} for case in cases]
    path = out / f'{name}-roundtrip-inputs.json'
    path.write_text(json.dumps({'profile': 'cx', 'decode_only': True, 'cases': inputs}, ensure_ascii=False, indent=2) + '\n')
    outputs = {
        'rust-candid': load_results(verify.command([ROOT / 'rust/target/debug/examples/fuzz', path], name + '-decode-rust')),
        'typescript-decodeCompatible': load_results(verify.command(['node', ROOT / 'ts/test/fuzz.mjs', path], name + '-decode-ts')),
    }
    roots_by_id = {case['id']: len(direct.arguments(case)[0]) for case in cases}
    ids, encoders = set(roots_by_id), set(sources)
    for decoder, rows in outputs.items():
        assert set(rows) == ids, (decoder, 'roundtrip case IDs')
        for row in rows.values():
            assert not row.get('error'), (decoder, row)
            assert set(row.get('roundtrips', {})) == encoders, (decoder, row)
            assert all(status == 'exact' for status in row['roundtrips'].values()), (decoder, row)
            if decoder == 'typescript-decodeCompatible':
                roots = roots_by_id[row['id']]
                assert row.get('client_canonical_checks') == {encoder: roots * 2 for encoder in encoders}, (decoder, row)
    records = out / f'{name}-roundtrips.json'
    records.write_text(json.dumps(outputs, ensure_ascii=False, indent=2) + '\n')
    return {'status': 'pass', 'cases': len(cases), 'successful_original_value_recoveries': len(cases) * len(sources) * len(outputs),
            'per_encoder': {encoder: {decoder: len(cases) for decoder in outputs} for encoder in sources},
            'equality': 'independently constructed original typed values; finite float bits including signed zero; NaN class; Reserved ignored',
            'ocaml_ordinary_decoder': 'not available; its actual output bytes are checked by Rust and TypeScript decoders',
            'standalone_wasm_ordinary_decoder': 'not supplied; its actual output bytes are checked by host Rust and TypeScript decoders',
            'client_canonical_byte_checks': {encoder: sum(row['client_canonical_checks'][encoder]
                for row in outputs['typescript-decodeCompatible'].values()) for encoder in encoders},
            'inputs_sha256': sha256(path.read_bytes()).hexdigest(), 'records_sha256': sha256(records.read_bytes()).hexdigest()}


def float32_edges():
    # Keep the original wire-level float32 controls in addition to the direct
    # native Float32 cases in the separate typed corpus.
    header = bytes.fromhex('4449444c016c01aaac8d9304730100')
    bits = [0, 0x80000000, 1, 0x80000001, 0x00800000, 0x80800000,
            0x7f7fffff, 0xff7fffff, 0x7f800000, 0xff800000,
            0x7f800001, 0xff800001, 0x7fc12345, 0xffdabcde]
    cases = []
    for n in bits:
        nan = (n & 0x7f800000) == 0x7f800000 and (n & 0x007fffff) != 0
        expected = 0x7fc00000 if nan else n
        cases.append({'id': f'float32-wire-{n:08x}', 'raw': (header + n.to_bytes(4, 'little')).hex(),
                      'canonical': (header + expected.to_bytes(4, 'little')).hex()})
    return cases


def fingerprints():
    # Use the same production/test provenance map for full verification,
    # seeded fuzzing, and extracted-package checks. Generated build outputs
    # such as Dune's *.install files are excluded centrally.
    return verify.source_fingerprints()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=lambda x: int(x, 0), default=0xcad1d)
    parser.add_argument('--cases', type=int, default=100)
    parser.add_argument('--batch-size', type=int, default=64)
    parser.add_argument('--input', type=Path, help='replay a saved corpus JSON')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--moc', type=Path, default=ROOT.parent / 'motoko_candid/bin/moc')
    parser.add_argument('--core', type=Path, default=ROOT.parent / 'motoko-core/src')
    parser.add_argument('--motoko-repo', type=Path, default=ROOT.parent / 'motoko_candid',
                        help='Nix development shell used when OCaml/Dune are absent from PATH')
    parser.add_argument('--rust-toolchain', default='1.98.1')
    parser.add_argument('--skip-build', action='store_true')
    parser.add_argument('--runtime-moc', type=Path,
                        help='exercise direct to_candid_canonical with this compiler; inbound checks use the native library')
    parser.add_argument('--wasm-artifact', type=Path, default=ROOT / 'wasm/artifacts/cx.wasm')
    parser.add_argument('--skip-wasm', action='store_true')
    args = parser.parse_args()
    if args.cases < 0 or args.batch_size < 1:
        parser.error('cases must be nonnegative; batch size must be positive')
    data = json.loads(args.input.read_text()) if args.input else corpus(args.seed, args.cases)
    seed = data['seed']
    out = (args.out or ROOT / '_out' / f'fuzz-{seed:x}-{data["random_records"]}').resolve()
    out.mkdir(parents=True, exist_ok=True)
    verify.OUT = out
    corpus_path = out / 'corpus.json'
    corpus_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    starting_sources = fingerprints()

    def fail(message, case=None):
        (out / 'failure.json').write_text(json.dumps({'seed': seed, 'message': message, 'case': case,
            'replay': f'{sys.executable} tests/fuzz.py --input {corpus_path}'}, ensure_ascii=False, indent=2) + '\n')
        raise AssertionError(message)

    try:
        rustc = verify.command(['rustup', 'which', '--toolchain', args.rust_toolchain, 'rustc'], 'rustc-path').strip()
        rustdoc = verify.command(['rustup', 'which', '--toolchain', args.rust_toolchain, 'rustdoc'], 'rustdoc-path').strip()
        env = {**os.environ, 'RUSTC': rustc, 'RUSTDOC': rustdoc}
        if not args.skip_build:
            verify.command(['rustup', 'run', args.rust_toolchain, 'cargo', 'build', '--locked', '--features', 'fuzz', '--example', 'fuzz', '--example', 'probe', '--manifest-path', ROOT / 'rust/Cargo.toml'], 'rust-build', env=env)
            verify.command(['npm', 'run', 'build'], 'ts-build', cwd=ROOT / 'ts')
            if shutil.which('dune') and shutil.which('ocamlc'):
                verify.command(['dune', 'build'], 'ocaml-build', cwd=ROOT / 'ocaml')
            else:
                verify.command(['nix', 'develop', '--command', 'dune', 'build', '--root', ROOT / 'ocaml'], 'ocaml-build', cwd=args.motoko_repo)
        import roundtrip_guard
        comparison_guards = roundtrip_guard.run(out, compiler=args.runtime_moc or args.moc, core=args.core)
        sources = {
            'rust': load_results(verify.command([ROOT / 'rust/target/debug/examples/fuzz', corpus_path], 'rust-typed')),
            'ts': load_results(verify.command(['node', ROOT / 'ts/test/fuzz.mjs', corpus_path], 'ts-typed')),
            'ocaml': load_results(verify.command([ROOT / 'ocaml/_build/default/direct_probe.exe', corpus_path], 'ocaml-typed')),
            'motoko': {},
        }
        if not args.skip_wasm and args.wasm_artifact.exists():
            sources['wasm'] = wasm_direct_results(data['cases'], args.wasm_artifact)
        if args.runtime_moc:
            sources['compiler'] = {}
        cases = data['cases']
        compiler = args.runtime_moc or args.moc
        moc = [compiler, '--package', 'core', args.core, '-wasi-system-api']
        for batch, start in enumerate(range(0, len(cases), args.batch_size)):
            path = out / f'typed-{batch}.mo'
            motoko_encode_source(cases[start:start + args.batch_size], path, bool(args.runtime_moc))
            wasm_path = path.with_suffix('.wasm')
            verify.command(moc + ['-o', wasm_path, path], f'motoko-typed-build-{batch}')
            result = verify.parse_probe(verify.wasm(wasm_path, f'motoko-typed-{batch}', 2), True)
            for c in cases[start:start + args.batch_size]:
                sources['motoko'][c['id']] = {'id': c['id'], 'raw': result['raw:' + c['id']].hex(),
                                             'canonical': result['canonical:' + c['id']].hex(), 'decoding': 'exact',
                                             'canonical_roundtrip': True, 'ordinary_roundtrip': True}
                if args.runtime_moc:
                    sources['compiler'][c['id']] = {'id': c['id'], 'raw': result['raw:' + c['id']].hex(),
                        'canonical': result['direct:' + c['id']].hex(), 'decoding': 'exact',
                        'canonical_roundtrip': True, 'direct_path': 'typed-values'}
            print(f'typed native Motoko: {min(start + args.batch_size, len(cases))}/{len(cases)}', flush=True)
        expected_ids = {c['id'] for c in cases}
        for lang, values in sources.items():
            if set(values) != expected_ids:
                fail(f'{lang}: wrong case IDs')
        direct.verify_outputs(cases, {lang: source for lang, source in sources.items() if lang != 'motoko'})
        roundtrips = cross_decode(cases, sources, out, 'shared')
        decoders = []
        rows, messages = [], []
        for c in cases:
            values = {lang: source[c['id']] for lang, source in sources.items()}
            if any(v.get('error') for v in values.values()):
                fail(f'{c["id"]}: encoder error', {'input': c, 'outputs': values})
            if values['ts'].get('compatible_decoding') != 'exact':
                fail(f'{c["id"]}: compatible SDK decoder recovery failure', {'input': c, 'output': values['ts']})
            if values['ts'].get('client_canonical_checks') != len(direct.arguments(c)[0]) * 20:
                fail(f'{c["id"]}: CanonicalBytes client verification incomplete', {'input': c, 'output': values['ts']})
            canons = {v['canonical'] for v in values.values()}
            if len(canons) != 1:
                fail(f'{c["id"]}: canonical bytes disagree', {'input': c, 'outputs': values})
            canonical = canons.pop()
            expected = direct.expected_bytes(c)
            if bytes.fromhex(canonical) != expected:
                fail(f'{c["id"]}: source-value oracle disagreement', {'input': c, 'outputs': values, 'expected': expected.hex()})
            digests = {lang: sha256(bytes.fromhex(v['canonical'])).hexdigest() for lang, v in values.items()}
            if len(set(digests.values())) != 1:
                fail(f'{c["id"]}: application hash mismatch', {'input': c, 'outputs': values})
            for lang, v in values.items():
                if lang in ('ocaml', 'wasm'):
                    if v['decoding'] != 'not-available':
                        fail(f'{c["id"]}: {lang} claims an unavailable decoder', v)
                elif v['decoding'] != 'exact':
                    decoders.append({'id': c['id'], 'language': lang, 'status': v['decoding']})
                    if v['decoding'] not in ('upstream-decoder-loss', 'sdk-recursive-optional-field-loss'):
                        fail(f'{c["id"]}: {lang} canonical decoder regression', {'input': c, 'output': v})
                if 'raw' in v:
                    raw = bytes.fromhex(v['raw'])
                    if reference_canonicalize(raw) != expected or reference_values(raw) != direct.normalized_values(c):
                        fail(f'{c["id"]}: {lang} ordinary data disagrees with typed source', {'input': c, 'output': v})
                    messages.append({'id': f'{c["id"]}-{lang}', 'raw': v['raw'], 'canonical': canonical})
            messages.append({'id': c['id'] + '-idempotent', 'raw': canonical, 'canonical': canonical})
            rows.append({'id': c['id'], 'raw': {lang: v['raw'] for lang, v in values.items() if 'raw' in v},
                         'direct': {lang: v['canonical'] for lang, v in values.items() if lang != 'motoko'},
                         'canonical': canonical, 'application_sha256': digests['rust']})
        lookup = {row['id']: row for row in rows}
        for group in data['equal_groups']:
            if len({lookup[name]['canonical'] for name in group}) != 1:
                fail(f'unused schema extension changes {group}', {'cases': [c for c in cases if c['id'] in group], 'outputs': [lookup[name] for name in group]})
        for a, b in data.get('distinct_pairs', []):
            if lookup[a]['canonical'] == lookup[b]['canonical'] or lookup[a]['application_sha256'] == lookup[b]['application_sha256']:
                fail(f'distinct data collapsed: {a}/{b}', {'a': lookup[a], 'b': lookup[b]})
        (out / 'records.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
        direct_report = run_direct_corpus(out, compiler=args.runtime_moc, core=args.core, batch_size=args.batch_size,
            wasm_artifact=args.wasm_artifact if not args.skip_wasm else None)
        float_cases = float32_edges()
        (out / 'float32-edges.json').write_text(json.dumps(float_cases, indent=2) + '\n')
        messages.extend(float_cases)
        wasm_backend = None
        if not args.skip_wasm and args.wasm_artifact.exists():
            sys.path.insert(0, str(ROOT))
            from wasm.runtime import Canonicalizer
            runtime = Canonicalizer(args.wasm_artifact)
            for message in messages:
                actual = runtime.canonicalize(bytes.fromhex(message['raw']))
                if actual.hex() != message['canonical']:
                    fail('Rust/Wasm: normalizer disagreement', message)
                if sha256(actual).hexdigest() != sha256(bytes.fromhex(message['canonical'])).hexdigest():
                    fail('Rust/Wasm: digest disagreement', message)
            wasm_backend = {'status': 'pass', 'messages': len(messages), 'shared_codec': 'Rust',
                            'artifact_sha256': sha256(args.wasm_artifact.read_bytes()).hexdigest()}
        for lang, command in {
            'rust': [ROOT / 'rust/target/debug/examples/probe'],
            'ts': ['node', ROOT / 'ts/test/probe.mjs'],
            'ocaml': [ROOT / 'ocaml/_build/default/probe.exe'],
        }.items():
            output = verify.command(command, lang + '-cross-normalize', input_text=''.join(c['raw'] + '\n' for c in messages)).splitlines()
            if len(output) != len(messages):
                fail(f'{lang}: wrong cross-normalization output count')
            for actual, message in zip(output, messages):
                if actual != 'ok ' + message['canonical']:
                    fail(f'{lang}: normalizer disagreement for {message["id"]}', {'message': message, 'actual': actual})
                # SHA equality follows from byte equality; compute it explicitly
                # to make the user's application-level assertion reviewable.
                if sha256(bytes.fromhex(actual[3:])).hexdigest() != sha256(bytes.fromhex(message['canonical'])).hexdigest():
                    fail(f'{lang}: digest disagreement', message)
        for batch, start in enumerate(range(0, len(messages), args.batch_size * 4)):
            subset = messages[start:start + args.batch_size * 4]
            source = verify.motoko_corpus(subset)
            # Keep each generated source/binary alongside its seed artifacts.
            path = out / f'normalize-{batch}.mo'
            source.rename(path)
            # verify.motoko_corpus expects one level below root by default.
            text = path.read_text().replace('"../motoko/src/lib"', '"' + os.path.relpath(ROOT / 'motoko/src/lib', out) + '"')
            path.write_text(text)
            wasm_path = path.with_suffix('.wasm')
            verify.command(moc + ['-o', wasm_path, path], f'motoko-normalize-build-{batch}')
            output = verify.wasm(wasm_path, f'motoko-normalize-{batch}', 2)
            if f'native-motoko-corpus-passed {len(subset)}' not in output:
                fail(f'Motoko: incomplete batch {batch}')
            print(f'all four normalize/hash: {min(start + len(subset), len(messages))}/{len(messages)}', flush=True)
        report = {'status': 'pass', 'profile': 'cx', 'seed': seed, 'seed_hex': hex(seed), 'random_records': data['random_records'],
                  'typed_record_cases': len(cases), 'unused_schema_extension_pairs': len(data['equal_groups']),
                  'distinct_data_pairs': len(data.get('distinct_pairs', [])),
                  'raw_encoders': ['Rust candid', '@icp-sdk/core IDL', 'native Motoko to_candid'],
                  'direct_encoders': ['Rust', 'TypeScript', 'OCaml'] + (['Motoko compiler/RTS'] if args.runtime_moc else [])
                      + (['Rust/Wasm typed-memory embedding'] if 'wasm' in sources else []),
                  'direct_typed_cases': direct_report,
                  'original_value_roundtrips': roundtrips, 'constructor_coverage': constructor_coverage(cases),
                  'roundtrip_comparison_guards': comparison_guards,
                  'self_canonical_roundtrips': {lang: sum(source[case['id']].get('canonical_roundtrip') is True for case in cases)
                                               for lang, source in sources.items()},
                  'native_motoko_adapter': 'temporary ordinary to_candid plus canonicalize fallback',
                  'normalizers': ['Rust', 'TypeScript', 'OCaml', 'native Motoko'],
                  'cross_messages_per_normalizer': len(messages), 'external_hash': 'Python hashlib.sha256 over full canonical bytes',
                  'canonical_decoder_regressions': 0, 'upstream_sdk_decoder_losses': decoders,
                  'decodeCompatible_original_value_recovery': 'exact for ordinary and canonical TS input',
                  'client_canonical_byte_checks': sum(source['client_canonical_checks'] for source in sources['ts'].values()),
                  'client_canonical_byte_equality': 'standalone direct producer bytes and external SHA256; captures decode to independent original typed values',
                  'rust_original_schema_reencoding': 'secondary byte control; original typed semantic equality is mandatory',
                  'ocaml_ordinary_decoder': 'not available; direct bytes and normalized data match independent typed oracle',
                  'directed_float32_wire_records': len(float_cases),
                  'motoko_fresh_instances_per_program': 2, 'corpus_sha256': sha256(corpus_path.read_bytes()).hexdigest(),
                  'source_sha256': starting_sources, 'sources_changed_during_run': fingerprints() != starting_sources,
                  'wasm_backend': wasm_backend,
                  'compiler_runtime_direct': {'typed_cases': len(cases),
                      'source_value_oracle_and_sha256': 'exact',
                      'ordinary_decoder_original_value_recovery': 'exact',
                      'inbound_normalizer': 'not exposed by the compiler'} if args.runtime_moc else None,
                  'rustc': verify.command([rustc, '--version'], 'rustc-version').strip(),
                  'moc': verify.command([compiler, '--version'], 'moc-version').strip(),
                  'node': verify.command(['node', '--version'], 'node-version').strip(),
                  'rust_candid': re.search(r'name = "candid"\nversion = "([^"]+)"', (ROOT / 'rust/Cargo.lock').read_text()).group(1),
                  'ts_sdk': json.loads((ROOT / 'ts/node_modules/@icp-sdk/core/package.json').read_text())['version'],
                  'completed_utc': datetime.now(timezone.utc).isoformat(),
                  'replay': shlex.join([sys.executable, 'tests/fuzz.py', '--seed', hex(seed),
                    '--cases', str(data['random_records']), '--moc', str(args.moc), '--core', str(args.core),
                    '--motoko-repo', str(args.motoko_repo)] +
                    (['--runtime-moc', str(args.runtime_moc)] if args.runtime_moc else []))}
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({**report, 'upstream_sdk_decoder_losses': len(decoders)}, indent=2))
    except Exception as error:
        if not (out / 'failure.json').exists():
            (out / 'failure.json').write_text(json.dumps({'seed': seed, 'error': str(error),
                'traceback': traceback.format_exc(), 'corpus': str(corpus_path)}, indent=2) + '\n')
        raise


if __name__ == '__main__':
    main()
