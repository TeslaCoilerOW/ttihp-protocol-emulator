module Processor_fifo = Fifo
open Hardcaml
open Signal

(* Verification wrappers elaborate the production functions directly. These are
   separate circuits, never substituted for the complete candidate top. *)
let fifo (config:Config.t) =
  let f=Processor_fifo.create ~clock:(input "clk" 1) ~clear:(input "clear" 1)
      ~width:config.data_width ~depth:config.fifo_words
      ~push:(input "push" 1) ~pop:(input "pop" 1)
      ~data:(input "data_in" config.data_width) in
  Circuit.create_exn ~name:"protocol_fifo"
    [output "ready" f.ready; output "valid" f.valid;
     output "data_out" f.data; output "level" f.level]

let engine (config:Config.t) =
  let e=Engine.create config {
    clock=input "clk" 1; clear=input "clear" 1; start=input "start" 1;
    stop=input "stop" 1; clear_fault=input "clear_fault" 1;
    instruction=input "instruction" 32; image_length=input "image_length" 24;
    ownership=input "ownership" 8; pins=input "pins" 8;
    timestamp=input "timestamp" 32; tx_valid=input "tx_valid" 1;
    tx_data=input "tx_data" config.data_width; rx_ready=input "rx_ready" 1;
    event=input "event_pending" 1} in
  Circuit.create_exn ~name:"protocol_engine"
    [output "pc" e.pc; output "running" e.running; output "fault" e.fault;
     output "stalled" e.stalled; output "tx_pop" e.tx_pop;
     output "rx_push" e.rx_push; output "rx_data" e.rx_data;
     output "pin_values" e.pin_values; output "pin_enables" e.pin_enables;
     output "signal_events" e.signal_events; output "consume_event" e.consume_event;
     output "completed" e.completed; output "issue" e.issue;
     output "wait_timer" e.wait_timer; output "wait_limit" e.wait_limit;
     output "blocked_cycles" e.blocked_cycles; output "repeat_count" e.repeat_count;
     output "transfer_edges" e.transfer_edges]

let () =
  let target=ref "" and config_path=ref "" and output_path=ref "" in
  Arg.parse ["--config",Arg.Set_string config_path,"Architecture JSON";
             "--output",Arg.Set_string output_path,"Generated verification RTL";
             "--target",Arg.Set_string target,"fifo, engine or processor"]
    (fun _->raise (Arg.Bad "positional arguments are not accepted"))
    "generate_formal --config config.json --target fifo|engine|processor --output verification.v";
  if !config_path="" || !output_path="" then
    (prerr_endline "--config and --output are required"; exit 2);
  try
    let config=Config.load !config_path in
    let circuit=match !target with
      | "fifo" -> fifo config | "engine" -> engine config
      | "processor" -> Processor.create ~debug:true config
      | _ -> invalid_arg "--target must be fifo, engine or processor" in
    Rtl.output ~output_mode:(To_file !output_path) Verilog circuit
  with exn -> prerr_endline (Printexc.to_string exn); exit 1
