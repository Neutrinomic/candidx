module C = Candidx
let tests = ref 0
let encoded typ value = match C.encode typ value with
  | Ok bytes -> bytes | Error message -> failwith message
let from_hex text = Bytes.init (String.length text / 2) (fun i ->
  Char.chr (int_of_string ("0x" ^ String.sub text (2 * i) 2)))
let check_bytes name actual expected =
  incr tests;
  if actual <> expected then failwith (name ^ ": bytes differ");
  (match C.canonicalize actual with
  | Ok bytes when bytes = actual -> ()
  | _ -> failwith (name ^ ": direct bytes are not idempotent"))
let check name typ value hex = check_bytes name (encoded typ value) (from_hex hex)
let rejects name typ value =
  incr tests;
  match C.encode typ value with
  | Error _ -> () | Ok _ -> failwith (name ^ ": expected rejection")
let invalid_schema name f =
  incr tests;
  try ignore (f ()); failwith (name ^ ": expected invalid schema")
  with Invalid_argument _ -> ()

type application = { a : string; b : string option; nested : Z.t option option }
let application = C.record [
  C.field "a" C.text (fun r -> r.a);
  C.field "b" (C.option C.text) (fun r -> r.b);
  C.field "nested" (C.option (C.option C.nat)) (fun r -> r.nested);
]
type event = A of Z.t | B of string | Unused
let events = C.variant [
  C.case "a" C.nat (function A n -> Some n | _ -> None);
  C.case "b" C.text (function B s -> Some s | _ -> None);
  C.case "unused" C.empty (fun _ -> None);
]
type chain = { head : Z.t; tail : chain option }
let chain = C.recursive (fun self -> C.record [
  C.field "head" C.nat (fun r -> r.head);
  C.field "tail" (C.option self) (fun r -> r.tail);
])
type nested = Stop | Next of nested
let nested = C.recursive (fun self -> C.variant [
  C.case "stop" C.null (function Stop -> Some () | _ -> None);
  C.case "next" self (function Next child -> Some child | _ -> None);
])

