open Hardcaml
open Signal

type t = {
  pc : Always.Variable.t;
  running : Always.Variable.t;
  fault : Always.Variable.t;
  registers : Always.Variable.t array;
  repeat_count : Always.Variable.t;
  timer : Always.Variable.t;
  limit : Always.Variable.t;
  blocked : Always.Variable.t;
  values : Always.Variable.t;
  enables : Always.Variable.t;
  transfer_pins : Always.Variable.t;
  completed : Always.Variable.t;
  transfer_edges : Always.Variable.t;
  transfer_tick : Always.Variable.t;
  transfer_period : Always.Variable.t;
  transfer_mode : Always.Variable.t;
}

let named_register spec name width =
  let v = Always.Variable.reg spec ~width in
  ignore (v.value -- name : Signal.t);
  v

let create spec ~pc_width ~data_width ~transfer_mode_width =
  let r = named_register spec in
  { pc = r "pc" pc_width;
    running = r "running" 1;
    fault = r "fault_code" 8;
    registers = Array.map (fun name -> r name data_width) [| "tx"; "rx"; "x"; "y" |];
    repeat_count = r "repeat_count" 16;
    timer = r "wait_timer" 24;
    limit = r "wait_limit" 24;
    blocked = r "blocked_cycles" 24;
    values = r "logical_output" 8;
    enables = r "logical_enable" 8;
    transfer_pins = r "transfer_pins" 9;
    completed = r "completed_instructions" 32;
    transfer_edges = r "transfer_edges" 7;
    transfer_tick = r "transfer_tick" 8;
    transfer_period = r "transfer_period" 8;
    transfer_mode = r "transfer_mode" transfer_mode_width }

let tx t = t.registers.(0)
let rx t = t.registers.(1)

let write_register t ~dest data =
  let open Always in
  List.init 4 (fun n -> when_ (dest ==:. n) [t.registers.(n) <-- data])

let bitmask pin = log_shift sll (of_int ~width:8 1) pin
let write_pin v pin bit = (v &: ~:(bitmask pin)) |: mux2 bit (bitmask pin) (zero 8)

let shift_tx t ~msb =
  let tx = (tx t).value in
  mux2 msb (sll tx 1) (srl tx 1)

let tx_bit ~msb data = mux2 msb (bit data (width data - 1)) (bit data 0)

let sample t ~msb data =
  let rx = (rx t).value in
  let w = width rx in
  mux2 msb
    (concat_msb [select rx (w-2) 0; data])
    (concat_msb [data; select rx (w-1) 1])

let start t ~clear_completed =
  let open Always in
  [ t.pc <--. 0; t.running <--. 1; t.fault <--. 0; t.repeat_count <--. 0; t.timer <--. 0;
    t.limit <--. 65535; t.blocked <--. 0; t.values <--. 0; t.enables <--. 0;
    t.transfer_pins <--. 0 ]
  @ (if clear_completed then [ t.completed <--. 0 ] else [])
  @ [ t.transfer_edges <--. 0; t.transfer_tick <--. 0; t.transfer_period <--. 0;
      t.transfer_mode <--. 0 ]
  @ Array.to_list (Array.map (fun r -> r <--. 0) t.registers)
