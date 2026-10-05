(* Shared typed schema/value test driver. Not part of the installed API. *)
module J = Test_json
module C = Candidx
type packed = Pack : 'a C.typ * (J.t -> 'a) -> packed
type 'a link = 'a node option
and 'a node = { item : 'a; next : 'a link; extra : string option }
type chain = { head : Z.t; tail : chain option }

let number j = Z.of_string (J.string j)
let byte j =
  let n = Z.to_int (number j) in
  if n < 0 || n > 255 then invalid_arg "test byte range";
  Char.chr n
let bytes j = Bytes.init (Array.length (J.array j)) (fun i -> byte (J.array j).(i))
let float j = match J.string j with
  | "nan" -> Float.nan | "inf" -> Float.infinity | "-inf" -> Float.neg_infinity
  | text -> float_of_string text
let bits width j =
  let n = number j in
  let sign = Z.shift_left Z.one (width - 1) in
  let modulus = Z.shift_left Z.one width in
  if Z.compare n (Z.neg sign) < 0 || Z.compare n modulus >= 0 then
    invalid_arg "test float bit range";
  if Z.compare n sign >= 0 then Z.sub n modulus else n
let optional decode j = match J.array j with
  | [||] -> None
  | [|v|] -> Some (decode v)
  | _ -> invalid_arg "test option arity"

let rec witness schema =
  let s = J.array schema in
  let kind = J.string s.(0) in
  match kind with
  | "null" -> Pack (C.null, (function J.Null -> () | _ -> invalid_arg "test null"))
  | "reserved" -> Pack (C.reserved, (fun _ -> ()))
  | "empty" -> Pack (C.empty, (fun _ -> invalid_arg "empty has no values"))
  | "bool" -> Pack (C.bool, J.bool)
  | "nat" -> Pack (C.nat, number)
  | "int" -> Pack (C.int, number)
  | "nat8" -> Pack (C.nat8, (fun j -> Z.to_int (number j)))
  | "nat16" -> Pack (C.nat16, (fun j -> Z.to_int (number j)))
  | "nat32" -> Pack (C.nat32, (fun j -> Z.to_int64 (number j)))
  | "nat64" -> Pack (C.nat64, number)
  | "int8" -> Pack (C.int8, (fun j -> Z.to_int (number j)))
  | "int16" -> Pack (C.int16, (fun j -> Z.to_int (number j)))
  | "int32" -> Pack (C.int32, (fun j -> Z.to_int32 (number j)))
  | "int64" -> Pack (C.int64, (fun j -> Z.to_int64 (number j)))
  | "float32" -> Pack (C.float32, float)
  | "float64" -> Pack (C.float64, float)
  | "float32bits" -> Pack (C.float32_bits, (fun j -> Z.to_int32 (bits 32 j)))
  | "float64bits" -> Pack (C.float64_bits, (fun j -> Z.to_int64 (bits 64 j)))
  | "text" -> Pack (C.text, J.string)
  | "principal" -> Pack (C.principal, bytes)
  | "blob" -> Pack (C.blob, bytes)
  | "opt" ->
    let Pack (child, decode) = witness s.(1) in
    Pack (C.option child, optional decode)
  | "vec" ->
    let Pack (child, decode) = witness s.(1) in
    Pack (C.array child, (fun j -> Array.map decode (J.array j)))
  | "record" ->
    let fields = List.init (Array.length s - 1) (fun i ->
      let f = J.array s.(i + 1) in
      let label = J.string f.(0) in
      let Pack (child, decode) = witness f.(1) in
      C.field label child (fun j -> decode (J.member label j))) in
    Pack (C.record fields, (fun j -> j))
  | "tuple" ->
    let fields = List.init (Array.length s - 1) (fun i ->
      let Pack (child, decode) = witness s.(i + 1) in
      C.field_num (Int64.of_int i) child (fun tuple -> decode tuple.(i))) in
    Pack (C.record fields, J.array)
  | "variant" ->
    let cases = List.init (Array.length s - 1) (fun i ->
      let f = J.array s.(i + 1) in
      let label = J.string f.(0) in
      let Pack (child, decode) = witness f.(1) in
      C.case label child (fun j -> Option.map decode (J.optional label j))) in
    Pack (C.variant cases, (function
      | J.Object [_] as j -> j | _ -> invalid_arg "test variant arity"))
  | "list" ->
    let Pack (child, decode) = witness s.(1) in
    let extra = J.bool s.(2) in
    let typ = C.recursive (fun self -> C.option (C.record
      ([C.field "item" child (fun r -> r.item);
        C.field "next" self (fun r -> r.next)] @
       if extra then [C.field "extra" (C.option C.text) (fun r -> r.extra)] else []))) in
    let rec decode_link j = optional (fun r -> {
      item = decode (J.member "item" r);
      next = decode_link (J.member "next" r);
      extra = if extra then optional J.string (J.member "extra" r) else None;
    }) j in
    Pack (typ, decode_link)
  | "chain" ->
    let typ = C.recursive (fun self -> C.record [
      C.field "head" C.nat (fun r -> r.head);
      C.field "tail" (C.option self) (fun r -> r.tail)]) in
    let rec decode j = { head = number (J.member "head" j);
      tail = optional decode (J.member "tail" j) } in
    Pack (typ, decode)
  | _ -> invalid_arg ("test schema kind " ^ kind)

let argument schema value =
  let Pack (typ, decode) = witness schema in
  C.arg typ (decode value)

let run c =
  let id = J.string (J.member "id" c) in
  try
    let args = match J.optional "schemas" c with
      | None -> [argument (J.member "schema" c) (J.member "value" c)]
      | Some schemas ->
        let schemas = J.array schemas and values = J.array (J.member "values" c) in
        if Array.length schemas <> Array.length values then invalid_arg "argument arity";
        Array.to_list (Array.mapi (fun i schema -> argument schema values.(i)) schemas)
    in
    match C.encode_args args with
    | Error message -> invalid_arg message
    | Ok encoded ->
      Printf.printf "{\"id\":%s,\"canonical\":%s,\"decoding\":\"not-available\",\"direct_path\":\"typed-values\"}\n"
        (J.quote id) (J.quote (J.hex encoded))
  with Invalid_argument message | Failure message ->
    Printf.printf "{\"id\":%s,\"error\":%s}\n" (J.quote id) (J.quote message)

let () =
  let corpus = J.read Sys.argv.(1) in
  Array.iter run (J.array (J.member "cases" corpus))
