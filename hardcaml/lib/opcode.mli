(** The opcodes of ISA v2 (docs/isa.md) and of the line-unit extension
    (docs/extension.md). The encoding is bits 31:24 of an instruction word.

    This is the one list of opcodes. The validity rules of
    {!Engine_decode.create} (the [valid] signal of {!Engine_decode.t}),
    {!Engine.create}'s execute stage (behaviour), {!Isa.encode} (operand
    rules) and {!mnemonic} each match on [t] without a wildcard, so adding a
    constructor fails the build until every one of them handles it. *)

type t =
  | Nop
  | Halt
  | Set
  | Dir
  | Wait
  | Jmp
  | Pull
  | Push
  | Out
  | In
  | Count
  | Loop
  | Limit
  | Waitpin
  | Signal
  | Waitevent
  | Pins
  | Xfer
  | Mov
  | Load
  | Add
  | Xor
  | And
  | Or
  | Shl
  | Shr
  | Jz
  | Not
  | Time
  | Fault
  | Ltim  (** line unit *)
  | Lcfg  (** line unit *)
  | Crc  (** line unit *)
  | Lstat  (** line unit *)
[@@deriving compare, enumerate, equal, sexp_of]

(** [all] is in encoding order: [List.map to_int all = [0; 1; ...; 33]]. *)

(** Bits 31:24 of the instruction word. *)
val to_int : t -> int

val of_int : int -> t option

(** The assembler mnemonic, e.g. ["WAITPIN"]. *)
val mnemonic : t -> string

val of_mnemonic : string -> t option

(** LTIM, LCFG, CRC and LSTAT: implemented only with the line unit
    ({!Line_options.enabled}); without it they are invalid instructions. *)
val requires_line_unit : t -> bool
