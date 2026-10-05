(* Small JSON reader for the test executables only; no library dependency. *)
type t =
  | Null
  | Bool of bool
  | Number of string
  | String of string
  | Array of t array
  | Object of (string * t) list

let string = function String s | Number s -> s | _ -> invalid_arg "JSON string"
let array = function Array a -> a | _ -> invalid_arg "JSON array"
let bool = function Bool b -> b | _ -> invalid_arg "JSON boolean"
let member name = function
  | Object fields -> (match List.assoc_opt name fields with
    | Some value -> value | None -> invalid_arg ("missing JSON member " ^ name))
  | _ -> invalid_arg "JSON object"
let optional name = function Object fields -> List.assoc_opt name fields | _ -> None

let parse source =
  let pos = ref 0 and n = String.length source in
  let invalid () = invalid_arg ("invalid JSON at byte " ^ string_of_int !pos) in
  let take () =
    if !pos = n then invalid ();
    let c = source.[!pos] in incr pos; c
  in
  let rec space () =
    if !pos < n && List.mem source.[!pos] [' '; '\t'; '\n'; '\r'] then
      (incr pos; space ())
  in
  let literal rest value =
    String.iter (fun c -> if take () <> c then invalid ()) rest;
    value
  in
  let unicode () =
    let digit c = match c with
      | '0' .. '9' -> Char.code c - Char.code '0'
      | 'a' .. 'f' -> Char.code c - Char.code 'a' + 10
      | 'A' .. 'F' -> Char.code c - Char.code 'A' + 10
      | _ -> invalid ()
    in
    let code = ref 0 in
    for _ = 1 to 4 do code := (!code lsl 4) lor digit (take ()) done;
    !code
  in
  let utf8 out c =
    let put n = Buffer.add_char out (Char.chr n) in
    if c < 0x80 then put c
    else if c < 0x800 then (put (0xc0 lor (c lsr 6)); put (0x80 lor (c land 63)))
    else if c < 0x10000 then begin
      put (0xe0 lor (c lsr 12)); put (0x80 lor ((c lsr 6) land 63));
      put (0x80 lor (c land 63))
    end else begin
      put (0xf0 lor (c lsr 18)); put (0x80 lor ((c lsr 12) land 63));
      put (0x80 lor ((c lsr 6) land 63)); put (0x80 lor (c land 63))
    end
  in
  let quoted () =
    let out = Buffer.create 32 in
    let rec loop () = match take () with
      | '"' -> Buffer.contents out
      | '\\' ->
        (match take () with
        | ('"' | '\\' | '/') as c -> Buffer.add_char out c
        | 'b' -> Buffer.add_char out '\b'
        | 'f' -> Buffer.add_char out '\012'
        | 'n' -> Buffer.add_char out '\n'
        | 'r' -> Buffer.add_char out '\r'
        | 't' -> Buffer.add_char out '\t'
        | 'u' ->
          let code = unicode () in
          let code = if code >= 0xd800 && code <= 0xdbff then begin
            if take () <> '\\' || take () <> 'u' then invalid ();
            let low = unicode () in
            if low < 0xdc00 || low > 0xdfff then invalid ();
            0x10000 + ((code - 0xd800) lsl 10) + low - 0xdc00
          end else if code >= 0xdc00 && code <= 0xdfff then invalid () else code in
          utf8 out code
        | _ -> invalid ());
        loop ()
      | c when Char.code c < 32 -> invalid ()
      | c -> Buffer.add_char out c; loop ()
    in
    loop ()
  in
  let rec value () =
    space ();
    match take () with
    | 'n' -> literal "ull" Null
    | 't' -> literal "rue" (Bool true)
    | 'f' -> literal "alse" (Bool false)
    | '"' -> String (quoted ())
    | '[' ->
      space ();
      if !pos < n && source.[!pos] = ']' then (incr pos; Array [||])
      else
        let rec items acc =
          let item = value () in space ();
          match take () with
          | ']' -> Array (Array.of_list (List.rev (item :: acc)))
          | ',' -> items (item :: acc)
          | _ -> invalid ()
        in items []
    | '{' ->
      space ();
      if !pos < n && source.[!pos] = '}' then (incr pos; Object [])
      else
        let rec fields acc =
          space (); if take () <> '"' then invalid ();
          let key = quoted () in
          if List.mem_assoc key acc then invalid ();
          space (); if take () <> ':' then invalid ();
          let item = value () in space ();
          match take () with
          | '}' -> Object (List.rev ((key, item) :: acc))
          | ',' -> fields ((key, item) :: acc)
          | _ -> invalid ()
        in fields []
    | ('-' | '0' .. '9') ->
      let start = !pos - 1 in
      while !pos < n && List.mem source.[!pos]
        ['0';'1';'2';'3';'4';'5';'6';'7';'8';'9';'.';'e';'E';'+';'-'] do
        incr pos
      done;
      Number (String.sub source start (!pos - start))
    | _ -> invalid ()
  in
  let result = value () in space ();
  if !pos <> n then invalid ();
  result

let quote s =
  let out = Buffer.create (String.length s + 2) in
  Buffer.add_char out '"';
  String.iter (fun c -> match c with
    | '"' | '\\' -> Buffer.add_char out '\\'; Buffer.add_char out c
    | '\n' -> Buffer.add_string out "\\n"
    | '\r' -> Buffer.add_string out "\\r"
    | '\t' -> Buffer.add_string out "\\t"
    | c when Char.code c < 32 -> Buffer.add_string out
      (Printf.sprintf "\\u%04x" (Char.code c))
    | c -> Buffer.add_char out c) s;
  Buffer.add_char out '"'; Buffer.contents out

let read path =
  let channel = open_in_bin path in
  Fun.protect ~finally:(fun () -> close_in channel) (fun () ->
    parse (really_input_string channel (in_channel_length channel)))

let hex bytes =
  let chars = "0123456789abcdef" in
  let out = Bytes.create (2 * Bytes.length bytes) in
  Bytes.iteri (fun i c ->
    let n = Char.code c in
    Bytes.set out (2 * i) chars.[n lsr 4];
    Bytes.set out (2 * i + 1) chars.[n land 15]) bytes;
  Bytes.to_string out
