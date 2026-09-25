open Hardcaml

let check name condition = if not condition then failwith name
let ins op immediate = (op lsl 24) lor immediate

let engine_test width =
  let config=Refinement_config.create {Config.default with data_width=width} in
  let sim=Cyclesim.create (Instruction_sram_formal.engine_model config) in
  let set name width value=Cyclesim.in_port sim name := Bits.of_int ~width value in
  let get name=Bits.to_int !(Cyclesim.out_port sim name) in
  let tick () =
    Cyclesim.cycle_before_clock_edge sim;
    let next= !(Cyclesim.out_port ~clock_edge:Before sim "next_pc") in
    Cyclesim.cycle_at_clock_edge sim;Cyclesim.cycle_after_clock_edge sim;
    check "formal wrapper exposes actual next PC" (Bits.equal next !(Cyclesim.out_port sim "pc")) in
  let write address word =
    set "write" 1 1;set "write_address" 6 address;set "write_data" 32 word;tick ();
    check "formal wrapper exposes write enable" (get "write_enable"=1 && get "read_enable"=0);
    set "write" 1 0 in
  set "clear" 1 1;tick ();set "clear" 1 0;
  set "ownership" 8 255;set "image_length" 24 64;
  write 0 (ins 5 63);write 63 (ins 17 ((2 lsl 16) lor (2 lsl 8)));
  set "start" 1 1;tick ();set "start" 1 0;
  check "START reads instruction0 with no added cycle" (get "pc"=0 && get "instruction"=ins 5 63);
  tick ();
  check "taken branch reads address63 on the PC edge"
    (get "pc"=63 && get "instruction"=ins 17 ((2 lsl 16) lor (2 lsl 8)));
  tick ();
  check "loaded XFER enters active transfer state" (get "transfer_edges"=4 && get "pc"=63);
  for _=1 to 3 do tick ();check "XFER preserves current fetch alignment" (get "pc"=63) done;
  set "stop" 1 1;tick ();set "stop" 1 0;
  check "stopping does not create another issue" (get "running"=0 && get "transfer_edges"=0);
  write 0 (ins 6 0);set "start" 1 1;tick ();set "start" 1 0;tick ();
  check "PULL exposes a real stalled instruction after reload" (get "stalled"=1 && get "pc"=0);
  set "tx_valid" 1 1;set "tx_data" width 42;tick ();
  check "releasing a stalled PULL advances aligned fetch" (get "pc"=1)

let () =
  List.iter engine_test [16;32];
  let adapter=Instruction_sram_formal.adapter () in
  let engine=Instruction_sram_formal.engine (Refinement_config.create Config.default) in
  check "fixed vendor adapter elaborates with its declared top"
    (Circuit.name adapter="protocol_instruction_sram_adapter");
  check "fixed vendor engine elaborates with its declared top"
    (Circuit.name engine="protocol_instruction_sram_engine");
  check "processor proof uses the actual SRAM processor factory"
    (Circuit.name (Instruction_sram_formal.processor (Refinement_config.create Config.default))
     ="protocol_processor_debug");
  Printf.printf "SRAM formal wrappers: actual PC observation, START, branch63, active XFER, STOP/reload and PULL stall passed for widths16/32; vendor circuits elaborated.\n"
