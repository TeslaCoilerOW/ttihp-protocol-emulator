open Hardcaml
open Signal

(* Verification-only construction. Production functions are called unchanged;
   the generator always selects their fixed vendor macro implementation. *)
let memory ~model ~engine_index inputs =
  if model then Instruction_sram.create_model ~engine_index inputs
  else Instruction_sram.create ~engine_index inputs

let memory_outputs (m:Instruction_sram.t) =
  [output "instruction" m.instruction;output "address" m.address;
   output "memory_enable" m.memory_enable;output "write_enable" m.write_enable;
   output "read_enable" m.read_enable]

let adapter_impl ~model () =
  let m=memory ~model ~engine_index:0 {
    clock=input "clk" 1;clear=input "clear" 1;write=input "write" 1;
    write_address=input "write_address" 6;write_data=input "write_data" 32;
    next_pc=input "next_pc" 24} in
  Circuit.create_exn ~name:"protocol_instruction_sram_adapter" (memory_outputs m)

let engine_impl ~model (refinement:Refinement_config.t) =
  let config=refinement.architecture in
  let clock=input "clk" 1 and clear=input "clear" 1 and instruction=wire 32 in
  let e=Engine.create ~options:refinement.options config {
    clock;clear;instruction;start=input "start" 1;stop=input "stop" 1;
    clear_fault=input "clear_fault" 1;image_length=input "image_length" 24;
    ownership=input "ownership" 8;pins=input "pins" 8;timestamp=input "timestamp" 32;
    tx_valid=input "tx_valid" 1;tx_data=input "tx_data" config.data_width;
    rx_ready=input "rx_ready" 1;event=input "event_pending" 1} in
  let next_pc=Engine.next_pc e in
  let m=memory ~model ~engine_index:0 {
    clock;clear;next_pc;write=input "write" 1;
    write_address=input "write_address" 6;write_data=input "write_data" 32} in
  instruction <== m.instruction;
  Circuit.create_exn ~name:"protocol_instruction_sram_engine"
    (memory_outputs m @ [output "pc" e.pc;output "next_pc" next_pc;
      output "running" e.running;output "fault" e.fault;output "issue" e.issue;
      output "stalled" e.stalled;output "completed" e.completed;
      output "transfer_edges" e.transfer_edges;output "wait_timer" e.wait_timer;
      output "pin_values" e.pin_values;output "pin_enables" e.pin_enables;
      output "tx_pop" e.tx_pop;output "rx_push" e.rx_push;output "rx_data" e.rx_data;
      output "consume_event" e.consume_event;output "signal_events" e.signal_events])

let adapter () = adapter_impl ~model:false ()
let engine config = engine_impl ~model:false config
let processor config = Processor.create_refinement ~debug:true config
let adapter_model () = adapter_impl ~model:true ()
let engine_model config = engine_impl ~model:true config
