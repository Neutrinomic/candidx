// Real-canister heap probe. The host invokes each operation between GC cycles.
import Prim "mo:⛔";
import Array "mo:core/Array";
import Principal "mo:core/Principal";
import Nat64 "mo:core/Nat64";
import CandidX "../src/lib";

actor {
  type Tx = { owner : Principal; amount : Nat; memo : ?Blob };
  type Block = { timestamp : Nat64; parentHash : ?Blob; tx : [Tx]; note : ?Text };
  public type Stats = {
    heap : Nat; allocated : Nat; reclaimed : Nat; memory : Nat;
    maxLive : Nat; collectorInstructions : Nat
  };
  transient var block : ?Block = null;
  transient var ordinary : Blob = "";
  transient var canonicalInput : Blob = "";
  transient var output : Blob = "";
  transient var decoded : ?Block = null;
  transient var kind : Nat = 0;
  transient var numbers : [Nat64] = [];
  transient var textValue : Text = "";
  transient var decodedNumbers : ?[Nat64] = null;
  transient var decodedText : ?Text = null;

  func canonical(raw : Blob) : Blob = switch (CandidX.canonicalize(raw)) {
    case (#ok bytes) bytes;
    case (#err e) Prim.trap(e)
  };
  func snapshot() : Stats = {
    heap = Prim.rts_heap_size(); allocated = Prim.rts_total_allocation();
    reclaimed = Prim.rts_reclaimed(); memory = Prim.rts_memory_size();
    maxLive = Prim.rts_max_live_size();
    collectorInstructions = Prim.rts_collector_instructions()
  };
  func encode() : Blob {
    if (kind == 1) return to_candid(numbers);
    if (kind >= 2) return to_candid(textValue);
    switch block { case (?b) to_candid(b); case null Prim.trap("configure first") }
  };
  func decode(raw : Blob) {
    if (kind == 1) decodedNumbers := from_candid(raw)
    else if (kind >= 2) decodedText := from_candid(raw)
    else decoded := from_candid(raw)
  };
  func matches() : Bool {
    if (kind == 1) return decodedNumbers == ?numbers;
    if (kind >= 2) return decodedText == ?textValue;
    decoded == block
  };
  public func configure(valueKind : Nat, count : Nat, memoSize : Nat) : async () {
    kind := valueKind;
    assert kind <= 3;
    let memo = Prim.arrayToBlob(Array.tabulate<Nat8>(memoSize, func(i) {
      Prim.natToNat8(i % 256)
    }));
    let b : Block = {
      timestamp = 1234567890;
      parentHash = ?Prim.arrayToBlob(Array.tabulate<Nat8>(32, func(_) { 7 }));
      tx = Array.tabulate<Tx>(count, func(i) {
        { owner = Principal.fromBlob("\01\02"); amount = 2 ** 200 + i;
          memo = if (i % 3 == 0 or memoSize == 65536) ?memo else null }
      }); note = null
    };
    block := ?b;
    numbers := if (kind == 1) Array.tabulate<Nat64>(count, func(i) { Nat64.fromNat(i * 17) }) else [];
    if (kind >= 2) {
      let bytes = Prim.arrayToBlob(Array.tabulate<Nat8>(memoSize, func(i) {
        if (kind == 3) ([0xf0, 0x9f, 0x8c, 0x90] : [Nat8])[i % 4]
        else Prim.natToNat8(97 + i % 26)
      }));
      textValue := switch (Prim.decodeUtf8(bytes)) { case (?t) t; case null Prim.trap("invalid benchmark text") }
    } else textValue := "";
    ordinary := encode();
    canonicalInput := canonical(ordinary);
    decode(ordinary); assert matches();
    decode(canonicalInput); assert matches();
    output := ""; decoded := null; decodedNumbers := null; decodedText := null
  };
  public func clear() : async () {
    output := ""; decoded := null; decodedNumbers := null; decodedText := null
  };
  public query func stats() : async Stats { snapshot() };
  public query func bytes() : async (Blob, Blob) { (ordinary, canonicalInput) };
  public query func check() : async Bool {
    // Read retained bytes and compare with the retained typed source even
    // after a normalization-only stress batch, where decoded is still null.
    let raw = if (output.size() != 0) output else ordinary;
    if (kind == 1) {
      let recovered : ?[Nat64] = from_candid(raw);
      recovered == ?numbers and (decodedNumbers == null or matches())
    } else if (kind >= 2) {
      let recovered : ?Text = from_candid(raw);
      recovered == ?textValue and (decodedText == null or matches())
    } else {
      let recovered : ?Block = from_candid(raw);
      recovered == block and (decoded == null or matches())
    }
  };
  public func run(phase : Nat, iterations : Nat) : async {
    before : Stats; after : Stats; instructions : Nat64; outputSize : Nat
  } {
    assert iterations > 0 and iterations <= 20_000;
    let before = snapshot();
    let started = Prim.performanceCounter(0);
    var i = 0;
    while (i < iterations) {
      switch phase {
        case 0 { output := encode() };
        case 1 { output := canonical(ordinary) };
        case 2 { output := canonical(encode()) };
        case 3 { decode(ordinary) };
        case 4 { decode(canonicalInput) };
        case 5 {}; // Control for measurement bookkeeping.
        case _ Prim.trap("unknown benchmark phase")
      };
      i += 1
    };
    let instructions = Prim.performanceCounter(0) - started;
    let after = snapshot();
    let outputSize = if (phase == 3 or phase == 4) {
      if (kind == 1) numbers.size() else if (kind >= 2) textValue.size()
      else switch decoded { case (?value) value.tx.size(); case null Prim.trap("decode failed") }
    } else output.size();
    { before; after; instructions; outputSize }
  }
}