let () =
  check "old record" application {a="abc"; b=None; nested=None}
    "4449444c016c016171010003616263";
  let old = C.record [C.field "a" C.text (fun r -> r.a)] in
  check_bytes "schema extension" (encoded application {a="abc";b=None;nested=None})
    (encoded old {a="abc";b=None;nested=None});
  check "Some None retains wrapper" (C.option (C.option C.text)) (Some None)
    "4449444c016e7f010001";
  check "nested present null" (C.option (C.option C.null)) (Some (Some ()))
    "4449444c026e016e7f01000101";
  check "absent root" (C.option C.nat) None "4449444c00017f";
  check "Some unit" (C.option C.null) (Some ()) "4449444c016e7f010001";
  check "empty nonbinary" (C.array C.text) [||] "4449444c016d6f010000";
  check "empty binary array" (C.array C.nat8) [||] "4449444c016d7b010000";
  check "empty binary list" (C.list C.nat8) [] "4449444c016d7b010000";
  check "empty blob" C.blob Bytes.empty "4449444c016d7b010000";
  let alias = C.recursive (fun _ -> C.contramap Fun.id C.nat8) in
  check "empty binary alias" (C.array alias) [||] "4449444c016d7b010000";
  let absent_alias = C.recursive (fun _ -> C.option C.text) in
  let alias_record = C.record [C.field "a" C.text fst; C.field "b" absent_alias snd] in
  check "optional alias omission" alias_record ("abc", None)
    "4449444c016c016171010003616263";
  check "negative zero64" C.float64 (-0.) "4449444c0001720000000000000080";
  check "negative zero32" C.float32 (-0.) "4449444c00017300000080";
  check "signaling NaN32" C.float32_bits 0xff80_0001l
    "4449444c0001730000c07f";
  check "signaling NaN64" C.float64_bits 0xfff0_0000_0000_0001L
    "4449444c000172000000000000f87f";
  check "smallest subnormal32" C.float32_bits 1l "4449444c00017301000000";
  check "smallest subnormal64" C.float64_bits 1L "4449444c0001720100000000000000";
  check "maximum nat64" C.nat64 (Z.pred (Z.shift_left Z.one 64))
    "4449444c000178ffffffffffffffff";
  check "minimum int64" C.int64 Int64.min_int "4449444c0001740000000000000080";
  check "maximum nat32" C.nat32 0xffff_ffffL "4449444c000179ffffffff";
  check "minimum int32" C.int32 Int32.min_int "4449444c00017500000080";
  check "signed LEB" C.int (Z.of_int (-65)) "4449444c00017cbf7f";
  check "unsigned LEB" C.nat (Z.of_int 128) "4449444c00017d8001";
  check "principal management" C.principal Bytes.empty "4449444c0001680100";
  check "explicit null retained" (C.record [
    C.field "a" C.text fst; C.field "b" C.null snd]) ("abc", ())
    "4449444c016c026171627f010003616263";
  check "reserved retained" (C.record [
    C.field "a" C.text fst; C.field "b" C.reserved snd]) ("abc", 42)
    "4449444c016c0261716270010003616263";
  check "field u32 max" (C.record [C.field_num 0xffff_ffffL C.nat Fun.id]) Z.one
    "4449444c016c01ffffffff0f7d010001";
  check "selected case only" events (B "abc")
    "4449444c016b01627101000003616263";
  let narrowed = C.variant [C.case "b" C.text (fun s -> Some s)] in
  check_bytes "unused cases" (encoded events (B "abc")) (encoded narrowed "abc");
  let mixed = [|{a="abc";b=None;nested=None};
    {a="xyz";b=Some "present";nested=Some None};
    {a="z";b=Some "";nested=Some (Some Z.one)}|] in
  let bytes = encoded (C.array application) mixed in
  check_bytes "mixed array records and nested wrappers" bytes bytes;
  let variants = encoded (C.list events) [B "text"; A Z.one; B ""] in
  check_bytes "variant join and rewritten indices" variants variants;
  let recursive = encoded chain {head=Z.of_int 128;tail=Some {head=Z.one;tail=None}} in
  check_bytes "finite recursive record" recursive recursive;
  (match C.encode_args [C.arg C.bool true; C.arg C.bool false; C.arg C.null ()] with
  | Ok bytes -> check_bytes "typed arguments" bytes (from_hex "4449444c00037e7e7f0100")
  | Error message -> failwith message);
  (match C.encode_args [] with
  | Ok bytes -> check_bytes "empty arguments" bytes (from_hex "4449444c0000")
  | Error message -> failwith message);
  rejects "negative nat" C.nat Z.minus_one;
  rejects "nat8 overflow" C.nat8 256;
  rejects "nat16 negative" C.nat16 (-1);
  rejects "nat32 overflow" C.nat32 0x1_0000_0000L;
  rejects "nat64 overflow" C.nat64 (Z.shift_left Z.one 64);
  rejects "int8 overflow" C.int8 128;
  rejects "int16 underflow" C.int16 (-32769);
  rejects "invalid UTF8" C.text "\255";
  rejects "principal length" C.principal (Bytes.make 30 '\000');
  rejects "no variant selector" events Unused;
  let ambiguous = C.variant [C.case "a" C.null (fun _ -> Some ());
    C.case "b" C.null (fun _ -> Some ())] in
  rejects "multiple variant selectors" ambiguous ();
  invalid_schema "named numeric collision" (fun () -> C.record [
    C.field "a" C.null Fun.id; C.field_num 97L C.null Fun.id]);
  invalid_schema "duplicate case" (fun () -> C.variant [
    C.case "a" C.null (fun _ -> Some ()); C.case "a" C.null (fun _ -> None)]);
  invalid_schema "negative field ID" (fun () -> C.field_num (-1L) C.null Fun.id);
  invalid_schema "overflow field ID" (fun () -> C.field_num 0x1_0000_0000L C.null Fun.id);
  invalid_schema "invalid UTF8 label" (fun () -> C.field "\255" C.null Fun.id);
  let long_integer = Z.shift_left Z.one (4096 * 7 - 1) in
  let bytes = encoded C.nat long_integer in
  check_bytes "4096 byte nat accepted" bytes bytes;
  rejects "4097 byte nat" C.nat (Z.shift_left long_integer 1);
  rejects "4097 byte signed int" C.int long_integer;
  let bytes = encoded C.int (Z.neg long_integer) in
  check_bytes "4096 byte negative int accepted" bytes bytes;
  let deep = List.fold_left (fun value _ -> Next value) Stop (List.init 257 Fun.id) in
  rejects "finite value depth" nested deep;
  let cycle = C.recursive Fun.id in
  rejects "unproductive recursion" cycle ();
  let calls = ref 0 in
  let changing = C.record [C.field "x" (C.option C.nat8) (fun () ->
    incr calls; if !calls = 1 then Some 1 else None)] in
  rejects "getter present to absent" changing ();
  if !calls <> 2 then failwith "present getter visited more than twice";
  let calls = ref 0 in
  let changing = C.record [C.field "x" (C.option C.nat8) (fun () ->
    incr calls; if !calls = 1 then None else Some 1)] in
  rejects "getter absent to present" changing ();
  if !calls <> 2 then failwith "absent getter was not visited twice";
  let calls = ref 0 in
  let changing = C.record [C.field "x" (C.array C.nat) (fun () ->
    incr calls; if !calls = 1 then [|Z.one|] else [||])] in
  rejects "getter makes vector empty" changing ();
  let calls = ref 0 in
  let adapted = C.contramap (fun () ->
    incr calls; if !calls = 1 then Some 1 else None) (C.option C.nat8) in
  let changing = C.record [C.field "x" adapted Fun.id] in
  rejects "mapper changes presence" changing ();
  if !calls <> 2 then failwith "mapper visited more than twice";
  let calls = ref 0 in
  let changing = C.variant [
    C.case "a" C.null (fun () -> incr calls;
      if !calls = 1 then Some () else None);
    C.case "b" C.null (fun () -> if !calls > 1 then Some () else None)] in
  rejects "selector changes used case" changing ();
  let schema = C.record (List.init 11 (fun i ->
    C.field_num (Int64.of_int i) (C.option C.null) (fun mask ->
      if mask land (1 lsl i) = 0 then None else Some ()))) in
  let arguments = List.init 1023 (fun mask -> C.arg schema mask) in
  (match C.encode_args arguments with
  | Ok bytes -> check_bytes "1024 output table entries accepted" bytes bytes
  | Error message -> failwith message);
  let arguments = List.init 1024 (fun mask -> C.arg schema mask) in
  (match C.encode_args arguments with
  | Error _ -> incr tests
  | Ok _ -> failwith "1025 output table entries were accepted");
  let maximum_blob = Bytes.make 1_999_999 '\000' in
  let bytes = encoded C.blob maximum_blob in
  check_bytes "2M-step blob accepted" bytes bytes;
  rejects "blob value step limit" C.blob (Bytes.make 2_000_000 '\000');
  Printf.printf "OCaml direct typed tests: %d passed\n" !tests
