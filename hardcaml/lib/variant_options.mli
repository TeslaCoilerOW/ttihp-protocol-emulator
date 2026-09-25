(** RTL variant knobs, the optional ["options"] object of a refinement config.
    docs/variants.md defines the semantics of every knob. *)

(** Reset style.
    - [Sync]: design of record; synchronous clear from [~(rst_n & ena)].
    - [Sync_registered]: [rst_n & ena] passes through two plain flops; the
      synchronous clear comes from the second, so reset assertion and release
      both take effect two edges later.
    - [Async]: asynchronous assert and release from [~(rst_n & ena)] on every
      register; FIFO FLUSH stays a synchronous clear.
    - [Async_sync_release]: asynchronous assert from [~(rst_n & ena)]; release
      through a two-flop synchronizer (registers leave reset two edges after
      [rst_n & ena] rises). *)
type reset = Sync | Sync_registered | Async | Async_sync_release

type pc_bits = Full | Saturating_7
type shift = Barrel | Byte_lane

type t = {
  reset : reset;
  fifo_storage_reset : bool;  (** FIFO storage words are registers that take the chip reset *)
  narrow_image_regs : bool;   (** image_length/image_loaded are log2(program_words)+1 bits *)
  debug_counters : bool;      (** per-engine completed-instruction counters (READ_SELECT 5) *)
  pc_bits : pc_bits;          (** [Saturating_7]: 7-bit PC, branch targets >= 128 saturate to 127 *)
  shift : shift;              (** [Byte_lane]: SHL/SHR only by 0/8/16/24; other counts fault 1 *)
}

(** No knob changed: the design of record. *)
val default : t
val is_default : t -> bool

(** [Async] or [Async_sync_release]. *)
val asynchronous : t -> bool

(** True when a knob changes the ISA contract (counters, PC width, shifts). *)
val isa_changed : t -> bool

(** READ_SELECT 7 value: 2, or 3 when [isa_changed]. *)
val isa_version : t -> int

(** Engine PC register width: 24 or 7. *)
val pc_width : t -> int

(** Absent fields take their default; unknown or duplicate fields and wrong
    types are rejected. *)
val of_json : Yojson.Safe.t -> t

(** Complete canonical object (every field). *)
val to_json : t -> Yojson.Safe.t

(** Check the options against an architecture; returns them unchanged. *)
val validate : Config.t -> t -> t
