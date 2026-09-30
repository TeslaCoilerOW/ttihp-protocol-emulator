open Hardcaml

let check condition message = if not condition then failwith message
(* An instruction word: the opcode and the raw 24-bit payload. *)
let instruction (op:Opcode.t) immediate = (Opcode.to_int op lsl 24) lor immediate
(* A host command code (docs/info.md, "Commands"). *)
let host_opcode (command:Host_command.t) = Host_command.to_int command
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
  let command (name:Host_command.t) value=write 0 ((host_opcode name lsl 24) lor value) in
  let load engine owner program =
    command Select engine; command Begin 0; command Own owner;
    List.iter (write 1) program;
    command Commit (List.length program) in
  (* Engine 0 creates a word and event; engine 1 consumes it through the mover.
     The two remaining flagship engines free-run while host diagnostics proceed. *)
  load 0 1 [instruction Load ((1 lsl 16) lor 0x1234); instruction Push 0;
             instruction Signal 2; instruction Halt 0];
  load 1 2 [instruction Waitevent 0; instruction Pull 0;
             instruction Mov (1 lsl 16); instruction Push 0;
             instruction Dir 2; instruction Set 2; instruction Wait 3; instruction Halt 0];
  for engine=2 to config.Config.engine_count-1 do
    let pin=1 lsl engine in
    load engine pin [instruction Dir pin; instruction Set pin; instruction Wait 1;
                    instruction Set 0; instruction Wait 2; instruction Jmp 1]
  done;
  (* Route engine 0's RX queue to engine 1 (bits 3:2), enabled (bit 4), one word (bits 20:5). *)
  command Route ((1 lsl 2) lor (1 lsl 4) lor (1 lsl 5));
  command Start ((1 lsl config.engine_count)-1);
  for _=1 to 50 do ignore(tick 0) done;
  check (get "uo_out" land fault=0) "engine/event/DMA smoke faulted";
  command Select 1;
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
    {Config.default with engine_count=2;data_width=16;program_words=32;fifo_words=32;issue=Scalar};
    {Config.default with engine_count=2;data_width=16;program_words=128;prefetch=true};
    {Config.default with data_width=16;program_words=128;fifo_words=32;issue=Scalar;prefetch=true};
    {Config.default with engine_count=2;program_words=32;fifo_words=32}] in
  List.iter exercise configs;
  Printf.printf "Cyclesim: %d architectures passed host/event/autonomous-transfer/reset smoke\n"
    (List.length configs)
