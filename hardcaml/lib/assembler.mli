type source = { name : string; architecture : Isa.architecture; engine : int;
                owned_pins : int; open_drain : int; clock_hz : int;
                instructions : Yojson.Safe.t list; notes : string list }
type image = { source : source; words : int32 list; labels : (string * int) list;
               decoded : Isa.instruction list; source_sha256 : string }
val source_of_json : Yojson.Safe.t -> source
val source_to_json : source -> Yojson.Safe.t
val assemble : source_bytes:string -> image
val image_to_json : image -> Yojson.Safe.t
val bytecode : image -> string
val sha256 : string -> string
val node : ?label:string -> ?target:string -> Isa.instruction -> Yojson.Safe.t
