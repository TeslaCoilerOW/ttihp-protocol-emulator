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
}

let create (config:Config.t) (i:inputs) =
  let open Always in
  let width = config.data_width in
  let spec = Reg_spec.create ~clock:i.clock ~clear:i.clear () in
  let r name w = let v = Variable.reg spec ~width:w in
    ignore (v.value -- name); v in
  let pc = r "pc" 24 and running = r "running" 1 and fault = r "fault_code" 8 in
  let image_length = uresize i.image_length 24 in
  let regs = Array.init 4 (fun n -> r ([|"tx";"rx";"x";"y"|].(n)) width) in
  let tx = regs.(0) and rx = regs.(1) in
  let repeat = r "repeat_count" 16 and timer = r "wait_timer" 24 in
  let limit = r "wait_limit" 24 and blocked = r "blocked_cycles" 24 in
  let values = r "logical_output" 8 and enables = r "logical_enable" 8 in
  let pins = r "transfer_pins" 9 and completed = r "completed_instructions" 32 in
  let xremaining = r "transfer_edges" 7 and xtick = r "transfer_tick" 8 in
  let xperiod = r "transfer_period" 8 and xmode = r "transfer_mode" 5 in
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
  let valid = mux op (List.init 256 (fun code ->
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
      (a <>:. 0) &: (a <=:. width) &: (b <>:. 0) &: (c <:. 32)
      &: owned ck &: ((~:(bit c 3)) |: owned out)
      &: ((~:(bit c 3)) |: (ck <>: out))
    | 18|20|21|22|23 -> reg_pair
    | 19|26 -> a <:. 4
    | 24|25 -> (a <:. 4) &: (b ==:. 0) &: (c <:. width)
    | 27|28 -> (a <:. 4) &: bc_zero
    | 29 -> (select word 23 8 ==:. 0) &: (low8 <>:. 0)
    | _ -> gnd)) in
  let active = running.value &: (fault.value ==:. 0) &: ~:(i.start) &: ~:(i.stop) &: ~:(i.clear) in
  let issue = active &: (timer.value ==:. 0) &: (xremaining.value ==:. 0)
              &: (pc.value <: image_length) &: valid in
  let waiting_pin = pin_input <>: bit b 0 in
  let stalled = issue
                &: (((op ==:. 6) &: ~:(i.tx_valid)) |: ((op ==:. 7) &: (a ==:. 0) &: ~:(i.rx_ready))
                    |: ((op ==:. 13) &: waiting_pin) |: ((op ==:. 15) &: ~:(i.event))) in
  let tx_pop = issue &: (op ==:. 6) &: i.tx_valid in
  let rx_push = issue &: (op ==:. 7) &: i.rx_ready in
  let consume_event = issue &: (op ==:. 15) &: i.event in
  let signal_events = mux2 (issue &: (op ==:. 14))
      (uresize imm24 config.engine_count) (zero config.engine_count) in
  let finish = [pc <-- pc.value +:. 1; completed <-- completed.value +:. 1; blocked <--. 0] in
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
          (fail (of_int ~width:8 3)) [blocked <-- blocked.value +:. 1]] in
  let ordinary = [if_ ((pc.value >=: image_length) |: ~:valid)
      (fail (mux2 (pc.value >=: image_length) (of_int ~width:8 2) (of_int ~width:8 1)))
      [switch op (List.map (fun (code, body) -> of_int ~width:8 code, body) [
        0,finish;
        1,finish @ [running <--. 0; enables <--. 0];
        2,finish @ [values <-- low8];
        3,finish @ [enables <-- low8];
        4,finish @ [timer <-- imm24];
        5,[pc <-- imm24; completed <-- completed.value +:. 1; blocked <--. 0];
        6,[when_ i.tx_valid (finish @ [tx <-- i.tx_data])];
        7,[if_ i.rx_ready finish [when_ (a ==:. 1) (fail (of_int ~width:8 4))]];
        8,finish @ [values <-- write_pin values.value pin (tx_bit (bit c 0) tx.value);
                    tx <-- shift_tx (bit c 0)];
        9,finish @ [rx <-- sample (bit c 0) pin_input];
        10,finish @ [repeat <-- imm16];
        11,finish @ [when_ (repeat.value <>:. 0)
                      [repeat <-- repeat.value -:. 1; pc <-- imm24]];
        12,finish @ [limit <-- imm24];
        13,[blocked_step (~:waiting_pin)];
        14,finish;
        15,[blocked_step i.event];
        16,finish @ [pins <-- select word 8 0];
        17,[xremaining <-- sll (uresize a 7) 1;
            xtick <-- b; xperiod <-- b; xmode <-- select c 4 0;
            values <-- write_pin values.value (select pins.value 2 0) (bit c 0);
            when_ ((~:(bit c 1)) &: bit c 3)
              [values <-- write_pin
                 (write_pin values.value (select pins.value 2 0) (bit c 0))
                 (select pins.value 5 3) (tx_bit (bit c 2) tx.value)]];
        18,finish @ write_reg source;
        19,finish @ write_reg (uresize imm16 width);
        20,finish @ write_reg (destination +: source);
        21,finish @ write_reg (destination ^: source);
        22,finish @ write_reg (destination &: source);
        23,finish @ write_reg (destination |: source);
        24,finish @ write_reg (log_shift sll destination c);
        25,finish @ write_reg (log_shift srl destination c);
        26,finish @ [when_ (destination ==:. 0) [pc <-- uresize imm16 24]];
        27,finish @ write_reg (~:destination);
        28,finish @ write_reg (uresize i.timestamp width);
        29,fail low8;
      ])]] in
  let ck = select pins.value 2 0 and out = select pins.value 5 3
  and inp = select pins.value 8 6 in
  let active_edge = ~:(bit xremaining.value 0) in
  let cpol = bit xmode.value 0 and cpha = bit xmode.value 1
  and msb = bit xmode.value 2 and drive = bit xmode.value 3 and sampling = bit xmode.value 4 in
  let sample_edge = active_edge ^: cpha in
  let new_clock = cpol ^: active_edge in
  let clock_values = write_pin values.value ck new_clock in
  let shifting = mux2 cpha active_edge (~:active_edge) &: drive in
  let output_data = mux2 cpha tx.value (shift_tx msb) in
  let update_out = shifting &: (cpha |: (xremaining.value <>:. 1)) in
  let transfer = [if_ (xtick.value >:. 1) [xtick <-- xtick.value -:. 1]
    [xtick <-- xperiod.value; xremaining <-- xremaining.value -:. 1;
     values <-- mux2 update_out
       (write_pin clock_values out (tx_bit msb output_data)) clock_values;
     when_ shifting [tx <-- shift_tx msb];
     when_ (sampling &: sample_edge)
       [rx <-- sample msb (mux inp (List.init 8 (bit i.pins)))];
     when_ (xremaining.value ==:. 1) finish]] in
  compile [when_ (i.clear_fault &: ~:(running.value)) [fault <--. 0];
    if_ i.stop [running <--. 0; enables <--. 0; xremaining <--. 0]
      [if_ i.start
        ([pc <--. 0; running <--. 1; fault <--. 0; repeat <--. 0; timer <--. 0;
          limit <--. 65535; blocked <--. 0; values <--. 0; enables <--. 0;
          pins <--. 0; completed <--. 0; xremaining <--. 0; xtick <--. 0;
          xperiod <--. 0; xmode <--. 0] @ Array.to_list (Array.map (fun r -> r <--. 0) regs))
        [when_ active
           [if_ (timer.value <>:. 0) [timer <-- timer.value -:. 1]
              [if_ (xremaining.value <>:. 0) transfer ordinary]]]]];
  {pc=pc.value; running=running.value; fault=fault.value; stalled;
   tx_pop; rx_push; rx_data=rx.value; pin_values=values.value;
   pin_enables=enables.value; signal_events; consume_event; completed=completed.value;
   issue; wait_timer=timer.value; wait_limit=limit.value; blocked_cycles=blocked.value;
   repeat_count=repeat.value; transfer_edges=xremaining.value}

(* Observe the D input compiled from the single Always control tree above.
   Synchronous clear is register metadata, so include it explicitly.  Do not
   reproduce instruction decoding here: every branch, hold, fault and START
   must use precisely the same next state as the actual PC register. *)
let next_pc (engine:t) =
  match engine.pc with
  | Signal.Type.Reg {register;d;_} ->
    let clear_is_zero = match register.reg_clear_value with
      | Signal.Type.Const {constant;_} -> Bits.to_int constant = 0
      | _ -> false in
    if width engine.pc <> 24 || register.reg_clock_edge <> Edge.Rising
       || not (is_empty register.reg_reset) || is_empty register.reg_clear
       || register.reg_clear_level <> Level.High || not clear_is_zero
       || not (is_vdd register.reg_enable)
    then invalid_arg "Engine.next_pc: unsupported PC register semantics";
    mux2 register.reg_clear register.reg_clear_value d
  | _ -> invalid_arg "Engine.next_pc: PC must remain a register"
