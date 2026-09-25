type t = {
  ready : Hardcaml.Signal.t;
  valid : Hardcaml.Signal.t;
  data : Hardcaml.Signal.t;
  level : Hardcaml.Signal.t;
}

val create : clock:Hardcaml.Signal.t -> clear:Hardcaml.Signal.t ->
  width:int -> depth:int -> push:Hardcaml.Signal.t -> pop:Hardcaml.Signal.t ->
  data:Hardcaml.Signal.t -> t
