(* Formal-depth observation wrapper (formal_depth/, not part of src/).

   Elaborates exactly Processor.create_refinement ~debug:true (the circuit that
   formal/ proves, via Instruction_sram_formal.processor) and adds output
   ports that observe existing signals. No logic is added or changed; every new
   port is a plain output of an existing register, memory read port or SRAM
   instance pin.

   It emits the same fv_* ports as formal/gen/generate_fv.ml (engine
   registers attributed structurally through image_length_k), plus:

     fd_host_prev_window   host register: window sampled on the last edge
     fd_host_write_index   host register: accepted nibbles of the partial word
     fd_host_write_buffer  host register: partial write word
     fd_host_read_index    host register: accepted nibbles of the read word
     fd_host_snapshot      host register: read snapshot
     fd_host_presenting    host register: a read snapshot is being presented
     fd_host_fault         processor register host_fault
     fd_host_read_select   processor register host_read_select
     fd_sram_lo_a_din / fd_sram_hi_a_din   SRAM data-in pins
     fd_tx_head            TX FIFO read-port data (the engine's PULL input)
     fd_tx_rd / fd_tx_wr / fd_rx_rd / fd_rx_wr   FIFO pointers

   The FIFO storage arrays are named fd_tx_mem_k / fd_rx_mem_k (a name is
   metadata only) and exposed afterwards as flat ports by
   formal_depth/gen/expose_fifo_mem.py.

   Soundness does not depend on this attribution being right: the harnesses
   use these ports only inside assertions (as strengthening invariants), never
   in assumptions. A wrong attribution makes a proof fail, not pass.
   Nevertheless every lookup below insists on a unique structural match and
   aborts otherwise. *)

module S = Hardcaml.Signal
module T = Hardcaml.Signal.Type
module C = Hardcaml.Circuit

let engine_registers =
  [ "pc"; "running"; "fault_code"; "tx"; "rx"; "x"; "y"; "repeat_count"
  ; "wait_timer"; "wait_limit"; "blocked_cycles"; "logical_output"
  ; "logical_enable"; "transfer_pins"; "completed_instructions"
  ; "transfer_edges"; "transfer_tick"; "transfer_period"; "transfer_mode" ]

exception Fd_error of string
let fail fmt = Printf.ksprintf (fun s -> raise (Fd_error s)) fmt
let lenient = ref false

(* --lenient (used for mutant netlists): if the host-register lookup fails,
   the fd_host_* ports are constant zero instead of aborting. The harnesses
   only use them in link_* assertions, which mutant runs disable. *)
let uid s = T.Uid.to_int (S.uid s)
let has_name (s : S.t) name =
  match s with T.Empty -> false | _ -> List.mem name (S.names s)

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

(* Every combinational node reachable from [roots] (registers are leaves). *)
let comb_nodes roots =
  let seen = Hashtbl.create 1024 and found = ref [] in
  let rec go (s : S.t) =
    match s with
    | T.Empty -> ()
    | _ when Hashtbl.mem seen (uid s) -> ()
    | _ ->
      Hashtbl.add seen (uid s) ();
      found := s :: !found;
      (match s with
       | T.Reg _ | T.Inst _ | T.Multiport_mem _ | T.Mem_read_port _ | T.Const _ | T.Empty -> ()
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

let is_reg_w w s = T.is_reg s && S.width s = w

let unique what = function
  | [ x ] -> x
  | l -> fail "expected exactly one %s, found %d" what (List.length l)

let main () =
  let config_path = ref "" and output_path = ref "" in
  Arg.parse
    [ "--config", Arg.Set_string config_path, "Refinement JSON"
    ; "--output", Arg.Set_string output_path, "Generated verification Verilog"
    ; "--lenient", Arg.Set lenient, "Zero-valued host ports if a lookup fails (mutants)" ]
    (fun _ -> raise (Arg.Bad "positional arguments are not accepted"))
    "generate_fd --config refinement.json --output processor_fd.v";
  if !config_path = "" || !output_path = "" then fail "--config and --output are required";
  let config = Refinement_config.load !config_path in
  let n = config.architecture.engine_count in
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
  let output_driver name =
    match List.filter (fun s -> has_name s name) (C.outputs circuit) with
    | [ s ] -> strip_wires s
    | _ -> fail "missing output %s" name
  in
  (* ---- engine registers (as formal/gen/generate_fv.ml) ---- *)
  let lengths = Array.init n (fun k -> named (Printf.sprintf "image_length_%d" k)) in
  let length_engine s =
    let u = uid s in
    let rec find k = if k = n then None else if uid lengths.(k) = u then Some k else find (k + 1) in
    find 0
  in
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
  (match output_driver "dbg_running" with
   | T.Cat { args; _ } ->
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
  in
  let processor_outputs =
    List.map
      (fun base -> pack ("fv_" ^ base) (fun k -> named (Printf.sprintf "%s_%d" base k)))
      [ "image_writing"; "image_loaded" ]
  in
  let uio_in = List.find (fun s -> has_name s "uio_in") (C.inputs circuit) in
  let ui_in = List.find (fun s -> has_name s "ui_in") (C.inputs circuit) in
  let sync1 =
    unique "uio_in synchroniser register"
      (List.filter (fun r -> match r with
           | T.Reg { d; _ } -> uid (strip_wires d) = uid uio_in | _ -> false) registers)
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
    @ [ pack "fd_sram_lo_a_din" (fun k -> port lo.(k) "A_DIN")
      ; pack "fd_sram_hi_a_din" (fun k -> port hi.(k) "A_DIN") ]
  in
  (* ---- host port registers (Host.create) ---- *)
  let host_regs () =
  let rd_valid, nibble =
    match output_driver "uo_out" with
    | T.Cat { args = [ _fault; _irq; rd_valid; _wr_ready; nibble ]; _ } ->
      strip_wires rd_valid, strip_wires nibble
    | _ -> fail "uo_out is not the five-field host concatenation"
  in
  let read_index, snapshot =
    match nibble with
    | T.Mux { select; cases; _ } when List.length cases = 8 ->
      let ri = strip_wires select in
      if not (is_reg_w 3 ri) then fail "read nibble select is not a 3-bit register";
      let snaps =
        List.mapi
          (fun i c -> match strip_wires c with
             | T.Select { arg; high; low; _ } when low = 4 * i && high = 4 * i + 3 -> strip_wires arg
             | _ -> fail "read nibble case %d is not snapshot[%d:%d]" i (4 * i + 3) (4 * i))
          cases
      in
      let snap = List.hd snaps in
      if not (List.for_all (fun s -> uid s = uid snap) snaps && is_reg_w 32 snap)
      then fail "read nibble cases do not select one 32-bit register";
      ri, snap
    | _ -> fail "read nibble is not an 8-way mux"
  in
  let rec and_leaves s =
    match strip_wires s with
    | T.Op2 { op = Signal_and; arg_a; arg_b; _ } -> and_leaves arg_a @ and_leaves arg_b
    | s -> [ s ]
  in
  let presenting = unique "presenting register" (List.filter (is_reg_w 1) (and_leaves rd_valid)) in
  let prev_window =
    unique "previous-window register"
      (List.filter (fun r -> match r with
           | T.Reg { d; _ } ->
             (match strip_wires d with
              | T.Select { arg; high = 7; low = 6; _ } -> uid (strip_wires arg) = uid ui_in
              | _ -> false)
           | _ -> false) registers)
  in
  let write_buffer =
    match output_driver "dbg_command_code" with
    | T.Select { arg; high = 31; low = 24; _ } ->
      (match strip_wires arg with
       | T.Cat { args = [ nib; rest ]; _ } ->
         (match strip_wires nib, strip_wires rest with
          | T.Select { arg = u; high = 3; low = 0; _ }, T.Select { arg = buf; high = 31; low = 4; _ }
            when uid (strip_wires u) = uid ui_in && is_reg_w 32 (strip_wires buf) -> strip_wires buf
          | _ -> fail "host word is not {ui_in[3:0], buffer[31:4]}")
       | _ -> fail "host word is not a concatenation")
    | _ -> fail "dbg_command_code is not word[31:24]"
  in
  let write_index =
    unique "write-index register (3-bit register == 7 in the host_tx cone)"
      (List.sort_uniq (fun a b -> compare (uid a) (uid b))
         (List.filter_map (fun s -> match s with
              | T.Op2 { op = Signal_eq; arg_a; arg_b; _ } ->
                let is7 c = match c with
                  | T.Const { constant; _ } -> Hardcaml.Bits.to_int constant = 7 | _ -> false in
                let a = strip_wires arg_a and b = strip_wires arg_b in
                if is_reg_w 3 a && is7 b then Some a
                else if is_reg_w 3 b && is7 a then Some b else None
              | _ -> None)
            (comb_nodes [ output_driver "dbg_host_tx" ])))
  in
  if uid write_index = uid read_index then fail "write and read index coincide";
  prev_window, write_index, write_buffer, read_index, snapshot, presenting
  in
  let prev_window, write_index, write_buffer, read_index, snapshot, presenting =
    try host_regs () with
    | Fd_error msg when !lenient ->
      Printf.eprintf "generate_fd: warning: host registers: %s; exporting zeros\n" msg;
      S.zero 2, S.zero 3, S.zero 32, S.zero 3, S.zero 32, S.zero 1
  in
  let host_outputs =
    [ S.output "fd_host_prev_window" prev_window; S.output "fd_host_write_index" write_index
    ; S.output "fd_host_write_buffer" write_buffer; S.output "fd_host_read_index" read_index
    ; S.output "fd_host_snapshot" snapshot; S.output "fd_host_presenting" presenting
    ; S.output "fd_host_fault" (named "host_fault")
    ; S.output "fd_host_read_select" (named "host_read_select") ]
  in
  (* ---- FIFOs: attribute each memory by the write data it stores ---- *)
  let data_args name =
    match output_driver name with
    | T.Cat { args; _ } -> Array.of_list (List.rev_map strip_wires args) (* engine 0 = last *)
    | _ -> fail "%s is not a concatenation" name
  in
  let tx_data = data_args "dbg_tx_data" and rx_data = data_args "dbg_rx_data" in
  let read_ports = List.filter (fun s -> match s with T.Mem_read_port _ -> true | _ -> false) signals in
  let fifo data k =
    unique (Printf.sprintf "FIFO read port for engine %d" k)
      (List.filter (fun s -> match s with
           | T.Mem_read_port { memory; _ } ->
             (match memory with
              | T.Multiport_mem { write_ports = [| wp |]; size = 8; _ } ->
                uid (strip_wires wp.Hardcaml.Write_port.write_data) = uid data.(k)
              | _ -> false)
           | _ -> false) read_ports)
  in
  let txs = Array.init n (fifo tx_data) and rxs = Array.init n (fifo rx_data) in
  let rx_head = data_args "dbg_rx_head" in
  Array.iteri (fun k p -> if uid p <> uid rx_head.(k)
                then fail "RX read port of engine %d is not dbg_rx_head" k) rxs;
  let pointers p =
    match p with
    | T.Mem_read_port { memory = T.Multiport_mem { write_ports = [| wp |]; _ } as m; read_address; _ } ->
      let rd = strip_wires read_address and wr = strip_wires wp.Hardcaml.Write_port.write_address in
      if not (is_reg_w 3 rd && is_reg_w 3 wr) then fail "FIFO pointers are not 3-bit registers";
      m, rd, wr
    | _ -> assert false
  in
  let txp = Array.map pointers txs and rxp = Array.map pointers rxs in
  (* Name the storage arrays so that expose_fifo_mem.py can find them in the
     emitted Verilog. Names are metadata only. *)
  Array.iteri (fun k (m, _, _) -> ignore (S.( -- ) m (Printf.sprintf "fd_tx_mem_%d" k))) txp;
  Array.iteri (fun k (m, _, _) -> ignore (S.( -- ) m (Printf.sprintf "fd_rx_mem_%d" k))) rxp;
  let fifo_outputs =
    [ pack "fd_tx_head" (fun k -> txs.(k))
    ; pack "fd_tx_rd" (fun k -> let _, rd, _ = txp.(k) in rd)
    ; pack "fd_tx_wr" (fun k -> let _, _, wr = txp.(k) in wr)
    ; pack "fd_rx_rd" (fun k -> let _, rd, _ = rxp.(k) in rd)
    ; pack "fd_rx_wr" (fun k -> let _, _, wr = rxp.(k) in wr) ]
  in
  let extra =
    engine_outputs @ processor_outputs @ sram_outputs
    @ [ S.output "fv_timestamp" (named "timestamp"); S.output "fv_sync1" sync1 ]
    @ host_outputs @ fifo_outputs
  in
  let fd = C.create_exn ~name:"protocol_processor_fd" (C.outputs circuit @ extra) in
  Hardcaml.Rtl.output ~output_mode:(To_file !output_path) Verilog fd;
  Printf.printf "host prev_window=%d write_index=%d write_buffer=%d read_index=%d snapshot=%d presenting=%d\n"
    (uid prev_window) (uid write_index) (uid write_buffer) (uid read_index) (uid snapshot) (uid presenting);
  Array.iteri
    (fun k regs ->
      Printf.printf "engine %d:%s\n" k
        (String.concat "" (List.map (fun name ->
             Printf.sprintf " %s=uid%d" name (uid (List.assoc name regs))) engine_registers)))
    table

let () =
  try main () with
  | Fd_error msg -> prerr_endline ("generate_fd: " ^ msg); exit 1
