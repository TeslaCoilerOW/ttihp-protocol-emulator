module Processor_fifo = Fifo
open Hardcaml
open Signal

type instruction_memory = Baseline | Ihp_pair | Synchronous_model

(* [options] (default: the design of record) selects the variant knobs;
   docs/variants.md defines them.  [clear] is the chip reset net: a
   synchronous clear in the synchronous styles, the asynchronous reset of every
   register in the asynchronous styles.  Either way it keeps gating the same
   combinational paths (outputs, ready/valid, SRAM enables, mover, triggers). *)
let create_with_memory ?(debug=false) ?(options=Variant_options.default) memory_backend
    (config:Config.t) =
  let config = Config.validate config in
  let options = Variant_options.validate config options in
  let open Always in
  let n = config.engine_count and width = config.data_width in
  let clock = input "clk" 1 and rst_n = input "rst_n" 1 and ena = input "ena" 1 in
  let ui = input "ui_in" 8 and uio = input "uio_in" 8 in
  let async = Variant_options.asynchronous options in
  let clear = match options.reset with
    | Variant_options.Sync -> (~:rst_n) |: (~:ena)
    | Sync_registered ->
      (* Two plain flops: rst_n reaches only the first D input. *)
      let plain = Reg_spec.create ~clock () in
      let first = reg plain (rst_n &: ena) -- "reset_sync_1" in
      let second = reg plain first -- "reset_sync_2" in
      ~:second
    | Async -> ~:(rst_n &: ena)
    | Async_sync_release ->
      (* Asynchronous assert; release after two edges. *)
      let raw = ~:(rst_n &: ena) in
      let sync = Reg_spec.create ~clock ~reset:raw () in
      let first = reg sync vdd -- "reset_sync_1" in
      let second = reg sync first -- "reset_sync_2" in
      ~:second in
  let spec = if async then Reg_spec.create ~clock ~reset:clear ()
    else Reg_spec.create ~clock ~clear () in
  let r name w = let v=Variable.reg spec ~width:w in ignore(v.value -- name); v in
  let selected = r "host_selected_engine" 2 and read_select = r "host_read_select" 3 in
  let host_fault = r "host_fault" 1 and timestamp = r "timestamp" 32 in
  let committed = Array.init n (fun k -> r (Printf.sprintf "image_valid_%d" k) 1) in
  let writing = Array.init n (fun k -> r (Printf.sprintf "image_writing_%d" k) 1) in
  (* Both are <= program_words by construction (COMMIT and write-ready). *)
  let iw = if options.narrow_image_regs then Config.log2 config.program_words + 1 else 16 in
  let lengths = Array.init n (fun k -> r (Printf.sprintf "image_length_%d" k) iw) in
  let loaded = Array.init n (fun k -> r (Printf.sprintf "image_loaded_%d" k) iw) in
  let owners = Array.init n (fun k -> r (Printf.sprintf "ownership_%d" k) 8) in
  let drains = Array.init n (fun k -> r (Printf.sprintf "open_drain_%d" k) 8) in
  let events = Array.init n (fun k -> r (Printf.sprintf "mailbox_%d" k) 1) in
  let trigger_config = Array.init n (fun k -> r (Printf.sprintf "pin_trigger_%d" k) 6) in
  let route_count = Array.init n (fun k -> r (Printf.sprintf "route_remaining_%d" k) 16) in
  let route_dest = Array.init n (fun k -> r (Printf.sprintf "route_destination_%d" k) 2) in
  let rr = r "dma_round_robin" (Config.log2 n) in
  let sync1 = reg spec uio in
  let synced_pins = reg spec sync1 in
  let previous_pins = reg spec synced_pins in
  let control w = Array.init n (fun _ -> wire w) in
  let starts=control 1 and stops=control 1 and clears=control 1 and flushes=control 1 in
  let instructions=control 32 and tx_pop=control 1 and rx_push=control 1
  and rx_data=control width and tx_push=control 1 and tx_data=control width
  and rx_pop=control 1 in
  let storage = if not options.fifo_storage_reset then Processor_fifo.Memory
    else if async then Processor_fifo.Registers_async_reset clear
    else Processor_fifo.Registers_sync_clear clear in
  (* Asynchronous styles: the chip reset is the queues' asynchronous reset and
     FLUSH alone stays their synchronous clear. *)
  let fifo k ~push ~pop ~data =
    if async then Processor_fifo.create_with ~async_reset:(Some clear) ~storage ~clock
        ~clear:flushes.(k) ~width ~depth:config.fifo_words ~push ~pop ~data
    else Processor_fifo.create_with ~async_reset:None ~storage ~clock
        ~clear:(clear |: flushes.(k)) ~width ~depth:config.fifo_words ~push ~pop ~data in
  let txs=Array.init n (fun k -> fifo k ~push:tx_push.(k) ~pop:tx_pop.(k) ~data:tx_data.(k)) in
  let rxs=Array.init n (fun k -> fifo k ~push:rx_push.(k) ~pop:rx_pop.(k) ~data:rx_data.(k)) in
  let engines=Array.init n (fun k -> Engine.create ~options config
      {clock; clear; start=starts.(k); stop=stops.(k); clear_fault=clears.(k);
       instruction=instructions.(k); image_length=lengths.(k).value;
       ownership=owners.(k).value; pins=synced_pins; timestamp=timestamp.value;
       tx_valid=txs.(k).valid; tx_data=txs.(k).data; rx_ready=rxs.(k).ready;
       event=events.(k).value}) in
  Array.iteri (fun k (e:Engine.t) -> tx_pop.(k) <== e.tx_pop;
      rx_push.(k) <== e.rx_push; rx_data.(k) <== e.rx_data) engines;
  let mux_arr sel get arr = mux (uresize sel (Config.log2 n)) (Array.to_list (Array.map get arr)) in
  let selected_engine get = mux_arr selected.value get engines in
  let selected_var arr = mux_arr selected.value (fun (v:Variable.t) -> v.value) arr in
  let selected_fifo arr get = mux_arr selected.value get arr in
  let any xs = List.fold_left ( |: ) gnd xs in
  let all xs = List.fold_left ( &: ) vdd xs in
  let active_engine_mask = concat_lsb (Array.to_list (Array.map (fun (e:Engine.t)->e.running) engines)) in
  let fault_engine_mask = concat_lsb (Array.to_list (Array.map (fun (e:Engine.t)->e.fault <>:. 0) engines)) in
  let any_fault = host_fault.value |: (fault_engine_mask <>:. 0) in
  let read_data=wire 32 and read_valid=wire 1 and write_ready=wire 1 in
  let host=Host.create_with ~async ~clock ~clear ~ui ~write_ready ~read_valid ~read_data
      ~irq:(any (List.init n (fun k->events.(k).value |: rxs.(k).valid))) ~fault:any_fault in
  let code=select host.word 31 24 and payload=select host.word 23 0 in
  let selected_is k = selected.value ==:. k in
  let halted = ~:(selected_engine (fun (e:Engine.t)->e.running)) in
  let low_engine_mask=select payload (n-1) 0 in
  let mask_ok = payload <:. (1 lsl n) in
  let selected_fault = selected_engine (fun (e:Engine.t)->e.fault) in
  let own=select payload 7 0 and drain=select payload 15 8 in
  let overlap = any (List.init n (fun k -> (~:(selected_is k))
      &: ((owners.(k).value &: own) <>:. 0))) in
  let cmd_valid = mux code (List.init 256 (fun command -> match command with
    | 0 -> payload <:. n
    | 1 -> payload ==:. 0
    | 2 -> halted &: selected_var writing &: (payload <>:. 0) &: (payload <=:. config.program_words)
           &: (if options.narrow_image_regs
               then uresize payload 16 ==: uresize (selected_var loaded) 16
               else uresize payload 16 ==: selected_var loaded)
    | 3 -> halted &: (select payload 23 16 ==:. 0) &: ~:overlap
           &: ((drain &: ~:own) ==:. 0)
    | 4 -> mask_ok &: all (List.init n (fun k -> (~:(bit payload k))
             |: (committed.(k).value &: (engines.(k).fault ==:. 0))))
    | 5|9 -> mask_ok
    | 6 -> (select payload 23 21 ==:. 0)
           &: (if n=4 then vdd else (select payload 1 0 <:. n) &: (select payload 3 2 <:. n))
    | 7 -> (select payload 22 n ==:. 0)
           &: ((low_engine_mask &: active_engine_mask) ==:. 0)
    | 8 -> payload <:. 8
    | 10 -> halted &: (payload ==:. 0)
    | 11 -> halted &: (select payload 23 6 ==:. 0)
    | _ -> gnd)) in
  let command_write = host.write &: (host.window ==:. 0) in
  let accepted = command_write &: cmd_valid in
  let command c = accepted &: (code ==:. c) in
  let program_write = host.write &: (host.window ==:. 1) in
  let host_tx = host.write &: (host.window ==:. 2) in
  let host_rx = host.read_word &: (host.window ==:. 3) in
  write_ready <== mux host.window
    [vdd; halted &: selected_var writing &: (selected_var loaded <:. config.program_words);
     selected_fifo txs (fun (f:Processor_fifo.t)->f.ready); gnd];
  read_valid <== ((host.window ==:. 0) |:
                 ((host.window ==:. 3) &: selected_fifo rxs (fun (f:Processor_fifo.t)->f.valid)));
  let status = concat_msb [zero 16; selected_fault; zero 4; selected_fault <>:. 0;
                          selected_engine (fun (e:Engine.t)->e.stalled); selected_var committed;
                          selected_engine (fun (e:Engine.t)->e.running)] in
  let fifo_levels=concat_msb [uresize (selected_fifo rxs (fun (f:Processor_fifo.t)->f.level)) 16;
                              uresize (selected_fifo txs (fun (f:Processor_fifo.t)->f.level)) 16] in
  let status_data=mux read_select.value [status; timestamp.value; fifo_levels;
      uresize (selected_engine (fun (e:Engine.t)->e.pc)) 32;
      uresize (selected_var events) 32;
      (if options.debug_counters then selected_engine (fun (e:Engine.t)->e.completed)
       else zero 32);
      uresize (selected_engine (fun (e:Engine.t)->e.rx_data)) 32;
      of_int ~width:32 (Variant_options.isa_version options)] in
  read_data <== mux2 (host.window ==:. 3)
      (uresize (selected_fifo rxs (fun (f:Processor_fifo.t)->f.data)) 32) status_data;
  Array.iteri (fun k _ ->
    starts.(k) <== (command 4 &: bit payload k);
    stops.(k) <== ((command 5 &: bit payload k) |: (command 1 &: selected_is k));
    clears.(k) <== (command 7 &: bit payload k);
    flushes.(k) <== (command 10 &: selected_is k);
    let pc=engines.(k).pc in
    let address_width=Config.log2 config.program_words in
    let wp={Write_port.write_clock=clock; write_address=uresize loaded.(k).value address_width;
            write_enable=program_write &: selected_is k &: ~:clear; write_data=host.word} in
    let instruction=match memory_backend with
      | Ihp_pair | Synchronous_model ->
        let inputs:Instruction_sram.inputs =
          {clock;clear;write=wp.write_enable;write_address=wp.write_address;
           write_data=wp.write_data;next_pc=Engine.next_pc engines.(k)} in
        let adapter=match memory_backend with
          | Ihp_pair -> Instruction_sram.create ~engine_index:k inputs
          | Synchronous_model -> Instruction_sram.create_model ~engine_index:k inputs
          | Baseline -> assert false in
        adapter.instruction
      | Baseline -> if config.prefetch then (
      let next=pc +:. 1 in
      let reads=multiport_memory config.program_words ~write_ports:[|wp|]
          ~read_addresses:[|uresize pc address_width; uresize next address_width|] in
      let word=reads.(0) and lookahead=reads.(1) in
      let cached=reg spec lookahead and tag=reg spec next and valid=reg spec vdd in
      mux2 (valid &: (tag ==: pc)) cached word)
      else memory config.program_words ~write_port:wp ~read_address:(uresize pc address_width) in
    instructions.(k) <== instruction) engines;
  (* A single accepted mover word per cycle. Rotate after acceptance, so a
     continuously eligible source is served within n successful grants. *)
  let eligible=Array.init n (fun k ->
    let dest=route_dest.(k).value in
    let destination_ready=mux_arr dest (fun (f:Processor_fifo.t)->f.ready) txs in
    let host_destination=host_tx &: (selected.value ==: dest) in
    let read_reserved=host.read_lock &: selected_is k in
    let route_edit=command 6 &: (select payload 1 0 ==:. k) in
    let flushing=(command 10) &: ((selected_is k) |: (selected.value ==: dest)) in
    (route_count.(k).value <>:. 0) &: rxs.(k).valid &: destination_ready
    &: ~:host_destination &: ~:read_reserved &: ~:route_edit &: ~:flushing &: ~:clear) in
  let grant_index=Variable.wire ~default:(zero (Config.log2 n)) in
  let grant_valid=Variable.wire ~default:gnd in
  let priorities=List.init n (fun start ->
    let rec choose offset = if offset=n then [] else
      let k=(start+offset) mod n in
      [if_ eligible.(k) [grant_index <--. k; grant_valid <--. 1] (choose (offset+1))] in
    of_int ~width:(Config.log2 n) start,choose 0) in
  compile [switch rr.value priorities];
  let grants=Array.init n (fun k -> grant_valid.value &: (grant_index.value ==:. k)) in
  let granted_dest=mux_arr grant_index.value (fun (v:Variable.t)->v.value) route_dest in
  let granted_data=mux_arr grant_index.value (fun (f:Processor_fifo.t)->f.data) rxs in
  Array.iteri (fun k _ ->
    let incoming=grant_valid.value &: (granted_dest ==:. k) in
    let from_host=host_tx &: selected_is k in
    tx_push.(k) <== (from_host |: incoming);
    tx_data.(k) <== mux2 from_host (uresize host.word width) granted_data;
    rx_pop.(k) <== ((host_rx &: selected_is k) |: grants.(k))) engines;
  let deliveries=Array.make n gnd in
  let pin_deliveries=Array.make n gnd in
  let control_updates=List.concat (List.init n (fun k ->
    let is_selected=selected_is k in
    let trigger=trigger_config.(k).value in
    let trigger_pin=select trigger 2 0 in
    let pin_now=mux trigger_pin (List.init 8 (bit synced_pins)) in
    let pin_before=mux trigger_pin (List.init 8 (bit previous_pins)) in
    let condition=mux (select trigger 4 3)
        [pin_now &: ~:pin_before; ~:pin_now &: pin_before; pin_now; ~:pin_now] in
    let external_event=bit trigger 5 &: condition &: ~:(command 11 &: is_selected) &: ~:clear in
    pin_deliveries.(k) <- external_event;
    let delivered= external_event |: (command 9 &: bit payload k) |:
      any (Array.to_list (Array.map (fun (e:Engine.t)->bit e.signal_events k) engines)) in
    deliveries.(k) <- delivered;
    [when_ (command 1 &: is_selected)
       [committed.(k) <--. 0; writing.(k) <--. 1; loaded.(k) <--. 0; lengths.(k) <--. 0];
     when_ (program_write &: is_selected) [loaded.(k) <-- loaded.(k).value +:. 1];
     when_ (command 2 &: is_selected)
       [committed.(k) <--. 1; writing.(k) <--. 0; lengths.(k) <-- select payload (iw-1) 0];
     when_ (command 3 &: is_selected) [owners.(k) <-- own; drains.(k) <-- drain];
     when_ (command 11 &: is_selected) [trigger_config.(k) <-- select payload 5 0];
     when_ engines.(k).consume_event [events.(k) <--. 0];
     when_ delivered [events.(k) <--. 1];
     when_ grants.(k) [route_count.(k) <-- route_count.(k).value -:. 1];
     when_ (command 6 &: (select payload 1 0 ==:. k))
       [route_dest.(k) <-- select payload 3 2;
        route_count.(k) <-- mux2 (bit payload 4) (select payload 20 5) (zero 16)];
     when_ (command 10 &: (is_selected |: (selected.value ==: route_dest.(k).value)))
       [route_count.(k) <--. 0]])) in
  compile ([timestamp <-- timestamp.value +:. 1;
            when_ (command_write &: ~:cmd_valid) [host_fault <--. 1];
            when_ (command 7 &: bit payload 23) [host_fault <--. 0];
            when_ (command 0) [selected <-- select payload 1 0];
            when_ (command 8) [read_select <-- select payload 2 0];
            when_ grant_valid.value [rr <-- grant_index.value +:. 1]] @ control_updates);
  let out=List.fold_left ( |: ) (zero 8) (List.init n (fun k ->
    let e=engines.(k) in
    e.pin_values &: owners.(k).value &: ~:(drains.(k).value)
    &: repeat (e.running &: (e.fault ==:. 0) &: ~:clear) 8)) in
  let oe=List.fold_left ( |: ) (zero 8) (List.init n (fun k ->
    let e=engines.(k) in
    let electrical_mask=(~:(drains.(k).value)) |: (~:(e.pin_values)) in
    e.pin_enables &: owners.(k).value &: electrical_mask
    &: repeat (e.running &: (e.fault ==:. 0) &: ~:clear) 8)) in
  let outputs=[output "uo_out" host.outputs; output "uio_out" out; output "uio_oe" oe] in
  let debug_outputs=if not debug then [] else (
    let packed name arr = output name (concat_lsb (Array.to_list arr)) in
    let variables name arr = packed name (Array.map (fun (v:Variable.t)->v.value) arr) in
    let engine_outputs name get = packed name (Array.map get engines) in
    let fifo_outputs name get arr = packed name (Array.map get arr) in
    [variables "dbg_ownership" owners; variables "dbg_open_drain" drains;
     variables "dbg_trigger_config" trigger_config; packed "dbg_trigger_event" pin_deliveries;
     output "dbg_synced_pins" synced_pins; output "dbg_previous_pins" previous_pins;
     variables "dbg_events" events; packed "dbg_event_set" deliveries;
     engine_outputs "dbg_event_clear" (fun (e:Engine.t)->e.consume_event);
     output "dbg_running" active_engine_mask; output "dbg_clear" clear;
     packed "dbg_start" starts; packed "dbg_stop" stops;
     packed "dbg_fifo_clear" flushes; packed "dbg_dma_grant" grants;
     packed "dbg_dma_eligible" eligible; output "dbg_dma_destination" granted_dest;
     output "dbg_dma_data" granted_data; output "dbg_round_robin" rr.value;
     variables "dbg_route_count" route_count; variables "dbg_route_destination" route_dest;
     packed "dbg_tx_push" tx_push; packed "dbg_tx_pop" tx_pop; packed "dbg_tx_data" tx_data;
     packed "dbg_rx_push" rx_push; packed "dbg_rx_pop" rx_pop; packed "dbg_rx_data" rx_data;
     fifo_outputs "dbg_tx_ready" (fun (f:Processor_fifo.t)->f.ready) txs;
     fifo_outputs "dbg_rx_valid" (fun (f:Processor_fifo.t)->f.valid) rxs;
     fifo_outputs "dbg_tx_level" (fun (f:Processor_fifo.t)->f.level) txs;
     fifo_outputs "dbg_rx_level" (fun (f:Processor_fifo.t)->f.level) rxs;
     fifo_outputs "dbg_rx_head" (fun (f:Processor_fifo.t)->f.data) rxs;
     output "dbg_host_tx" host_tx; output "dbg_host_rx" host_rx;
     output "dbg_host_rx_reserved" host.read_lock; output "dbg_host_selected" selected.value;
     output "dbg_command_accepted" accepted; output "dbg_command_code" code;
     output "dbg_command_payload" payload; variables "dbg_image_valid" committed;
     variables "dbg_image_length" lengths]) in
  Circuit.create_exn ~name:(if debug then "protocol_processor_debug" else "tt_um_protocol_processor")
    (outputs @ debug_outputs)

let create ?debug ?options config = create_with_memory ?debug ?options Baseline config
let create_refinement ?debug (config:Refinement_config.t) =
  create_with_memory ?debug ~options:config.options Ihp_pair config.architecture
let create_refinement_model ?debug (config:Refinement_config.t) =
  create_with_memory ?debug ~options:config.options Synchronous_model config.architecture
