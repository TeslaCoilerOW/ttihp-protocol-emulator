type t =
  | Scalar
  | Fused
[@@deriving compare, enumerate, equal, sexp_of]

let to_string = function
  | Scalar -> "scalar"
  | Fused -> "fused"

let of_string s =
  match List.find_opt (fun t -> String.equal (to_string t) s) all with
  | Some t -> t
  | None -> invalid_arg "issue must be scalar or fused"
