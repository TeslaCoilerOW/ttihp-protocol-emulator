(** Runnable examples. Supported framing and external timing limits are embedded
    in each source image and detailed in docs/firmware.md. *)
val names : string list
val make : ?architecture:Isa.architecture -> ?half_period:int -> ?mode:int ->
  ?clock_hz:int -> ?byte_lane_shifts:bool -> string -> Assembler.source
