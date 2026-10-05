let profile = "cx"
let max_bytes = 32 * 1024 * 1024
let max_items = 2_000_000
let max_types = 1024
let max_depth = 256
exception Invalid of string
let fail message = raise (Invalid message)

type node = { op : int; labels : int array; edges : int array }
type value =
  | Scalar of int * bytes
  | Some_value of value
  | Vector of value list
  | Blob of bytes
  | Record of (int * value) list
  | Variant of int * value
type shape = { kind : int; fields : int list; children : shape list }
let scalar kind = { kind; fields = []; children = [] }
let unary kind child = { kind; fields = []; children = [child] }
let null_value = Scalar (-1, Bytes.empty)
let is_null = function Scalar (-1, _) -> true | _ -> false

type reader = { data : bytes; mutable pos : int }
let take r n =
  if n < 0 || n > Bytes.length r.data - r.pos then fail "truncated message";
  let bytes = Bytes.sub r.data r.pos n in
  r.pos <- r.pos + n;
  bytes
let byte r =
  if r.pos = Bytes.length r.data then fail "truncated message";
  let b = Char.code (Bytes.get r.data r.pos) in
  r.pos <- r.pos + 1;
  b
let integer r signed limit =
  let rec loop i acc =
    if i >= limit then fail "integer length limit";
    let b = byte r in
    let acc = Z.logor acc (Z.shift_left (Z.of_int (b land 127)) (7 * i)) in
    if b < 128 then
      if signed && b land 64 <> 0 then
        Z.sub acc (Z.shift_left Z.one (7 * (i + 1)))
      else acc
    else loop (i + 1) acc
  in
  loop 0 Z.zero
let scalar_integer r signed =
  let start = r.pos in
  let rec scan i =
    if i = 4096 then fail "integer length limit";
    if byte r >= 128 then scan (i + 1)
  in
  scan 0;
  let end_ = ref r.pos in
  let rec trim () =
    if !end_ - start > 1 then begin
      let last = Char.code (Bytes.get r.data (!end_ - 1)) land 127 in
      let previous = Char.code (Bytes.get r.data (!end_ - 2)) in
      if (not signed && last = 0) || (signed &&
        ((last = 0 && previous land 64 = 0) ||
         (last = 127 && previous land 64 <> 0)))
      then begin decr end_; trim () end
    end
  in
  trim ();
  let b = Bytes.sub r.data start (!end_ - start) in
  let last = Bytes.length b - 1 in
  Bytes.set b last (Char.chr (Char.code (Bytes.get b last) land 127));
  b
let number r signed =
  let n = integer r signed 10 in
  if not (Z.fits_int n) then fail "index range";
  Z.to_int n
let length r =
  let n = number r false in
  if n > max_items then fail "collection length limit";
  n
let refs r = Array.init (length r) (fun _ -> number r true)
let valid_utf8 bytes =
  let n = Bytes.length bytes in
  let at i = Char.code (Bytes.get bytes i) in
  let rec scan i =
    if i = n then true
    else
      let b = at i in
      if b < 128 then scan (i + 1)
      else
        let width, low, high =
          if b >= 0xc2 && b <= 0xdf then 2, 0x80, 0xbf
          else if b = 0xe0 then 3, 0xa0, 0xbf
          else if b = 0xed then 3, 0x80, 0x9f
          else if b >= 0xe1 && b <= 0xef then 3, 0x80, 0xbf
          else if b = 0xf0 then 4, 0x90, 0xbf
          else if b = 0xf4 then 4, 0x80, 0x8f
          else if b >= 0xf1 && b <= 0xf3 then 4, 0x80, 0xbf
          else 0, 0, 0
        in
        width > 0 && width <= n - i && at (i + 1) >= low &&
        at (i + 1) <= high &&
        (width < 3 || (at (i + 2) >= 0x80 && at (i + 2) <= 0xbf)) &&
        (width < 4 || (at (i + 3) >= 0x80 && at (i + 3) <= 0xbf)) &&
        scan (i + width)
  in
  scan 0
