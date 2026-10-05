"""Independent test oracle for cx.

Based on the local Candid type-table parser from the earlier fixed-schema
experiment. Unlike that experiment this rebuilds both schema and payload from
normalized data. This is test infrastructure, not a shipped Python package.
"""
from dataclasses import dataclass

MAX_TYPES = 1024
MAX_BYTES = 32 * 1024 * 1024
MAX_STEPS = 2_000_000
PRIMITIVES = frozenset(range(-17, 0)) | {-24}


@dataclass(frozen=True)
class Node:
    opcode: int
    labels: tuple
    edges: tuple[int, ...]


def leb(value, signed=False):
    if value < 0 and not signed:
        raise ValueError("negative unsigned LEB128")
    out = bytearray()
    while True:
        byte = value & 127
        value >>= 7
        done = ((value == 0 and byte < 64) or
                (value == -1 and byte >= 64)) if signed else value == 0
        out.append(byte if done else byte | 128)
        if done:
            return bytes(out)


class Reader:
    def __init__(self, data, pos=0):
        if len(data) > MAX_BYTES:
            raise ValueError("research message size limit")
        self.data, self.pos = data, pos

    def take(self, count):
        if count < 0 or count > len(self.data) - self.pos:
            raise ValueError("truncated Candid message")
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def byte(self):
        return self.take(1)[0]

    def integer(self, signed=False, minimal=False, max_bytes=4096):
        start, result, shift = self.pos, 0, 0
        for _ in range(max_bytes):
            byte = self.byte()
            result |= (byte & 127) << shift
            shift += 7
            if byte < 128:
                if signed and byte & 64:
                    result -= 1 << shift
                if minimal and self.data[start:self.pos] != leb(result, signed):
                    raise ValueError("nonminimal value LEB128")
                return result
        raise ValueError("research integer length limit")

    def length(self, minimal=False):
        n = self.integer(minimal=minimal, max_bytes=10)
        if n > MAX_STEPS:
            raise ValueError("research collection length limit")
        return n

    def references(self):
        return tuple(self.integer(True, max_bytes=10)
                     for _ in range(self.length()))

    def text(self, minimal=False):
        value = self.take(self.length(minimal))
        value.decode("utf-8", errors="strict")
        return value


def parse(data):
    r = Reader(data)
    if r.take(4) != b"DIDL":
        raise ValueError("missing DIDL magic")
    count = r.length()
    if count > MAX_TYPES:
        raise ValueError("research type-table limit")
    nodes = []
    for _ in range(count):
        op = r.integer(True, max_bytes=10)
        if op in (-18, -19):
            node = Node(op, (), (r.integer(True, max_bytes=10),))
        elif op in (-20, -21):
            fields = tuple((r.integer(max_bytes=10),
                            r.integer(True, max_bytes=10))
                           for _ in range(r.length()))
            labels = tuple(label for label, _ in fields)
            if (any(label > 0xffffffff for label in labels) or
                    any(a >= b for a, b in zip(labels, labels[1:]))):
                raise ValueError("invalid or unordered field IDs")
            node = Node(op, labels, tuple(ref for _, ref in fields))
        elif op == -22:
            args, rets = r.references(), r.references()
            modes = tuple(r.byte() for _ in range(r.length()))
            if len(modes) > 1 or any(m not in (1, 2, 3) for m in modes):
                raise ValueError("unsupported function annotations")
            if modes == (2,) and rets:
                raise ValueError("oneway function with return types")
            node = Node(op, (len(args), len(rets), modes), args + rets)
        elif op == -23:
            methods = tuple((r.text(), r.integer(True, max_bytes=10))
                            for _ in range(r.length()))
            labels = tuple(label for label, _ in methods)
            if any(a >= b for a, b in zip(labels, labels[1:])):
                raise ValueError("unordered service methods")
            node = Node(op, labels, tuple(ref for _, ref in methods))
        else:
            raise ValueError(f"unsupported compound opcode {op}")
        nodes.append(node)
    roots = r.references()
    for ref in roots + tuple(e for n in nodes for e in n.edges):
        if ref >= len(nodes) or (ref < 0 and ref not in PRIMITIVES):
            raise ValueError("invalid type reference")
    for node in nodes:
        if node.opcode == -23:
            if any(e < 0 or nodes[e].opcode != -22 for e in node.edges):
                raise ValueError("service method type must be func")
    return tuple(nodes), roots, r.pos


