type t = {
  engine_count : int;
  data_width : int;
  program_words : int;
  fifo_words : int;
  issue : string;
  prefetch : bool;
}

val default : t
val validate : t -> t
val of_json : Yojson.Safe.t -> t
val load : string -> t
val log2 : int -> int