let read_text r =
  let bytes = take r (length r) in
  if not (valid_utf8 bytes) then fail "invalid UTF-8";
  bytes
let leb n signed =
  let out = Buffer.create 8 in
  let rec loop n =
    let b = Z.to_int (Z.logand n (Z.of_int 127)) in
    let next = Z.shift_right n 7 in
    let done_ = if signed then
      (Z.equal next Z.zero && b < 64) ||
      (Z.equal next Z.minus_one && b >= 64)
      else Z.equal next Z.zero
    in
    Buffer.add_char out (Char.chr (b lor if done_ then 0 else 128));
    if not done_ then loop next
  in
  loop n;
  Bytes.of_string (Buffer.contents out)

let parse r =
  if take r 4 <> Bytes.of_string "DIDL" then fail "missing DIDL magic";
  let count = length r in
  if count > max_types then fail "input type-table limit";
  let nodes = Array.init count (fun _ ->
    let op = number r true in
    let labels, edges = match op with
      | -18 | -19 -> [||], [|number r true|]
      | -20 | -21 ->
        let n = length r in
        let fields = Array.init n (fun _ ->
          let id = number r false in
          if id > 0xffff_ffff then fail "field ID range";
          let ref_ = number r true in id, ref_)
        in
        Array.iteri (fun i (id, _) ->
          if i > 0 && fst fields.(i - 1) >= id then
            fail "unordered field IDs") fields;
        Array.map fst fields, Array.map snd fields
      | -22 ->
        let args = refs r in
        let rets = refs r in
        let modes = take r (length r) in
        let count = Bytes.length modes in
        if count > 1 || (count = 1 &&
          not (List.mem (Char.code (Bytes.get modes 0)) [1; 2; 3])) ||
          (modes = Bytes.of_string "\002" && Array.length rets > 0)
        then fail "invalid function annotations";
        [||], Array.append args rets
      | -23 ->
        let n = length r in
        let previous = ref None in
        let edges = Array.init n (fun _ ->
          let name = read_text r in
          (match !previous with
          | Some p when Bytes.compare p name >= 0 ->
            fail "unordered service methods"
          | _ -> ());
          previous := Some name;
          number r true)
        in
        [||], edges
      | _ -> fail "unsupported compound type"
    in
    { op; labels; edges })
  in
  let roots = refs r in
  let check e =
    if e >= Array.length nodes || (e < 0 &&
      not (e >= -17 && e <= -1) && e <> -24)
    then fail "invalid type reference"
  in
  Array.iter check roots;
  Array.iter (fun n -> Array.iter check n.edges) nodes;
  Array.iter (fun n ->
    if n.op = -23 then Array.iter (fun e ->
      if e < 0 || nodes.(e).op <> -22 then fail "method must be func")
      n.edges) nodes;
  nodes, roots

