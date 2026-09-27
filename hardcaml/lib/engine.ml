open Hardcaml
open Signal

type inputs = {
  clock : Signal.t; clear : Signal.t; start : Signal.t; stop : Signal.t;
  clear_fault : Signal.t; instruction : Signal.t; image_length : Signal.t;
  ownership : Signal.t; pins : Signal.t; timestamp : Signal.t;
  tx_valid : Signal.t; tx_data : Signal.t; rx_ready : Signal.t; event : Signal.t;
}
type t = {
  pc : Signal.t; running : Signal.t; fault : Signal.t; stalled : Signal.t;
  tx_pop : Signal.t; rx_push : Signal.t; rx_data : Signal.t;
  pin_values : Signal.t; pin_enables : Signal.t;
  signal_events : Signal.t; consume_event : Signal.t; completed : Signal.t;
  issue : Signal.t; wait_timer : Signal.t; wait_limit : Signal.t;
  blocked_cycles : Signal.t; repeat_count : Signal.t; transfer_edges : Signal.t;
  queue_observe : Signal.t; line_state : (string * Signal.t) list;
}

(* Line-unit registers (Line_options.Rec16, docs/extension.md). The ticker's
   count and half-period are the XFER registers transfer_tick and
   transfer_period (a classic XFER stops the ticker). *)
type line_regs = {
  l_run : Always.Variable.t;     (* ticker running *)
  l_phase : Always.Variable.t;   (* 0: the next tick is a bit boundary, 1: mid-bit *)
  l_frac : Always.Variable.t;    (* fraction Q/256 per tick *)
  l_acc : Always.Variable.t;     (* phase accumulator *)
  l_seen : Always.Variable.t;    (* a drive+sample XFER has driven its first boundary *)
  l_cfg : Always.Variable.t;     (* LCFG bits 9..0 *)
  l_level : Always.Variable.t;   (* line level last driven *)
  l_rx_prev : Always.Variable.t; (* previous raw sample (NRZI decoding) *)
  l_cell : Always.Variable.t;    (* bit of the current cell (Manchester, arbitration) *)
  l_man : Always.Variable.t;     (* Manchester second half pending *)
  l_se0 : Always.Variable.t;     (* an RX XFER ended on SE0 *)
  l_rem : Always.Variable.t;     (* data bits remaining when it did *)
  l_trail : Always.Variable.t;   (* the XFER continues for a trailing stuff bit *)
  s_run : Always.Variable.t;     (* stuffing: current run length *)
  s_last : Always.Variable.t;    (* stuffing: last line bit *)
  s_err : Always.Variable.t;     (* stuffing: a received stuff bit had the wrong value *)
  a_lost : Always.Variable.t;    (* arbitration lost *)
  c_state : Always.Variable.t;   (* CRC register *)
  c_preset : Always.Variable.t;  (* CRC polynomial preset *)
}

let line_registers r = {
  l_run = r "line_run" 1; l_phase = r "line_phase" 1; l_frac = r "line_frac" 8;
  l_acc = r "line_acc" 8; l_seen = r "line_boundary_seen" 1; l_cfg = r "line_cfg" 10;
  l_level = r "line_level" 1; l_rx_prev = r "line_rx_prev" 1; l_cell = r "line_cell_bit" 1;
  l_man = r "line_man_pending" 1; l_se0 = r "line_se0" 1; l_rem = r "line_remaining" 6;
  l_trail = r "line_trailing_stuff" 1; s_run = r "stuff_run" 4; s_last = r "stuff_last" 1;
  s_err = r "stuff_error" 1; a_lost = r "arbitration_lost" 1;
  c_state = r "crc_state" Line_unit.crc_width; c_preset = r "crc_preset" 2 }

let line_register_names =
  [ "line_run"; "line_phase"; "line_frac"; "line_acc"; "line_boundary_seen"; "line_cfg"
  ; "line_level"; "line_rx_prev"; "line_cell_bit"; "line_man_pending"; "line_se0"
  ; "line_remaining"; "line_trailing_stuff"; "stuff_run"; "stuff_last"; "stuff_error"
  ; "arbitration_lost"; "crc_state"; "crc_preset" ]

let line_register_list l =
  List.combine line_register_names
    [ l.l_run; l.l_phase; l.l_frac; l.l_acc; l.l_seen; l.l_cfg; l.l_level; l.l_rx_prev
    ; l.l_cell; l.l_man; l.l_se0; l.l_rem; l.l_trail; l.s_run; l.s_last; l.s_err
    ; l.a_lost; l.c_state; l.c_preset ]