def emit_header(nodes, roots):
    out = bytearray(b"DIDL" + leb(len(nodes)))
    for node in nodes:
        out += leb(node.opcode, True)
        if node.opcode in (-18, -19):
            out += leb(node.edges[0], True)
        elif node.opcode in (-20, -21):
            out += leb(len(node.labels))
            for label, edge in zip(node.labels, node.edges):
                out += leb(label) + leb(edge, True)
        elif node.opcode == -22:
            args, rets, modes = node.labels
            out += leb(args)
            for edge in node.edges[:args]:
                out += leb(edge, True)
            out += leb(rets)
            for edge in node.edges[args:]:
                out += leb(edge, True)
            out += leb(len(modes)) + bytes(modes)
        elif node.opcode == -23:
            out += leb(len(node.labels))
            for label, edge in zip(node.labels, node.edges):
                out += leb(len(label)) + label + leb(edge, True)
        else:
            raise ValueError("unsupported graph node")
    out += leb(len(roots))
    for root in roots:
        out += leb(root, True)
    return bytes(out)


def canonical_header(nodes, roots):
    # Coarsest bisimulation; including the old class makes refinement monotone.
    classes = [0] * len(nodes)
    while nodes:
        signatures, refined = {}, []
        for i, node in enumerate(nodes):
            signature = (classes[i], node.opcode, node.labels,
                         tuple((0, e) if e < 0 else (1, classes[e])
                               for e in node.edges))
            refined.append(signatures.setdefault(signature, len(signatures)))
        stable = len(set(refined)) == len(set(classes))
        classes = refined
        if stable:
            break
    representatives = {c: nodes[i] for i, c in enumerate(classes)}
    numbering, ordered, pending = {}, [], list(reversed(roots))
    while pending:
        ref = pending.pop()
        if ref < 0 or classes[ref] in numbering:
            continue
        cls = classes[ref]
        numbering[cls] = len(ordered)
        node = representatives[cls]
        ordered.append(node)
        pending.extend(reversed(node.edges))

    def remap(ref):
        return ref if ref < 0 else numbering[classes[ref]]

    minimal = tuple(Node(n.opcode, n.labels, tuple(map(remap, n.edges)))
                    for n in ordered)
    return emit_header(minimal, tuple(map(remap, roots)))


@dataclass(frozen=True)
class Value:
    op: int
    data: object = b""
    binary: bool = False


@dataclass(frozen=True)
class Shape:
    op: int
    labels: tuple = ()
    children: tuple = ()


NULL = Value(-1)
NULL_TYPE = Shape(-1)
EMPTY_TYPE = Shape(-17)
MAX_DEPTH = 256


