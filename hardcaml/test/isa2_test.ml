open Hardcaml

let check message condition = if not condition then failwith message
let instruction op immediate = (op lsl 24) lor immediate
let input = Signal.input
let output = Signal.output

let engine_test width =
  let config={Config.default with data_width=width} in
  let e=Engine.create config {
    clock=input "clk" 1;clear=input "clear" 1;start=input "start" 1;
    stop=input "stop" 1;clear_fault=input "clear_fault" 1;
    instruction=input "instruction" 32;image_length=input "image_length" 24;
    ownership=input "ownership" 8;pins=input "pins" 8;timestamp=input "timestamp" 32;
    tx_valid=input "tx_valid" 1;tx_data=input "tx_data" width;
    rx_ready=input "rx_ready" 1;event=input "event_pending" 1} in
  let circuit=Circuit.create_exn ~name:"strict_push_test"
    [output "pc" e.pc;output "running" e.running;output "fault" e.fault;
     output "stalled" e.stalled;output "rx_push" e.rx_push;output "rx_data" e.rx_data;
     output "enables" e.pin_enables;output "completed" e.completed] in
  let sim=Cyclesim.create circuit in
  let set name width value=Cyclesim.in_port sim name := Bits.of_int ~width value in
  let get name=Bits.to_int !(Cyclesim.out_port sim name) in
  let tick word ready =
    set "instruction" 32 word;set "rx_ready" 1 ready;
    Cyclesim.cycle_before_clock_edge sim;
    let pushed=Bits.to_int !(Cyclesim.out_port ~clock_edge:Before sim "rx_push")
    and stalled=Bits.to_int !(Cyclesim.out_port ~clock_edge:Before sim "stalled") in
    Cyclesim.cycle_at_clock_edge sim;Cyclesim.cycle_after_clock_edge sim;
    pushed,stalled in
  let start () =
    set "clear" 1 1;ignore(tick 0 0);set "clear" 1 0;
    set "ownership" 8 1;set "image_length" 24 16;
    set "start" 1 1;ignore(tick 0 0);set "start" 1 0;
    ignore(tick (instruction 19 ((1 lsl 16) lor 0xbeef)) 0);
    ignore(tick (instruction 3 1) 0);
    ignore(tick (instruction 2 1) 0);
    check "push setup" (get "pc"=3 && get "completed"=3 && get "enables"=1) in
  start ();
  check "strict full neither accepts nor stalls" (tick 0x07010000 0=(0,0));
  check "strict full halts with fault4" (get "fault"=4 && get "running"=0 && get "enables"=0);
  check "strict full preserves captured byte and instruction state"
    (get "rx_data"=0xbeef && get "pc"=3 && get "completed"=3);
  check "faulted engine cannot enqueue when capacity returns" (tick 0x07010000 1=(0,0));
  start ();
  check "strict ready accepts once" (tick 0x07010000 1=(1,0));
  check "strict ready completes normally" (get "fault"=0 && get "running"=1 && get "pc"=4 && get "completed"=4);
  start ();
  for _=1 to 4 do
    check "legacy full remains a visible stall" (tick 0x07000000 0=(0,1));
    check "legacy full preserves state" (get "pc"=3 && get "completed"=3 && get "fault"=0)
  done;
  check "legacy resumes when ready" (tick 0x07000000 1=(1,0));
  List.iter (fun word ->
    start ();
    check "malformed PUSH cannot enqueue" (tick word 1=(0,0));
    check "malformed PUSH uses fault1" (get "fault"=1 && get "pc"=3 && get "completed"=3))
    [0x07020000;0x07010100;0x07010001]

