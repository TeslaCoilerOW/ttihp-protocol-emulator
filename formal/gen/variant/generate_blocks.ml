(* Standalone FIFO and engine circuits of a design variant, for the formal
   harnesses fifo_conservation.sv and engine_safety.sv.

   hardcaml/bin/generate_formal.exe emits the same two circuits from an
   architecture config, which carries no variant options. This generator reads
   a refinement config (with its optional "options" object) and calls the same
   production constructors with those options:

   - engine: Engine.create ~options, ports exactly as generate_formal's
     protocol_engine (pc is 7 bits with pc_bits=saturating_7; in the
     asynchronous reset styles [clear] is the engine's asynchronous reset);
   - fifo: Fifo.create_with as Processor.create_with_memory builds its queues:
     in the asynchronous styles the chip reset is a separate asynchronous input
     [rst] and [clear] is FLUSH (a synchronous clear); in the synchronous styles
     [clear] is chip clear OR FLUSH as in generate_formal. With
     fifo_storage_reset the storage words are registers taking the chip reset.
   Nothing is added to or changed in the production logic. *)

module Processor_fifo = Fifo
open Hardcaml
open Signal

let fifo (r : Refinement_config.t) =
  let config = r.architecture and options = r.options in
  let clock = input "clk" 1 in
  let async = Variant_options.asynchronous options in
  let rst = if async then Some (input "rst" 1) else None in
  let clear = input "clear" 1 in
  let storage =
    if not options.fifo_storage_reset then Processor_fifo.Memory
    else match rst with
      | Some reset -> Processor_fifo.Registers_async_reset reset
      | None -> Processor_fifo.Registers_sync_clear clear
  in
  let f =
    Processor_fifo.create_with ~async_reset:rst ~storage ~clock ~clear
      ~width:config.data_width ~depth:config.fifo_words
      ~push:(input "push" 1) ~pop:(input "pop" 1)
      ~data:(input "data_in" config.data_width)
  in
  Circuit.create_exn ~name:"protocol_fifo"
    [ output "ready" f.ready; output "valid" f.valid
    ; output "data_out" f.data; output "level" f.level ]

let engine (r : Refinement_config.t) =
  let config = r.architecture in
  let e =
    Engine.create ~options:r.options config
      { clock = input "clk" 1; clear = input "clear" 1; start = input "start" 1
      ; stop = input "stop" 1; clear_fault = input "clear_fault" 1
      ; instruction = input "instruction" 32; image_length = input "image_length" 24
      ; ownership = input "ownership" 8; pins = input "pins" 8
      ; timestamp = input "timestamp" 32; tx_valid = input "tx_valid" 1
      ; tx_data = input "tx_data" config.data_width; rx_ready = input "rx_ready" 1
      ; event = input "event_pending" 1 }
  in
  Circuit.create_exn ~name:"protocol_engine"
    [ output "pc" e.pc; output "running" e.running; output "fault" e.fault
    ; output "stalled" e.stalled; output "tx_pop" e.tx_pop
    ; output "rx_push" e.rx_push; output "rx_data" e.rx_data
    ; output "pin_values" e.pin_values; output "pin_enables" e.pin_enables
    ; output "signal_events" e.signal_events; output "consume_event" e.consume_event
    ; output "completed" e.completed; output "issue" e.issue
    ; output "wait_timer" e.wait_timer; output "wait_limit" e.wait_limit
    ; output "blocked_cycles" e.blocked_cycles; output "repeat_count" e.repeat_count
    ; output "transfer_edges" e.transfer_edges ]

let () =
  let target = ref "" and config_path = ref "" and output_path = ref "" in
  Arg.parse
    [ "--config", Arg.Set_string config_path, "Refinement JSON (with optional options)"
    ; "--output", Arg.Set_string output_path, "Generated verification RTL"
    ; "--target", Arg.Set_string target, "fifo or engine" ]
    (fun _ -> raise (Arg.Bad "positional arguments are not accepted"))
    "generate_blocks --config refinement.json --target fifo|engine --output verification.v";
  if !config_path = "" || !output_path = "" then (
    prerr_endline "--config and --output are required";
    exit 2);
  try
    let config = Refinement_config.load !config_path in
    let circuit =
      match !target with
      | "fifo" -> fifo config
      | "engine" -> engine config
      | _ -> invalid_arg "--target must be fifo or engine"
    in
    Rtl.output ~output_mode:(To_file !output_path) Verilog circuit
  with exn -> prerr_endline (Printexc.to_string exn); exit 1
