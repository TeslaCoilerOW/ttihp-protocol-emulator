(** Instruction decode: the operand fields of the instruction word and the
    validity rule of every opcode (docs/isa.md, docs/extension.md).

    An instruction whose rule is false, or whose opcode has no rule, faults
    with code 1 when it would issue. The rules read the word, the ownership
    mask and engine registers (the PINS assignment, and the line unit's
    configuration and ticker); they do not read the queues or the pin
    inputs. *)

open Hardcaml

(** Slices of the 32-bit instruction word. *)
type fields = {
  word : Signal.t;
  op : Signal.t;  (** 31:24 *)
  a : Signal.t;  (** 23:16 *)
  b : Signal.t;  (** 15:8 *)
  c : Signal.t;  (** 7:0 *)
  imm24 : Signal.t;  (** 23:0 *)
  imm16 : Signal.t;  (** 15:0 *)
  low8 : Signal.t;  (** 7:0, the pin-mask and fault-code immediate *)
  dest : Signal.t;  (** a[1:0]: destination register *)
  src : Signal.t;  (** b[1:0]: source register *)
  pin : Signal.t;  (** a[2:0]: pin of OUT, IN and WAITPIN *)
}

val fields : Signal.t -> fields

(** The line-unit registers that the XFER and LTIM rules read. *)
type line = {
  cfg : Signal.t;  (** LCFG bits 9:0 *)
  ticker_running : Signal.t;
  mutation : Line_unit.mutation option;  (** formal negative controls only *)
}

type t = {
  fields : fields;
  valid : Signal.t;
      (** the validity rule of the word's opcode: 0 for opcodes 34..255 and
          for an opcode not implemented in this configuration (XFER with
          scalar issue, the line-unit opcodes without the unit) *)
}

(** [split_instruction_decode] ({!Timing_options}) selects the form of
    [valid]: a 256-way multiplexer on the opcode, or the OR over implemented
    opcodes of (op = code) & rule(code). The two are equal. [line] is given
    exactly when the engine has the line unit. *)
val create :
  config:Config.t ->
  shift:Variant_options.shift ->
  split_instruction_decode:bool ->
  ?line:line ->
  ownership:Signal.t ->
  transfer_pins:Signal.t ->
  Signal.t ->
  t
