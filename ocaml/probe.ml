let decode_hex line =
  if String.length line mod 2 <> 0 then invalid_arg "odd hex";
  Bytes.init (String.length line / 2) (fun i ->
    Char.chr (int_of_string ("0x" ^ String.sub line (i * 2) 2)))
let encode_hex bytes =
  let out = Buffer.create (2 * Bytes.length bytes) in
  Bytes.iter (fun c -> Buffer.add_string out
    (Printf.sprintf "%02x" (Char.code c))) bytes;
  Buffer.contents out
let () =
  try while true do
    let line = read_line () in
    match Candidx.canonicalize (decode_hex line) with
    | Ok bytes -> print_endline ("ok " ^ encode_hex bytes)
    | Error message -> print_endline ("error " ^ message)
  done with End_of_file -> ()

