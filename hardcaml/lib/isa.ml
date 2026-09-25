type architecture = { engine_count : int; data_width : int; program_words : int;
                      fifo_words : int; issue : string; prefetch : bool }
type instruction = { mnemonic : string; a : int; b : int; c : int; imm : int }
let flagship = { engine_count=4; data_width=32; program_words=64;
                 fifo_words=8; issue="fused"; prefetch=false }
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
  if not (List.mem issue ["scalar";"fused"]) then fail "issue must be scalar or fused";
  {engine_count;data_width;program_words;fifo_words;issue;prefetch}
let architecture_to_json t = `Assoc [
  "schema_version",`String "protocol-emulator.architecture.v1";
  "engine_count",`Int t.engine_count; "data_width",`Int t.data_width;
  "program_words",`Int t.program_words; "fifo_words",`Int t.fifo_words;
  "issue",`String t.issue; "prefetch",`Bool t.prefetch]
let names = [|"NOP";"HALT";"SET";"DIR";"WAIT";"JMP";"PULL";"PUSH";
 "OUT";"IN";"COUNT";"LOOP";"LIMIT";"WAITPIN";"SIGNAL";"WAITEVENT";
 "PINS";"XFER";"MOV";"LOAD";"ADD";"XOR";"AND";"OR";"SHL";"SHR";
 "JZ";"NOT";"TIME";"FAULT"|]
let opcode s =
  let rec find n = if n=Array.length names then fail "unknown mnemonic %s" s
    else if names.(n)=s then n else find (n+1) in find 0
let instruction ?(a=0) ?(b=0) ?(c=0) ?(imm=0) mnemonic = {mnemonic;a;b;c;imm}
let range name lo hi n = if n<lo || n>hi then fail "%s must be %d..%d, got %d" name lo hi n
let zero name n = if n<>0 then fail "unused operand %s must be zero" name
(* [byte_lane_shifts]: the target implements SHL/SHR only for counts 0, 8, 16
   and 24 (RTL variant option shift=byte_lane); any other count is rejected. *)
let encode ?(byte_lane_shifts=false) arch ~owned_pins i =
  let op = opcode i.mnemonic in
  range "owned_pins" 0 255 owned_pins;
  range "a" 0 255 i.a; range "b" 0 255 i.b; range "c" 0 255 i.c;
  range "imm" 0 0xffffff i.imm;
  let no_abc () = zero "a" i.a;zero "b" i.b;zero "c" i.c in
  let no_imm () = zero "imm" i.imm in
  let reg n = range "register" 0 3 n in
  let owns pin = if owned_pins land (1 lsl pin)=0 then fail "pin %d is not owned" pin in
  let imm16 () = range "imm16" 0 65535 i.imm;zero "b" i.b;zero "c" i.c in
  (match op with
   | 0|1|6|15 -> no_abc ();no_imm ()
   | 7 -> range "PUSH mode" 0 1 i.a;zero "b" i.b;zero "c" i.c;no_imm ()
   | 2|3 -> no_abc ();range "pin mask" 0 255 i.imm;
      if i.imm land (lnot owned_pins)<>0 then fail "pin mask exceeds ownership"
   | 4 -> no_abc ()
   | 5|11 -> no_abc ();range "branch target" 0 (arch.program_words-1) i.imm
   | 8|9 -> no_imm ();range "pin" 0 7 i.a;zero "b" i.b;range "direction" 0 1 i.c;
      if op=8 then owns i.a
   | 10 -> no_abc ();range "repeat" 0 65535 i.imm
   | 12 -> no_abc ();range "LIMIT" 1 0xffffff i.imm
   | 13 -> no_imm ();range "pin" 0 7 i.a;range "expected bit" 0 1 i.b;zero "c" i.c
   | 14 -> no_abc ();range "event mask" 0 ((1 lsl arch.engine_count)-1) i.imm
   | 16 -> no_abc ();range "PINS" 0 511 i.imm
   | 17 -> no_imm ();
      if arch.issue<>"fused" then fail "XFER is unavailable in scalar issue configuration";
      range "XFER bits" 1 arch.data_width i.a;range "XFER half period" 1 255 i.b;
      range "XFER flags" 0 31 i.c
   | 18|20|21|22|23 -> no_imm ();reg i.a;reg i.b;zero "c" i.c
   | 19 -> reg i.a;imm16 ()
   | 24|25 -> no_imm ();reg i.a;zero "b" i.b;range "shift count" 0 (arch.data_width-1) i.c;
      if byte_lane_shifts && i.c land 7<>0 then
        fail "shift count %d is not a byte lane (0, 8, 16 or 24 below the datapath width)" i.c
   | 26 -> reg i.a;imm16 ();range "branch target" 0 (arch.program_words-1) i.imm
   | 27|28 -> no_imm ();reg i.a;zero "b" i.b;zero "c" i.c
   | 29 -> no_abc ();range "fault code" 1 255 i.imm
   | _ -> assert false);
  let payload = if List.mem op [19;26] then (i.a lsl 16) lor i.imm
    else if List.mem op [2;3;4;5;10;11;12;14;16;29] then i.imm
    else (i.a lsl 16) lor (i.b lsl 8) lor i.c in
  Int32.logor (Int32.shift_left (Int32.of_int op) 24) (Int32.of_int payload)
let minimum_cycles i = match i.mnemonic with
  | "WAIT" -> 1+i.imm | "XFER" -> 1+2*i.a*i.b | _ -> 1
let blocking i = match i.mnemonic with
  | "PULL" -> "tx_fifo_unbounded" | "PUSH" when i.a=0 -> "rx_fifo_unbounded"
  | "WAITPIN" -> "pin_limit" | "WAITEVENT" -> "event_limit" | _ -> "none"