let read_values r nodes roots =
  let steps = ref 0 in
  let rec read ref_ depth =
    incr steps;
    if !steps > max_items || depth > max_depth then
      fail "value traversal limit";
    if ref_ >= 0 then
      let node = nodes.(ref_) in
      match node.op with
      | -18 -> (match byte r with
        | 0 -> null_value
        | 1 -> Some_value (read node.edges.(0) (depth + 1))
        | _ -> fail "invalid option tag")
      | -19 ->
        let n = length r in
        if node.edges.(0) = -5 then begin
          steps := !steps + n;
          if !steps > max_items || (n <> 0 && depth + 1 > max_depth) then
            fail "value traversal limit";
          Blob (take r n)
        end else Vector (List.init n (fun _ -> read node.edges.(0) (depth + 1)))
      | -20 ->
        let fields = Array.to_list (Array.mapi (fun i edge ->
          let v = read edge (depth + 1) in
          let absent = edge >= 0 && nodes.(edge).op = -18 && is_null v in
          node.labels.(i), v, absent) node.edges)
        in
        Record (List.filter_map (fun (id, v, absent) ->
          if absent then None else Some (id, v)) fields)
      | -21 ->
        let i = length r in
        if i >= Array.length node.edges then fail "invalid variant index";
        Variant (node.labels.(i), read node.edges.(i) (depth + 1))
      | _ -> fail "function/service values outside data profile"
    else
      let raw = match ref_ with
        | -1 | -16 -> Bytes.empty
        | -17 -> fail "empty has no values"
        | -2 -> let b = take r 1 in
          if Char.code (Bytes.get b 0) > 1 then fail "invalid bool"; b
        | -3 | -4 -> scalar_integer r (ref_ = -4)
        | n when n >= -12 && n <= -5 -> take r (1 lsl ((-n - 5) mod 4))
        | -13 | -14 ->
          let size, frac, exp = if ref_ = -13 then 4, 23, 8 else 8, 52, 11 in
          let b = take r size in
          let bits = ref Z.zero in
          Bytes.iteri (fun i c -> bits := Z.logor !bits
            (Z.shift_left (Z.of_int (Char.code c)) (8 * i))) b;
          let exp_mask = Z.pred (Z.shift_left Z.one exp) in
          if Z.equal (Z.logand (Z.shift_right !bits frac) exp_mask) exp_mask &&
            not (Z.equal (Z.logand !bits (Z.pred (Z.shift_left Z.one frac))) Z.zero)
          then Bytes.of_string (if size = 4 then "\000\000\192\127"
            else "\000\000\000\000\000\000\248\127") else b
        | -15 -> let b = read_text r in
          Bytes.cat (leb (Z.of_int (Bytes.length b)) false) b
        | -24 ->
          if byte r <> 1 then fail "opaque principal";
          let n = length r in
          if n > 29 then fail "principal length exceeds 29";
          Bytes.cat (Bytes.init 2 (fun i -> Char.chr (if i = 0 then 1 else n)))
            (take r n)
        | _ -> fail "unsupported primitive"
      in
      Scalar (ref_, raw)
  in
  Array.to_list (Array.map (fun e -> read e 0) roots)

let only = function [x] -> x | _ -> fail "invalid internal unary shape"
let rec join a b depth =
  if depth > max_depth then fail "type nesting limit";
  if a = b || b.kind = -17 then a
  else if a.kind = -17 then b
  else if a.kind = -1 then if b.kind = -18 then b else unary (-18) b
  else if b.kind = -1 then if a.kind = -18 then a else unary (-18) a
  else if a.kind = -18 || b.kind = -18 then
    unary (-18) (join (if a.kind = -18 then only a.children else a)
      (if b.kind = -18 then only b.children else b) (depth + 1))
  else if a.kind <> b.kind || not (List.mem a.kind [-19; -20; -21]) then
    fail "incompatible value shapes"
  else if a.kind = -19 then
    unary (-19) (join (only a.children) (only b.children) (depth + 1))
  else
    let aa = List.combine a.fields a.children in
    let bb = List.combine b.fields b.children in
    let fields = List.sort_uniq Int.compare (a.fields @ b.fields) in
    let children = List.map (fun k ->
      match List.assoc_opt k aa, List.assoc_opt k bb with
      | None, Some b when a.kind = -21 -> b
      | Some child, None when a.kind = -21 -> child
      | x, y -> join (Option.value x ~default:(scalar (-1)))
        (Option.value y ~default:(scalar (-1))) (depth + 1)) fields
    in
    { kind = a.kind; fields; children }
