(* RTL variant knobs (Variant_options, docs/variants.md).

   1. Configuration: the "options" object parses, validates and round-trips.
   2. FIFO: every depth and storage/reset form against a queue model.
   3. Engine: [Engine.next_pc] equals the PC after every edge for every reset
      style, PC width and shift form.
   4. Processor lockstep: each variant runs in Cyclesim next to the design of
      record with the same architecture and must match every output before and
      after every edge.  Reset styles are compared through their documented
      reset-timing transformation of rst_n/ena:
        sync_registered     reference rst_n(t) = (rst_n & ena)(t-2)
        async               reference rst_n(t) = (rst_n & ena)(t)
        async_sync_release  reference rst_n(t) = AND of (rst_n & ena)(t, t-1, t-2)
      Asynchronous resets are emulated exactly (Cyclesim samples reset only
      through [Cyclesim.reset]): the raw reset clears every register as soon as
      it is asserted and at every edge while asserted; for async_sync_release
      the two synchronizer flops keep clocking while the chip-wide net holds
      the other registers.  The first before-edge comparison of an
      asynchronous assertion is skipped, since there the reference still shows
      pre-reset register state by design.  ISA-changing variants are compared
      on traffic that avoids the changed behaviour (READ_SELECT 3/5/7,
      non-byte-lane shift counts); FIFO storage words are compared only where
      they are observable (valid entries, granted or pushed words).
   5. Directed ISA checks of the changed behaviour: READ_SELECT 7, READ_SELECT 5,
      saturating PC readback, byte-lane shift results and faults, FIFO depth.

   Usage: variant_test.exe                      quick suite (dune test)
          variant_test.exe --lockstep NAME --seed N --ops K
          variant_test.exe --dump NAME --seed N --ops K FILE   (lockstep, and
            write the per-cycle stimulus for a Verilog replay of the cores)
          variant_test.exe --list *)
module Processor_fifo = Fifo
open Hardcaml
module O = Variant_options

let check label condition = if not condition then failwith label
let instruction op immediate = (op lsl 24) lor (immediate land 0xffffff)
let op3 op a b c = (op lsl 24) lor (a lsl 16) lor (b lsl 8) lor c
let input = Signal.input
let output = Signal.output

(* ------------------------------------------------------------------ *)
(* Variants                                                            *)

let cn = {O.default with reset=O.Async; fifo_storage_reset=true; narrow_image_regs=true}
let cn_s2 = {cn with reset=O.Async_sync_release}
let diet = {cn_s2 with debug_counters=false; pc_bits=O.Saturating_7; shift=O.Byte_lane}
let named = [
  "base",8,O.default; "rstreg",8,{O.default with reset=O.Sync_registered};
  "cn",8,cn; "cn_s2",8,cn_s2; "diet4",4,diet; "diet2",2,diet]
let extra = [
  "async",8,{O.default with reset=O.Async};
  "async_sync_release",8,{O.default with reset=O.Async_sync_release};
  "storage_reset_sync",8,{O.default with fifo_storage_reset=true};
  "storage_reset_rstreg",4,{O.default with reset=O.Sync_registered; fifo_storage_reset=true};
  "async_memory_fifo4",4,{O.default with reset=O.Async};
  "narrow_image",8,{O.default with narrow_image_regs=true};
  "no_counters",8,{O.default with debug_counters=false};
  "pc7",8,{O.default with pc_bits=O.Saturating_7};
  "byte_lane",8,{O.default with shift=O.Byte_lane};
  "base_fifo4",4,O.default; "base_fifo2",2,O.default;
  "diet8",8,diet; "diet4_sync",4,{diet with reset=O.Sync};
  "diet4_rstreg",4,{diet with reset=O.Sync_registered}]
let variants = named @ extra
let find_variant name =
  match List.find_opt (fun (n,_,_) -> n=name) variants with
  | Some (_,fifo_words,options) -> fifo_words,options
  | None -> invalid_arg ("unknown variant "^name)

(* ------------------------------------------------------------------ *)
(* 1. Configuration                                                    *)

let architecture_json (c:Config.t) = `Assoc [
  "schema_version",`String "protocol-emulator.architecture.v1";
  "engine_count",`Int c.engine_count;"data_width",`Int c.data_width;
  "program_words",`Int c.program_words;"fifo_words",`Int c.fifo_words;
  "issue",`String c.issue;"prefetch",`Bool c.prefetch]
