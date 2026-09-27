(** Combinational pieces of the line unit (docs/extension.md), shared by
    {!Engine.create} and the formal generators. *)

(** CRC register width of the [Rec16] feature set. *)
val crc_width : int

(** The four polynomial presets: (name, reflected form, normal form). The
    reflected form is used by LSB-first transfers (right-shifting register),
    the normal form, left-aligned in the 16-bit register, by MSB-first
    transfers (left-shifting register).
    - 0 CRC-5/USB, x^5+x^2+1: 0x0014 / 0x2800 (0x05 << 11)
    - 1 CRC-16 (ARC, USB), x^16+x^15+x^2+1: 0xA001 / 0x8005
    - 2 CRC-15/CAN, x^15+x^14+x^10+x^8+x^7+x^4+x^3+1: 0x4CD1 / 0x8B32 (0x4599 << 1)
    - 3 CRC-16/CCITT, x^16+x^12+x^5+1: 0x8408 / 0x1021 *)
val presets : (string * int * int) list

(** Seeded defects for the formal negative controls (formal/README.md,
    "Line unit"). Production generators never pass one. *)
type mutation =
  | Crc_tap            (** the reflected CRC feeds back bit 1 instead of bit 0 *)
  | Rx_destuff_run     (** the receiver removes a stuff bit one bit later *)
  | Nrzi_decode        (** the receiver's NRZI decoding is inverted *)
  | Manchester_halves  (** the Manchester first half is the bit, not its complement *)
  | Pin_leak           (** a line drive also writes the pin above the data pin *)
  | Start_keeps_crc    (** START does not clear the CRC register *)
  | Ltim_overflow      (** LTIM with P = 255 and a non-zero fraction is accepted *)
  | Stuff_run          (** transmitter and receiver stuff one bit later *)
  | Arbitration_off    (** the arbitration monitor never sets [lost] *)
  | Fraction_carry     (** the fraction's carry never lengthens a tick *)

val mutation_of_string : string -> mutation
val mutation_names : string list

(** [preset_polynomial ~msb sel]: the 16-bit polynomial of preset [sel]
    (2 bits) in the form that the bit order [msb] needs. *)
val preset_polynomial : msb:Hardcaml.Signal.t -> Hardcaml.Signal.t -> Hardcaml.Signal.t

(** One CRC step: [crc_next ~msb ~poly crc bit]. LSB first (msb = 0):
    feedback = crc[0] ^ bit, crc' = (crc >> 1) ^ (feedback ? poly : 0).
    MSB first: feedback = crc[15] ^ bit, crc' = (crc << 1) ^ (feedback ? poly : 0). *)
val crc_next :
  ?mutation:mutation -> msb:Hardcaml.Signal.t -> poly:Hardcaml.Signal.t ->
  Hardcaml.Signal.t -> Hardcaml.Signal.t -> Hardcaml.Signal.t