let rec shape value depth =
  if depth > max_depth then fail "type nesting limit";
  match value with
  | Scalar (kind, _) -> scalar kind
  | Some_value v -> unary (-18) (shape v (depth + 1))
  | Blob _ -> unary (-19) (scalar (-5))
  | Vector vs -> unary (-19) (List.fold_left (fun a v ->
    join a (shape v (depth + 1)) 0) (scalar (-17)) vs)
  | Record fields ->
    { kind = -20; fields = List.map fst fields;
      children = List.map (fun (_, v) -> shape v (depth + 1)) fields }
  | Variant (k, v) ->
    { kind = -21; fields = [k]; children = [shape v (depth + 1)] }

type output = {
  buffer : Buffer.t;
  add : bytes -> unit;
  add_string : string -> unit;
  put : int -> unit;
  num : int -> bool -> unit;
}

let make_output types =
  let indices = Hashtbl.create 32 in
  let table = ref [] in
  let rec intern t =
    if not (List.mem t.kind [-18; -19; -20; -21]) then t.kind
    else match Hashtbl.find_opt indices t with
    | Some i -> i
    | None ->
      let i = Hashtbl.length indices in
      if i >= max_types then fail "output type-table limit";
      Hashtbl.add indices t i;
      table := t :: !table;
      List.iter (fun c -> ignore (intern c)) t.children;
      i
  in
  let roots = List.map intern types in
  let out = Buffer.create 128 in
  let add b =
    if Bytes.length b > max_bytes - Buffer.length out then
      fail "output byte limit";
    Buffer.add_bytes out b
  in
  let add_string s =
    if String.length s > max_bytes - Buffer.length out then
      fail "output byte limit";
    Buffer.add_string out s
  in
  let put b =
    if Buffer.length out = max_bytes then fail "output byte limit";
    Buffer.add_char out (Char.chr b)
  in
  let num n signed = add (leb (Z.of_int n) signed) in
  add (Bytes.of_string "DIDL"); num (Hashtbl.length indices) false;
  List.iter (fun t ->
    num t.kind true;
    if t.kind = -18 || t.kind = -19 then num (intern (only t.children)) true
    else begin
      num (List.length t.fields) false;
      List.iter2 (fun k c -> num k false; num (intern c) true)
        t.fields t.children
    end) (List.rev !table);
  num (List.length roots) false;
  List.iter (fun i -> num i true) roots;
  { buffer = out; add; add_string; put; num }

let encode_values values =
  let types = List.map (fun v -> shape v 0) values in
  let out = make_output types in
  let add = out.add and num = out.num in
  let rec emit v t depth =
    if depth > max_depth then fail "emitted value nesting limit";
    if t.kind = -18 then
      if is_null v then add (Bytes.of_string "\000")
      else begin
        add (Bytes.of_string "\001");
        emit (match v with Some_value c -> c | _ -> v)
          (only t.children) (depth + 1)
      end
    else match v with
    | Scalar (op, b) when op = t.kind -> add b
    | Blob bytes when t.kind = -19 && (only t.children).kind = -5 ->
      num (Bytes.length bytes) false; add bytes
    | Vector vs when t.kind = -19 ->
      num (List.length vs) false;
      List.iter (fun c -> emit c (only t.children) (depth + 1)) vs
    | Record fields when t.kind = -20 ->
      List.iter2 (fun k c -> emit
        (Option.value (List.assoc_opt k fields) ~default:null_value) c (depth + 1))
        t.fields t.children
    | Variant (k, v) when t.kind = -21 ->
      let rec find i fields children = match fields, children with
        | k' :: _, c :: _ when k = k' -> i, c
        | _ :: ks, _ :: cs -> find (i + 1) ks cs
        | _ -> fail "missing derived variant"
      in
      let i, c = find 0 t.fields t.children in
      num i false; emit v c (depth + 1)
    | _ -> fail "cannot emit at derived type"
  in
  List.iter2 (fun v t -> emit v t 0) values types;
  Bytes.of_string (Buffer.contents out.buffer)

let canonicalize bytes =
  try
    if Bytes.length bytes > max_bytes then fail "input byte limit";
    let r = { data = bytes; pos = 0 } in
    let nodes, roots = parse r in
    let values = read_values r nodes roots in
    if r.pos <> Bytes.length bytes then fail "trailing payload";
    Ok (encode_values values)
  with Invalid message -> Error message

