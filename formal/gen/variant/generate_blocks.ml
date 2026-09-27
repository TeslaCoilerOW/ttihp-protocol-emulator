(* Standalone FIFO and engine circuits of a design variant, for the formal
   harnesses fifo_conservation.sv and engine_safety.sv.

   hardcaml/bin/generate_formal.exe emits the same two circuits from an
   architecture config, which carries no variant options. This generator reads
   a refinement config (with its optional "options" object) and calls the same
   production constructors with those options:

   - engine: Engine.create ~options ~line, ports exactly as generate_formal's
     protocol_engine (pc is 7 bits with pc_bits=saturating_7; in the
     asynchronous reset styles [clear] is the engine's asynchronous reset);
   - engine_line (variants with options.line_unit, docs/extension.md): the
     same engine as module protocol_engine_line with extra observation
     outputs: ls_<name> for every line-unit register (Engine.line_state),
     transfer_pins and transfer_mode. --mutation NAME seeds one defect of
     Line_unit.mutation (negative controls of formal/line_unit*.sv);
   - crc_step: the line unit's CRC step function (Line_unit.crc_next with
     Line_unit.preset_polynomial, as Engine.create uses them) as a
     combinational module line_crc_step (crc[16], data, preset[2], msb -> next[16]),
     optionally with --mutation crc_tap;
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

let engine_inputs (config : Config.t) : Engine.inputs =
  { clock = input "clk" 1; clear = input "clear" 1; start = input "start" 1
  ; stop = input "stop" 1; clear_fault = input "clear_fault" 1
  ; instruction = input "instruction" 32; image_length = input "image_length" 24
  ; ownership = input "ownership" 8; pins = input "pins" 8
  ; timestamp = input "timestamp" 32; tx_valid = input "tx_valid" 1
  ; tx_data = input "tx_data" config.data_width; rx_ready = input "rx_ready" 1
  ; event = input "event_pending" 1 }

let engine_outputs (e : Engine.t) =
  [ output "pc" e.pc; output "running" e.running; output "fault" e.fault
  ; output "stalled" e.stalled; output "tx_pop" e.tx_pop
  ; output "rx_push" e.rx_push; output "rx_data" e.rx_data
  ; output "pin_values" e.pin_values; output "pin_enables" e.pin_enables
  ; output "signal_events" e.signal_events; output "consume_event" e.consume_event
  ; output "completed" e.completed; output "issue" e.issue
  ; output "wait_timer" e.wait_timer; output "wait_limit" e.wait_limit
  ; output "blocked_cycles" e.blocked_cycles; output "repeat_count" e.repeat_count
  ; output "transfer_edges" e.transfer_edges ]

(* Engine with the line unit plus observation outputs; the registers
   transfer_pins and transfer_mode are found by name (unique in one engine). *)
let engine_line ?mutation (r : Refinement_config.t) =
  if not (Line_options.enabled r.line) then invalid_arg "engine_line needs options.line_unit";
  let config = r.architecture in
  let e =
    Engine.create ~options:r.options ~timing:r.timing ~line:r.line ?line_mutation:mutation config
      (engine_inputs config) in
  let roots = e.pc :: e.pin_values :: e.rx_data :: List.map snd e.line_state in
  let named name =
    let found = Signal_graph.fold (Signal_graph.create roots) ~init:[] ~f:(fun acc s ->
        match s with
        | Signal.Type.Reg _ -> if List.mem name (Signal.names s) then s :: acc else acc
        | _ -> acc) in
    match found with
    | [ s ] -> s
    | l -> failwith (Printf.sprintf "expected one signal named %s, found %d" name (List.length l)) in
  let transfer_pins = named "transfer_pins" and transfer_mode = named "transfer_mode" in
  Circuit.create_exn ~name:"protocol_engine_line"
    (engine_outputs e
     @ List.map (fun (name, s) -> output ("ls_" ^ name) s) e.line_state
     @ [ output "transfer_pins" transfer_pins; output "transfer_mode" transfer_mode ])

let crc_step ?mutation () =
  let crc = input "crc" Line_unit.crc_width and data = input "data" 1
  and preset = input "preset" 2 and msb = input "msb" 1 in
  let poly = Line_unit.preset_polynomial ~msb preset in
  Circuit.create_exn ~name:"line_crc_step"
    [ output "next" (Line_unit.crc_next ?mutation ~msb ~poly crc data) ]

let engine (r : Refinement_config.t) =
  let config = r.architecture in
  let e =
    Engine.create ~options:r.options ~line:r.line config
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
  let target = ref "" and config_path = ref "" and output_path = ref "" and mutation = ref "" in
  Arg.parse
    [ "--config", Arg.Set_string config_path, "Refinement JSON (with optional options)"
    ; "--output", Arg.Set_string output_path, "Generated verification RTL"
    ; "--target", Arg.Set_string target, "fifo, engine, engine_line or crc_step"
    ; "--mutation", Arg.Set_string mutation, "engine_line/crc_step: seeded defect (Line_unit.mutation)" ]
    (fun _ -> raise (Arg.Bad "positional arguments are not accepted"))
    "generate_blocks --config refinement.json --target fifo|engine --output verification.v";
  if !config_path = "" || !output_path = "" then (
    prerr_endline "--config and --output are required";
    exit 2);
  try
    let config = Refinement_config.load !config_path in
    let mutation = if !mutation = "" then None else Some (Line_unit.mutation_of_string !mutation) in
    let circuit =
      match !target with
      | "fifo" -> fifo config
      | "engine" -> engine config
      | "engine_line" -> engine_line ?mutation config
      | "crc_step" -> crc_step ?mutation ()
      | _ -> invalid_arg "--target must be fifo, engine, engine_line or crc_step"
    in
    Rtl.output ~output_mode:(To_file !output_path) Verilog circuit
  with exn -> prerr_endline (Printexc.to_string exn); exit 1
