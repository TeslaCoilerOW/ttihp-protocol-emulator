(** Cyclesim harness for the waveform expect tests (see ../README.md).

    The simulated circuit is the design of record's architecture
    ([Config.default]: four engines, 32-bit datapath, 64-word images, 8-word
    queues) built by [Processor.create_refinement_model ~debug:true]: the
    shipped RTL's logic with a synchronous model in place of the SRAM macros.
    [ui_in] and [uo_out] are split into the host-port fields of docs/info.md,
    and a few [dbg_*] outputs are exposed per engine. *)

open! Base
open Hardcaml

module I : sig
  type 'a t =
    { clock : 'a
    ; rst_n : 'a
    ; ena : 'a
    ; window : 'a (** ui[7:6] *)
    ; read_ready : 'a (** ui[5] *)
    ; write_valid : 'a (** ui[4] *)
    ; write_nibble : 'a (** ui[3:0] *)
    ; uio_in : 'a array
    }
  [@@deriving hardcaml]
end

module O : sig
  type 'a t =
    { fault : 'a (** uo[7] *)
    ; irq : 'a (** uo[6] *)
    ; read_valid : 'a (** uo[5] *)
    ; write_ready : 'a (** uo[4] *)
    ; read_nibble : 'a (** uo[3:0] *)
    ; uio_out : 'a array
    ; uio_oe : 'a array
    ; command_accepted : 'a (** dbg_command_accepted *)
    ; command_code : 'a (** dbg_command_code *)
    ; image_valid : 'a array (** dbg_image_valid, per engine *)
    ; running : 'a array (** dbg_running, per engine *)
    ; tx_level : 'a array (** dbg_tx_level, per engine *)
    ; rx_level : 'a array (** dbg_rx_level, per engine *)
    }
  [@@deriving hardcaml]
end

type t =
  { sim : Cyclesim.With_interface(I)(O).t
  ; waves : Hardcaml_waveterm.Waveform.t
  ; mutable cycle : int (** clock edges so far; the reset cycle is cycle 0 *)
  ; mutable window : int
  ; mutable environment : int O.t -> int
    (** The value of [uio_in] for the next edge, from the current outputs. *)
  }

(** A simulator after one reset cycle, with window 0 selected. *)
val create : ?environment:(int O.t -> int) -> unit -> t

(** One clock edge. Returns the outputs sampled before the edge (what a host
    samples). *)
val cycle : t -> int O.t

val idle : t -> int -> unit

(** Change the window bits and spend the bubble cycle; nothing if unchanged. *)
val enter_window : t -> int -> unit

(** Eight nibbles, least significant first, each held until write-ready.
    Returns whether a window-0 command word was accepted. *)
val write_word : t -> window:int -> int -> bool

(** Eight nibbles, least significant first, each taken when read-valid. *)
val read_word : t -> window:int -> int

(** Host commands (docs/info.md, "Commands"). *)
module Command : sig
  type t =
    | Select
    | Begin
    | Commit
    | Own
    | Start
    | Stop
    | Route
    | Clear
    | Read_select
    | Event
    | Flush
    | Trigger
  [@@deriving sexp_of]

  val opcode : t -> int
end

val command : t -> Command.t -> int -> bool
val command_exn : t -> Command.t -> int -> unit

(** READ_SELECT [index], re-enter window 0 and read the status word. *)
val read_status : t -> int -> int

(** One instruction word, encoded by [Isa.encode] for the flagship
    architecture. *)
val instruction : ?a:int -> ?b:int -> ?c:int -> ?imm:int -> owned:int -> string -> int

(** SELECT, BEGIN, the words in window 1, OWN, COMMIT (docs/info.md,
    "Loading and starting"). *)
val load : t -> engine:int -> owned:int -> ?open_drain:int -> int list -> unit

(** An example firmware image from [Firmware.make]: its words and owned pins. *)
val firmware : ?half_period:int -> string -> int list * int

val bit : string -> string * Wave_format.t
val hex : string -> string * Wave_format.t
val unsigned : string -> string * Wave_format.t

(** Print the clock and the given ports from [start_cycle] to the current
    cycle. [wave_width] as in [Hardcaml_waveterm]: [0] is two characters per
    cycle, [-n] is [n] cycles per character. *)
val print
  :  ?start_cycle:int
  -> ?wave_width:int
  -> t
  -> (string * Wave_format.t) list
  -> unit