(* The typed path carries native values and their witnesses through both
   passes. Only finite shapes are retained between passes, never an erased
   value tree or an ordinary serialized message. *)
type empty = |
type 'a primitive = {
  opcode : int;
  validate : 'a -> unit;
  write : output -> 'a -> unit;
}
type _ typ =
  | Primitive : 'a primitive -> 'a typ
  | Empty : empty typ
  | Option : 'a typ -> 'a option typ
  | Array : 'a typ -> 'a array typ
  | List : 'a typ -> 'a list typ
  | Bytes : bytes typ
  | Record_type : 'r field array -> 'r typ
  | Variant_type : 'v case array -> 'v typ
  | Recursive : 'a typ Lazy.t -> 'a typ
  | View : ('a -> 'b) * 'b typ -> 'a typ
and 'r field = Field : int * 'a typ * ('r -> 'a) -> 'r field
and 'v case = Case : int * 'a typ * ('v -> 'a option) -> 'v case
type argument = Argument : 'a typ * 'a -> argument
type selected = Selected : int * 'a typ * 'a -> selected

let primitive opcode validate write = Primitive { opcode; validate; write }
let ignore_value _ = ()
let null = primitive (-1) ignore_value (fun _ () -> ())
let reserved = Primitive {
  opcode = -16; validate = ignore_value; write = (fun _ _ -> ()) }
let empty = Empty
let bool = primitive (-2) ignore_value (fun out b -> out.put (if b then 1 else 0))

let check_integer signed n =
  if not signed && Z.sign n < 0 then fail "negative nat";
  let bits = Z.numbits (if signed && Z.sign n < 0 then Z.lognot n else n) in
  let bits = if signed then bits + 1 else bits in
  if max 1 ((bits + 6) / 7) > 4096 then fail "integer length limit"
let write_integer out signed n =
  let rec loop n =
    let b = Z.to_int (Z.logand n (Z.of_int 127)) in
    let next = Z.shift_right n 7 in
    let done_ = if signed then
      (Z.equal next Z.zero && b < 64) ||
      (Z.equal next Z.minus_one && b >= 64)
      else Z.equal next Z.zero
    in
    out.put (b lor if done_ then 0 else 128);
    if not done_ then loop next
  in
  loop n
let nat = primitive (-3) (check_integer false) (fun o n -> write_integer o false n)
let int = primitive (-4) (check_integer true) (fun o n -> write_integer o true n)

let write_int64 out width n =
  for i = 0 to width - 1 do
    out.put (Int64.to_int (Int64.logand
      (Int64.shift_right_logical n (8 * i)) 255L))
  done
let small_int opcode low high width =
  primitive opcode (fun n ->
    if n < low || n > high then fail "fixed integer range")
    (fun out n -> write_int64 out width (Int64.of_int n))
let nat8 = small_int (-5) 0 255 1
let nat16 = small_int (-6) 0 65535 2
let nat32 = primitive (-7) (fun n ->
  if Int64.compare n 0L < 0 || Int64.compare n 0xffff_ffffL > 0 then
    fail "fixed integer range") (fun out n -> write_int64 out 4 n)
let nat64 = primitive (-8) (fun n ->
  if Z.sign n < 0 || Z.numbits n > 64 then fail "fixed integer range")
  (fun out n ->
    for i = 0 to 7 do
      out.put (Z.to_int (Z.extract n (8 * i) 8))
    done)
let int8 = small_int (-9) (-128) 127 1
let int16 = small_int (-10) (-32768) 32767 2
let int32 = primitive (-11) ignore_value
  (fun out n -> write_int64 out 4 (Int64.of_int32 n))
let int64 = primitive (-12) ignore_value (fun out n -> write_int64 out 8 n)

