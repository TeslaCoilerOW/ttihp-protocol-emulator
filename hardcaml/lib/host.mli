type t = {
  word : Hardcaml.Signal.t; write : Hardcaml.Signal.t; window : Hardcaml.Signal.t;
  read_word : Hardcaml.Signal.t; read_lock : Hardcaml.Signal.t;
  outputs : Hardcaml.Signal.t;
  last_nibble_strobe : Hardcaml.Signal.t;
  (** With the timing knob [split_command_decode]: the write strobe of a word's
      last nibble before the window's write-ready term, so that
      [write = write_ready & last_nibble_strobe]. Otherwise equal to [write]. *)
}

val create : clock:Hardcaml.Signal.t -> clear:Hardcaml.Signal.t ->
  ui:Hardcaml.Signal.t -> write_ready:Hardcaml.Signal.t ->
  read_valid:Hardcaml.Signal.t -> read_data:Hardcaml.Signal.t ->
  irq:Hardcaml.Signal.t -> fault:Hardcaml.Signal.t -> t

(** [create] with [async] true: [clear] is an asynchronous reset of the host
    registers rather than a synchronous clear ([create] is [~async:false]).
    [timing] applies the host knobs [host_nibble_slots] and
    [split_command_decode]; {!Timing_options.default} is the host of [create].
    [strobe_gate] replaces [clear] in the raw last-nibble strobe of
    [split_command_decode] ([clear] in [create]); ready, valid and the
    registers use [clear]. *)
val create_with : timing:Timing_options.t -> strobe_gate:Hardcaml.Signal.t -> async:bool ->
  clock:Hardcaml.Signal.t ->
  clear:Hardcaml.Signal.t -> ui:Hardcaml.Signal.t -> write_ready:Hardcaml.Signal.t ->
  read_valid:Hardcaml.Signal.t -> read_data:Hardcaml.Signal.t ->
  irq:Hardcaml.Signal.t -> fault:Hardcaml.Signal.t -> t
