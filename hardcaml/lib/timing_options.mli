(** Timing-restructuring knobs of a closed refinement (docs/timing-closure.md).

    They are read from the same ["options"] object as {!Variant_options} and
    change how the logic is built, never what it computes: with every knob
    false the generators emit exactly the design selected by the other options.
    Each knob is behaviour-preserving on its own (docs/timing-closure.md
    section 4.1 states the argument for each, section 6 the checks). *)

type t = {
  host_nibble_slots : bool;
  (** The host write buffer keeps nibble [i] of a word at bits [4i+3..4i]
      (loaded in place) instead of shifting every nibble down by four bits.
      The completed word is the same; only the unobservable partial-word
      contents differ. *)
  split_command_decode : bool;
  (** Each host command is decoded on its own: [command c] is the write strobe
      AND "code = c" AND the validity rule of [c], without the shared 256-way
      validity multiplexer; the host strobes of windows 0-2 are formed from the
      raw strobe terms; the mover grant is a flat rotating-priority function. *)
  split_engine_issue : bool;
  (** The engine's next-state logic is enabled by [running & fault = 0 &
      ~clear]; START/STOP/clear gate only the issue-derived outputs (FIFO
      pop/push, event consume/signal, stalled). *)
  split_instruction_decode : bool;
  (** The engine's instruction-validity rule is an OR of per-opcode terms
      instead of a 256-way multiplexer, and the completed-instruction counter
      has its own increment enable instead of being assigned in every
      completing instruction branch. *)
  fifo_write_staging : bool;
  (** Every TX/RX queue registers the pushed word and writes it to its storage
      slot one edge later, reading a staged slot through a bypass
      ({!Fifo.create_staged}); occupancy, ready, valid and head are unchanged. *)
  clear_outputs_only : bool;
  (** The chip clear gates only what is visible or not reset: the pins, the
      host's ready/valid bits and the SRAM enables and write. It no longer
      gates next-state logic (engine enables and issue, host strobes, mover
      eligibility, pin triggers and, with [fifo_write_staging] or
      [fifo_write_free_slot], queue acceptance), because every register takes the clear as its own
      synchronous clear or asynchronous reset. *)
  keep_counter_increments : bool;
  (** The incremented or decremented value of each wide counter whose enable
      arrives late (engine PC, completed counter, blocked/repeat/timer
      counters; image load count; route count) carries a [keep] attribute, a
      synthesis hint that stops the enable from being merged into the carry
      chain. The logic is unchanged. *)
  fifo_write_free_slot : bool;
  (** Every TX/RX queue writes the incoming data to the slot at its write
      pointer in every cycle in which it is not full ({!Fifo.create_free_slot});
      occupancy, ready, valid and the head of a non-empty queue are unchanged.
      Ignored when [fifo_write_staging] is set. *)
}

(** Every knob false. *)
val default : t
val is_default : t -> bool

(** The option keys that belong to this module. *)
val keys : string list

(** Reads the knobs from an ["options"] object; keys of other modules are
    ignored, a wrong type is rejected. *)
val of_options_json : Yojson.Safe.t -> t

(** Only the knobs that are set, as ["options"] fields. *)
val to_json_fields : t -> (string * Yojson.Safe.t) list
