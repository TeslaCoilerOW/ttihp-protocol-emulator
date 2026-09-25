type t = {
  word : Hardcaml.Signal.t; write : Hardcaml.Signal.t; window : Hardcaml.Signal.t;
  read_word : Hardcaml.Signal.t; read_lock : Hardcaml.Signal.t;
  outputs : Hardcaml.Signal.t;
}

val create : clock:Hardcaml.Signal.t -> clear:Hardcaml.Signal.t ->
  ui:Hardcaml.Signal.t -> write_ready:Hardcaml.Signal.t ->
  read_valid:Hardcaml.Signal.t -> read_data:Hardcaml.Signal.t ->
  irq:Hardcaml.Signal.t -> fault:Hardcaml.Signal.t -> t