let processor_test config =
  let sim=Cyclesim.create (Processor.create ~debug:true config) in
  let set name width value=Cyclesim.in_port sim name := Bits.of_int ~width value in
  let get name=Bits.to_int !(Cyclesim.out_port sim name) in
  let tick ui =
    set "ui_in" 8 ui;Cyclesim.cycle_before_clock_edge sim;
    let before=Bits.to_int !(Cyclesim.out_port ~clock_edge:Before sim "uo_out") in
    Cyclesim.cycle_at_clock_edge sim;Cyclesim.cycle_after_clock_edge sim;before in
  let idle count=for _=1 to count do ignore(tick 0) done in
  let reset pins =
    set "ena" 1 1;set "rst_n" 1 0;set "uio_in" 8 pins;ignore(tick 0);
    set "rst_n" 1 1;idle 4 in
  let enter window=ignore(tick (window lsl 6));ignore(tick (window lsl 6)) in
  let write ?(on_nibble=(fun _->())) window word =
    enter window;
    for nibble=0 to 7 do
      on_nibble nibble;
      let ui=(window lsl 6) lor 16 lor ((word lsr (4*nibble)) land 15) in
      let rec accept left =
        check "host write bounded" (left>0);
        if tick ui land 16=0 then accept (left-1) in
      accept 100
    done;ignore(tick (window lsl 6)) in
  let command op payload=write 0 (instruction op payload) in
  let load engine words =
    command 0 engine;command 1 0;command 3 0;
    List.iter (write 1) words;command 2 (List.length words) in
  let read_status () =
    enter 1;enter 0;
    let value=ref 0 in
    for nibble=0 to 7 do
      let rec take left =
        check "host read bounded" (left>0);
        let sample=tick 32 in
        if sample land 32=0 then take (left-1)
        else value:= !value lor ((sample land 15) lsl (4*nibble)) in
      take 100
    done;!value in
  let status selection=command 8 selection;read_status () in
  reset 0;
  load 0 [instruction 19 ((1 lsl 16) lor 65535);instruction 24 ((1 lsl 16) lor 1);instruction 1 0];
  command 4 1;idle 4;
  check "RX inspection zero extends configured datapath" (status 6=(if config.Config.data_width=16 then 65534 else 131070));
  check "ISA version register" (status 7=2);
  command 8 8;
  check "invalid READ_SELECT reports host fault" (get "uo_out" land 128<>0);
  check "invalid READ_SELECT preserves selection" (read_status ()=2);
  (* All trigger modes observe pins without owning outputs, and run while halted. *)
  List.iter (fun mode ->
    let initial=if mode=1 || mode=3 then 1 else 0 in
    reset initial;command 11 (32 lor (mode lsl 3));
    check "inactive configured trigger" (get "dbg_events"=0 && get "dbg_running"=0);
    set "uio_in" 8 (1-initial);
    ignore(tick 0);check "trigger waits for synchronizer first edge" (get "dbg_events"=0);
    ignore(tick 0);check "trigger consumes prior sync2" (get "dbg_events"=0);
    ignore(tick 0);check "trigger delivered after two-stage synchronization" (get "dbg_events"=1);
    check "halted trigger raises IRQ without pin ownership" (get "uo_out" land 64<>0 && get "uio_oe"=0))
    [0;1;2;3];
  (* Suppress only the selected old trigger exactly on accepted configuration. *)
  reset 0;command 11 32;command 0 1;command 11 32;command 0 0;
  write ~on_nibble:(fun nibble -> if nibble=5 then set "uio_in" 8 1) 0 (instruction 11 0);
  check "configuration edge suppresses selected old trigger only" (get "dbg_events"=2);
  command 11 32;idle 4;
  check "enabling at static high does not synthesize rising edge" (get "dbg_events"=2);
  set "uio_in" 8 0;idle 4;set "uio_in" 8 1;idle 4;
  check "fresh edge delivered after configuration" (get "dbg_events"=3);
  (* Level delivery wins event consumption, and invalid running writes are atomic. *)
  reset 0;load 0 [instruction 15 0;instruction 5 0];command 11 48;command 4 1;
  set "uio_in" 8 1;idle 5;
  check "level event consumption keeps pending delivery" (get "dbg_events"=1 && get "dbg_running"=1);
  command 11 0;
  check "running trigger edit is rejected atomically"
    (get "uo_out" land 128<>0 && get "dbg_trigger_config" land 63=48);
  command 5 1;idle 3;
  check "STOP does not disable trigger observer" (get "dbg_running"=0 && get "dbg_trigger_config" land 63=48 && get "dbg_events"=1);
  command 11 64;
  check "reserved trigger bits rejected atomically" (get "dbg_trigger_config" land 63=48);
  set "ena" 1 0;ignore(tick 0);
  check "deselection clears triggers, samples, mailbox and IRQ"
    (get "dbg_trigger_config"=0 && get "dbg_synced_pins"=0 && get "dbg_previous_pins"=0
     && get "dbg_events"=0 && get "uo_out" land 64=0)

let () =
  List.iter engine_test [16;32];
  List.iter processor_test [Config.default;
    {Config.default with engine_count=2;data_width=16;program_words=32;prefetch=true}];
  Printf.printf "ISA2 Cyclesim: strict PUSH, legacy stalls, RX/version status and four synchronized trigger modes passed.\n"
