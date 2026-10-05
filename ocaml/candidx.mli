(** Typed value-based canonical Candid encoding; no hashing. *)
val profile : string

(** A witness describes the Candid kind of an existing OCaml value. The
    encoder derives its finite wire type from the value, not from all of the
    declarations in its witness. *)
type 'a typ
type empty

val null : unit typ
val reserved : 'a typ
val empty : empty typ
val bool : bool typ
val nat : Z.t typ
val int : Z.t typ
val nat8 : int typ
val nat16 : int typ
val nat32 : int64 typ
val nat64 : Z.t typ
val int8 : int typ
val int16 : int typ
val int32 : int32 typ
val int64 : int64 typ
val float32 : float typ
val float64 : float typ

(** Bit-pattern witnesses retain every non-NaN bit, including negative zero.
    All NaNs are replaced by the profile's positive quiet NaN. [float32]
    rounds the OCaml float to IEEE binary32 first. *)
val float32_bits : int32 typ
val float64_bits : int64 typ
val text : string typ

(** A public principal is its raw bytes, of length at most 29. *)
val principal : bytes typ
val blob : bytes typ
val option : 'a typ -> 'a option typ
val array : 'a typ -> 'a array typ
val list : 'a typ -> 'a list typ

(** Adapt a native application representation without building a generic
    Candid value tree. The conversion, getters and selectors must be pure,
    and the value must remain unchanged during encoding. *)
val contramap : ('a -> 'b) -> 'b typ -> 'a typ

type 'r field
val field : string -> 'a typ -> ('r -> 'a) -> 'r field
val field_num : int64 -> 'a typ -> ('r -> 'a) -> 'r field
val record : 'r field list -> 'r typ

(** A selector returns [Some payload] precisely when its case is selected.
    Exactly one selector must select a case for each encoded variant value. *)
type 'v case
val case : string -> 'a typ -> ('v -> 'a option) -> 'v case
val case_num : int64 -> 'a typ -> ('v -> 'a option) -> 'v case
val variant : 'v case list -> 'v typ

(** Candid's UTF-8 label hash, modulo 2^32. Record/variant construction checks
    numeric labels, duplicate IDs and collisions, including named/numeric
    collisions. Invalid schema construction raises [Invalid_argument]. *)
val label_id : string -> int64

(** Tie a recursive declaration. Only actual finite values are traversed;
    unused cases and absent children need not unfold their witness. *)
val recursive : ('a typ -> 'a typ) -> 'a typ

type argument
val arg : 'a typ -> 'a -> argument

(** Derive used shapes and directly emit a canonical DIDL message from native
    values. No ordinary serialization or binary parsing occurs on this path.
    Invalid scalar data, selectors, resource limits, or a used shape that
    changes between the two passes return [Error]. *)
val encode : 'a typ -> 'a -> (bytes, string) result
val encode_args : argument list -> (bytes, string) result

(** Normalize a complete inbound ordinary Candid message before receiver
    projection. Malformed, unsupported, or resource-limited messages return
    [Error]. This utility is separate from the typed encoding path. *)
val canonicalize : bytes -> (bytes, string) result
