(** Issue configuration of an architecture (the ["issue"] field of
    protocol-emulator.architecture.v1). [Fused] has the XFER instruction;
    [Scalar] rejects it, so firmware times each bit with instructions. *)
type t =
  | Scalar
  | Fused
[@@deriving compare, enumerate, equal, sexp_of]

(** ["scalar"] or ["fused"], as in the JSON configs. *)
val to_string : t -> string

(** Raises [Invalid_argument "issue must be scalar or fused"] for any other
    string. *)
val of_string : string -> t