let canonical_float32 bits =
  if Int32.logand bits 0x7f80_0000l = 0x7f80_0000l &&
    Int32.logand bits 0x007f_ffffl <> 0l
  then 0x7fc0_0000l else bits
let canonical_float64 bits =
  if Int64.logand bits 0x7ff0_0000_0000_0000L = 0x7ff0_0000_0000_0000L &&
    Int64.logand bits 0x000f_ffff_ffff_ffffL <> 0L
  then 0x7ff8_0000_0000_0000L else bits
let float32_bits = primitive (-13) ignore_value
  (fun out bits -> write_int64 out 4 (Int64.of_int32 (canonical_float32 bits)))
let float64_bits = primitive (-14) ignore_value
  (fun out bits -> write_int64 out 8 (canonical_float64 bits))
let contramap f typ = View (f, typ)
let float32 = contramap Int32.bits_of_float float32_bits
let float64 = contramap Int64.bits_of_float float64_bits

let check_length n = if n > max_items then fail "collection length limit"
let bounded_list_length values =
  List.fold_left (fun n _ -> let n = n + 1 in check_length n; n) 0 values
let text = primitive (-15) (fun s ->
  check_length (String.length s);
  if not (valid_utf8 (Bytes.unsafe_of_string s)) then fail "invalid UTF-8")
  (fun out s -> out.num (String.length s) false; out.add_string s)
let principal = primitive (-24) (fun b ->
  if Bytes.length b > 29 then fail "principal length exceeds 29")
  (fun out b -> out.put 1; out.num (Bytes.length b) false; out.add b)
let blob = Bytes
let option typ = Option typ
let array typ = Array typ
let list typ = List typ
let recursive f =
  let rec self = Recursive (lazy (f self)) in
  self
let arg typ value = Argument (typ, value)

let label_id label =
  if not (valid_utf8 (Bytes.unsafe_of_string label)) then
    invalid_arg "Candidx: invalid UTF-8 label";
  let id = ref 0L in
  String.iter (fun c -> id := Int64.logand 0xffff_ffffL
    (Int64.add (Int64.mul !id 223L) (Int64.of_int (Char.code c)))) label;
  !id
let numeric_id id =
  if Int64.compare id 0L < 0 || Int64.compare id 0xffff_ffffL > 0 ||
    Int64.compare id (Int64.of_int max_int) > 0 then
    invalid_arg "Candidx: field ID range";
  Int64.to_int id
let field_num id typ get = Field (numeric_id id, typ, get)
let field label typ get = field_num (label_id label) typ get
let case_num id typ select = Case (numeric_id id, typ, select)
let case label typ select = case_num (label_id label) typ select
let sorted_unique kind id xs =
  (try ignore (bounded_list_length xs) with Invalid _ ->
    invalid_arg ("Candidx: too many " ^ kind));
  let xs = Array.of_list xs in
  if Array.length xs > max_items then
    invalid_arg ("Candidx: too many " ^ kind);
  Array.sort (fun a b -> Int.compare (id a) (id b)) xs;
  Array.iteri (fun i x ->
    if i > 0 && id xs.(i - 1) = id x then
      invalid_arg ("Candidx: duplicate or colliding " ^ kind ^ " ID")) xs;
  xs
let record fields = Record_type (sorted_unique "field"
  (fun (Field (id, _, _)) -> id) fields)
let variant cases = Variant_type (sorted_unique "case"
  (fun (Case (id, _, _)) -> id) cases)

let select_case cases value =
  let found = ref None in
  Array.iter (fun (Case (id, typ, select)) ->
    match select value with
    | None -> ()
    | Some payload ->
      if Option.is_some !found then fail "multiple selected variant cases";
      found := Some (Selected (id, typ, payload))) cases;
  match !found with
  | Some selected -> selected
  | None -> fail "no selected variant case"

let check_aliases aliases =
  if aliases > max_depth then fail "unproductive or excessive witness indirection"