def join(a, b, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("type nesting limit")
    if a == b or b.op == -17:
        return a
    if a.op == -17:
        return b
    if a.op == -1:
        return b if b.op == -18 else Shape(-18, (), (b,))
    if b.op == -1:
        return a if a.op == -18 else Shape(-18, (), (a,))
    if a.op == -18 or b.op == -18:
        x = a.children[0] if a.op == -18 else a
        y = b.children[0] if b.op == -18 else b
        return Shape(-18, (), (join(x, y, depth + 1),))
    if a.op != b.op or a.op not in (-19, -20, -21):
        raise ValueError("incompatible value shapes")
    if a.op == -19:
        return Shape(-19, (), (join(a.children[0], b.children[0], depth + 1),))
    aa, bb = dict(zip(a.labels, a.children)), dict(zip(b.labels, b.children))
    labels = tuple(sorted(aa.keys() | bb.keys()))
    children = []
    for label in labels:
        if a.op == -21 and label not in aa:
            children.append(bb[label])
        elif a.op == -21 and label not in bb:
            children.append(aa[label])
        else:
            children.append(join(aa.get(label, NULL_TYPE),
                                 bb.get(label, NULL_TYPE), depth + 1))
    return Shape(a.op, labels, tuple(children))


def shape(v, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("type nesting limit")
    if v.op == -18:
        return Shape(-18, (), (shape(v.data, depth + 1),))
    if v.op == -19:
        element = Shape(-5) if v.binary else EMPTY_TYPE
        for item in v.data:
            element = join(element, shape(item, depth + 1))
        return Shape(-19, (), (element,))
    if v.op in (-20, -21):
        return Shape(v.op, tuple(k for k, _ in v.data),
                     tuple(shape(item, depth + 1) for _, item in v.data))
    return Shape(v.op)


def values(data):
    nodes, roots, offset = parse(data)
    r = Reader(data, offset)
    steps = 0

    def read(ref, depth):
        nonlocal steps
        steps += 1
        if steps > MAX_STEPS or depth > MAX_DEPTH:
            raise ValueError("value traversal limit")
        if ref >= 0:
            node = nodes[ref]
            if node.opcode == -18:
                tag = r.byte()
                if tag == 0:
                    return NULL
                if tag != 1:
                    raise ValueError("invalid option tag")
                child = read(node.edges[0], depth + 1)
                return Value(-18, child)
            if node.opcode == -19:
                return Value(-19, tuple(read(node.edges[0], depth + 1)
                                        for _ in range(r.length())), node.edges[0] == -5)
            if node.opcode == -20:
                fields = []
                for label, edge in zip(node.labels, node.edges):
                    child = read(edge, depth + 1)
                    absent = edge >= 0 and nodes[edge].opcode == -18 and child.op == -1
                    if not absent:
                        fields.append((label, child))
                return Value(-20, tuple(fields))
            if node.opcode == -21:
                index = r.integer(max_bytes=10)
                if index >= len(node.edges):
                    raise ValueError("invalid variant index")
                return Value(-21, ((node.labels[index], read(node.edges[index], depth + 1)),))
            raise ValueError("function/service values outside data profile")
        if ref == -1:
            return NULL
        if ref == -16:
            return Value(-16)
        if ref == -17:
            raise ValueError("empty has no values")
        if ref == -2:
            raw = r.take(1)
            if raw not in (b"\x00", b"\x01"):
                raise ValueError("invalid bool")
        elif ref in (-3, -4):
            raw = leb(r.integer(signed=ref == -4), signed=ref == -4)
        elif -12 <= ref <= -5:
            raw = r.take(1 << ((-ref - 5) % 4))
        elif ref in (-13, -14):
            size = 4 if ref == -13 else 8
            raw = r.take(size)
            bits = int.from_bytes(raw, "little")
            frac, exponent = (23, 8) if size == 4 else (52, 11)
            if ((bits >> frac) & ((1 << exponent) - 1) == (1 << exponent) - 1
                    and bits & ((1 << frac) - 1)):
                raw = (0x7fc00000 if size == 4 else 0x7ff8000000000000).to_bytes(size, "little")
        elif ref == -15:
            text = r.text()
            raw = leb(len(text)) + text
        elif ref == -24:
            if r.byte() != 1:
                raise ValueError("opaque principal")
            n = r.length()
            if n > 29:
                raise ValueError("principal length exceeds 29")
            raw = b"\x01" + leb(n) + r.take(n)
        else:
            raise ValueError("unsupported primitive")
        return Value(ref, raw)

    result = tuple(read(root, 0) for root in roots)
    if r.pos != len(data):
        raise ValueError("trailing payload")
    return result


def encode_values(vs):
    types = tuple(shape(v) for v in vs)
    table, refs = [], {}

    def intern(t):
        if t.op not in (-18, -19, -20, -21):
            return t.op
        if t in refs:
            return refs[t]
        index = len(table)
        if index >= MAX_TYPES:
            raise ValueError("output type-table limit")
        refs[t] = index
        table.append(None)
        table[index] = Node(t.op, t.labels, tuple(intern(c) for c in t.children))
        return index

    roots = tuple(intern(t) for t in types)
    out = bytearray(emit_header(tuple(table), roots))

    def emit(v, t, depth=0):
        if depth > MAX_DEPTH:
            raise ValueError("emitted value nesting limit")
        if t.op == -18:
            if v.op == -1:
                out.append(0)
            else:
                out.append(1)
                emit(v.data if v.op == -18 else v, t.children[0], depth + 1)
        elif t.op == -19 and v.op == -19:
            out.extend(leb(len(v.data)))
            for child in v.data:
                emit(child, t.children[0], depth + 1)
        elif t.op == -20 and v.op == -20:
            fields = dict(v.data)
            for label, child_type in zip(t.labels, t.children):
                emit(fields.get(label, NULL), child_type, depth + 1)
        elif t.op == -21 and v.op == -21:
            label, child = v.data[0]
            index = t.labels.index(label)
            out.extend(leb(index))
            emit(child, t.children[index], depth + 1)
        elif v.op == t.op and t.op not in (-17, -18, -19, -20, -21):
            out.extend(v.data)
        else:
            raise ValueError("cannot emit value at derived type")
        if len(out) > MAX_BYTES:
            raise ValueError("output byte limit")

    for value, typ in zip(vs, types):
        emit(value, typ)
    return bytes(out)


def canonicalize(data):
    return encode_values(values(data))
