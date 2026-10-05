module C = Candidx
type transaction = { owner : bytes; amount : Z.t; memo : bytes option }
type block = {
  timestamp : Z.t;
  parent_hash : bytes option;
  tx : transaction array;
  note : string option;
}

let transaction = C.record [
  C.field "owner" C.principal (fun t -> t.owner);
  C.field "amount" C.nat (fun t -> t.amount);
  C.field "memo" (C.option C.blob) (fun t -> t.memo);
]
let block = C.record [
  C.field "timestamp" C.nat64 (fun b -> b.timestamp);
  C.field "parentHash" (C.option C.blob) (fun b -> b.parent_hash);
  C.field "tx" (C.array transaction) (fun b -> b.tx);
  C.field "note" (C.option C.text) (fun b -> b.note);
]

let encode b = match C.encode block b with
  | Ok bytes -> bytes | Error message -> failwith message
let hex bytes =
  let chars = "0123456789abcdef" in
  String.init (2 * Bytes.length bytes) (fun i ->
    let n = Char.code (Bytes.get bytes (i / 2)) in
    chars.[if i mod 2 = 0 then n lsr 4 else n land 15])

let checksum = ref 0
let sample f =
  let consume () = checksum := !checksum lxor Sys.opaque_identity (f ()) in
  for _ = 1 to 3 do consume () done;
  let started = Unix.gettimeofday () in
  consume ();
  let elapsed = max 0.000001 (Unix.gettimeofday () -. started) in
  let iterations = max 2 (min 2000 (int_of_float (0.025 /. elapsed))) in
  let trials = Array.init 7 (fun _ ->
    Gc.full_major ();
    let before = Gc.quick_stat () in
    let allocated = Gc.allocated_bytes () in
    let started = Unix.gettimeofday () in
    for _ = 1 to iterations do consume () done;
    let us = (Unix.gettimeofday () -. started) *. 1e6 /. float iterations in
    let allocated = (Gc.allocated_bytes () -. allocated) /. float iterations in
    let after = Gc.quick_stat () in
    us, allocated,
    after.Gc.minor_collections - before.Gc.minor_collections,
    after.Gc.major_collections - before.Gc.major_collections,
    after.Gc.compactions - before.Gc.compactions)
  in
  Array.sort (fun (a, _, _, _, _) (b, _, _, _, _) -> Float.compare a b) trials;
  let us, allocated, minor, major, compactions = trials.(3) in
  let min_us, _, _, _, _ = trials.(0) and max_us, _, _, _, _ = trials.(6) in
  Printf.sprintf
    "{\"median_us\":%.6f,\"min_us\":%.6f,\"max_us\":%.6f,\"allocated_bytes_per_op\":%.0f,\"iterations_per_trial\":%d,\"trials\":7,\"minor_collections_per_trial\":%d,\"major_collections_per_trial\":%d,\"compactions_per_trial\":%d}"
    us min_us max_us allocated iterations minor major compactions

let () =
  let cases = List.map (fun (name, count, memo_size) ->
    let memo = Bytes.init memo_size (fun i -> Char.chr (i mod 256)) in
    let huge = Z.shift_left Z.one 200 in
    let value = {
      timestamp = Z.of_int 1234567890;
      parent_hash = Some (Bytes.make 32 '\007');
      note = None;
      tx = Array.init count (fun i -> {
        owner = Bytes.of_string "\001\002";
        amount = Z.add huge (Z.of_int i);
        memo = if i mod 3 = 0 || memo_size = 65536 then Some memo else None;
      });
    } in
    let canonical = encode value in
    (match C.canonicalize canonical with
    | Ok bytes when bytes = canonical -> ()
    | _ -> failwith "direct benchmark idempotence failure");
    let timing = sample (fun () -> Bytes.length (encode (Sys.opaque_identity value))) in
    Printf.sprintf
      "{\"name\":%S,\"transactions\":%d,\"memo_bytes\":%d,\"canonical_bytes\":%d,\"canonical_hex\":%S,\"canonical_encode_direct\":%s}"
      name count memo_size (Bytes.length canonical) (hex canonical) timing)
    ["small", 4, 32; "records-128", 128, 32;
     "blob-64k", 1, 65536; "records-1024", 1024, 0]
  in
  Printf.printf
    "{\"language\":\"OCaml\",\"ordinary_baseline\":\"unavailable: the audited OCaml Candid frontend has no ordinary binary data codec\",\"checksum\":%d,\"cases\":[%s]}\n"
    !checksum (String.concat "," cases)
