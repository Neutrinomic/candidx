import Prim "mo:⛔";
// The first encode observes a cold native BigInt literal.
let large = 18446744073709551616 : Nat;
let largeBytes = to_candid_canonical(large);
assert largeBytes == "DIDL\00\01\7d\80\80\80\80\80\80\80\80\80\02";
assert (from_candid(largeBytes) : ?Nat) == ?large;
type Original = { amount : Nat; missing : ?Text };
let value : Original = { amount = 255; missing = null };
let result = to_candid_canonical(value);
assert result == "DIDL\01\6c\01\d8\a3\8c\a8\0d\7d\01\00\ff\01";
assert result == to_candid_canonical({ amount = 255 : Nat });
let decoded : ?Original = from_candid(result);
assert decoded == ?value;
let raw = to_candid(value);
assert (from_candid(raw) : ?Original) == ?value;
assert to_candid_canonical("" : Blob) == to_candid_canonical([] : [Nat8]);
let nested : [??Null] = [?(?null), null];
let nestedBytes = to_candid_canonical(nested);
assert nestedBytes == "DIDL\03\6d\01\6e\02\6e\7f\01\00\02\01\01\00";
switch (from_candid(nestedBytes) : ?[??Null]) {
  case (?recovered) {
    assert recovered.size() == 2;
    switch (recovered[0], recovered[1]) {
      case (?(?null), null) {};
      case _ Prim.trap("nested option recovery changed presence");
    }
  };
  case null Prim.trap("nested option recovery failed");
};
assert to_candid_canonical() == "DIDL\00\00";
Prim.debugPrint("CandidX generated direct encoding smoke passed");
