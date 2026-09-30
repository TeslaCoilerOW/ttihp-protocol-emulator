open! Base
open Hardcaml
module Waveform = Hardcaml_waveterm.Waveform
module Display_rule = Hardcaml_waveterm.Display_rule

let engine_count = 4

module I = struct
  type 'a t =
    { clock : 'a
    ; rst_n : 'a
    ; ena : 'a
    ; window : 'a [@bits 2]
    ; read_ready : 'a
    ; write_valid : 'a
    ; write_nibble : 'a [@bits 4]
    ; uio_in : 'a array [@length 8]
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { fault : 'a
    ; irq : 'a
    ; read_valid : 'a
    ; write_ready : 'a
    ; read_nibble : 'a [@bits 4]
    ; uio_out : 'a array [@length 8]
    ; uio_oe : 'a array [@length 8]
    ; command_accepted : 'a
    ; command_code : 'a [@bits 8]
    ; image_valid : 'a array [@length engine_count]
    ; running : 'a array [@length engine_count]
    ; tx_level : 'a array [@length engine_count] [@bits 4]
    ; rx_level : 'a array [@length engine_count] [@bits 4]
    }
  [@@deriving hardcaml]
end

let find_port ports name =
  match
    List.find ports ~f:(fun port -> List.mem (Signal.names port) name ~equal:String.equal)
  with
  | Some port -> port
  | None -> raise_s [%message "the processor has no port" (name : string)]
;;

(* [Processor.create_refinement_model] names its own port wires (clk, rst_n, ena,
   ui_in, uio_in). They are ordinary undriven wires, so they can be driven from
   the typed interface; its outputs are split the same way. Only bit selects and
   concatenations are added, so the logic simulated is exactly the processor's.

   [Circuit.create_exn] renumbers the processor's signal uids from 1, and Cyclesim
   keys its storage on uids, so the interface signals must not reuse them. They
   are created after the processor, from the global uid generator, which has
   already passed every renumbered uid; [create_circuit] checks this. *)
let create_circuit processor (i : Signal.t I.t) : Signal.t O.t =
  let newest =
    Signal_graph.fold (Circuit.signal_graph processor) ~init:0 ~f:(fun acc signal ->
      max acc (Signal.Uid.to_int (Signal.uid signal)))
  in
  I.iter i ~f:(fun port ->
    if Signal.Uid.to_int (Signal.uid port) <= newest
    then raise_s [%message "interface uid overlaps the processor's" (port : Signal.t)]);
  let drive name value = Signal.(find_port (Circuit.inputs processor) name <== value) in
  drive "clk" i.clock;
  drive "rst_n" i.rst_n;
  drive "ena" i.ena;
  drive
    "ui_in"
    (Signal.concat_msb [ i.window; i.read_ready; i.write_valid; i.write_nibble ]);
  drive "uio_in" (Signal.concat_lsb (Array.to_list i.uio_in));
  let output = find_port (Circuit.outputs processor) in
  let bits name = Array.of_list (Signal.bits_lsb (output name)) in
  let per_engine name =
    let packed = output name in
    Signal.split_lsb ~part_width:(Signal.width packed / engine_count) packed
    |> Array.of_list
  in
  let uo = output "uo_out" in
  let o =
    { O.fault = Signal.bit uo 7
    ; irq = Signal.bit uo 6
    ; read_valid = Signal.bit uo 5
    ; write_ready = Signal.bit uo 4
    ; read_nibble = Signal.select uo 3 0
    ; uio_out = bits "uio_out"
    ; uio_oe = bits "uio_oe"
    ; command_accepted = output "dbg_command_accepted"
    ; command_code = output "dbg_command_code"
    ; image_valid = per_engine "dbg_image_valid"
    ; running = per_engine "dbg_running"
    ; tx_level = per_engine "dbg_tx_level"
    ; rx_level = per_engine "dbg_rx_level"
    }
  in
  O.Of_signal.assert_widths o;
  o
;;

module Sim = Cyclesim.With_interface (I) (O)

type t =
  { sim : Sim.t
  ; waves : Waveform.t
  ; mutable cycle : int
  ; mutable window : int
  ; mutable environment : int O.t -> int
  }

let cycle t =
  let inputs = Cyclesim.inputs t.sim in
  let pins = t.environment (O.map (Cyclesim.outputs t.sim) ~f:(fun r -> Bits.to_int !r)) in
  Array.iteri inputs.uio_in ~f:(fun k pin -> pin := Bits.of_bool (pins land (1 lsl k) <> 0));
  Cyclesim.cycle_before_clock_edge t.sim;
  let sample = O.map (Cyclesim.outputs ~clock_edge:Before t.sim) ~f:(fun r -> Bits.to_int !r) in
  Cyclesim.cycle_at_clock_edge t.sim;
  Cyclesim.cycle_after_clock_edge t.sim;
  t.cycle <- t.cycle + 1;
  sample
;;

let idle t n =
  for _ = 1 to n do
    ignore (cycle t : int O.t)
  done
;;

let create ?(environment = fun _ -> 0) () =
  let processor =
    Processor.create_refinement_model ~debug:true (Refinement_config.create Config.default)
  in
  let waves, sim = Waveform.create (Sim.create (create_circuit processor)) in
  let t = { sim; waves; cycle = 0; window = 0; environment } in
  let inputs = Cyclesim.inputs sim in
  inputs.ena := Bits.vdd;
  inputs.rst_n := Bits.gnd;
  idle t 1;
  inputs.rst_n := Bits.vdd;
  t
;;

let enter_window t window =
  if t.window <> window
  then (
    let inputs = Cyclesim.inputs t.sim in
    inputs.window := Bits.of_int ~width:2 window;
    t.window <- window;
    (* The cycle in which the window bits change is a bubble. *)
    idle t 1)
;;

let transfer t ~what ~ready =
  let rec loop budget =
    if budget = 0 then raise_s [%message "host port stalled" (what : string)];
    let sample = cycle t in
    if ready sample then sample else loop (budget - 1)
  in
  loop 1_000
;;

let write_word t ~window word =
  enter_window t window;
  let inputs = Cyclesim.inputs t.sim in
  inputs.write_valid := Bits.vdd;
  let last =
    List.fold (List.range 0 8) ~init:None ~f:(fun _ nibble ->
      inputs.write_nibble := Bits.of_int ~width:4 ((word lsr (4 * nibble)) land 0xf);
      Some (transfer t ~what:"write" ~ready:(fun o -> o.write_ready = 1)))
  in
  inputs.write_valid := Bits.gnd;
  inputs.write_nibble := Bits.zero 4;
  match last with
  | Some o -> o.command_accepted = 1
  | None -> false
;;

let read_word t ~window =
  enter_window t window;
  let inputs = Cyclesim.inputs t.sim in
  inputs.read_ready := Bits.vdd;
  let word =
    List.fold (List.range 0 8) ~init:0 ~f:(fun word nibble ->
      let o = transfer t ~what:"read" ~ready:(fun o -> o.read_valid = 1) in
      word lor (o.read_nibble lsl (4 * nibble)))
  in
  inputs.read_ready := Bits.gnd;
  word
;;

module Command = Host_command

let command t command payload =
  write_word t ~window:0 ((Command.to_int command lsl 24) lor payload)
;;

let command_exn t command payload =
  if not (write_word t ~window:0 ((Command.to_int command lsl 24) lor payload))
  then raise_s [%message "command rejected" (command : Command.t) (payload : int)]
;;

let read_status t index =
  command_exn t Read_select index;
  (* A status word is captured when window 0 is entered; re-enter it. *)
  enter_window t 1;
  read_word t ~window:0
;;

let instruction ?a ?b ?c ?imm ~owned op =
  Isa.encode Isa.flagship ~owned_pins:owned (Isa.instruction ?a ?b ?c ?imm op)
  |> Int32.to_int_exn
;;

let load t ~engine ~owned ?(open_drain = 0) words =
  command_exn t Select engine;
  command_exn t Begin 0;
  List.iter words ~f:(fun word -> ignore (write_word t ~window:1 word : bool));
  command_exn t Own (owned lor (open_drain lsl 8));
  command_exn t Commit (List.length words)
;;

let firmware ?half_period name =
  let source = Firmware.make ?half_period name in
  let image =
    Assembler.assemble
      ~source_bytes:(Yojson.Safe.to_string (Assembler.source_to_json source))
  in
  List.map image.Assembler.words ~f:Int32.to_int_exn, image.source.Assembler.owned_pins
;;

let bit name = name, Wave_format.Bit
let hex name = name, Wave_format.Hex
let unsigned name = name, Wave_format.Unsigned_int

let print ?(start_cycle = 0) ?(wave_width = 0) t signals =
  let signals = bit "clock" :: signals in
  let display_rules =
    List.map signals ~f:(fun (name, wave_format) ->
      Display_rule.port_name_is name ~wave_format)
  in
  let cycles = t.cycle - start_cycle in
  let wave_chars =
    if wave_width >= 0
    then cycles * 2 * (wave_width + 1)
    else (cycles + -wave_width - 1) / -wave_width
  in
  let signals_width =
    2 + List.fold signals ~init:0 ~f:(fun acc (name, _) -> max acc (String.length name))
  in
  let display_height =
    List.fold signals ~init:2 ~f:(fun acc (_, format) ->
      acc
      +
      match format with
      | Wave_format.Bit -> 2
      | _ -> 3)
  in
  Waveform.print
    ~display_rules
    ~display_width:(signals_width + wave_chars + 2)
    ~display_height
    ~signals_width
    ~wave_width
    ~start_cycle
    t.waves
;;
