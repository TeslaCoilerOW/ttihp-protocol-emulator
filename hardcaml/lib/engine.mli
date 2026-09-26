type inputs = {
  clock : Hardcaml.Signal.t; clear : Hardcaml.Signal.t;
  start : Hardcaml.Signal.t; stop : Hardcaml.Signal.t;
  clear_fault : Hardcaml.Signal.t; instruction : Hardcaml.Signal.t;
  image_length : Hardcaml.Signal.t; ownership : Hardcaml.Signal.t;
  pins : Hardcaml.Signal.t; timestamp : Hardcaml.Signal.t;
  tx_valid : Hardcaml.Signal.t; tx_data : Hardcaml.Signal.t;
  rx_ready : Hardcaml.Signal.t; event : Hardcaml.Signal.t;
}

type t = {
  pc : Hardcaml.Signal.t; running : Hardcaml.Signal.t; fault : Hardcaml.Signal.t;
  stalled : Hardcaml.Signal.t; tx_pop : Hardcaml.Signal.t;
  rx_push : Hardcaml.Signal.t; rx_data : Hardcaml.Signal.t;
  pin_values : Hardcaml.Signal.t; pin_enables : Hardcaml.Signal.t;
  signal_events : Hardcaml.Signal.t; consume_event : Hardcaml.Signal.t;
  completed : Hardcaml.Signal.t;
  issue : Hardcaml.Signal.t; wait_timer : Hardcaml.Signal.t;
  wait_limit : Hardcaml.Signal.t; blocked_cycles : Hardcaml.Signal.t;
  repeat_count : Hardcaml.Signal.t; transfer_edges : Hardcaml.Signal.t;
}

(** [options] (default [Variant_options.default]) selects the engine-level
    variant knobs: reset style (synchronous clear or asynchronous reset from
    [clear]), debug counters, PC width and shift implementation. [timing]
    (default {!Timing_options.default}) applies [split_engine_issue] and
    [split_instruction_decode]. [gate] (default [clear]) is the clear that
    gates the next-state enable and the issue outputs; the registers always
    take [clear]. *)
val create : ?options:Variant_options.t -> ?timing:Timing_options.t -> ?gate:Hardcaml.Signal.t ->
  Config.t -> inputs -> t

(** Actual compiled PC next value (24 bits) including the synchronous clear or
    asynchronous reset.  Rejects any other clock/reset/enable contract rather
    than duplicating the control decoder. *)
val next_pc : t -> Hardcaml.Signal.t
