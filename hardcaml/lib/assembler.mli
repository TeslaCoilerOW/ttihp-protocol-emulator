type source = { name : string; architecture : Isa.architecture; engine : int;
                owned_pins : int; open_drain : int; clock_hz : int;
                instructions : Yojson.Safe.t list; notes : string list }
type image = { source : source; words : int32 list; labels : (string * int) list;
               decoded : Isa.instruction list; source_sha256 : string }
val source_of_json : Yojson.Safe.t -> source
val source_to_json : source -> Yojson.Safe.t
val assemble : source_bytes:string -> image

(** [assemble] for a target whose SHL/SHR accept only byte-lane counts
    (0, 8, 16, 24) when [byte_lane_shifts] is true.  The image is unchanged
    in form: every accepted image is also a valid ISA v2 image. *)
val assemble_with : byte_lane_shifts:bool -> source_bytes:string -> image
val image_to_json : image -> Yojson.Safe.t
val bytecode : image -> string
val sha256 : string -> string
val node : ?label:string -> ?target:string -> Isa.instruction -> Yojson.Safe.t