let refinement_json ?options architecture = `Assoc ([
  "schema_version",`String "protocol-emulator.refinement.v1";
  "architecture",architecture;"implementation",`String Refinement_config.implementation]
  @ (match options with None -> [] | Some o -> ["options",o]))
let rejected what json = match Refinement_config.of_json json with
  | _ -> failwith ("accepted "^what)
  | exception Invalid_argument _ -> ()

let all_options =
  List.concat_map (fun reset -> List.concat_map (fun fifo_storage_reset ->
    List.concat_map (fun narrow_image_regs -> List.concat_map (fun debug_counters ->
      List.concat_map (fun pc_bits -> List.map (fun shift ->
        {O.reset;fifo_storage_reset;narrow_image_regs;debug_counters;pc_bits;shift})
        [O.Barrel;O.Byte_lane]) [O.Full;O.Saturating_7]) [true;false]) [false;true]) [false;true])
    [O.Sync;O.Sync_registered;O.Async;O.Async_sync_release]

let configuration_test () =
  let arch=architecture_json Config.default in
  check "absent options select the design of record"
    (O.is_default (Refinement_config.of_json (refinement_json arch)).options);
  check "empty options select the design of record"
    (O.is_default (Refinement_config.of_json (refinement_json ~options:(`Assoc []) arch)).options);
  check "128 option combinations" (List.length all_options=128);
  List.iter (fun o ->
    check "options JSON round trip" (O.of_json (O.to_json o)=o);
    check "refinement carries options"
      ((Refinement_config.of_json (refinement_json ~options:(O.to_json o) arch)).options=o);
    check "ISA version rule"
      (O.isa_version o=(if o.debug_counters && o.pc_bits=O.Full && o.shift=O.Barrel then 2 else 3)))
    all_options;
  List.iter (fun (what,options) -> rejected what (refinement_json ~options arch)) [
    "unknown option",`Assoc ["surprise",`Bool true];
    "duplicate option",`Assoc ["reset",`String "sync";"reset",`String "async"];
    "bad reset style",`Assoc ["reset",`String "asynchronous"];
    "reset style type",`Assoc ["reset",`Bool true];
    "boolean type",`Assoc ["fifo_storage_reset",`String "true"];
    "pc_bits value",`Assoc ["pc_bits",`String "saturating_8"];
    "shift value",`Assoc ["shift",`String "none"];
    "null options",`Null;"list options",`List []];
  (match refinement_json ~options:(`Assoc []) arch with
   | `Assoc fields -> rejected "duplicate options object" (`Assoc (("options",`Assoc [])::fields))
   | _ -> assert false);
  List.iter (fun fifo_words ->
    let r=Refinement_config.of_json (refinement_json (architecture_json {Config.default with fifo_words})) in
    check "FIFO depth accepted" (r.architecture.fifo_words=fifo_words)) [2;4;8;32];
  List.iter (fun fifo_words ->
    rejected "FIFO depth" (refinement_json (architecture_json {Config.default with fifo_words})))
    [1;3;16;64];
  (match Processor.create ~options:{O.default with pc_bits=O.Saturating_7}
          {Config.default with program_words=128} with
   | _ -> failwith "accepted a saturating 7-bit PC with 128-word images"
   | exception Invalid_argument _ -> ());
  ignore (Processor.create ~options:{O.default with pc_bits=O.Saturating_7}
            {Config.default with program_words=32});
  List.iter (fun (name,_,o) ->
    let expected=if List.mem name ["diet4";"diet2"] then 3 else 2 in
    check ("ISA version of "^name) (O.isa_version o=expected)) named

(* ------------------------------------------------------------------ *)
(* 2. FIFO against a queue model                                        *)

type fifo_kind = Memory_sync | Registers_sync | Memory_async | Registers_async

let fifo_test ~depth kind =
  let width=32 in
  let clock=input "clk" 1 and reset=input "reset" 1 and flush=input "flush" 1 in
  let push=input "push" 1 and pop=input "pop" 1 and data=input "data" width in
  let async=(kind=Memory_async || kind=Registers_async) in
  let storage=match kind with
    | Memory_sync | Memory_async -> Processor_fifo.Memory
    | Registers_sync -> Processor_fifo.Registers_sync_clear reset
    | Registers_async -> Processor_fifo.Registers_async_reset reset in
  let f=if async
    then Processor_fifo.create_with ~async_reset:(Some reset) ~storage ~clock ~clear:flush
        ~width ~depth ~push ~pop ~data
    else Processor_fifo.create_with ~async_reset:None ~storage ~clock
        ~clear:Signal.(reset |: flush) ~width ~depth ~push ~pop ~data in
  let sim=Cyclesim.create (Circuit.create_exn ~name:"variant_fifo"
    [output "ready" f.ready;output "valid" f.valid;output "data_out" f.data;output "level" f.level]) in
  let set name w v=Cyclesim.in_port sim name := Bits.of_int ~width:w v in
  let get name=Bits.to_int !(Cyclesim.out_port ~clock_edge:Before sim name) in
  let queue=Queue.create () in
  let st=Random.State.make [|depth;Hashtbl.hash kind|] in
  for cycle=0 to 19_999 do
    let r=if Random.State.int st 97=0 then 1 else 0 in
    let fl=if Random.State.int st 61=0 then 1 else 0 in
    let pu=Random.State.int st 2 and po=Random.State.int st 2 in
    let d=Random.State.bits st lor ((Random.State.int st 4) lsl 30) in
    set "reset" 1 r;set "flush" 1 fl;set "push" 1 pu;set "pop" 1 po;set "data" width d;
    if async && r=1 then (Cyclesim.reset sim;Queue.clear queue);
    Cyclesim.cycle_before_clock_edge sim;
    let level=Queue.length queue in
    let where=Printf.sprintf "FIFO depth %d cycle %d" depth cycle in
    check (where^" level") (get "level"=level);
    check (where^" ready") (get "ready"=(if level<>depth then 1 else 0));
    check (where^" valid") (get "valid"=(if level<>0 then 1 else 0));
    if level<>0 then check (where^" head") (get "data_out"=Queue.peek queue);
    Cyclesim.cycle_at_clock_edge sim;
    if async && r=1 then Cyclesim.reset sim;
    Cyclesim.cycle_after_clock_edge sim;
    if r=1 || fl=1 then Queue.clear queue
    else begin
      if po=1 && level<>0 then ignore (Queue.pop queue);
      if pu=1 && level<>depth then Queue.push d queue
    end
  done

(* ------------------------------------------------------------------ *)
(* 3. Engine next_pc                                                    *)

let next_pc_test ~options ~width =
  let config={Config.default with data_width=width} in
  let e=Engine.create ~options config {
    clock=input "clk" 1;clear=input "clear" 1;start=input "start" 1;
    stop=input "stop" 1;clear_fault=input "clear_fault" 1;
    instruction=input "instruction" 32;image_length=input "image_length" 24;
    ownership=input "ownership" 8;pins=input "pins" 8;timestamp=input "timestamp" 32;
    tx_valid=input "tx_valid" 1;tx_data=input "tx_data" width;
    rx_ready=input "rx_ready" 1;event=input "event_pending" 1} in
  let sim=Cyclesim.create (Circuit.create_exn ~name:"variant_next_pc"
    [output "pc" e.pc;output "next_pc" (Engine.next_pc e);output "issue" e.issue]) in
  let set name w v=Cyclesim.in_port sim name := Bits.of_int ~width:w v in
  let async=O.asynchronous options in
  let words=[|0x00000000;0x04000002;0x05000007;0x06000000;0x07000000;0x07010000;
    0x0a000001;0x0b000003;0x0d000000;0x0c000003;0x0f000000;0x0e00000f;
    0x10000000;0x11010200;0x13000000;0x1a000002;0x1d000008;0x01000000;
    0xff000000;0x05ffffff;0x0500007f;0x05000080;0x05000040;0x1a00ffff;
    0x0b00012c;0x0a000002;0x18000008;0x18000018;0x19000010;0x18000001;0x19000005|] in
  let st=Random.State.make [|0x5a17;width|] in
  for cycle=0 to 3999 do
    let clear=if cycle mod 97=0 then 1 else 0 in
    set "clear" 1 clear;
    set "start" 1 (if cycle mod 17=1 then 1 else 0);
    set "stop" 1 (if cycle mod 53=2 then 1 else 0);
    set "clear_fault" 1 (if cycle mod 79=2 then 1 else 0);
    set "instruction" 32 words.(Random.State.int st (Array.length words));
    set "image_length" 24 (if cycle mod 41=0 then 0 else 64);
    set "ownership" 8 255;set "pins" 8 (cycle land 255);set "timestamp" 32 cycle;
    set "tx_valid" 1 (cycle mod 2);set "tx_data" width cycle;
    set "rx_ready" 1 (cycle / 2 mod 2);set "event_pending" 1 (if cycle mod 7=0 then 1 else 0);
    if async && clear=1 then Cyclesim.reset sim;
    Cyclesim.cycle_before_clock_edge sim;
    let predicted=Bits.to_int !(Cyclesim.out_port ~clock_edge:Before sim "next_pc") in
    Cyclesim.cycle_at_clock_edge sim;
    if async && clear=1 then Cyclesim.reset sim;
    Cyclesim.cycle_after_clock_edge sim;
    let pc=Bits.to_int !(Cyclesim.out_port sim "pc") in
    check (Printf.sprintf "next_pc equals the PC after edge %d" cycle) (predicted=pc);
    if options.pc_bits=O.Saturating_7 then check "7-bit PC" (pc<128)
  done

(* ------------------------------------------------------------------ *)
(* 4. Processor harness (lockstep or single)                            *)

type harness = {
  dut : Cyclesim.t_port_list;
  reference : Cyclesim.t_port_list option;
  options : O.t;
  reference_style : O.reset;         (* reset transformation applied to the reference *)
  emulate : bool;                    (* emulate asynchronous resets in Cyclesim *)
  label : string;
  mutable rst_n : int; mutable ena : int; mutable uio : int;
  mutable history : int * int;       (* (rst_n & ena) at the previous two edges *)
  mutable raw_previous : int;        (* raw reset asserted in the previous cycle *)
  mutable cycle : int;
  mutable compared : int;
  coverage : int array;              (* running, grants, accepted, fault, reset cycles *)
  mutable trace : Buffer.t option;   (* per-cycle stimulus for a Verilog replay *)
}

let make_harness ?(lockstep=true) ~label ~fifo_words options =
  let arch={Config.default with fifo_words} in
  let config=if options.O.reset=O.Async_sync_release then Cyclesim.Config.trace_all
    else Cyclesim.Config.default in
  let sim options=Cyclesim.create ~config
      (Processor.create_refinement_model ~debug:true (Refinement_config.create ~options arch)) in
  {dut=sim options;reference=(if lockstep then Some (sim O.default) else None);options;label;
   reference_style=options.reset;emulate=true;coverage=Array.make 5 0;trace=None;
   rst_n=0;ena=1;uio=0;history=(0,0);raw_previous=1;cycle=0;compared=0}

let port ports name = match List.assoc_opt name ports with
  | Some v -> !v | None -> failwith ("missing port "^name)

(* Zero the parts of FIFO storage that are not architecturally observable. *)
let observable ports name value =
  let lanes flags lane_width =
    let flags=Bits.to_int (port ports flags) in
    Bits.concat_lsb (List.init (Bits.width value / lane_width) (fun k ->
      let lane=Bits.select value (lane_width*(k+1)-1) (lane_width*k) in
      if (flags lsr k) land 1=1 then lane else Bits.zero lane_width)) in
  match name with
  | "dbg_rx_head" -> lanes "dbg_rx_valid" (Bits.width value / 4)
  | "dbg_tx_data" -> lanes "dbg_tx_push" (Bits.width value / 4)
  | "dbg_dma_data" -> if Bits.to_int (port ports "dbg_dma_grant")=0 then Bits.zero (Bits.width value) else value
  | _ -> value

let same name a b =
  if Bits.width a=Bits.width b then Bits.equal a b
  else if name="dbg_image_length" then
    (* Narrow image registers: compare the four engines' lengths numerically. *)
    let wa=Bits.width a / 4 and wb=Bits.width b / 4 in
    List.for_all (fun k ->
      Bits.to_int (Bits.select a (wa*(k+1)-1) (wa*k))=Bits.to_int (Bits.select b (wb*(k+1)-1) (wb*k)))
      [0;1;2;3]
  else false

let compare h edge =
  match h.reference with
  | None -> ()
  | Some reference ->
    let dut_ports=Cyclesim.out_ports ~clock_edge:edge h.dut in
    let ref_ports=Cyclesim.out_ports ~clock_edge:edge reference in
    List.iter (fun (name,value) ->
      let r=observable ref_ports name !value and d=observable dut_ports name (port dut_ports name) in
      if not (same name d r) then
        failwith (Printf.sprintf "%s: differs from the design of record at cycle %d (%s edge), port %s: variant %s, reference %s"
          h.label h.cycle (match edge with Before -> "before" | After -> "after")
          name (Bits.to_string d) (Bits.to_string r)))
      ref_ports;
    h.compared <- h.compared+1

let reg_value sim name = match Cyclesim.lookup_reg_by_name sim name with
  | Some r -> Cyclesim.Reg.to_int r | None -> failwith ("no traced register "^name)
let set_reg sim name value = match Cyclesim.lookup_reg_by_name sim name with
  | Some r -> Cyclesim.Reg.of_int r value | None -> failwith ("no traced register "^name)

(* The design of record's rst_n/ena that reproduce the variant's reset timing,
   from the current (rst_n & ena) and the values at the previous two edges.
   The variant's registered reset net changes right after an edge, whereas the
   reference's reset input can only change with the next cycle's inputs
   (Cyclesim samples inputs before the edge); the after-edge comparison of an
   edge where the reference reset input changes is therefore skipped, and the
   state is compared again before the next edge. *)
let reference_reset h =
  let raw_n=h.rst_n land h.ena and h1,h2=h.history in
  match h.reference_style with
  | O.Sync -> h.rst_n,h.ena
  | O.Sync_registered -> h2,1
  | O.Async -> raw_n,1
  | O.Async_sync_release -> raw_n land h1 land h2,1

let tick h ui =
  let raw_n=h.rst_n land h.ena in
  let raw=1-raw_n in
  let set sim name w v=Cyclesim.in_port sim name := Bits.of_int ~width:w v in
  let set_reference_reset () =
    let rst_n,ena=reference_reset h in
    Option.iter (fun r -> set r "rst_n" 1 rst_n;set r "ena" 1 ena) h.reference in
  set h.dut "ui_in" 8 ui;set h.dut "uio_in" 8 h.uio;set h.dut "rst_n" 1 h.rst_n;set h.dut "ena" 1 h.ena;
  Option.iter (fun r -> set r "ui_in" 8 ui;set r "uio_in" 8 h.uio) h.reference;
  let reference_now=reference_reset h in
  set_reference_reset ();
  let async=h.emulate && O.asynchronous h.options in
  if async && raw=1 then Cyclesim.reset h.dut;
  Cyclesim.cycle_before_clock_edge h.dut;
  Option.iter Cyclesim.cycle_before_clock_edge h.reference;
  if not (async && raw=1 && h.raw_previous=0) then compare h Before;
  let before=Bits.to_int !(Cyclesim.out_port ~clock_edge:Before h.dut "uo_out") in
  let seen name=Bits.to_int !(Cyclesim.out_port ~clock_edge:Before h.dut name)<>0 in
  List.iteri (fun k hit -> if hit then h.coverage.(k) <- h.coverage.(k)+1)
    [seen "dbg_running";seen "dbg_dma_grant";seen "dbg_command_accepted";before land 128<>0;raw=1];
  let held=h.emulate && (match h.options.reset with
    | O.Async_sync_release -> raw=1 || reg_value h.dut "reset_sync_2"=0
    | O.Async -> raw=1
    | O.Sync | O.Sync_registered -> false) in
  Cyclesim.cycle_at_clock_edge h.dut;
  Option.iter Cyclesim.cycle_at_clock_edge h.reference;
  if held then begin
    if raw=1 then Cyclesim.reset h.dut
    else begin
      let s1=reg_value h.dut "reset_sync_1" and s2=reg_value h.dut "reset_sync_2" in
      Cyclesim.reset h.dut;set_reg h.dut "reset_sync_1" s1;set_reg h.dut "reset_sync_2" s2
    end
  end;
  h.history <- (raw_n,fst h.history);
  Cyclesim.cycle_after_clock_edge h.dut;
  Option.iter Cyclesim.cycle_after_clock_edge h.reference;
  let after_compared=reference_reset h=reference_now in
  if after_compared then compare h After;
  Option.iter (fun b ->
    (* ui uio rst_n ena reference_rst_n reference_ena compare_before compare_after *)
    Printf.bprintf b "%02x %02x %d %d %d %d %d %d\n" ui h.uio h.rst_n h.ena
      (fst reference_now) (snd reference_now)
      (if async && raw=1 && h.raw_previous=0 then 0 else 1) (if after_compared then 1 else 0))
    h.trace;
  h.raw_previous <- raw;h.cycle <- h.cycle+1;
  before

let get h name=Bits.to_int !(Cyclesim.out_port h.dut name)
let idle h count=for _=1 to count do ignore (tick h 0) done
let enter h window=ignore (tick h (window lsl 6));ignore (tick h (window lsl 6))

(* Write one word; [None] budget fails the test when a nibble is refused for
   100 cycles, [Some n] abandons the word instead (returns false). *)
let write ?budget h window word =
  enter h window;
  let rec nibbles n =
    if n=8 then true else begin
      let ui=(window lsl 6) lor 16 lor ((word lsr (4*n)) land 15) in
      let rec accept left =
        if left=0 then false
        else if tick h ui land 16<>0 then true else accept (left-1) in
      if accept (match budget with Some b -> b | None -> 100) then nibbles (n+1)
      else (check (h.label^": host write bounded") (budget<>None);false)
    end in
  let ok=nibbles 0 in
  if ok then ignore (tick h (window lsl 6)) else enter h ((window+1) land 3);
  ok
let command h op payload=ignore (write h 0 (instruction op payload))
let load h engine owner words =
  command h 0 engine;command h 1 0;command h 3 owner;
  List.iter (fun w -> ignore (write h 1 w)) words;command h 2 (List.length words)
let read ?budget h window =
  enter h window;
  let value=ref 0 in
  let rec nibbles n =
    if n=8 then true else begin
      let rec take left =
        if left=0 then false else begin
          let sample=tick h ((window lsl 6) lor 32) in
          if sample land 32=0 then take (left-1)
          else (value:= !value lor ((sample land 15) lsl (4*n));true) end in
      if take (match budget with Some b -> b | None -> 100) then nibbles (n+1)
      else (check (h.label^": host read bounded") (budget<>None);false)
    end in
  if nibbles 0 then Some !value else None
let read_status h =
  enter h 1;
  match read h 0 with Some v -> v | None -> failwith "status read"
let status h selection=command h 8 selection;read_status h
let reset h cycles=h.rst_n <- 0;idle h cycles;h.rst_n <- 1;idle h 4

(* ------------------------------------------------------------------ *)
(* Directed traffic shared by every variant (no READ_SELECT 3/5/7, only
   byte-lane shift counts, targets below 128).                          *)

let directed h ~fifo_words =
  reset h 3;
  load h 0 3 [instruction 2 1;instruction 3 3;instruction 6 0;
    instruction 18 (1 lsl 16);instruction 7 0;instruction 10 2;
    instruction 4 2;instruction 11 6;instruction 19 ((2 lsl 16) lor 0x5a);
    op3 24 2 0 8;op3 25 2 0 16;op3 24 1 0 24;
    instruction 26 ((2 lsl 16) lor 14);instruction 29 99;instruction 12 255;
    instruction 13 ((7 lsl 16) lor (1 lsl 8));instruction 15 0;
    instruction 2 0;instruction 16 ((1 lsl 3) lor (7 lsl 6));
    instruction 17 ((4 lsl 16) lor (2 lsl 8) lor 24);
    instruction 7 0;instruction 1 0];
  load h 1 4 [instruction 6 0;instruction 18 (1 lsl 16);instruction 7 0;
    instruction 2 4;instruction 3 4;instruction 15 0;instruction 1 0];
  load h 2 8 [instruction 3 8;instruction 2 8;instruction 4 1;
    instruction 2 0;instruction 4 2;instruction 5 1];
  load h 3 16 (List.init 64 (fun index ->
    if index=0 then instruction 5 63 else if index=63 then instruction 1 0 else instruction 29 77));
  command h 6 ((1 lsl 2) lor (1 lsl 4) lor (1 lsl 5));command h 4 15;idle h 20;
  command h 0 0;ignore (write h 2 0xa5);idle h 15;
  h.uio <- 128;idle h 5;command h 9 3;idle h 60;
  ignore (status h 0);ignore (status h 1);ignore (status h 2);ignore (status h 4);ignore (status h 6);
  (* Engine 3 reloads while the pin generator keeps running; out-of-image PC. *)
  load h 3 16 [instruction 19 ((1 lsl 16) lor 0x55aa);instruction 7 0;instruction 5 3];
  command h 4 8;idle h 15;command h 7 8;
  (* Strict overflow fills the RX queue of engine 3. *)
  load h 3 16 [instruction 19 ((1 lsl 16) lor 0x1234);instruction 7 (1 lsl 16);instruction 5 1];
  command h 4 8;idle h (fifo_words*3+10);
  command h 0 3;
  for _=1 to fifo_words+1 do ignore (read ~budget:40 h 3) done;
  command h 7 8;command h 10 0;
  (* Reset in the middle of running engines and partial host words. *)
  command h 4 7;idle h 7;
  enter h 2;ignore (tick h ((2 lsl 6) lor 16 lor 5));
  h.rst_n <- 0;ignore (tick h 0);h.rst_n <- 1;idle h 6;
  load h 0 1 [instruction 2 1;instruction 3 1;instruction 5 2];command h 4 1;idle h 5;
  check (h.label^": fresh load after reset drives its pin") (get h "uio_oe"=1 && get h "uio_out"=1);
  h.ena <- 0;idle h 2;h.ena <- 1;idle h 4;
  check (h.label^": deselection releases outputs") (get h "uio_oe"=0 && get h "dbg_running"=0);
  (* TX fill to the configured depth. *)
  command h 0 1;
  for k=1 to fifo_words do check (h.label^": TX accepts its depth") (write ~budget:20 h 2 k) done;
  check (h.label^": TX full refuses") (not (write ~budget:20 h 2 99));
  check (h.label^": TX level readback") (status h 2 land 0xffff=fifo_words);
  command h 10 0;
  check (h.label^": FLUSH empties") (status h 2=0)

(* ------------------------------------------------------------------ *)
(* Random traffic                                                       *)

let random_instruction st ~isa_changed ~image =
  let r n=Random.State.int st n in
  let small_target ()=if r 8=0 then (if r 2=0 then 64+r 64 else 128+r 70000) else r (image+2) in
  let shift_count ()=if isa_changed then 8*r 4 else if r 10=0 then 32+r 8 else r 32 in
  match r 34 with
  | 0 -> instruction 0 0 | 1 -> instruction 1 0
  | 2 -> instruction 2 (r 256) | 3 -> instruction 3 (r 256)
  | 4 -> instruction 4 (r 12) | 5 -> instruction 5 (small_target ())
  | 6 -> instruction 6 0 | 7 -> op3 7 (r 2) 0 0
  | 8 -> op3 8 (r 8) 0 (r 2) | 9 -> op3 9 (r 8) 0 (r 2)
  | 10 -> instruction 10 (r 4) | 11 -> instruction 11 (small_target ())
  | 12 -> instruction 12 (1+r 40) | 13 -> op3 13 (r 8) (r 2) 0
  | 14 -> instruction 14 (r 16) | 15 -> instruction 15 0
  | 16 -> instruction 16 (r 512) | 17 -> op3 17 (1+r 8) (1+r 3) (r 32)
  | 18|20|21|22|23 as op -> op3 op (r 4) (r 4) 0
  | 19 -> instruction 19 (((r 4) lsl 16) lor r 65536)
  | 24|25 as op -> op3 op (r 4) 0 (shift_count ())
  | 26 -> instruction 26 (((r 4) lsl 16) lor (small_target () land 0xffff))
  | 27 -> op3 27 (r 4) 0 0 | 28 -> op3 28 (r 4) 0 0
  | 29 -> instruction 29 (1+r 255)
  | 30 -> instruction 7 0
  | 31 -> instruction 6 0
  | 32 -> op3 (30+r 226) (r 4) (r 4) (r 4)
  | _ -> instruction 4 (r 4)

(* A mostly legal instruction for an engine that owns [owned] (a nonempty
   pin mask): operands in range, owned pins for outputs, branch targets inside
   the image, so engines keep running and exercise queues, events and XFER. *)
let legal_instruction st ~isa_changed ~owned ~len =
  let r n=Random.State.int st n in
  let owned_pins=List.filter (fun p -> (owned lsr p) land 1=1) [0;1;2;3;4;5;6;7] in
  let any_owned ()=List.nth owned_pins (r (List.length owned_pins)) in
  let subset ()=owned land r 256 in
  let target ()=r len in
  let reg ()=r 4 in
  match r 30 with
  | 0 -> instruction 2 (subset ()) | 1 -> instruction 3 (subset ())
  | 2 -> instruction 4 (r 6) | 3 -> instruction 6 0
  | 4 -> op3 7 (if r 4=0 then 1 else 0) 0 0 | 5 -> op3 8 (any_owned ()) 0 (r 2)
  | 6 -> op3 9 (r 8) 0 (r 2) | 7 -> instruction 10 (r 3)
  | 8 -> instruction 11 (target ()) | 9 -> instruction 12 (1+r 60)
  | 10 -> op3 13 (r 8) (r 2) 0 | 11 -> instruction 14 (r 16)
  | 12 -> instruction 15 0
  | 13 -> let ck=any_owned () and out=any_owned () in
    instruction 16 (ck lor (out lsl 3) lor ((r 8) lsl 6))
  | 14 -> op3 17 (1+r 8) (1+r 3) ((r 3) lor ((r 2) lsl 2) lor ((r 2) lsl 4) lor (if r 2=0 then 8 else 0))
  | 15|16 -> op3 (List.nth [18;20;21;22;23] (r 5)) (reg ()) (reg ()) 0
  | 17 -> instruction 19 (((reg ()) lsl 16) lor r 65536)
  | 18|19 -> op3 (24+r 2) (reg ()) 0 (if isa_changed then 8*r 4 else r 32)
  | 20 -> instruction 26 (((reg ()) lsl 16) lor target ())
  | 21 -> op3 (27+r 2) (reg ()) 0 0
  | 22 -> instruction 5 (target ())
  | 23 -> if r 6=0 then instruction 1 0 else instruction 0 0
  | 24 -> if r 10=0 then instruction 29 (1+r 255) else instruction 4 (r 3)
  | _ -> op3 18 (reg ()) (reg ()) 0

(* Load all four engines with legal programs on disjoint pin pairs, configure
   routes between them and start everything. *)
let busy_scenario h st ~isa_changed =
  let r n=Random.State.int st n in
  command h 5 15;command h 7 (15 lor (1 lsl 23));
  for engine=0 to 3 do
    let owned=3 lsl (2*engine) and len=3+r 14 in
    let body=List.init (len-1) (fun _ -> legal_instruction st ~isa_changed ~owned ~len) in
    load h engine owned (body @ [instruction 5 (r (len-1))])
  done;
  for source=0 to 3 do
    if r 2=0 then command h 6 (source lor ((r 4) lsl 2) lor (1 lsl 4) lor ((1+r 20) lsl 5))
  done;
  for engine=0 to 3 do
    if r 2=0 then (command h 0 engine;
                   for _=1 to r 4 do ignore (write ~budget:10 h 2 (Random.State.bits st)) done)
  done;
  command h 4 15

let random_traffic h ~seed ~ops =
  let st=Random.State.make [|seed;Hashtbl.hash h.label|] in
  let r n=Random.State.int st n in
  let isa_changed=O.isa_changed h.options in
  for _=1 to ops do
    (match r 24 with
     | 0|1 ->
       let engine=r 4 and len=1+r 12 in
       let owner=if r 6=0 then r 256 else 3 lsl (2*engine) in
       load h engine owner (List.init len (fun _ -> random_instruction st ~isa_changed ~image:len))
     | 2 ->
       let engine=r 4 and len=2+r 14 in
       let owned=3 lsl (2*engine) in
       load h engine owned (List.init len (fun _ -> legal_instruction st ~isa_changed ~owned ~len));
       command h 4 (1 lsl engine)
     | 20|21 -> busy_scenario h st ~isa_changed
     | 22 -> command h 5 15;command h 7 (15 lor (1 lsl 23));command h 4 (r 16)
     | 3 -> command h 4 (if r 8=0 then r 32 else 1+r 15)
     | 4 -> command h 5 (r 16)
     | 5 -> command h 7 ((r 16) lor (if r 3=0 then 1 lsl 23 else 0))
     | 6 -> command h 9 (r 16)
     | 7 -> command h 6 ((r 4) lor ((r 4) lsl 2) lor ((r 2) lsl 4) lor ((r 6) lsl 5))
     | 8 -> command h 0 (r 4);command h 10 (if r 8=0 then 1 else 0)
     | 9 -> command h 0 (r 4);command h 11 (if r 8=0 then r 256 else r 64)
     | 10 ->
       let allowed=if isa_changed then [0;1;2;4;6] else [0;1;2;3;4;5;6;7] in
       ignore (status h (List.nth allowed (r (List.length allowed))))
     | 11 -> command h 0 (r 4);ignore (write ~budget:(1+r 30) h 2 (Random.State.bits st))
     | 12 -> command h 0 (r 4);ignore (read ~budget:(1+r 30) h 3)
     | 13 -> idle h (1+r 60)
     | 14 -> h.uio <- r 256;idle h (r 3)
     | 15 ->
       (* Abandoned partial word, then a window change. *)
       let window=r 4 in
       enter h window;
       for _=1 to 1+r 6 do ignore (tick h ((window lsl 6) lor 16 lor 32 lor r 16)) done;
       enter h ((window+1+r 3) land 3)
     | 16 -> if r 4=0 then (h.rst_n <- 0;idle h (1+r 3);h.rst_n <- 1;idle h (r 4))
             else if r 3=0 then (h.ena <- 0;idle h (1+r 2);h.ena <- 1;idle h (r 4))
             else idle h 2
     | 17 -> ignore (write h 0 (((12+r 244) lsl 24) lor r 0x1000000))
     | 18 -> command h 8 (if isa_changed then List.nth [0;1;2;4;6] (r 5) else r 10)
     | _ ->
       (* Raw host-port noise, then two real window changes discard any
          partial word (entering the current window would not). *)
       for _=1 to 1+r 20 do ignore (tick h ((r 4) lsl 6 lor r 64)) done;enter h 2;enter h 0)
  done

(* ------------------------------------------------------------------ *)
(* 5. Directed ISA checks of changed behaviour                          *)

let isa_checks ~label ~fifo_words options =
  let h=make_harness ~lockstep:false ~label ~fifo_words options in
  reset h 3;
  let fault_code ()=(status h 0 lsr 8) land 255 in
  check (label^": READ_SELECT 7") (status h 7=O.isa_version options);
  (* Byte-lane shift results (identical in both shift forms). *)
  command h 0 0;
  load h 0 0 [instruction 19 ((2 lsl 16) lor 0x1234);op3 24 2 0 8;op3 18 1 2 0;instruction 7 0;
              op3 24 2 0 16;op3 18 1 2 0;instruction 7 0;op3 25 2 0 24;op3 18 1 2 0;instruction 7 0;
              op3 24 2 0 0;op3 18 1 2 0;op3 25 1 0 8;instruction 7 0;instruction 1 0];
  command h 4 1;idle h 30;
  command h 0 0;
  let expected=[0x123400;0x34000000;0x34;0] in
  let got=List.init (min fifo_words 4) (fun _ -> match read ~budget:30 h 3 with Some v -> v | None -> -1) in
  check (label^": byte-lane shift results") (got=List.filteri (fun i _ -> i<fifo_words) expected);
  check (label^": no fault after legal shifts") (fault_code ()=0);
  (* Completed-instruction counter. *)
  let completed=status h 5 in
  check (label^": READ_SELECT 5") (if options.debug_counters then completed>0 else completed=0);
  (* Non-byte-lane counts. *)
  List.iter (fun count ->
    load h 0 0 [instruction 19 ((2 lsl 16) lor 1);op3 24 2 0 count;instruction 1 0];
    command h 4 1;idle h 6;
    let code=fault_code () in
    let expected=if count>=32 || (options.shift=O.Byte_lane && count land 7<>0) then 1 else 0 in
    check (Printf.sprintf "%s: SHL by %d fault code %d" label count code) (code=expected);
    command h 7 1) [1;4;12;31;32];
  (* PC width: out-of-image targets fault with code 2; PC readback. *)
  List.iter (fun (program,target) ->
    load h 0 0 program;command h 4 1;idle h 6;
    check (label^": out-of-image branch faults 2") (fault_code ()=2);
    let pc=status h 3 in
    let expected=if options.pc_bits=O.Saturating_7 && target>=128 then 127 else target in
    check (Printf.sprintf "%s: PC after branch to %d reads %d" label target pc) (pc=expected);
    command h 7 1)
    [[instruction 5 200],200;[instruction 5 100],100;[instruction 5 127],127;[instruction 5 128],128;
     [instruction 5 0xffffff],0xffffff;[instruction 26 (1 lsl 16 lor 0xffff)],0xffff;
     [instruction 10 1;instruction 11 300],300;
     (List.init 64 (fun k -> if k=63 then instruction 0 0 else instruction 5 63)),64];
  (* FIFO depth: level readback saturates at the depth. *)
  command h 0 2;
  for k=1 to fifo_words do check (label^": TX accepts") (write ~budget:20 h 2 k) done;
  check (label^": TX refuses beyond depth") (not (write ~budget:20 h 2 0));
  check (label^": FIFO level") (status h 2 land 0xffff=fifo_words);
  h.cycle

(* ------------------------------------------------------------------ *)

let lockstep ?(report=false) ?dump ~name ~seed ~ops () =
  let fifo_words,options=find_variant name in
  let h=make_harness ~label:name ~fifo_words options in
  if dump<>None then h.trace <- Some (Buffer.create (1 lsl 20));
  if seed=0 then directed h ~fifo_words else (reset h 3;random_traffic h ~seed ~ops);
  (match dump,h.trace with
   | Some path,Some b -> Out_channel.with_open_bin path (fun oc -> Buffer.output_buffer oc b)
   | _ -> ());
  if report then Printf.printf "coverage %s seed %d: running %d, mover grants %d, accepted commands %d, fault-visible %d, reset %d cycles\n"
      name seed h.coverage.(0) h.coverage.(1) h.coverage.(2) h.coverage.(3) h.coverage.(4);
  h.cycle,h.compared

(* The lockstep harness must detect a wrong reset-timing model, a missing
   asynchronous-reset emulation and an ISA-visible difference. *)
let negative_controls () =
  let expect_difference what run =
    match run () with
    | () -> failwith ("negative control passed unexpectedly: "^what)
    | exception Failure message ->
      let needle="differs from the design of record" in
      let rec found i = i+String.length needle<=String.length message
        && (String.sub message i (String.length needle)=needle || found (i+1)) in
      if not (found 0) then failwith ("negative control "^what^" failed differently: "^message) in
  let harness name =
    let fifo_words,options=find_variant name in make_harness ~label:name ~fifo_words options,fifo_words in
  List.iter (fun (what,name,style,emulate) ->
    expect_difference what (fun () ->
      let h,fifo_words=harness name in
      directed {h with reference_style=style;emulate} ~fifo_words))
    ["sync_registered compared without its delay","rstreg",O.Sync,true;
     "async_sync_release compared as async","cn_s2",O.Async,true;
     "async_sync_release compared as sync_registered","cn_s2",O.Sync_registered,true;
     "async reset not emulated","cn",O.Async,false];
  List.iter (fun selection ->
    expect_difference (Printf.sprintf "diet4 READ_SELECT %d" selection) (fun () ->
      let h,_=harness "diet4" in reset h 3;
      load h 0 0 [instruction 0 0;instruction 5 200];command h 4 1;idle h 5;
      ignore (status h selection)))
    [3;5;7];
  expect_difference "diet4 SHL by 4" (fun () ->
    let h,_=harness "diet4" in reset h 3;
    load h 0 0 [op3 24 0 0 4;instruction 1 0];command h 4 1;idle h 5)

let quick () =
  configuration_test ();
  negative_controls ();
  List.iter (fun depth -> List.iter (fifo_test ~depth)
    [Memory_sync;Registers_sync;Memory_async;Registers_async]) [2;4;8;32];
  List.iter (fun reset -> List.iter (fun pc_bits -> List.iter (fun shift ->
    List.iter (fun width -> next_pc_test ~options:{O.default with reset;pc_bits;shift} ~width) [16;32])
    [O.Barrel;O.Byte_lane]) [O.Full;O.Saturating_7]) [O.Sync;O.Async];
  let isa_cycles=List.fold_left (fun total (name,fifo_words,options) ->
    total+isa_checks ~label:name ~fifo_words options) 0 variants in
  let cycles=ref 0 and compared=ref 0 in
  List.iter (fun (name,_,_) -> List.iter (fun (seed,ops) ->
    let c,k=lockstep ~name ~seed ~ops () in cycles:= !cycles+c;compared:= !compared+k)
    [0,0;1,60]) variants;
  Printf.printf "Variant knobs: 128 option sets, 8 lockstep negative controls, FIFO depths 2/4/8/32 x 4 storage forms, 16 next_pc configurations, %d directed ISA cycles, %d lockstep cycles (%d comparisons) across %d variants passed.\n"
    isa_cycles !cycles !compared (List.length variants)

let () =
  match Array.to_list Sys.argv with
  | [_] -> quick ()
  | [_;"--list"] -> List.iter (fun (n,f,o) ->
      Printf.printf "%s fifo_words=%d %s\n" n f (Yojson.Safe.to_string (O.to_json o))) variants
  | [_;"--lockstep";name;"--seed";seed;"--ops";ops] ->
    let seed=int_of_string seed and ops=int_of_string ops in
    let start=Unix.gettimeofday () in
    let cycles,compared=lockstep ~report:true ~name ~seed ~ops () in
    Printf.printf "lockstep %s seed %d ops %d: %d cycles, %d comparisons, %.1f s: PASS\n"
      name seed ops cycles compared (Unix.gettimeofday () -. start)
  | [_;"--dump";name;"--seed";seed;"--ops";ops;path] ->
    let cycles,_=lockstep ~dump:path ~name ~seed:(int_of_string seed) ~ops:(int_of_string ops) () in
    Printf.printf "dump %s seed %s: %d cycles to %s\n" name seed cycles path
  | [_;"--isa";name] ->
    let fifo_words,options=find_variant name in
    Printf.printf "isa %s: %d cycles: PASS\n" name (isa_checks ~label:name ~fifo_words options)
  | _ -> prerr_endline "usage: variant_test.exe [--list | --lockstep NAME --seed N --ops K | --dump NAME --seed N --ops K FILE | --isa NAME]"; exit 2
