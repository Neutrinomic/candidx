import Prim "mo:⛔";
type Original = { amount : Nat; missing : ?Text };
let value : Original = { amount = 255; missing = null };
let raw = to_candid(value);
let result = to_candid_canonical(value);
assert result == Prim.canonicalizeCandid(raw);
assert result == to_candid_canonical({ amount = 255 : Nat });
assert result == Prim.canonicalizeCandid(to_candid({ amount = 255 : Nat }));
assert Prim.canonicalizeCandid(result) == result;
let decoded : ?Original = from_candid(result);
assert decoded == ?value;
assert to_candid_canonical("" : Blob) == to_candid_canonical([] : [Nat8]);
assert to_candid_canonical([?(?null), null] : [??Null]) ==
  Prim.canonicalizeCandid(to_candid([?(?null), null] : [??Null]));
assert to_candid_canonical() == "DIDL\00\00";
Prim.debugPrint("CandidX direct typed Rust/RTS smoke passed");