let rec is_nat8 : type a. a typ -> int -> bool = fun typ aliases ->
  check_aliases aliases;
  match typ with
  | Primitive p -> p.opcode = -5
  | View (_, child) -> is_nat8 child (aliases + 1)
  | Recursive child -> is_nat8 (Lazy.force child) (aliases + 1)
  | _ -> false

let tick steps depth =
  incr steps;
  if !steps > max_items || depth > max_depth then fail "value traversal limit"
let count_blob steps depth bytes =
  let n = Bytes.length bytes in
  check_length n;
  steps := !steps + n;
  if !steps > max_items || (n <> 0 && depth + 1 > max_depth) then
    fail "value traversal limit"

type observation = { actual : shape; absent_optional : bool }
let observed actual = { actual; absent_optional = false }
let observed_absent = { actual = scalar (-1); absent_optional = true }

let observe_native steps typ value depth =
  let rec walk : type a. a typ -> a -> int -> observation = fun typ value depth ->
    tick steps depth;
    at typ value depth 0
  and at : type a. a typ -> a -> int -> int -> observation =
    fun typ value depth aliases ->
    check_aliases aliases;
    match typ with
    | View (f, child) -> at child (f value) depth (aliases + 1)
    | Recursive child -> at (Lazy.force child) value depth (aliases + 1)
    | Primitive p -> p.validate value; observed (scalar p.opcode)
    | Empty -> (match value with _ -> .)
    | Option child -> (match value with
      | None -> observed_absent
      | Some v -> observed (unary (-18) (walk child v (depth + 1)).actual))
    | Bytes -> count_blob steps depth value; observed (unary (-19) (scalar (-5)))
    | Array child ->
      check_length (Array.length value);
      let element = Array.fold_left (fun acc v ->
        join acc (walk child v (depth + 1)).actual 0) (scalar (-17)) value in
      let element = if Array.length value = 0 && is_nat8 child 0
        then scalar (-5) else element in
      observed (unary (-19) element)
    | List child ->
      let n = ref 0 in
      let element = List.fold_left (fun acc v ->
        incr n; check_length !n;
        join acc (walk child v (depth + 1)).actual 0) (scalar (-17)) value in
      let element = if !n = 0 && is_nat8 child 0 then scalar (-5) else element in
      observed (unary (-19) element)
    | Record_type fields ->
      let used = Array.fold_right (fun (Field (id, child, get)) acc ->
        let observation = walk child (get value) (depth + 1) in
        if observation.absent_optional then acc
        else (id, observation.actual) :: acc) fields [] in
      observed { kind = -20; fields = List.map fst used; children = List.map snd used }
    | Variant_type cases ->
      let Selected (id, child, v) = select_case cases value in
      observed { kind = -21; fields = [id]; children = [(walk child v (depth + 1)).actual] }
  in
  walk typ value depth

