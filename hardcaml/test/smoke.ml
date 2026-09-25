open Hardcaml

let check condition message = if not condition then failwith message
let instruction op immediate = (op lsl 24) lor immediate

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
  let enter window=ignore(tick (window lsl 6)); ignore(tick (window lsl 6)) in
  let write window word =
    enter window;
    for nibble=0 to 7 do
      let ui=(window lsl 6) lor 16 lor ((word lsr (4*nibble)) land 15) in
      let rec accept remaining =
        check (remaining>0) "host write deadlock";
        if tick ui land 16=0 then accept (remaining-1) in
      accept 100
    done;
    ignore(tick (window lsl 6)) in
  let command op value=write 0 (instruction op value) in
  let load engine owner program =
    command 0 engine; command 1 0; command 3 owner;
    List.iter (write 1) program;
    command 2 (List.length program) in
  (* Engine 0 creates a word and event; engine 1 consumes it through the mover.
     The two remaining flagship engines free-run while host diagnostics proceed. *)
  load 0 1 [instruction 19 ((1 lsl 16) lor 0x1234); instruction 7 0;
             instruction 14 2; instruction 1 0];
  load 1 2 [instruction 15 0; instruction 6 0;
             instruction 18 (1 lsl 16); instruction 7 0;
             instruction 3 2; instruction 2 2; instruction 4 3; instruction 1 0];
  for engine=2 to config.Config.engine_count-1 do
    let pin=1 lsl engine in
    load engine pin [instruction 3 pin; instruction 2 pin; instruction 4 1;
                    instruction 2 0; instruction 4 2; instruction 5 1]
  done;
  command 6 ((1 lsl 2) lor (1 lsl 4) lor (1 lsl 5));
  command 4 ((1 lsl config.engine_count)-1);
  for _=1 to 50 do ignore(tick 0) done;
  check (get "uo_out" land 128=0) "engine/event/DMA smoke faulted";
  command 0 1;
  enter 3;
  let received=ref 0 in
  for nibble=0 to 7 do
    let rec take remaining =
      check (remaining>0) "host RX deadlock";
      let value=tick ((3 lsl 6) lor 32) in
      if value land 32=0 then take (remaining-1)
      else received := !received lor ((value land 15) lsl (4*nibble)) in
    take 100
  done;
  check (!received=0x1234) "autonomous FIFO transfer corrupted payload";
  set "ena" 1 0; ignore(tick 0);
  check (get "uio_oe"=0) "deselection must release every output";
  check (get "uo_out" land 128=0) "deselection must clear fault state"

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
