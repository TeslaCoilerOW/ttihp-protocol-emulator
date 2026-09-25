open Hardcaml

let check label condition = if not condition then failwith label
let instruction op immediate = (op lsl 24) lor immediate

let architecture_json (c:Config.t) = `Assoc [
  "schema_version",`String "protocol-emulator.architecture.v1";
  "engine_count",`Int c.engine_count;"data_width",`Int c.data_width;
  "program_words",`Int c.program_words;"fifo_words",`Int c.fifo_words;
  "issue",`String c.issue;"prefetch",`Bool c.prefetch]
let refinement_json architecture = `Assoc [
  "schema_version",`String "protocol-emulator.refinement.v1";
  "architecture",architecture;"implementation",`String Refinement_config.implementation]
let reject json =
  match Refinement_config.of_json json with
  | _ -> failwith "invalid refinement accepted"
  | exception Invalid_argument _ -> ()

let configuration_test () =
  let good=refinement_json (architecture_json Config.default) in
  check "closed refinement accepts baseline architecture"
    ((Refinement_config.of_json good).architecture=Config.default);
  List.iter (fun config -> reject (refinement_json (architecture_json config)))
    [{Config.default with engine_count=2};{Config.default with program_words=32};
     {Config.default with program_words=128};{Config.default with prefetch=true};
     {Config.default with data_width=8};{Config.default with fifo_words=16};
     {Config.default with issue="custom"}];
  let fields=match good with `Assoc fields -> fields | _ -> assert false in
  List.iter reject [
    `Null;`Assoc (List.tl fields);`Assoc (("extra",`Null)::fields);
    `Assoc (("architecture",architecture_json Config.default)::fields);
    `Assoc (("schema_version",`String "protocol-emulator.refinement.v2")::List.remove_assoc "schema_version" fields);
    `Assoc (("implementation",`String "other_macro")::List.remove_assoc "implementation" fields);
    `Assoc (("implementation",`Bool true)::List.remove_assoc "implementation" fields);
    refinement_json (`Assoc (("prefetch",`Bool false)::
      (match architecture_json Config.default with `Assoc fields->fields | _->assert false)))]

let adapter_test () =
  let input=Signal.input and output=Signal.output in
  let m=Instruction_sram.create_model ~engine_index:0 {
    clock=input "clk" 1;clear=input "clear" 1;write=input "write" 1;
    write_address=input "write_address" 6;write_data=input "write_data" 32;
    next_pc=input "next_pc" 24} in
  let sim=Cyclesim.create (Circuit.create_exn ~name:"instruction_sram_model_test"
    [output "data" m.instruction;output "address" m.address;
     output "men" m.memory_enable;output "wen" m.write_enable;output "ren" m.read_enable]) in
  let set name width value=Cyclesim.in_port sim name := Bits.of_int ~width value in
  let get name=Bits.to_int !(Cyclesim.out_port sim name) in
  let tick ~clear ~write ~address ~data ~next =
    set "clear" 1 clear;set "write" 1 write;set "write_address" 6 address;
    set "write_data" 32 data;set "next_pc" 24 next;Cyclesim.cycle sim in
  tick ~clear:0 ~write:1 ~address:0 ~data:0x1234beef ~next:63;
  check "write disables read and selects loader address" (get "wen"=1 && get "ren"=0 && get "address"=0);
  tick ~clear:0 ~write:0 ~address:63 ~data:0 ~next:0;
  check "same-edge read supplies complete 32-bit instruction" (get "data"=0x1234beef);
  set "next_pc" 24 63;Cyclesim.cycle_before_clock_edge sim;
  check "address changes cannot change registered DOUT"
    (Bits.to_int !(Cyclesim.out_port ~clock_edge:Before sim "data")=0x1234beef);
  Cyclesim.cycle_at_clock_edge sim;Cyclesim.cycle_after_clock_edge sim;
  tick ~clear:0 ~write:0 ~address:0 ~data:0 ~next:0;
  tick ~clear:1 ~write:1 ~address:0 ~data:0xbad ~next:0;
  check (Printf.sprintf "clear suppresses access without resetting DOUT: men=%d wen=%d data=%x"
    (get "men") (get "wen") (get "data"))
    (get "men"=0 && get "wen"=0 && get "data"=0x1234beef);
  tick ~clear:0 ~write:1 ~address:63 ~data:0xface9876 ~next:0;
  check "write-only access holds DOUT" (get "data"=0x1234beef);
  tick ~clear:0 ~write:0 ~address:0 ~data:0 ~next:63;
  check "address 63 and upper half preserve data" (get "data"=0xface9876);
  tick ~clear:0 ~write:0 ~address:63 ~data:0 ~next:0;
  check "clear cannot corrupt previously loaded instruction" (get "data"=0x1234beef)

let next_pc_test config =
  let input=Signal.input and output=Signal.output in
  let e=Engine.create config {
    clock=input "clk" 1;clear=input "clear" 1;start=input "start" 1;
    stop=input "stop" 1;clear_fault=input "clear_fault" 1;
    instruction=input "instruction" 32;image_length=input "image_length" 24;
    ownership=input "ownership" 8;pins=input "pins" 8;timestamp=input "timestamp" 32;
    tx_valid=input "tx_valid" 1;tx_data=input "tx_data" config.Config.data_width;
    rx_ready=input "rx_ready" 1;event=input "event_pending" 1} in
  let sim=Cyclesim.create (Circuit.create_exn ~name:"actual_next_pc_test"
    [output "pc" e.pc;output "next_pc" (Engine.next_pc e);output "issue" e.issue]) in
  let set name width value=Cyclesim.in_port sim name := Bits.of_int ~width value in
  let words=[|0x00000000;0x04000002;0x05000007;0x06000000;0x07000000;0x07010000;
    0x0a000001;0x0b000003;0x0d000000;0x0c000003;0x0f000000;0x0e00000f;
    0x10000000;0x11010200;0x13000000;0x1a000002;0x1d000008;0x01000000;
    0xff000000;0x05ffffff|] in
  let state=Random.State.make [|0x5a17|] in
  for cycle=0 to 3999 do
    set "clear" 1 (if cycle mod 97=0 then 1 else 0);
    set "start" 1 (if cycle mod 17=1 then 1 else 0);
    set "stop" 1 (if cycle mod 53=2 then 1 else 0);
    set "clear_fault" 1 (if cycle mod 79=2 then 1 else 0);
    set "instruction" 32 words.(Random.State.int state (Array.length words));
    set "image_length" 24 (if cycle mod 41=0 then 0 else 64);
    set "ownership" 8 255;set "pins" 8 (cycle land 255);set "timestamp" 32 cycle;
    set "tx_valid" 1 (cycle mod 2);set "tx_data" config.data_width cycle;
    set "rx_ready" 1 (cycle / 2 mod 2);set "event_pending" 1 (if cycle mod 7=0 then 1 else 0);
    Cyclesim.cycle_before_clock_edge sim;
    let predicted= !(Cyclesim.out_port ~clock_edge:Before sim "next_pc") in
    Cyclesim.cycle_at_clock_edge sim;Cyclesim.cycle_after_clock_edge sim;
    check (Printf.sprintf "actual compiled PC transition at cycle %d" cycle)
      (Bits.equal predicted !(Cyclesim.out_port sim "pc"))
  done

(* Compare every existing public/debug output on both sides of every clock.
   SRAM data itself may be stale while halted; all architecturally observable
   engine/host/event/FIFO/pin behavior must nevertheless be cycle-identical. *)
let processor_test config =
  let baseline=Cyclesim.create (Processor.create ~debug:true config) in
  let model=Cyclesim.create (Processor.create_refinement_model ~debug:true (Refinement_config.create config)) in
  let cycle=ref 0 in
  let set name width value =
    List.iter (fun sim -> Cyclesim.in_port sim name := Bits.of_int ~width value) [baseline;model] in
  let compare edge =
    List.iter (fun (name,value) ->
      check (Printf.sprintf "SRAM differs at cycle %d, output %s" !cycle name)
        (Bits.equal !value !(Cyclesim.out_port ~clock_edge:edge model name)))
      (Cyclesim.out_ports ~clock_edge:edge baseline) in
  let get name=Bits.to_int !(Cyclesim.out_port baseline name) in
  let tick ui =
    set "ui_in" 8 ui;
    List.iter Cyclesim.cycle_before_clock_edge [baseline;model];compare Before;
    let before=Bits.to_int !(Cyclesim.out_port ~clock_edge:Before baseline "uo_out") in
    List.iter Cyclesim.cycle_at_clock_edge [baseline;model];
    List.iter Cyclesim.cycle_after_clock_edge [baseline;model];compare After;
    incr cycle;before in
  let idle count=for _=1 to count do ignore(tick 0) done in
  let enter window=ignore(tick (window lsl 6));ignore(tick (window lsl 6)) in
  let write window word =
    enter window;
    for nibble=0 to 7 do
      let ui=(window lsl 6) lor 16 lor ((word lsr (4*nibble)) land 15) in
      let rec accept left =
        check "bounded host write" (left>0);
        if tick ui land 16=0 then accept (left-1) in
      accept 100
    done;ignore(tick (window lsl 6)) in
  let command op payload=write 0 (instruction op payload) in
  let load engine owner words =
    command 0 engine;command 1 0;command 3 owner;
    List.iter (write 1) words;command 2 (List.length words) in
  set "rst_n" 1 0;set "ena" 1 1;set "uio_in" 8 0;idle 2;
  set "rst_n" 1 1;idle 2;
  load 0 3 [instruction 2 1;instruction 3 3;instruction 6 0;
    instruction 18 (1 lsl 16);instruction 7 0;instruction 10 2;
    instruction 4 2;instruction 11 6;instruction 19 (2 lsl 16);
    instruction 26 ((2 lsl 16) lor 11);instruction 29 99;instruction 12 255;
    instruction 13 ((7 lsl 16) lor (1 lsl 8));instruction 15 0;
    instruction 2 0;instruction 16 ((1 lsl 3) lor (7 lsl 6));
    (if config.Config.issue="fused" then instruction 17 ((4 lsl 16) lor (2 lsl 8) lor 24) else 0);
    instruction 7 0;instruction 1 0];
  load 1 4 [instruction 6 0;instruction 18 (1 lsl 16);instruction 7 0;
    instruction 2 4;instruction 3 4;instruction 15 0;instruction 1 0];
  load 2 8 [instruction 3 8;instruction 2 8;instruction 4 1;
    instruction 2 0;instruction 4 2;instruction 5 1];
  load 3 16 (List.init 64 (fun index ->
    if index=0 then instruction 5 63 else if index=63 then instruction 1 0 else instruction 29 77));
  command 6 ((1 lsl 2) lor (1 lsl 4) lor (1 lsl 5));command 4 15;idle 20;
  check "other engines run while PULL stalls and address63 halts"
    (get "dbg_running"=7 && get "uo_out" land 128=0);
  command 0 0;write 2 0xa5;idle 15;
  set "uio_in" 8 128;idle 5;command 9 3;idle 60;
  check "branches, waits and fused transfer complete without fault"
    (get "uo_out" land 128=0 && get "dbg_running"=4);
  (* Reload a halted engine while the independent pin generator keeps running. *)
  load 3 16 [instruction 19 ((1 lsl 16) lor 0x55aa);instruction 7 0;instruction 5 3];
  command 4 8;idle 15;
  check "out-of-image next address faults after committed words"
    (get "uo_out" land 128<>0 && get "dbg_running"=4);
  command 7 8;
  load 3 16 [instruction 19 ((1 lsl 16) lor 0x1234);instruction 7 (1 lsl 16);instruction 5 1];
  command 4 8;idle (config.fifo_words*3+10);
  check "strict overflow preserves trapping behavior" (get "uo_out" land 128<>0 && get "dbg_running"=4);
  command 7 8;command 10 0;
  load 3 16 [instruction 12 3;instruction 13 (7 lsl 16)];command 4 8;idle 10;
  check "bounded WAITPIN preserves timeout fault" (get "uo_out" land 128<>0 && get "dbg_running"=4);
  command 7 8;
  load 3 16 [instruction 3 16;instruction 16 4;
    (if config.issue="fused" then instruction 17 ((8 lsl 16) lor (31 lsl 8) lor 16)
     else instruction 4 1000);instruction 5 2];
  command 4 8;idle 8;command 5 8;idle 3;
  check "STOP interrupts committed transfer or timer without advancing it"
    (get "dbg_running"=4 && get "uo_out" land 128=0);
  command 4 8;idle 8;
  check "START restarts word0 after an interrupted timed operation" (get "dbg_running"=12);
  (* Abort loading/running through reset, then a fresh START must fetch word0. *)
  set "rst_n" 1 0;idle 2;set "rst_n" 1 1;idle 3;
  check "reset discards commits and releases outputs" (get "dbg_image_valid"=0 && get "uio_oe"=0);
  load 0 1 [instruction 2 1;instruction 3 1;instruction 5 2];command 4 1;idle 5;
  check "fresh load after reset executes word0" (get "uio_oe"=1 && get "uio_out"=1);
  set "ena" 1 0;idle 2;
  check "deselection clears exposed state" (get "dbg_running"=0 && get "uio_oe"=0);
  !cycle

let () =
  configuration_test ();adapter_test ();
  let configs=List.concat_map (fun data_width -> List.concat_map (fun issue ->
    List.map (fun fifo_words -> {Config.default with data_width;issue;fifo_words}) [8;32])
    ["scalar";"fused"]) [16;32] in
  let cycles=List.fold_left (fun total config -> next_pc_test config;total+processor_test config) 0 configs in
  Printf.printf "Instruction SRAM Cyclesim: closed config, adapter timing, 32000 actual PC transitions and %d complete-processor cycle comparisons across 8 refinements passed.\n" cycles
