open Hardcaml
open Signal

type t = {
  word : Signal.t; write : Signal.t; window : Signal.t;
  read_word : Signal.t; read_lock : Signal.t; outputs : Signal.t;
  last_nibble_strobe : Signal.t;
}

(* The host port is synchronous to clk. Snapshots survive arbitrary read stalls;
   a read FIFO is only popped after its eighth accepted nibble.
   With [async] (false in [create]) [clear] is an asynchronous reset for the host
   registers instead of a synchronous clear; it still gates ready/valid. *)
(* Timing knob host_nibble_slots. The shifting write buffer above loads every
   bit from its neighbour, a direct flop-to-flop path that hold repair pads
   with delay cells on the same nets that feed the command decoder. Here
   nibble i of a word (i = 0..6) is loaded in place into slot i and the eighth
   nibble is used live from ui, so no slot loads from another. The completed
   word, {ui[3:0], slot6, ..., slot0}, equals the shifting buffer's
   {ui[3:0], buffer[31:4]}: both hold the seven earlier nibbles of the current
   word in arrival order. Partial words, which nothing consumes, differ. *)
let slots_impl ~split ~strobe_gate ~async ~clock ~clear ~ui ~write_ready ~read_valid ~read_data ~irq ~fault =
  let open Always in
  let spec = if async then Reg_spec.create ~clock ~reset:clear ()
    else Reg_spec.create ~clock ~clear () in
  let reg w = Variable.reg spec ~width:w in
  let previous_window = reg 2 and write_index = reg 3 in
  let slots = Array.init 7 (fun i ->
      let v = reg 4 in ignore (v.value -- Printf.sprintf "host_nibble_%d" i); v) in
  let read_index = reg 3 and snapshot = reg 32 and presenting = reg 1 in
  let window = select ui 7 6 in
  let changed = window <>: previous_window.value in
  let wr_ready = write_ready &: ~:changed &: ~:clear in
  let wr_accept = wr_ready &: bit ui 4 in
  let rd_valid = presenting.value &: ~:changed &: ~:clear in
  let rd_accept = rd_valid &: bit ui 5 in
  let word = concat_msb (select ui 3 0
      :: List.rev_map (fun (v:Variable.t) -> v.value) (Array.to_list slots)) in
  let write = wr_accept &: (write_index.value ==:. 7) in
  let read_word = rd_accept &: (read_index.value ==:. 7) in
  let read_lock = (window ==:. 3) &: ~:changed in
  compile ([previous_window <-- window;
    if_ changed [write_index <--. 0; read_index <--. 0; presenting <--. 0]
      [when_ wr_accept [write_index <-- write_index.value +:. 1];
       when_ ((~:(presenting.value)) &: read_valid)
         [snapshot <-- read_data; read_index <--. 0; presenting <--. 1];
       when_ rd_accept [read_index <-- read_index.value +:. 1;
                       when_ (read_index.value ==:. 7) [presenting <--. 0]]]]
    @ Array.to_list (Array.mapi (fun i (slot:Variable.t) ->
        when_ (wr_accept &: (write_index.value ==:. i)) [slot <-- select ui 3 0]) slots));
  let nibble = mux read_index.value
      (List.init 8 (fun n -> select snapshot.value (4*n+3) (4*n))) in
  let outputs = concat_msb [fault; irq; rd_valid; wr_ready; nibble] in
  let last_nibble_strobe = if split
    then bit ui 4 &: ~:changed &: ~:strobe_gate &: (write_index.value ==:. 7) else write in
  {word; write; window; read_word; read_lock; outputs; last_nibble_strobe}

let create_with ~timing ~strobe_gate ~async ~clock ~clear ~ui ~write_ready
    ~read_valid ~read_data ~irq ~fault =
  if timing.Timing_options.host_nibble_slots then
    slots_impl ~split:timing.split_command_decode ~strobe_gate ~async ~clock ~clear ~ui
      ~write_ready ~read_valid ~read_data ~irq ~fault
  else
  let open Always in
  let spec = if async then Reg_spec.create ~clock ~reset:clear ()
    else Reg_spec.create ~clock ~clear () in
  let reg w = Variable.reg spec ~width:w in
  let previous_window = reg 2 and write_index = reg 3 and write_buffer = reg 32 in
  let read_index = reg 3 and snapshot = reg 32 and presenting = reg 1 in
  let window = select ui 7 6 in
  let changed = window <>: previous_window.value in
  let wr_ready = write_ready &: ~:changed &: ~:clear in
  let wr_accept = wr_ready &: bit ui 4 in
  let rd_valid = presenting.value &: ~:changed &: ~:clear in
  let rd_accept = rd_valid &: bit ui 5 in
  let word = concat_msb [select ui 3 0; select write_buffer.value 31 4] in
  let write = wr_accept &: (write_index.value ==:. 7) in
  let read_word = rd_accept &: (read_index.value ==:. 7) in
  let read_lock = (window ==:. 3) &: ~:changed in
  compile [previous_window <-- window;
    if_ changed [write_index <--. 0; write_buffer <--. 0;
                 read_index <--. 0; presenting <--. 0]
      [when_ wr_accept [write_buffer <-- word; write_index <-- write_index.value +:. 1];
       when_ ((~:(presenting.value)) &: read_valid)
         [snapshot <-- read_data; read_index <--. 0; presenting <--. 1];
       when_ rd_accept [read_index <-- read_index.value +:. 1;
                       when_ (read_index.value ==:. 7) [presenting <--. 0]]]];
  let nibble = mux read_index.value
      (List.init 8 (fun n -> select snapshot.value (4*n+3) (4*n))) in
  let outputs = concat_msb [fault; irq; rd_valid; wr_ready; nibble] in
  (* Timing knob split_command_decode: the raw strobe of a word's last nibble,
     so that [write = write_ready & last_nibble_strobe]. Without the knob no
     signal is added and the field repeats [write]. *)
  let last_nibble_strobe = if timing.split_command_decode
    then bit ui 4 &: ~:changed &: ~:strobe_gate &: (write_index.value ==:. 7) else write in
  {word; write; window; read_word; read_lock; outputs; last_nibble_strobe}

let create ~clock ~clear ~ui ~write_ready ~read_valid ~read_data ~irq ~fault =
  create_with ~timing:Timing_options.default ~strobe_gate:clear ~async:false ~clock ~clear ~ui ~write_ready
    ~read_valid ~read_data ~irq ~fault
