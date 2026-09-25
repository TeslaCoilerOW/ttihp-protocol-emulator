(* Formal-only observation wrapper for the timing-isolation miter.

   It elaborates exactly the circuit that generate_refinement_formal emits for
   --target processor (Processor.create_refinement ~debug:true), then adds
   output ports that observe existing state. No logic is added or changed:
   every new port is a plain output of an existing register or SRAM
   instantiation port.

   The production RTL names the per-engine registers "pc", "pc_0", ... in an
   order chosen by the RTL name mangler, so names cannot identify the engine.
   Instead every engine register is attributed structurally: engine k's
   registers are exactly those whose next-state logic reads the processor
   register "image_length_k" (every Engine.create register is assigned inside
   the [pc >= image_length] fault branch, and image_length_k feeds nothing but
   engine k). The generator fails if any register is attributed to zero or
   several engines, if any engine does not own exactly one register of each
   name, or if the result disagrees with the dbg_running export.

   Design variants (the refinement's "options"): without debug counters the
   engines have no completed_instructions register and fv_completed_instructions
   reads zero; the PC and image registers keep their (possibly narrower) native
   widths; with a reset synchronizer its two flops are exported as
   fv_reset_sync = {reset_sync_2, reset_sync_1}. The design of record's output is
   unchanged. *)

module S = Hardcaml.Signal
module T = Hardcaml.Signal.Type
module C = Hardcaml.Circuit

let engine_registers =
  [ "pc"; "running"; "fault_code"; "tx"; "rx"; "x"; "y"; "repeat_count"
  ; "wait_timer"; "wait_limit"; "blocked_cycles"; "logical_output"
  ; "logical_enable"; "transfer_pins"; "completed_instructions"
  ; "transfer_edges"; "transfer_tick"; "transfer_period"; "transfer_mode" ]

let fail fmt = Printf.ksprintf (fun s -> prerr_endline ("generate_fv: " ^ s); exit 1) fmt
let uid s = T.Uid.to_int (S.uid s)
let has_name (s : S.t) name =
  match s with T.Empty -> false | _ -> List.mem name (S.names s)

(* Registers reached combinationally from [roots]; stops at registers,
   memories and instantiations (all sequential). *)
let register_cone roots =
  let seen = Hashtbl.create 1024 and found = ref [] in
  let rec go (s : S.t) =
    match s with
    | T.Empty -> ()
    | _ when Hashtbl.mem seen (uid s) -> ()
    | _ ->
      Hashtbl.add seen (uid s) ();
      (match s with
       | T.Reg _ -> found := s :: !found
       | T.Inst _ | T.Multiport_mem _ | T.Mem_read_port _ | T.Const _ | T.Empty -> ()
       | T.Wire { driver; _ } -> go !driver
       | T.Op2 { arg_a; arg_b; _ } -> go arg_a; go arg_b
       | T.Mux { select; cases; _ } -> go select; List.iter go cases
       | T.Cat { args; _ } -> List.iter go args
       | T.Not { arg; _ } | T.Select { arg; _ } -> go arg)
  in
  List.iter go roots;
  !found

let reg_inputs (s : S.t) =
  match s with
  | T.Reg { d; register; _ } -> [ d; register.reg_clear; register.reg_enable ]
  | _ -> []

let rec strip_wires (s : S.t) =
  match s with
  | T.Wire { driver; _ } when not (T.is_empty !driver) -> strip_wires !driver
  | _ -> s

let () =
  let config_path = ref "" and output_path = ref "" in
  Arg.parse
    [ "--config", Arg.Set_string config_path, "Refinement JSON"
    ; "--output", Arg.Set_string output_path, "Generated verification Verilog" ]
    (fun _ -> raise (Arg.Bad "positional arguments are not accepted"))
    "generate_fv --config refinement.json --output processor_fv.v";
  if !config_path = "" || !output_path = "" then fail "--config and --output are required";
  let config = Refinement_config.load !config_path in
  let n = config.architecture.engine_count in
  let engine_registers =
    if config.options.Variant_options.debug_counters then engine_registers
    else List.filter (fun name -> name <> "completed_instructions") engine_registers in
  let circuit = Instruction_sram_formal.processor config in
  let signals =
    Hardcaml.Signal_graph.fold (C.signal_graph circuit) ~init:[] ~f:(fun acc s ->
        match s with T.Empty -> acc | _ -> s :: acc)
  in
  let registers = List.filter T.is_reg signals in
  let named name =
    match List.filter (fun s -> has_name s name) signals with
    | [ s ] -> s
    | l -> fail "expected exactly one signal named %s, found %d" name (List.length l)
  in
  let lengths = Array.init n (fun k -> named (Printf.sprintf "image_length_%d" k)) in
  let length_engine s =
    let u = uid s in
    let rec find k = if k = n then None else if uid lengths.(k) = u then Some k else find (k + 1) in
    find 0
  in
  (* table.(k) : (name * register) list *)
  let table = Array.make n [] in
  List.iter
    (fun r ->
      match List.find_opt (has_name r) engine_registers with
      | None -> ()
      | Some name ->
        let engines =
          List.sort_uniq compare (List.filter_map length_engine (register_cone (reg_inputs r)))
        in
        (match engines with
         | [ k ] ->
           if List.mem_assoc name table.(k) then fail "engine %d has two %s registers" k name;
           table.(k) <- (name, r) :: table.(k)
         | l -> fail "register %s reads image_length of %d engines" name (List.length l)))
    registers;
  Array.iteri
    (fun k regs ->
      List.iter
        (fun name -> if not (List.mem_assoc name regs) then fail "engine %d lacks %s" k name)
        engine_registers)
    table;
  (* Cross-check against the production debug export. *)
  (match strip_wires (named "dbg_running") with
   | T.Cat { args; _ } ->
     (* concat_lsb: the last argument is engine 0 *)
     List.iteri
       (fun i a ->
         let k = n - 1 - i in
         if uid (strip_wires a) <> uid (List.assoc "running" table.(k))
         then fail "structural attribution disagrees with dbg_running for engine %d" k)
       args
   | _ -> fail "dbg_running is not a concatenation");
  let pack name get = S.output name (S.concat_lsb (List.init n get)) in
  let engine_outputs =
    List.map (fun name -> pack ("fv_" ^ name) (fun k -> List.assoc name table.(k))) engine_registers
    @ (if List.mem "completed_instructions" engine_registers then []
       else [ pack "fv_completed_instructions" (fun _ -> S.zero 32) ])
  in
  let reset_sync =
    match List.filter (fun r -> has_name r "reset_sync_1" || has_name r "reset_sync_2") registers with
    | [] -> []
    | [ _; _ ] -> [ S.output "fv_reset_sync" (S.concat_msb [ named "reset_sync_2"; named "reset_sync_1" ]) ]
    | l -> fail "expected zero or two reset synchronizer registers, found %d" (List.length l)
  in
  let processor_outputs =
    List.map
      (fun base -> pack ("fv_" ^ base) (fun k -> named (Printf.sprintf "%s_%d" base k)))
      [ "image_writing"; "image_loaded" ]
  in
  let uio_in = List.find (fun s -> has_name s "uio_in") (C.inputs circuit) in
  let sync1 =
    match List.filter (fun r -> match r with
        | T.Reg { d; _ } -> uid (strip_wires d) = uid uio_in | _ -> false) registers with
    | [ r ] -> r
    | l -> fail "expected one uio_in synchroniser register, found %d" (List.length l)
  in
  let instance name =
    match List.filter (fun s -> match s with
        | T.Inst { instantiation; _ } -> instantiation.inst_instance = name | _ -> false) signals with
    | [ s ] -> s
    | _ -> fail "missing instance %s" name
  in
  let port inst p =
    match inst with
    | T.Inst { instantiation; _ } -> List.assoc p instantiation.inst_inputs
    | _ -> assert false
  in
  let sram half = Array.init n (fun k -> instance (Printf.sprintf "instruction_sram_e%d_%s" k half)) in
  let lo = sram "lo" and hi = sram "hi" in
  let sram_outputs =
    [ pack "fv_sram_dout" (fun k -> S.concat_msb [ hi.(k); lo.(k) ]) ]
    @ List.concat_map
        (fun p ->
          [ pack ("fv_sram_lo_" ^ String.lowercase_ascii p) (fun k -> port lo.(k) p)
          ; pack ("fv_sram_hi_" ^ String.lowercase_ascii p) (fun k -> port hi.(k) p) ])
        [ "A_MEN"; "A_WEN"; "A_REN"; "A_ADDR" ]
  in
  let extra =
    engine_outputs @ processor_outputs @ sram_outputs
    @ [ S.output "fv_timestamp" (named "timestamp"); S.output "fv_sync1" sync1 ] @ reset_sync
  in
  let fv = C.create_exn ~name:"protocol_processor_fv" (C.outputs circuit @ extra) in
  Hardcaml.Rtl.output ~output_mode:(To_file !output_path) Verilog fv;
  Array.iteri
    (fun k regs ->
      Printf.printf "engine %d:%s\n" k
        (String.concat "" (List.map (fun name ->
             Printf.sprintf " %s=uid%d" name (uid (List.assoc name regs))) engine_registers)))
    table
