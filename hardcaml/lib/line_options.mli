(** Line-unit extension knob of a closed refinement (docs/extension.md).

    It is read from the same ["options"] object as {!Variant_options} and
    {!Timing_options}. The default, [No_line_unit], is the design of record:
    every generator path taken with it is the one that existed before this
    knob, so the design of record and the earlier variants are emitted byte
    for byte. *)

(** [Rec16]: the recommended feature set of docs/extension-study.md
    ("REC16"): per engine a free-running bit ticker that shares the XFER
    tick/period registers, an 8-bit fraction, NRZ/NRZI/Manchester (TX only)
    line coding, programmable bit stuffing, a complementary pin pair with SE0
    detection, a CAN arbitration monitor and a 16-bit CRC with four
    polynomial presets; opcodes 30-33 (LTIM, LCFG, CRC, LSTAT) and the line
    and CRC mode bits of XFER. *)
type feature_set = No_line_unit | Rec16

type t = { line_unit : feature_set }

val default : t
val is_default : t -> bool
val enabled : t -> bool

(** The option keys that belong to this module (["line_unit"]). *)
val keys : string list

(** Reads the knob from an ["options"] object; keys of other modules are
    ignored, a wrong type or value is rejected. *)
val of_options_json : Yojson.Safe.t -> t

(** Only the knob when it is set, as ["options"] fields. *)
val to_json_fields : t -> (string * Yojson.Safe.t) list

(** Rejects the unit on architectures it does not support (it needs fused
    issue and a 32-bit datapath) and together with the timing knob
    [split_instruction_decode], whose completed-instruction counter does not
    model line-mode XFER completion. Returns the knob unchanged. *)
val validate : Config.t -> Timing_options.t -> t -> t

(** Constant capability bits read in READ_SELECT 7 bits 23..8 (bit 8 of the
    word is bit 0 of this value): [0] line unit, [1] fraction, [2] stuffing,
    [3] arbitration monitor, [4] CRC-16, [5] CRC-32, [6] CRC presets instead
    of a programmable polynomial, [11:8] engines that have the unit. Zero
    without the unit. *)
val capabilities : engine_count:int -> t -> int
