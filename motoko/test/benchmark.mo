import Prim "mo:⛔";
import Array "mo:core/Array";
import Nat "mo:core/Nat";
import Principal "mo:core/Principal";
import CandidX "../src/lib";

type Tx = { owner : Principal; amount : Nat; memo : ?Blob };
type Block = { timestamp : Nat64; parentHash : ?Blob; tx : [Tx]; note : ?Text };
func canonical(raw : Blob) : Blob = switch (CandidX.canonicalize(raw)) {
  case (#ok bytes) bytes;
  case (#err e) Prim.trap(e);
};
func decode(raw : Blob) : Block {
  let block : ?Block = from_candid(raw);
  switch block { case (?b) b; case null Prim.trap("decode failed") }
};
var checksum = 0;
func bench(name : Text, phase : Text, iterations : Nat, f : () -> Nat) {
  for (_ in Array.tabulate<Nat>(3, func(i) { i }).values()) checksum += f();
  var trial = 0;
  while (trial < 7) {
    Prim.debugPrint("start " # name # " " # phase # " " # Nat.toText(trial) # " " # Nat.toText(iterations));
    let before = Prim.rts_total_allocation();
    var i = 0;
    while (i < iterations) { checksum += f(); i += 1 };
    let allocated = Prim.abs((Prim.rts_total_allocation() : Int) - before);
    Prim.debugPrint("end " # name # " " # phase # " " # Nat.toText(trial) # " " # Nat.toText(allocated));
    trial += 1;
  }
};
for ((name, count, memoSize) in [
  ("small", 4, 32), ("records-128", 128, 32),
  ("blob-64k", 1, 65536), ("records-1024", 1024, 0)
].values()) {
  let memo = Prim.arrayToBlob(Array.tabulate<Nat8>(memoSize, func(i) {
    Prim.natToNat8(i % 256)
  }));
  let block : Block = {
    timestamp = 1234567890; parentHash = ?Prim.arrayToBlob(Array.tabulate<Nat8>(32, func(_) { 7 }));
    tx = Array.tabulate<Tx>(count, func(i) {
      { owner = Principal.fromBlob("\01\02"); amount = 2 ** 200 + i;
        memo = if (i % 3 == 0 or memoSize == 65536) ?memo else null }
    }); note = null
  };
  let raw = to_candid(block);
  let canon = canonical(raw);
  assert decode(raw) == block;
  assert decode(canon) == block;
  Prim.debugPrint("case " # name # " " # Nat.toText(raw.size()) # " " # Nat.toText(canon.size()));
  let fast = if (count <= 4) 200 else 20;
  let slow = if (memoSize == 65536 or count == 1024) 1 else if (count == 128) 3 else 30;
  bench(name, "ordinary_encode", fast, func() { (to_candid(block)).size() });
  bench(name, "canonicalize_preencoded", slow, func() { (canonical(raw)).size() });
  bench(name, "canonical_encode_pipeline", slow, func() { (canonical(to_candid(block))).size() });
  bench(name, "ordinary_decode", fast, func() { (decode(raw)).tx.size() });
  bench(name, "canonical_decode", fast, func() { (decode(canon)).tx.size() });
};
Prim.debugPrint("checksum " # Nat.toText(checksum));
