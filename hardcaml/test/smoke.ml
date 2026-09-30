open Hardcaml

let check condition message = if not condition then failwith message
(* The opcode by mnemonic (Isa); the immediate is the raw 24-bit payload. *)
let instruction mnemonic immediate = (Isa.opcode mnemonic lsl 24) lor immediate
(* Host command opcodes, in order (docs/info.md, "Commands"). *)
let host_opcode name =
  let rec find n = function
    | [] -> invalid_arg ("unknown host command "^name)
    | c::rest -> if c=name then n else find (n+1) rest in
  find 0 ["SELECT";"BEGIN";"COMMIT";"OWN";"START";"STOP";"ROUTE";"CLEAR";
          "READ_SELECT";"EVENT";"FLUSH";"TRIGGER"]
(* Host port bits (docs/info.md, "Host interface"): ui_in, then uo_out. *)
let window_shift = 6 and write_valid = 16 and read_ready = 32
let write_ready = 16 and read_valid = 32 and fault = 128

let exercise config =
  let sim=Cyclesim.create (Processor.create config) in
  let set name width value=Cyclesim.in_port sim name := Bits.of_int ~width value in
  let get name=Bits.to_int !(Cyclesim.out_port sim name) in
  let tick ui =
    set "ui_in" 8 ui;
    Cyclesim.cycle_before_clock_edge sim;
    let before=Bits.to_int !(Cyclesim.out_port ~clock_edge:Before sim "uo_out") in
    Cyclesim.cycle_at_clock_edge sim;
    Cyclesim.cycle_after_clock_edge sim;
    before in
  set "rst_n" 1 0; set "ena" 1 1; set "uio_in" 8 0;
  ignore(tick 0); set "rst_n" 1 1; ignore(tick 0);
  check (get "uio_oe"=0) "reset must release outputs";
  let enter window=ignore(tick (window lsl window_shift)); ignore(tick (window lsl window_shift)) in
  let write window word =
    enter window;
    for nibble=0 to 7 do
      let ui=(window lsl window_shift) lor write_valid lor ((word lsr (4*nibble)) land 15) in
      let rec accept remaining =
        check (remaining>0) "host write deadlock";
        if tick ui land write_ready=0 then accept (remaining-1) in
      accept 100
    done;
    ignore(tick (window lsl window_shift)) in
  let command name value=write 0 ((host_opcode name lsl 24) lor value) in
  let load engine owner program =
    command "SELECT" engine; command "BEGIN" 0; command "OWN" owner;
    List.iter (write 1) program;
    command "COMMIT" (List.length program) in
  (* Engine 0 creates a word and event; engine 1 consumes it through the mover.
     The two remaining flagship engines free-run while host diagnostics proceed. *)
  load 0 1 [instruction "LOAD" ((1 lsl 16) lor 0x1234); instruction "PUSH" 0;
             instruction "SIGNAL" 2; instruction "HALT" 0];
  load 1 2 [instruction "WAITEVENT" 0; instruction "PULL" 0;
             instruction "MOV" (1 lsl 16); instruction "PUSH" 0;
             instruction "DIR" 2; instruction "SET" 2; instruction "WAIT" 3; instruction "HALT" 0];
  for engine=2 to config.Config.engine_count-1 do
    let pin=1 lsl engine in
    load engine pin [instruction "DIR" pin; instruction "SET" pin; instruction "WAIT" 1;
                    instruction "SET" 0; instruction "WAIT" 2; instruction "JMP" 1]
  done;
  (* Route engine 0's RX queue to engine 1 (bits 3:2), enabled (bit 4), one word (bits 20:5). *)
  command "ROUTE" ((1 lsl 2) lor (1 lsl 4) lor (1 lsl 5));
  command "START" ((1 lsl config.engine_count)-1);
  for _=1 to 50 do ignore(tick 0) done;
  check (get "uo_out" land fault=0) "engine/event/DMA smoke faulted";
  command "SELECT" 1;
  enter 3;
  let received=ref 0 in
  for nibble=0 to 7 do
    let rec take remaining =
      check (remaining>0) "host RX deadlock";
      let value=tick ((3 lsl window_shift) lor read_ready) in
      if value land read_valid=0 then take (remaining-1)
      else received := !received lor ((value land 15) lsl (4*nibble)) in
    take 100
  done;
  check (!received=0x1234) "autonomous FIFO transfer corrupted payload";
  set "ena" 1 0; ignore(tick 0);
  check (get "uio_oe"=0) "deselection must release every output";
  check (get "uo_out" land fault=0) "deselection must clear fault state"

let () =
  let configs=[Config.default;
    {Config.default with prefetch=true};
    {Config.default with engine_count=2;data_width=16;program_words=32;fifo_words=32;issue="scalar"};
    {Config.default with engine_count=2;data_width=16;program_words=128;prefetch=true};
    {Config.default with data_width=16;program_words=128;fifo_words=32;issue="scalar";prefetch=true};
    {Config.default with engine_count=2;program_words=32;fifo_words=32}] in
  List.iter exercise configs;
  Printf.printf "Cyclesim: %d architectures passed host/event/autonomous-transfer/reset smoke\n"
    (List.length configs)
