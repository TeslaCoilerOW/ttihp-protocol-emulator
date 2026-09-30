type architecture = { engine_count : int; data_width : int; program_words : int;
                      fifo_words : int; issue : Issue.t; prefetch : bool }
type instruction = { op : Opcode.t; a : int; b : int; c : int; imm : int }
let flagship = { engine_count=4; data_width=32; program_words=64;
                 fifo_words=8; issue=Fused; prefetch=false }
let fail fmt = Printf.ksprintf invalid_arg fmt
let object_fields where allowed = function
  | `Assoc fields ->
    let seen = Hashtbl.create 16 in
    List.iter (fun (k, _) ->
      if not (List.mem k allowed) then fail "%s: unknown field %s" where k;
      if Hashtbl.mem seen k then fail "%s: duplicate field %s" where k;
      Hashtbl.add seen k ()) fields;
    fields
  | _ -> fail "%s must be an object" where
let required key fields = match List.assoc_opt key fields with
  | Some v -> v | None -> fail "missing field %s" key
let json_int where = function `Int n -> n | _ -> fail "%s must be an integer" where
let json_string where = function `String s -> s | _ -> fail "%s must be a string" where
let architecture_of_json j =
  let f = object_fields "architecture"
    ["schema_version";"engine_count";"data_width";"program_words";"fifo_words";"issue";"prefetch"] j in
  if required "schema_version" f <> `String "protocol-emulator.architecture.v1" then fail "architecture schema version mismatch";
  let i k = json_int k (required k f) in
  let engine_count=i "engine_count" and data_width=i "data_width" in
  let program_words=i "program_words" and fifo_words=i "fifo_words" in
  let issue=json_string "issue" (required "issue" f) in
  let prefetch=match required "prefetch" f with `Bool b -> b | _ -> fail "prefetch must be boolean" in
  if not (List.mem engine_count [2;4]) then fail "engine_count must be 2 or 4";
  if not (List.mem data_width [16;32]) then fail "data_width must be 16 or 32";
  if not (List.mem program_words [32;64;128]) then fail "program_words must be 32, 64 or 128";
  if not (List.mem fifo_words [2;4;8;32]) then fail "fifo_words must be 2, 4, 8 or 32";
  let issue=Issue.of_string issue in
  {engine_count;data_width;program_words;fifo_words;issue;prefetch}
let architecture_to_json t = `Assoc [
  "schema_version",`String "protocol-emulator.architecture.v1";
  "engine_count",`Int t.engine_count; "data_width",`Int t.data_width;
  "program_words",`Int t.program_words; "fifo_words",`Int t.fifo_words;
  "issue",`String (Issue.to_string t.issue); "prefetch",`Bool t.prefetch]
let instruction ?(a=0) ?(b=0) ?(c=0) ?(imm=0) op = {op;a;b;c;imm}
let range name lo hi n = if n<lo || n>hi then fail "%s must be %d..%d, got %d" name lo hi n
let zero name n = if n<>0 then fail "unused operand %s must be zero" name

(* Where the operands sit in the 24-bit payload (bits 23:0 of the word). *)
type layout =
  | Abc  (** a in 23:16, b in 15:8, c in 7:0 *)
  | Imm24  (** imm in 23:0 *)
  | Reg_imm16  (** register a in 23:16, imm in 15:0 *)

let layout : Opcode.t -> layout = function
  | Load | Jz -> Reg_imm16
  | Set | Dir | Wait | Jmp | Count | Loop | Limit | Signal | Pins | Fault | Ltim | Lcfg -> Imm24
  | Nop | Halt | Pull | Push | Out | In | Waitpin | Waitevent | Xfer | Mov | Add | Xor | And
  | Or | Shl | Shr | Not | Time | Crc | Lstat -> Abc

(* [byte_lane_shifts]: the target implements SHL/SHR only for counts 0, 8, 16
   and 24 (RTL variant option shift=byte_lane); any other count is rejected.
   [line_unit]: the target has the line unit (options.line_unit): LTIM, LCFG,
   CRC, LSTAT and the XFER line/CRC flags are accepted; without it they are
   rejected. *)
let encode ?(byte_lane_shifts=false) ?(line_unit=false) arch ~owned_pins i =
  let mnemonic = Opcode.mnemonic i.op in
  if Opcode.requires_line_unit i.op && not line_unit then
    fail "%s needs a target with the line unit (options.line_unit)" mnemonic;
  range "owned_pins" 0 255 owned_pins;
  range "a" 0 255 i.a; range "b" 0 255 i.b; range "c" 0 255 i.c;
  range "imm" 0 0xffffff i.imm;
  let no_abc () = zero "a" i.a;zero "b" i.b;zero "c" i.c in
  let no_imm () = zero "imm" i.imm in
  let reg n = range "register" 0 3 n in
  let owns pin = if owned_pins land (1 lsl pin)=0 then fail "pin %d is not owned" pin in
  let imm16 () = range "imm16" 0 65535 i.imm;zero "b" i.b;zero "c" i.c in
  let fused () =
    if not (Issue.equal arch.issue Fused) then fail "XFER is unavailable in scalar issue configuration" in
  (match i.op with
   | Nop | Halt | Pull | Waitevent -> no_abc ();no_imm ()
   | Push -> range "PUSH mode" 0 1 i.a;zero "b" i.b;zero "c" i.c;no_imm ()
   | Set | Dir -> no_abc ();range "pin mask" 0 255 i.imm;
      if i.imm land (lnot owned_pins)<>0 then fail "pin mask exceeds ownership"
   | Wait -> no_abc ()
   | Jmp | Loop -> no_abc ();range "branch target" 0 (arch.program_words-1) i.imm
   | Out -> no_imm ();range "pin" 0 7 i.a;zero "b" i.b;range "direction" 0 1 i.c;owns i.a
   | In -> no_imm ();range "pin" 0 7 i.a;zero "b" i.b;range "direction" 0 1 i.c
   | Count -> no_abc ();range "repeat" 0 65535 i.imm
   | Limit -> no_abc ();range "LIMIT" 1 0xffffff i.imm
   | Waitpin -> no_imm ();range "pin" 0 7 i.a;range "expected bit" 0 1 i.b;zero "c" i.c
   | Signal -> no_abc ();range "event mask" 0 ((1 lsl arch.engine_count)-1) i.imm
   | Pins -> no_abc ();range "PINS" 0 511 i.imm
   | Xfer when line_unit -> no_imm ();fused ();
      range "XFER bits" 1 arch.data_width i.a;
      range "XFER flags" 0 127 i.c;
      if i.c land 0x20<>0 then begin
        (* line mode: the ticker times the bits; b and c[1:0] must be zero *)
        zero "b (line XFER)" i.b;
        if i.c land 3<>0 then fail "line XFER flags: bits 0 and 1 must be zero";
        if i.c land 0x18=0 then fail "line XFER must drive (c bit 3), sample (c bit 4) or both"
      end else range "XFER half period" 1 255 i.b
   | Xfer -> no_imm ();fused ();
      range "XFER bits" 1 arch.data_width i.a;range "XFER half period" 1 255 i.b;
      range "XFER flags" 0 31 i.c
   | Mov | Add | Xor | And | Or -> no_imm ();reg i.a;reg i.b;zero "c" i.c
   | Load -> reg i.a;imm16 ()
   | Shl | Shr -> no_imm ();reg i.a;zero "b" i.b;range "shift count" 0 (arch.data_width-1) i.c;
      if byte_lane_shifts && i.c land 7<>0 then
        fail "shift count %d is not a byte lane (0, 8, 16 or 24 below the datapath width)" i.c
   | Jz -> reg i.a;imm16 ();range "branch target" 0 (arch.program_words-1) i.imm
   | Not | Time -> no_imm ();reg i.a;zero "b" i.b;zero "c" i.c
   | Fault -> no_abc ();range "fault code" 1 255 i.imm
   | Ltim -> no_abc ();
      if i.imm land 0xff=255 && (i.imm lsr 8) land 0xff<>0 then
        fail "LTIM: half period 255 needs fraction 0"
   | Lcfg -> no_abc ();range "LCFG" 0 2047 i.imm;
      if i.imm land 3=3 then fail "LCFG: line code 3 is invalid";
      if i.imm land 0x7c=0x0c then fail "LCFG: stuffing on runs of either polarity needs a run length of at least 2"
   | Crc -> no_imm ();
      (match i.c with
       | 1 -> zero "a" i.a;reg i.b
       | 2 -> reg i.a;zero "b" i.b
       | 3 -> zero "a" i.a;range "CRC preset" 0 3 i.b
       | _ -> fail "CRC: c must be 1 (set from register b), 2 (read into register a) or 3 (preset b)")
   | Lstat -> no_imm ();reg i.a;zero "b" i.b;zero "c" i.c);
  let payload = match layout i.op with
    | Reg_imm16 -> (i.a lsl 16) lor i.imm
    | Imm24 -> i.imm
    | Abc -> (i.a lsl 16) lor (i.b lsl 8) lor i.c in
  Int32.logor (Int32.shift_left (Int32.of_int (Opcode.to_int i.op)) 24) (Int32.of_int payload)
let minimum_cycles i = match i.op with
  | Wait -> 1+i.imm
  | Xfer -> 1+2*i.a*i.b
  | Nop | Halt | Set | Dir | Jmp | Pull | Push | Out | In | Count | Loop | Limit | Waitpin
  | Signal | Waitevent | Pins | Mov | Load | Add | Xor | And | Or | Shl | Shr | Jz | Not
  | Time | Fault | Ltim | Lcfg | Crc | Lstat -> 1
let blocking i = match i.op with
  | Pull -> "tx_fifo_unbounded"
  | Push when i.a=0 -> "rx_fifo_unbounded"
  | Waitpin -> "pin_limit"
  | Waitevent -> "event_limit"
  | Nop | Halt | Set | Dir | Wait | Jmp | Push | Out | In | Count | Loop | Limit | Signal
  | Pins | Xfer | Mov | Load | Add | Xor | And | Or | Shl | Shr | Jz | Not | Time | Fault
  | Ltim | Lcfg | Crc | Lstat -> "none"
