open Hardcaml
open Signal

type t = {
  word : Signal.t; write : Signal.t; window : Signal.t;
  read_word : Signal.t; read_lock : Signal.t; outputs : Signal.t;
}

(* The host port is synchronous to clk. Snapshots survive arbitrary read stalls;
   a read FIFO is only popped after its eighth accepted nibble. *)
let create ~clock ~clear ~ui ~write_ready ~read_valid ~read_data ~irq ~fault =
  let open Always in
  let spec = Reg_spec.create ~clock ~clear () in
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
  {word; write; window; read_word; read_lock; outputs}