(* [options] (default: the design of record) selects the variant knobs that
   live inside an engine.  With an asynchronous reset style [i.clear] is the
   chip-wide asynchronous reset net rather than a synchronous clear; it still
   gates [active] combinationally, exactly as in the synchronous design. *)
let create ?(options=Variant_options.default) ?(timing=Timing_options.default)
    ?(line=Line_options.default) ?line_mutation ?gate
    (config:Config.t) (i:inputs) =
  (* [gate]: the clear that gates the next-state enable and the issue outputs
     (constant 0 with the timing knob clear_outputs_only); [i.clear] when
     absent. The registers always take [i.clear]. *)
  let gate = match gate with Some g -> g | None -> i.clear in
  (* [line]: the line-unit extension (docs/extension.md). Without it no
     signal below is created in a different order than before the knob
     existed, so the emitted Verilog is unchanged. [line_mutation] seeds a
     defect for the formal negative controls only. *)
  let line_on = Line_options.enabled line in
  let mutated m = line_mutation = Some m in
  let open Always in
  let width = config.data_width in
  let spec = if Variant_options.asynchronous options
    then Reg_spec.create ~clock:i.clock ~reset:i.clear ()
    else Reg_spec.create ~clock:i.clock ~clear:i.clear () in
  let r name w = let v = Variable.reg spec ~width:w in
    ignore (v.value -- name); v in
  let pcw = Variant_options.pc_width options in
  let pc = r "pc" pcw and running = r "running" 1 and fault = r "fault_code" 8 in
  let image_length = uresize i.image_length pcw in
  (* Saturating PC: a branch target >= 2^pcw becomes the all-ones PC (127),
     which is outside every image (<= 64 words) and so still faults with
     code 2.  Identity for the 24-bit PC. *)
  let sat v = if pcw = 24 then v
    else mux2 (select v 23 pcw <>:. 0) (ones pcw) (select v (pcw-1) 0) in
  let byte_lane = options.shift = Variant_options.Byte_lane in
  (* Byte-lane shifts: only counts 0, 8, 16, 24 (below the datapath width)
     are valid; any other count is an invalid operand (fault code 1). *)
  let lane_bits = Config.log2 (width / 8) in
  let byte_shift op d c =
    mux (select c (2 + lane_bits) 3)
      (List.init (width / 8) (fun k -> if k = 0 then d else op d (8 * k))) in
  let shift_valid c = if byte_lane
    then (c <:. width) &: ((c &: of_int ~width:8 0xe7) ==:. 0)
    else c <:. width in
  let regs = Array.init 4 (fun n -> r ([|"tx";"rx";"x";"y"|].(n)) width) in
  let tx = regs.(0) and rx = regs.(1) in
  let repeat = r "repeat_count" 16 and timer = r "wait_timer" 24 in
  let limit = r "wait_limit" 24 and blocked = r "blocked_cycles" 24 in
  let values = r "logical_output" 8 and enables = r "logical_enable" 8 in
  let pins = r "transfer_pins" 9 and completed = r "completed_instructions" 32 in
  let xremaining = r "transfer_edges" 7 and xtick = r "transfer_tick" 8 in
  (* transfer_mode gains bit 5 (line mode) and bit 6 (feed the CRC). *)
  let xmode_w = if line_on then 7 else 5 in
  let xperiod = r "transfer_period" 8 and xmode = r "transfer_mode" xmode_w in
  let lu = if line_on then Some (line_registers r) else None in
  let word = i.instruction in
  let op = select word 31 24 and a = select word 23 16
  and b = select word 15 8 and c = select word 7 0 in
  let imm24 = select word 23 0 and imm16 = select word 15 0 in
  let low8 = select word 7 0 in
  let all_zero = imm24 ==:. 0 and bc_zero = imm16 ==:. 0 in
  let dest = select a 1 0 and src = select b 1 0 in
  let source = mux src (Array.to_list (Array.map (fun (r:Variable.t) -> r.value) regs)) in
  let destination = mux dest (Array.to_list (Array.map (fun (r:Variable.t) -> r.value) regs)) in
  let pin = select a 2 0 in
  let bitmask pin = log_shift sll (of_int ~width:8 1) pin in
  let owned pin = (bitmask pin &: i.ownership) <>:. 0 in
  let write_pin v pin bit = (v &: ~:(bitmask pin)) |: mux2 bit (bitmask pin) (zero 8) in
  let pin_input = mux pin (List.init 8 (bit i.pins)) in
  let reg_pair = (a <:. 4) &: (b <:. 4) &: (c ==:. 0) in
  let split_decode = timing.Timing_options.split_instruction_decode in
  (* Timing knob keep_counter_increments: the incremented or decremented value
     of each wide counter carries a keep attribute. Synthesis then keeps it as
     a net of its own, so the counter's enable selects between two finished
     values instead of being merged into the carry chain (ABC's area mapping
     otherwise starts the chain with the enable). Identity without the knob. *)
  let step s = if timing.Timing_options.keep_counter_increments
    then Signal.add_attribute s (Rtl_attribute.create "keep" ~value:(Rtl_attribute.Value.Bool true))
    else s in
  let valid_rule code =
    match code with
    | 0|1|6|15 -> all_zero
    | 7 -> (a <:. 2) &: bc_zero
    | 2|3 -> (select word 23 8 ==:. 0) &: ((low8 &: ~:(i.ownership)) ==:. 0)
    | 4|5|11 -> vdd
    | 8 -> (a <:. 8) &: (b ==:. 0) &: (c <:. 2) &: owned pin
    | 9 -> (a <:. 8) &: (b ==:. 0) &: (c <:. 2)
    | 10 -> a ==:. 0
    | 12 -> imm24 <>:. 0
    | 13 -> (a <:. 8) &: (b <:. 2) &: (c ==:. 0)
    | 14 -> imm24 <:. (1 lsl config.engine_count)
    | 16 -> select word 23 9 ==:. 0
    | 17 when config.issue = "fused" ->
      let ck = select pins.value 2 0 and out = select pins.value 5 3 in
      (match lu with
       | None ->
      (a <>:. 0) &: (a <=:. width) &: (b <>:. 0) &: (c <:. 32)
      &: owned ck &: ((~:(bit c 3)) |: owned out)
      &: ((~:(bit c 3)) |: (ck <>: out))
       | Some l ->
         (* c bit 5 selects line mode, bit 6 feeds the CRC (also legal in a
            classic XFER), bit 7 must be zero. A line XFER: b = 0,
            c[1:0] = 0, the ticker running, no sampling in Manchester, the
            data pin owned when driving, and with the pair set the pair pin
            owned and distinct. *)
         let classic = (a <>:. 0) &: (a <=:. width) &: (b <>:. 0)
           &: owned ck &: ((~:(bit c 3)) |: owned out)
           &: ((~:(bit c 3)) |: (ck <>: out)) in
         let cfg = l.l_cfg.value in
         let manchester = select cfg 1 0 ==:. 2 and pair = bit cfg 7 in
         let line_rule = (a <>:. 0) &: (a <=:. width) &: (b ==:. 0) &: (select c 1 0 ==:. 0)
           &: l.l_run.value &: ~:(manchester &: bit c 4)
           &: ((~:(bit c 3)) |: owned out)
           &: ((~:(bit c 3 &: pair)) |: (owned ck &: (ck <>: out))) in
         (~:(bit c 7)) &: mux2 (bit c 5) line_rule classic)
    (* LTIM: any value except P = 255 with a fraction (P + carry must fit). *)
    | 30 when line_on ->
      if mutated Line_unit.Ltim_overflow then vdd
      else ~:((select word 7 0 ==:. 255) &: (select word 15 8 <>:. 0))
    (* LCFG: bits 23..11 zero, line code 3 invalid, and no stuffing on runs
       of either polarity of length 1 (the stuff bit itself would complete
       the next run, so no data bit would ever follow). *)
    | 31 when line_on -> (select word 23 11 ==:. 0) &: (select word 1 0 <>:. 3)
                         &: ~:(bit word 2 &: bit word 3 &: (select word 6 4 ==:. 0))
    (* CRC: c = 1 set from register b, c = 2 read into register a, c = 3 preset b. *)
    | 32 when line_on ->
      ((c ==:. 1) &: (a ==:. 0) &: (b <:. 4)) |: ((c ==:. 2) &: (a <:. 4) &: (b ==:. 0))
      |: ((c ==:. 3) &: (a ==:. 0) &: (b <:. 4))
    (* LSTAT *)
    | 33 when line_on -> (a <:. 4) &: bc_zero
    | 18|20|21|22|23 -> reg_pair
    | 19|26 -> a <:. 4
    | 24|25 -> (a <:. 4) &: (b ==:. 0) &: shift_valid c
    | 27|28 -> (a <:. 4) &: bc_zero
    | 29 -> (select word 23 8 ==:. 0) &: (low8 <>:. 0)
    | _ -> gnd in
  let valid =
    if not split_decode then mux op (List.init 256 valid_rule)
    else
      (* Timing knob split_instruction_decode: the 256-way multiplexer on the
         opcode equals the OR over opcodes with a rule of (op = c) & rule(c);
         every other opcode's rule is the constant gnd. *)
      List.fold_left (fun acc (c,rule) -> if rule == gnd then acc else acc |: ((op ==:. c) &: rule))
        gnd (List.init 256 (fun c -> c, valid_rule c)) in
  let active, issue =
    if not timing.Timing_options.split_engine_issue then
      let active = running.value &: (fault.value ==:. 0) &: ~:(i.start) &: ~:(i.stop) &: ~:gate in
      let issue = active &: (timer.value ==:. 0) &: (xremaining.value ==:. 0)
                  &: (pc.value <: image_length) &: valid in
      active, issue
    else
      (* Timing knob split_engine_issue. The next-state logic below sits in the
         else branches of STOP and START, where both are low, so enabling it
         with [running & fault = 0 & ~clear] computes the same next state as
         [active]; START and STOP (host commands, late in the cycle) then gate
         only the issue-derived outputs that act outside the engine. *)
      let executing = running.value &: (fault.value ==:. 0) &: ~:gate in
      let ready = executing &: (timer.value ==:. 0) &: (xremaining.value ==:. 0)
                  &: (pc.value <: image_length) &: valid in
      let gate = ~:(i.start) &: ~:(i.stop) in
      executing, ready &: gate in
  let waiting_pin = pin_input <>: bit b 0 in
  let stalled = issue
                &: (((op ==:. 6) &: ~:(i.tx_valid)) |: ((op ==:. 7) &: (a ==:. 0) &: ~:(i.rx_ready))
                    |: ((op ==:. 13) &: waiting_pin) |: ((op ==:. 15) &: ~:(i.event))) in
  let tx_pop = issue &: (op ==:. 6) &: i.tx_valid in
  let rx_push = issue &: (op ==:. 7) &: i.rx_ready in
  let consume_event = issue &: (op ==:. 15) &: i.event in
  let signal_events = mux2 (issue &: (op ==:. 14))
      (uresize imm24 config.engine_count) (zero config.engine_count) in
  (* With split_instruction_decode the completed-instruction counter is not
     assigned in the instruction branches below but by its own enable (see the
     end of this function). *)
  let finish = if not split_decode
    then [pc <-- step (pc.value +:. 1); completed <-- step (completed.value +:. 1); blocked <--. 0]
    else [pc <-- step (pc.value +:. 1); blocked <--. 0] in
  let fail code = [fault <-- code; running <--. 0; enables <--. 0; xremaining <--. 0] in
  let write_reg data = List.init 4 (fun n -> when_ (dest ==:. n) [regs.(n) <-- data]) in
  let shift_tx msb = mux2 msb (sll tx.value 1) (srl tx.value 1) in
  let tx_bit msb data = mux2 msb (bit data (width-1)) (bit data 0) in
  let sample msb data = mux2 msb
      (concat_msb [select rx.value (width-2) 0; data])
      (concat_msb [data; select rx.value (width-1) 1]) in
  let blocked_step condition = if_ condition finish
      [if_ (blocked.value +:. 1 >=: mux2 (limit.value ==:. 0)
                                      (of_int ~width:24 65535) limit.value)
          (fail (of_int ~width:8 3)) [blocked <-- step (blocked.value +:. 1)]] in
  (* ---------------- line unit (docs/extension.md) ----------------
     Built only with the knob: [values_now] is the logical output after this
     cycle's autonomous Manchester second half, which instruction pin writes
     build on; [line_ops] are opcodes 30-33; [line_pre] runs the ticker and
     the Manchester second half in every active cycle; [line_transfer] is a
     line-mode XFER in progress; [line_start] clears the unit on START;
     [crc_step msb bit] feeds one bit to the CRC. *)
  let values_now, line_ops, line_pre, line_start, line_transfer, crc_step, queue_status =
    match lu with
    | None -> values.value, [], [], [], [], (fun _ _ -> []), gnd
    | Some l ->
      let cfg = l.l_cfg.value in
      let code = select cfg 1 0 in
      let is_nrzi = code ==:. 1 and is_man = code ==:. 2 in
      let stuff_en = bit cfg 2 and stuff_any = bit cfg 3 in
      let run_n = uresize (select cfg 6 4) 4 +:. (if mutated Line_unit.Stuff_run then 2 else 1) in
      let rx_run_n = if mutated Line_unit.Rx_destuff_run then run_n +:. 1 else run_n in
      let pair = bit cfg 7 and arb = bit cfg 8 and se0_end = bit cfg 9 in
      let ck_pin = select pins.value 2 0 and out_pin = select pins.value 5 3
      and in_pin = select pins.value 8 6 in
      let pin_of p = mux p (List.init 8 (bit i.pins)) in
      (* Ticker: a tick in every cycle in which the count is 1; ticks
         alternate bit boundary (phase 0) and mid-bit (phase 1). *)
      let tick = l.l_run.value &: (xtick.value ==:. 1) in
      let boundary_tick = tick &: ~:(l.l_phase.value) and mid_tick = tick &: l.l_phase.value in
      (* Drive the data pin, and with the pair set its complement on the pair pin. *)
      let write_pair v level =
        let v1 = write_pin v out_pin level in
        let v1 = if mutated Line_unit.Pin_leak then write_pin v1 (out_pin +:. 1) level else v1 in
        mux2 pair (write_pin v1 ck_pin (~:level)) v1 in
      let flip = mid_tick &: l.l_man.value in
      let flipped = write_pair values.value l.l_cell.value in
      let values_now = mux2 flip flipped values.value in
      let crc_step msb d =
        [l.c_state <-- Line_unit.crc_next ?mutation:line_mutation ~msb
           ~poly:(Line_unit.preset_polynomial ~msb l.c_preset.value) l.c_state.value d] in
      let sum = uresize l.l_acc.value 9 +: uresize l.l_frac.value 9 in
      let ticker = [when_ l.l_run.value
          [if_ (xtick.value <=:. 1)
             [xtick <-- xperiod.value
                        +: uresize (if mutated Line_unit.Fraction_carry then gnd else bit sum 8) 8;
              l.l_phase <-- ~:(l.l_phase.value);
              l.l_acc <-- select sum 7 0]
             [xtick <-- xtick.value -:. 1]]] in
      let man_flip = [when_ flip [l.l_level <-- l.l_cell.value; values <-- flipped; l.l_man <--. 0]] in
      let status = concat_msb [zero 18; l.l_rem.value; gnd; l.l_run.value; l.l_level.value;
                               i.rx_ready; i.tx_valid; l.s_err.value; l.a_lost.value; l.l_se0.value] in
      let p = select word 7 0 and q = select word 15 8 and d = select word 23 16 in
      let line_ops = [
        30, finish @ [xperiod <-- p; xtick <-- mux2 (d ==:. 0) p d; l.l_frac <-- q;
                      l.l_run <-- (p <>:. 0); l.l_acc <--. 0; l.l_phase <--. 0];
        31, finish @ [l.l_cfg <-- select word 9 0; l.l_level <-- bit word 10;
                      l.l_rx_prev <-- bit word 10; l.s_run <--. 0; l.s_last <-- bit word 10;
                      l.l_se0 <--. 0; l.s_err <--. 0; l.a_lost <--. 0; l.l_rem <--. 0;
                      l.l_man <--. 0; l.l_cell <--. 0];
        32, finish @ [when_ (select c 1 0 ==:. 1) [l.c_state <-- select source (Line_unit.crc_width-1) 0];
                      when_ (select c 1 0 ==:. 2) (write_reg (uresize l.c_state.value width));
                      when_ (select c 1 0 ==:. 3) [l.c_preset <-- select b 1 0]];
        33, finish @ write_reg status] in
      (* Line-mode XFER in progress: [xremaining] counts data bits. *)
      let xcrc = bit xmode.value 6 in
      let l_msb = bit xmode.value 2 and l_drive = bit xmode.value 3 and l_samp = bit xmode.value 4 in
      let tx_only = l_drive &: ~:l_samp in
      let runv = l.s_run.value and lastv = l.s_last.value and lost = l.a_lost.value in
      (* Run length after line bit [x]: either polarity (CAN) counts equal
         bits, ones mode (USB, HDLC) counts 1s. *)
      let run_after x = mux2 stuff_any (mux2 (x ==: lastv) (runv +:. 1) (of_int ~width:4 1))
          (mux2 x (runv +:. 1) (zero 4)) in
      let stuff_value = stuff_any &: ~:lastv in
      let run_reset = mux2 stuff_any (of_int ~width:4 1) (zero 4) in
      (* One data bit done: the XFER ends after its last data bit unless a
         stuff bit is due, which it then carries as a trailing stuff bit. *)
      let count_data threshold run_new =
        let remaining = xremaining.value -:. 1 in
        [if_ (remaining ==:. 0)
           [if_ (stuff_en &: (run_new ==: threshold)) [xremaining <--. 1; l.l_trail <--. 1]
              ([xremaining <--. 0] @ finish)]
           [xremaining <-- remaining]] in
      let end_stuff = [when_ l.l_trail.value ([xremaining <--. 0; l.l_trail <--. 0] @ finish)] in
      let is_stuff = stuff_en &: (runv ==: run_n) in
      let rx_is_stuff = stuff_en &: (runv ==: rx_run_n) in
      let data = mux2 lost vdd (tx_bit l_msb tx.value) in
      let line_bit = mux2 lost vdd (mux2 is_stuff stuff_value data) in
      let level = l.l_level.value in
      let encoded = mux code
          [line_bit; mux2 line_bit level (~:level);
           (if mutated Line_unit.Manchester_halves then line_bit else ~:line_bit); line_bit] in
      let boundary = [when_ (boundary_tick &: l_drive)
          [l.l_level <-- encoded; values <-- write_pair values_now encoded; l.l_man <-- is_man;
           l.l_cell <-- line_bit; l.l_seen <--. 1;
           when_ (~:is_stuff) [tx <-- shift_tx l_msb];
           when_ tx_only
             [if_ is_stuff ([l.s_run <-- run_reset; l.s_last <-- stuff_value] @ end_stuff)
                ([when_ xcrc (crc_step l_msb data); l.s_run <-- run_after data; l.s_last <-- data]
                 @ count_data run_n (run_after data))]]] in
      let raw = pin_of in_pin and pair_raw = pin_of ck_pin in
      let se0 = se0_end &: pair &: ~:raw &: ~:pair_raw in
      let decoded = mux2 is_nrzi
          (if mutated Line_unit.Nrzi_decode then raw ^: l.l_rx_prev.value
           else ~:(raw ^: l.l_rx_prev.value)) raw in
      let middle = [when_ (mid_tick &: l_samp &: (~:l_drive |: l.l_seen.value))
          [if_ se0
             ([l.l_se0 <--. 1; l.l_rem <-- select xremaining.value 5 0; xremaining <--. 0;
               l.l_trail <--. 0] @ finish)
             [l.l_rx_prev <-- raw;
              when_ ((if mutated Line_unit.Arbitration_off then gnd else arb)
                     &: l_drive &: l.l_cell.value &: ~:raw) [l.a_lost <--. 1];
              if_ rx_is_stuff
                ([when_ (decoded <>: stuff_value) [l.s_err <--. 1]; l.s_run <-- run_reset;
                  l.s_last <-- decoded] @ end_stuff)
                ([rx <-- sample l_msb decoded; when_ xcrc (crc_step l_msb decoded);
                  l.s_run <-- run_after decoded; l.s_last <-- decoded]
                 @ count_data rx_run_n (run_after decoded))]]] in
      let line_start = List.filter_map (fun (name, (v:Variable.t)) ->
          if name = "crc_state" && mutated Line_unit.Start_keeps_crc then None else Some (v <--. 0))
          (line_register_list l) in
      values_now, line_ops, ticker @ man_flip, line_start, boundary @ middle, crc_step,
      issue &: (op ==:. 33) in
  let ordinary = [if_ ((pc.value >=: image_length) |: ~:valid)
      (fail (mux2 (pc.value >=: image_length) (of_int ~width:8 2) (of_int ~width:8 1)))
      [switch op (List.map (fun (code, body) -> of_int ~width:8 code, body) ([
        0,finish;
        1,finish @ [running <--. 0; enables <--. 0];
        2,finish @ [values <-- low8];
        3,finish @ [enables <-- low8];
        4,finish @ [timer <-- imm24];
        5,(if not split_decode
           then [pc <-- sat imm24; completed <-- step (completed.value +:. 1); blocked <--. 0]
           else [pc <-- sat imm24; blocked <--. 0]);
        6,[when_ i.tx_valid (finish @ [tx <-- i.tx_data])];
        7,[if_ i.rx_ready finish [when_ (a ==:. 1) (fail (of_int ~width:8 4))]];
        8,finish @ [values <-- write_pin values_now pin (tx_bit (bit c 0) tx.value);
                    tx <-- shift_tx (bit c 0)];
        9,finish @ [rx <-- sample (bit c 0) pin_input];
        10,finish @ [repeat <-- imm16];
        11,finish @ [when_ (repeat.value <>:. 0)
                      [repeat <-- step (repeat.value -:. 1); pc <-- sat imm24]];
        12,finish @ [limit <-- imm24];
        13,[blocked_step (~:waiting_pin)];
        14,finish;
        15,[blocked_step i.event];
        16,finish @ [pins <-- select word 8 0];
        17,(match lu with
            | None ->
            [xremaining <-- sll (uresize a 7) 1;
            xtick <-- b; xperiod <-- b; xmode <-- select c 4 0;
            values <-- write_pin values.value (select pins.value 2 0) (bit c 0);
            when_ ((~:(bit c 1)) &: bit c 3)
              [values <-- write_pin
                 (write_pin values.value (select pins.value 2 0) (bit c 0))
                 (select pins.value 5 3) (tx_bit (bit c 2) tx.value)]]
            | Some l ->
              (* A line XFER only arms the unit (the ticker keeps running); a
                 classic XFER takes over the shared tick/period registers and
                 stops the ticker. *)
              [if_ (bit c 5)
                 [xremaining <-- uresize a 7; xmode <-- select c 6 0;
                  l.l_seen <--. 0; l.l_trail <--. 0]
                 [xremaining <-- sll (uresize a 7) 1;
                  xtick <-- b; xperiod <-- b; xmode <-- select c 6 0;
                  values <-- write_pin values_now (select pins.value 2 0) (bit c 0);
                  when_ ((~:(bit c 1)) &: bit c 3)
                    [values <-- write_pin
                       (write_pin values_now (select pins.value 2 0) (bit c 0))
                       (select pins.value 5 3) (tx_bit (bit c 2) tx.value)];
                  l.l_run <--. 0]]);
        18,finish @ write_reg source;
        19,finish @ write_reg (uresize imm16 width);
        20,finish @ write_reg (destination +: source);
        21,finish @ write_reg (destination ^: source);
        22,finish @ write_reg (destination &: source);
        23,finish @ write_reg (destination |: source);
        24,finish @ write_reg (if byte_lane then byte_shift sll destination c
                               else log_shift sll destination c);
        25,finish @ write_reg (if byte_lane then byte_shift srl destination c
                               else log_shift srl destination c);
        26,finish @ [when_ (destination ==:. 0) [pc <-- sat (uresize imm16 24)]];
        27,finish @ write_reg (~:destination);
        28,finish @ write_reg (uresize i.timestamp width);
        29,fail low8;
      ] @ line_ops))]] in
  let ck = select pins.value 2 0 and out = select pins.value 5 3
  and inp = select pins.value 8 6 in
  let active_edge = ~:(bit xremaining.value 0) in
  let cpol = bit xmode.value 0 and cpha = bit xmode.value 1
  and msb = bit xmode.value 2 and drive = bit xmode.value 3 and sampling = bit xmode.value 4 in
  let sample_edge = active_edge ^: cpha in
  let new_clock = cpol ^: active_edge in
  let clock_values = write_pin values_now ck new_clock in
  let shifting = mux2 cpha active_edge (~:active_edge) &: drive in
  let output_data = mux2 cpha tx.value (shift_tx msb) in
  let update_out = shifting &: (cpha |: (xremaining.value <>:. 1)) in
  let transfer = match lu with
    | None ->
  [if_ (xtick.value >:. 1) [xtick <-- xtick.value -:. 1]
    [xtick <-- xperiod.value; xremaining <-- xremaining.value -:. 1;
     values <-- mux2 update_out
       (write_pin clock_values out (tx_bit msb output_data)) clock_values;
     when_ shifting [tx <-- shift_tx msb];
     when_ (sampling &: sample_edge)
       [rx <-- sample msb (mux inp (List.init 8 (bit i.pins)))];
     when_ (xremaining.value ==:. 1) finish]]
    | Some _ ->
      (* Classic XFER with the CRC bit: a drive-only XFER feeds each data
         bit as it is shifted out, a sampling XFER each sampled bit. A line
         XFER runs [line_transfer] instead. *)
      let xcrc = bit xmode.value 6 in
      let input_bit = mux inp (List.init 8 (bit i.pins)) in
      let classic =
        [if_ (xtick.value >:. 1) [xtick <-- xtick.value -:. 1]
           [xtick <-- xperiod.value; xremaining <-- xremaining.value -:. 1;
            values <-- mux2 update_out
              (write_pin clock_values out (tx_bit msb output_data)) clock_values;
            when_ shifting
              ([tx <-- shift_tx msb; when_ (xcrc &: ~:sampling) (crc_step msb (tx_bit msb tx.value))]);
            when_ (sampling &: sample_edge)
              [rx <-- sample msb input_bit; when_ xcrc (crc_step msb input_bit)];
            when_ (xremaining.value ==:. 1) finish]] in
      [if_ (bit xmode.value 5) line_transfer classic] in
  compile [when_ (i.clear_fault &: ~:(running.value)) [fault <--. 0];
    if_ i.stop [running <--. 0; enables <--. 0; xremaining <--. 0]
      [if_ i.start
        ((if not split_decode then
          [pc <--. 0; running <--. 1; fault <--. 0; repeat <--. 0; timer <--. 0;
          limit <--. 65535; blocked <--. 0; values <--. 0; enables <--. 0;
          pins <--. 0; completed <--. 0; xremaining <--. 0; xtick <--. 0;
          xperiod <--. 0; xmode <--. 0]
          else
          [pc <--. 0; running <--. 1; fault <--. 0; repeat <--. 0; timer <--. 0;
          limit <--. 65535; blocked <--. 0; values <--. 0; enables <--. 0;
          pins <--. 0; xremaining <--. 0; xtick <--. 0;
          xperiod <--. 0; xmode <--. 0]) @ Array.to_list (Array.map (fun r -> r <--. 0) regs)
         @ line_start)
        [when_ active
           (line_pre @
           [if_ (timer.value <>:. 0) [timer <-- step (timer.value -:. 1)]
              [if_ (xremaining.value <>:. 0) transfer ordinary]])]]];
  if split_decode then begin
    (* The counter increments exactly where the branches above execute
       [finish] or JMP: in the else branches of STOP and START, with the
       engine running without a fault and no clear, no WAIT timer pending, and
       either the last edge of a transfer (tick <= 1, one edge left) or an
       in-image valid instruction that completes (every opcode except XFER and
       FAIL, PULL with TX valid, PUSH with RX ready, WAITPIN with the pin at
       its level, WAITEVENT with an event pending). *)
    let completes = ~:((op ==:. 17) |: (op ==:. 29))
      &: ((op <>:. 6) |: i.tx_valid) &: ((op <>:. 7) |: i.rx_ready)
      &: ((op <>:. 13) |: ~:waiting_pin) &: ((op <>:. 15) |: i.event) in
    let increment = running.value &: (fault.value ==:. 0) &: ~:gate &: (timer.value ==:. 0)
      &: mux2 (xremaining.value <>:. 0)
           ((xtick.value <=:. 1) &: (xremaining.value ==:. 1))
           ((pc.value <: image_length) &: valid &: completes) in
    compile [if_ i.stop [] [if_ i.start [completed <--. 0]
                               [when_ increment [completed <-- step (completed.value +:. 1)]]]]
  end;
  {pc=pc.value; running=running.value; fault=fault.value; stalled;
   tx_pop; rx_push; rx_data=rx.value; pin_values=values.value;
   pin_enables=enables.value; signal_events; consume_event;
   (* Without debug counters the completed register has no reader and is not
      emitted; READ_SELECT 5 reads zero. *)
   completed=(if options.debug_counters then completed.value else zero 32);
   issue; wait_timer=timer.value; wait_limit=limit.value; blocked_cycles=blocked.value;
   repeat_count=repeat.value; transfer_edges=xremaining.value;
   queue_observe=queue_status;
   line_state=(match lu with
       | None -> []
       | Some l -> List.map (fun (name, (v:Variable.t)) -> name, v.value) (line_register_list l))}

(* Observe the D input compiled from the single Always control tree above.
   Synchronous clear and asynchronous reset are register metadata, so include
   them explicitly.  Do not reproduce instruction decoding here: every branch,
   hold, fault and START must use precisely the same next state as the actual
   PC register.  Two register forms are accepted: the design of record
   (synchronous active-high clear to zero, no reset) and the asynchronous
   variants (active-high asynchronous reset to zero, no clear).  With an
   asynchronous reset the value after an edge at which reset is asserted is
   the reset value.  A 7-bit (saturating) PC is zero-extended to 24 bits. *)
let next_pc (engine:t) =
  match engine.pc with
  | Signal.Type.Reg {register;d;_} ->
    let is_zero = function
      | Signal.Type.Const {constant;_} -> Bits.to_int constant = 0
      | _ -> false in
    let clear_is_zero = is_zero register.reg_clear_value in
    let pc_width = width engine.pc in
    if is_empty register.reg_reset then begin
      if pc_width <> 24 && pc_width <> 7 || register.reg_clock_edge <> Edge.Rising
         || is_empty register.reg_clear
         || register.reg_clear_level <> Level.High || not clear_is_zero
         || not (is_vdd register.reg_enable)
      then invalid_arg "Engine.next_pc: unsupported PC register semantics";
      uresize (mux2 register.reg_clear register.reg_clear_value d) 24
    end else begin
      if pc_width <> 24 && pc_width <> 7 || register.reg_clock_edge <> Edge.Rising
         || not (is_empty register.reg_clear)
         || register.reg_reset_edge <> Edge.Rising || not (is_zero register.reg_reset_value)
         || not (is_vdd register.reg_enable)
      then invalid_arg "Engine.next_pc: unsupported PC register semantics";
      uresize (mux2 register.reg_reset register.reg_reset_value d) 24
    end
  | _ -> invalid_arg "Engine.next_pc: PC must remain a register"
