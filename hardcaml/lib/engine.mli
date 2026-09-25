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

val create : Config.t -> inputs -> t

(** Actual compiled PC D input including synchronous clear.  Rejects a changed
    clock/reset/enable contract rather than duplicating the control decoder. *)
val next_pc : t -> Hardcaml.Signal.t
