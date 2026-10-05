"""Host access to the standalone Rust/Wasm ABI; no hashing or external imports."""
from pathlib import Path
import struct
import wasmtime


class Canonicalizer:
    def __init__(self, path=None, fuel=10_000_000_000):
        self.path = Path(path) if path else Path(__file__).with_name("artifacts") / "cx.wasm"
        config = wasmtime.Config()
        config.consume_fuel = True
        self.engine = wasmtime.Engine(config)
        self.store = wasmtime.Store(self.engine)
        self.fuel = fuel
        self.store.set_fuel(fuel)
        self.module = wasmtime.Module.from_file(self.engine, str(self.path))
        assert not self.module.imports, "standalone ABI unexpectedly needs a host import"
        self.instance = wasmtime.Instance(self.store, self.module, [])
        self.exports = self.instance.exports(self.store)

    def canonicalize(self, raw):
        self.store.set_fuel(self.fuel)
        self.exports["cx_reset"](self.store)
        pointer = self.exports["cx_alloc"](self.store, len(raw))
        if not pointer:
            raise ValueError("input byte limit")
        self.exports["memory"].write(self.store, raw, pointer)
        status = self.exports["cx_canonicalize"](self.store, pointer, len(raw))
        result = self.exports["cx_result_ptr"](self.store)
        length = self.exports["cx_result_len"](self.store)
        output = bytes(self.exports["memory"].read(self.store, result, result + length))
        self.scratch_bytes = self.exports["cx_scratch_used"](self.store)
        self.memory_bytes = self.exports["cx_memory_pages"](self.store) * 65536
        self.fuel_used = self.fuel - self.store.get_fuel()
        if status:
            raise ValueError(output.decode("utf-8"))
        return output


class TypedEncoder(Canonicalizer):
    """Direct typed-memory encoder; its inputs are value trees, not Candid bytes.

    Trees use ``kind`` plus ``value`` for scalars/options/variants, ``values``
    and ``binary`` for vectors, and ``fields`` for records. Each field contains
    its numeric ``id``, source ``optional`` flag, and child ``value`` tree.
    ``float32bits``/``float64bits`` preserve exact IEEE input bits.
    """

    def encode_args(self, trees):
        self.store.set_fuel(self.fuel)
        self.exports["cx_reset"](self.store)
        memory = self.exports["memory"]
        nodes = []

        def span(data):
            data = bytes(data)
            if not data:
                return 0
            pointer = self.exports["cx_alloc"](self.store, len(data))
            if not pointer:
                raise ValueError("typed allocation length limit")
            memory.write(self.store, data, pointer)
            return pointer

        kinds = dict(null=-1, bool=-2, nat=-3, int=-4, nat8=-5,
                     nat16=-6, nat32=-7, nat64=-8, int8=-9, int16=-10,
                     int32=-11, int64=-12, float32=-13, float32bits=-13,
                     float64=-14, float64bits=-14, text=-15, reserved=-16,
                     opt=-18, vec=-19, record=-20, variant=-21,
                     principal=-24, blob=-26)

        def visit(tree, depth=0):
            if depth > 256:
                raise ValueError("value nesting limit")
            kind = tree["kind"]
            tag = kinds[kind]
            flags = a = b = 0
            if kind in ("nat", "int"):
                number = int(tree["value"])
                if kind == "nat" and number < 0:
                    raise ValueError("negative Nat")
                flags = int(number < 0)
                number = abs(number)
                data = number.to_bytes((number.bit_length() + 7) // 8, "little")
                a, b = span(data), len(data)
            elif kind in ("text", "blob", "principal"):
                data = tree["value"].encode("utf-8") if kind == "text" else bytes(tree["value"])
                a, b = span(data), len(data)
            elif kind == "bool":
                a = int(tree["value"])
            elif kind.startswith(("nat", "int")):
                width = int(kind[3:])
                number = int(tree["value"])
                low, high = (0, (1 << width) - 1) if kind.startswith("nat") else (-(1 << (width - 1)), (1 << (width - 1)) - 1)
                if not low <= number <= high:
                    raise ValueError("typed integer range")
                bits = number & ((1 << width) - 1)
                a, b = bits & 0xffffffff, bits >> 32
            elif kind.startswith("float"):
                width = 32 if "32" in kind else 64
                bits = int(tree["value"]) if kind.endswith("bits") else int.from_bytes(struct.pack("<f" if width == 32 else "<d", tree["value"]), "little")
                if not 0 <= bits < 1 << width:
                    raise ValueError("typed float bits range")
                a, b = bits & 0xffffffff, bits >> 32
            elif kind == "opt":
                a = 0xffffffff if tree["value"] is None else visit(tree["value"], depth + 1)
            elif kind == "vec":
                children = [visit(child, depth + 1) for child in tree["values"]]
                flags, a, b = int(tree.get("binary", False)), span(b"".join(struct.pack("<I", child) for child in children)), len(children)
            elif kind == "record":
                edges = []
                for field in sorted(tree["fields"], key=lambda field: field["id"]):
                    child = visit(field["value"], depth + 1)
                    edges.append(struct.pack("<III", field["id"], child, int(field.get("optional", False))))
                a, b = span(b"".join(edges)), len(edges)
            elif kind == "variant":
                flags, a = tree["id"], visit(tree["value"], depth + 1)
            elif kind not in ("null", "reserved"):
                raise ValueError("unknown typed kind")
            index = len(nodes)
            nodes.append(struct.pack("<iIIIII", tag, flags, a, b, 0, 0))
            return index

        roots = [visit(tree) for tree in trees]
        nodes_pointer = span(b"".join(nodes))
        roots_pointer = span(b"".join(struct.pack("<I", root) for root in roots))
        status = self.exports["cx_encode_typed"](self.store, nodes_pointer, len(nodes), roots_pointer, len(roots))
        result = self.exports["cx_result_ptr"](self.store)
        length = self.exports["cx_result_len"](self.store)
        output = bytes(memory.read(self.store, result, result + length))
        self.scratch_bytes = self.exports["cx_scratch_used"](self.store)
        self.memory_bytes = self.exports["cx_memory_pages"](self.store) * 65536
        self.fuel_used = self.fuel - self.store.get_fuel()
        if status:
            raise ValueError(output.decode("utf-8"))
        return output
