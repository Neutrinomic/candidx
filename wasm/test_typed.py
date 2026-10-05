#!/usr/bin/env python3
"""Direct typed-memory golden checks, scalar properties, and hostile ABI spans."""
from pathlib import Path
import json
import random
import struct
from runtime import TypedEncoder


def leb(number, signed=False):
    out = bytearray()
    while True:
        byte = number & 127
        number >>= 7
        done = (number == 0 and byte < 64) or (number == -1 and byte >= 64) if signed else number == 0
        out.append(byte | (0 if done else 128))
        if done:
            return bytes(out)


def scalar(kind, data):
    return b'DIDL\0\1' + leb(kind, True) + data


def main():
    encoder = TypedEncoder()
    fixed = [
        ([], '4449444c0000'),
        ([{'kind': 'null'}], '4449444c00017f'),
        ([{'kind': 'reserved'}], '4449444c000170'),
        ([{'kind': 'nat', 'value': 255}], '4449444c00017dff01'),
        ([{'kind': 'int', 'value': -65}], '4449444c00017cbf7f'),
        ([{'kind': 'blob', 'value': b''}], '4449444c016d7b010000'),
        ([{'kind': 'vec', 'binary': True, 'values': []}], '4449444c016d7b010000'),
        ([{'kind': 'vec', 'binary': False, 'values': []}], '4449444c016d6f010000'),
        ([{'kind': 'opt', 'value': None}], '4449444c00017f'),
        ([{'kind': 'opt', 'value': {'kind': 'opt', 'value': None}}], '4449444c016e7f010001'),
        ([{'kind': 'text', 'value': '\ufeffλ'}], '4449444c00017105efbbbfcebb'),
        ([{'kind': 'principal', 'value': b'\1\2'}], '4449444c00016801020102'),
        ([{'kind': 'record', 'fields': [{'id': 7, 'optional': True, 'value': {'kind': 'opt', 'value': None}}]}], '4449444c016c000100'),
        ([{'kind': 'record', 'fields': [{'id': 4294967295, 'value': {'kind': 'nat', 'value': 1}}]}], '4449444c016c01ffffffff0f7d010001'),
        ([{'kind': 'variant', 'id': 4294967295, 'value': {'kind': 'null'}}], '4449444c016b01ffffffff0f7f010000'),
        ([{'kind': 'vec', 'values': [{'kind': 'opt', 'value': None}, {'kind': 'opt', 'value': {'kind': 'nat', 'value': 128}}]}], '4449444c026d016e7d01000200018001'),
    ]
    for trees, expected in fixed:
        assert encoder.encode_args(trees).hex() == expected, trees
    seed = 0xcad1d
    rng = random.Random(seed)
    properties = 0
    # Independent scalar arithmetic oracle exercises magnitude conversion and
    # shortest signed/unsigned LEB at byte boundaries and random large values.
    integers = [0, 1, 63, 64, 127, 128] + [2**bits + shift for bits in [60, 61, 62, 63, 64, 119, 120, 121, 255, 256, 1023] for shift in [-1, 0, 1]]
    integers += [rng.getrandbits(rng.randrange(1, 2048)) for _ in range(100)]
    for n in integers:
        assert encoder.encode_args([{'kind': 'nat', 'value': n}]) == scalar(-3, leb(n))
        for signed in [n, -n]:
            assert encoder.encode_args([{'kind': 'int', 'value': signed}]) == scalar(-4, leb(signed, True))
            properties += 1
        properties += 1
    for width, tag, mask, fraction, qnan in [
        (32, -13, 0x7f800000, 0x007fffff, 0x7fc00000),
        (64, -14, 0x7ff0000000000000, 0x000fffffffffffff, 0x7ff8000000000000),
    ]:
        values = [0, 1, 1 << (width - 1), (1 << (width - 1)) | 1, mask, mask | (1 << (width - 1)), mask | 1, qnan, qnan | (1 << (width - 1)), mask | fraction]
        values += [rng.getrandbits(width) for _ in range(200)]
        for bits in values:
            expected = qnan if bits & mask == mask and bits & fraction else bits
            assert encoder.encode_args([{'kind': f'float{width}bits', 'value': bits}]) == scalar(tag, expected.to_bytes(width // 8, 'little'))
            properties += 1
    roots = [
        {'kind': 'record', 'fields': [
            {'id': 9, 'value': {'kind': 'reserved'}},
            {'id': 7, 'value': {'kind': 'nat', 'value': 255}},
            {'id': 8, 'optional': True, 'value': {'kind': 'opt', 'value': None}},
        ]},
        {'kind': 'vec', 'binary': True, 'values': [{'kind': 'nat8', 'value': 0}, {'kind': 'nat8', 'value': 255}]},
    ]
    expected = encoder.encode_args(roots)
    pages = encoder.memory_bytes
    for _ in range(100):
        assert encoder.encode_args(roots) == expected
        assert encoder.memory_bytes == pages

    exports, store, memory = encoder.exports, encoder.store, encoder.exports['memory']
    bounds = 0

    def abi(nodes, roots, edges=b'', edge_offset=None):
        exports['cx_reset'](store)
        def put(data):
            pointer = exports['cx_alloc'](store, len(data))
            assert pointer
            memory.write(store, data, pointer)
            return pointer
        if edges:
            pointer = put(edges)
            if edge_offset is not None:
                nodes = bytearray(nodes)
                struct.pack_into('<I', nodes, edge_offset, pointer)
        return exports['cx_encode_typed'](store, put(nodes), len(nodes) // 24, put(roots), len(roots) // 4)

    def node(tag, flags=0, a=0, b=0, c=0, d=0):
        return struct.pack('<iIIIII', tag, flags, a, b, c, d)

    invalids = [
        (node(-2, a=2), struct.pack('<I', 0), b'', None),
        (node(-1, flags=1), struct.pack('<I', 0), b'', None),
        (node(-1, c=1), struct.pack('<I', 0), b'', None),
        (node(-5, a=256), struct.pack('<I', 0), b'', None),
        (node(-22), struct.pack('<I', 0), b'', None),
        (node(-18, a=0), struct.pack('<I', 0), b'', None),
        (node(-1), struct.pack('<I', 0xffffffff), b'', None),
        (node(-1), struct.pack('<I', 178956970), b'', None),
        (node(-15, b=2), struct.pack('<I', 0), b'\xc0\xaf', 8),
        (node(-24, b=30), struct.pack('<I', 0), bytes(30), 8),
        (node(-20, b=1) + node(-1), struct.pack('<I', 0), struct.pack('<III', 7, 1, 2), 8),
        (node(-20, b=1) + node(-1), struct.pack('<I', 0), struct.pack('<III', 7, 1, 1), 8),
        (node(-20, b=2) + node(-1), struct.pack('<I', 0), struct.pack('<IIIIII', 7, 1, 0, 7, 1, 0), 8),
        (node(-20, b=2) + node(-1), struct.pack('<I', 0), struct.pack('<IIIIII', 8, 1, 0, 7, 1, 0), 8),
        (node(-19, flags=1, b=1) + node(-1), struct.pack('<I', 0), struct.pack('<I', 1), 8),
    ]
    for args in invalids:
        assert abi(*args) == 1, args
        bounds += 1
    exports['cx_reset'](store)
    assert exports['cx_encode_typed'](store, 0xfffffff0, 1, 0, 0) == 1
    assert exports['cx_encode_typed'](store, 0, 2000001, 0, 0) == 1
    assert exports['cx_encode_typed'](store, 0, 0, 0, 2000001) == 1
    bounds += 3
    assert encoder.encode_args([]) == b'DIDL\0\0'
    print(json.dumps({'profile': 'cx', 'seed': seed, 'goldens': len(fixed), 'scalar_properties': properties,
                      'malformed_typed_abi_cases': bounds, 'arena_reuse_calls': 100, 'memory_bytes': pages}))


if __name__ == '__main__':
    main()
