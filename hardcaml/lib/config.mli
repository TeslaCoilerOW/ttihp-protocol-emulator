(** Architecture of a generated processor (protocol-emulator.architecture.v1,
    the ["architecture"] object of a refinement config). *)
type t = {
  engine_count : int;  (** 2 or 4 *)
  data_width : int;  (** 16 or 32 *)
  program_words : int;  (** 32, 64 or 128 instructions per engine *)
  fifo_words : int;  (** 2, 4, 8 or 32 words per TX and RX queue *)
  issue : Issue.t;
  prefetch : bool;  (** register-store top only: next-word prefetch *)
}

(** The design of record: 4 engines, 32-bit data, 64 words, 8-word queues,
    fused issue, no prefetch. *)
val default : t

(** Rejects values outside the ranges above; returns [t] unchanged. *)
val validate : t -> t

val of_json : Yojson.Safe.t -> t
val load : string -> t

(** Bits needed to index [n] items: the least [b] with [2^b >= n]. *)
val log2 : int -> int
