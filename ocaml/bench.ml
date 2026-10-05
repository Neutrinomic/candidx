let decode_hex line =
  if String.length line mod 2 <> 0 then invalid_arg "odd hex";
  Bytes.init (String.length line / 2) (fun i ->
    Char.chr (int_of_string ("0x" ^ String.sub line (i * 2) 2)))
let run bytes = match Candidx.canonicalize bytes with
  | Ok result -> Sys.opaque_identity (Bytes.length result)
  | Error message -> failwith message
let () =
  let bytes = decode_hex (read_line ()) in
  let checksum = ref 0 in
  for _ = 1 to 3 do checksum := !checksum lxor run bytes done;
  let started = Unix.gettimeofday () in
  checksum := !checksum lxor run bytes;
  let elapsed = max 0.000001 (Unix.gettimeofday () -. started) in
  let iterations = max 2 (min 2000 (int_of_float (0.025 /. elapsed))) in
  let samples = Array.init 7 (fun _ ->
    Gc.full_major ();
    let before = Gc.allocated_bytes () in
    let started = Unix.gettimeofday () in
    for _ = 1 to iterations do checksum := !checksum lxor run bytes done;
    let us = (Unix.gettimeofday () -. started) *. 1e6 /. float iterations in
    let allocated = (Gc.allocated_bytes () -. before) /. float iterations in
    us, allocated)
  in
  Array.sort (fun (a, _) (b, _) -> Float.compare a b) samples;
  Printf.printf "{\"median_us\":%.6f,\"min_us\":%.6f,\"max_us\":%.6f,\"allocated_bytes_per_op\":%.0f,\"iterations_per_trial\":%d,\"trials\":7,\"checksum\":%d}\n"
    (fst samples.(3)) (fst samples.(0)) (fst samples.(6))
    (snd samples.(3)) iterations !checksum