let emit_native out steps typ value wire depth =
  let rec emit : type a. a typ -> a -> shape -> int -> observation =
    fun typ value wire depth ->
    tick steps depth;
    at typ value wire depth 0
  and at : type a. a typ -> a -> shape -> int -> int -> observation =
    fun typ value wire depth aliases ->
    check_aliases aliases;
    if depth > max_depth then fail "emitted value nesting limit";
    match typ with
    | View (f, child) -> at child (f value) wire depth (aliases + 1)
    | Recursive child -> at (Lazy.force child) value wire depth (aliases + 1)
    | Option child -> (match value with
      | None -> absent wire; observed_absent
      | Some v ->
        if wire.kind <> -18 then fail "changed option value during encoding";
        out.put 1;
        let child = emit child v (only wire.children) (depth + 1) in
        observed (unary (-18) child.actual))
    | _ when wire.kind = -18 ->
      (match typ with
      | Primitive p when p.opcode = -1 -> out.put 0; observed (scalar (-1))
      | _ ->
        out.put 1;
        at typ value (only wire.children) (depth + 1) aliases)
    | Primitive p ->
      if p.opcode <> wire.kind then fail "cannot emit scalar at derived type";
      p.validate value;
      p.write out value;
      observed (scalar p.opcode)
    | Empty -> (match value with _ -> .)
    | Bytes ->
      count_blob steps depth value;
      if wire.kind <> -19 || (only wire.children).kind <> -5 then
        fail "cannot emit blob at derived type";
      out.num (Bytes.length value) false;
      out.add value;
      observed (unary (-19) (scalar (-5)))
    | Array child ->
      if wire.kind <> -19 then fail "cannot emit array at derived type";
      check_length (Array.length value);
      out.num (Array.length value) false;
      let element = Array.fold_left (fun acc v ->
        join acc (emit child v (only wire.children) (depth + 1)).actual 0)
        (scalar (-17)) value in
      let element = if Array.length value = 0 && is_nat8 child 0 then scalar (-5) else element in
      observed (unary (-19) element)
    | List child ->
      if wire.kind <> -19 then fail "cannot emit list at derived type";
      let n = bounded_list_length value in
      out.num n false;
      let element = List.fold_left (fun acc v ->
        join acc (emit child v (only wire.children) (depth + 1)).actual 0)
        (scalar (-17)) value in
      let element = if n = 0 && is_nat8 child 0 then scalar (-5) else element in
      observed (unary (-19) element)
    | Record_type fields ->
      if wire.kind <> -20 then fail "cannot emit record at derived type";
      let remaining = ref (List.combine wire.fields wire.children) in
      let used = Array.fold_left (fun acc (Field (id, typ, get)) ->
        let rec skip_missing () = match !remaining with
          | (candidate, child) :: rest when candidate < id ->
            absent child; remaining := rest; skip_missing ()
          | _ -> ()
        in
        skip_missing ();
        let v = get value in
        let observation = match !remaining with
          | (candidate, child) :: rest when candidate = id ->
            remaining := rest; emit typ v child (depth + 1)
          | _ ->
            let observation = observe_native steps typ v (depth + 1) in
            if not observation.absent_optional then
              fail "changed record fields during encoding";
            observation
        in
        if observation.absent_optional then acc
        else (id, observation.actual) :: acc) [] fields in
      List.iter (fun (_, child) -> absent child) !remaining;
      let used = List.rev used in
      observed { kind = -20; fields = List.map fst used; children = List.map snd used }
    | Variant_type cases ->
      if wire.kind <> -21 then fail "cannot emit variant at derived type";
      let Selected (id, child, v) = select_case cases value in
      let rec find index fields children = match fields, children with
        | candidate :: _, shape :: _ when candidate = id -> index, shape
        | _ :: ids, _ :: shapes -> find (index + 1) ids shapes
        | _ -> fail "changed selected variant during encoding"
      in
      let index, payload = find 0 wire.fields wire.children in
      out.num index false;
      let payload = emit child v payload (depth + 1) in
      observed { kind = -21; fields = [id]; children = [payload.actual] }
  and absent wire =
    if wire.kind = -18 then out.put 0
    else if wire.kind <> -1 then fail "cannot emit absence at derived type"
  in
  emit typ value wire depth

let encode_args arguments =
  try
    ignore (bounded_list_length arguments);
    let steps = ref 0 in
    let derive typ value depth = (observe_native steps typ value depth).actual in
    let types = List.map (fun (Argument (typ, value)) -> derive typ value 0) arguments in
    let out = make_output types in
    let steps = ref 0 in
    let emit typ value wire depth = emit_native out steps typ value wire depth in
    List.iter2 (fun (Argument (typ, value)) wire ->
      if (emit typ value wire 0).actual <> wire then
        fail "value shape changed during encoding")
      arguments types;
    Ok (Bytes.of_string (Buffer.contents out.buffer))
  with Invalid message -> Error message

let encode typ value = encode_args [arg typ value]
