import Prim "mo:⛔";
import CandidX "../src/lib";

func printBlob(name : Text, bytes : Blob) {
  let data = Prim.blobToArray(bytes); var offset = 0; var chunk = 0;
  while (offset < data.size()) {
    let remaining = Prim.abs((data.size() : Int) - offset);
    let n = if (remaining > 128) 128 else remaining;
    let part = Prim.arrayToBlob(Prim.Array_tabulate<Nat8>(n, func(i) {
      data[offset + i]
    }));
    Prim.debugPrint(name # ":" # debug_show(chunk) # " " # debug_show(part));
    offset += n; chunk += 1;
  }
};
func emit(name : Text, raw : Blob) : Blob {
  let canonical = switch (CandidX.canonicalize(raw)) {
    case (#ok(bytes)) bytes;
    case (#err(e)) Prim.trap(e);
  };
  assert CandidX.canonicalize(canonical) == #ok(canonical);
  printBlob("raw:" # name, raw); printBlob("canonical:" # name, canonical);
  canonical
};
type Old = { a : Text };
type New = { a : Text; b : ?Text };
type Newer = { a : Text; b : ?Text; c : ?[Nat] };
type NestedOption = { a : Text; b : ??Text };
type Choice = { #a; #b : Nat };
type MoreChoice = { #a; #b : Nat; #c : Text };
type OuterOld = { inner : Old; items : [Choice] };
type OuterNew = { inner : New; items : [MoreChoice]; extra : ?Nat };
let old : Old = { a = "abc" };
let nw : New = { a = "abc"; b = null };
let newer : Newer = { a = "abc"; b = null; c = null };
let oldBytes = emit("evolution-record-old", to_candid(old));
let newBytes = emit("evolution-record-new", to_candid(nw));
assert oldBytes == newBytes;
assert (from_candid(oldBytes) : ?New) == ?nw;
assert (from_candid(newBytes) : ?Old) == ?old;
ignore emit("evolution-record-newer", to_candid(newer));
ignore emit("evolution-present-empty", to_candid({ a = "abc"; b = ?"" } : New));
ignore emit("evolution-present-text", to_candid({ a = "abc"; b = ?"new data" } : New));
ignore emit("evolution-nested-none", to_candid({ a = "abc"; b = null } : NestedOption));
let someNone : NestedOption = { a = "abc"; b = ?null };
let someSome : NestedOption = { a = "abc"; b = ??"x" };
assert (from_candid(emit("evolution-nested-some-none", to_candid(someNone))) : ?NestedOption) == ?someNone;
assert (from_candid(emit("evolution-nested-some-some", to_candid(someSome))) : ?NestedOption) == ?someSome;
ignore emit("evolution-variant-old", to_candid(#b(128) : Choice));
ignore emit("evolution-variant-new", to_candid(#b(128) : MoreChoice));
ignore emit("evolution-variant-used-new", to_candid(#c("new") : MoreChoice));
let olds : [Old] = [old, { a = "xyz" }];
let news : [New] = [nw, { a = "xyz"; b = null }];
let mixed : [New] = [nw, { a = "xyz"; b = ?"present" }];
let mixedNewer : [Newer] = [newer, { a = "xyz"; b = ?"present"; c = null }];
ignore emit("evolution-array-old", to_candid(olds));
ignore emit("evolution-array-new", to_candid(news));
assert (from_candid(emit("evolution-array-mixed", to_candid(mixed))) : ?[New]) == ?mixed;
ignore emit("evolution-array-mixed-newer", to_candid(mixedNewer));
ignore emit("evolution-variant-array-old", to_candid([#a, #b(5)] : [Choice]));
ignore emit("evolution-variant-array-new", to_candid([#a, #b(5)] : [MoreChoice]));
ignore emit("evolution-outer-old", to_candid({ inner = old; items = [#b(7)] } : OuterOld));
ignore emit("evolution-outer-new", to_candid({ inner = nw; items = [#b(7)]; extra = null } : OuterNew));
ignore emit("evolution-empty-nat-vector", to_candid([] : [Nat]));
ignore emit("evolution-empty-text-vector", to_candid([] : [Text]));
let emptyBytes = emit("evolution-empty-byte-vector", to_candid([] : [Nat8]));
assert emptyBytes == (switch (CandidX.canonicalize(to_candid("" : Blob))) {
  case (#ok(b)) b; case (#err(e)) Prim.trap(e)
});
assert (from_candid(emptyBytes) : ?Blob) == ?("" : Blob);
assert (from_candid(emptyBytes) : ?[Nat8]) == ?[];
ignore emit("evolution-option-none-nat", to_candid(null : ?Nat));
ignore emit("evolution-option-none-text", to_candid(null : ?Text));
ignore emit("evolution-option-some-none", to_candid(?null : ??Nat));
let option : ??Nat = ??7;
assert (from_candid(emit("evolution-option-some-some", to_candid(option))) : ???Nat) == ?option;
let nullable : [??Nat] = [null, ?null, ??7];
assert (from_candid(emit("evolution-nullable-array", to_candid(nullable))) : ?[??Nat]) == ?nullable;
