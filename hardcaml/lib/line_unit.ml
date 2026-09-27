(* Combinational pieces of the line unit (docs/extension.md). *)
open Hardcaml
open Signal

let crc_width = 16

let presets =
  [ "CRC-5/USB", 0x0014, 0x2800
  ; "CRC-16/ARC", 0xA001, 0x8005
  ; "CRC-15/CAN", 0x4CD1, 0x8B32
  ; "CRC-16/CCITT", 0x8408, 0x1021 ]

type mutation =
  | Crc_tap
  | Rx_destuff_run
  | Nrzi_decode
  | Manchester_halves
  | Pin_leak
  | Start_keeps_crc
  | Ltim_overflow
  | Stuff_run
  | Arbitration_off
  | Fraction_carry

let mutation_table =
  [ Crc_tap, "crc_tap"; Rx_destuff_run, "rx_destuff_run"; Nrzi_decode, "nrzi_decode"
  ; Manchester_halves, "manchester_halves"; Pin_leak, "pin_leak"
  ; Start_keeps_crc, "start_keeps_crc"; Ltim_overflow, "ltim_overflow"; Stuff_run, "stuff_run"
  ; Arbitration_off, "arbitration_off"; Fraction_carry, "fraction_carry" ]

let mutation_names = List.map snd mutation_table

let mutation_of_string s =
  match List.find_opt (fun (_, n) -> n = s) mutation_table with
  | Some (m, _) -> m
  | None -> invalid_arg ("unknown line-unit mutation " ^ s)

let preset_polynomial ~msb sel =
  let table f = mux sel (List.map (fun p -> of_int ~width:crc_width (f p)) presets) in
  mux2 msb (table (fun (_, _, normal) -> normal)) (table (fun (_, reflected, _) -> reflected))

let crc_next ?mutation ~msb ~poly crc data =
  let low = if mutation = Some Crc_tap then bit crc 1 else bit crc 0 in
  let feedback = mux2 msb (bit crc (crc_width - 1)) low ^: data in
  mux2 msb (sll crc 1) (srl crc 1) ^: (poly &: repeat feedback crc_width)
