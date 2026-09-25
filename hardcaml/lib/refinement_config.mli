(** Closed, separate implementation refinement.  Architecture.v1 is unchanged. *)
type t = private { architecture : Config.t }
val implementation : string
val create : Config.t -> t
val of_json : Yojson.Safe.t -> t
val load : string -> t
