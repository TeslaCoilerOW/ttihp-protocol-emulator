open Hardcaml
open Signal

module Registers = struct
  module Pre = struct
    type 'a t = {
      run : 'a;
      phase : 'a;
      frac : 'a;
      acc : 'a;
      boundary_seen : 'a;
      cfg : 'a;
      level : 'a;
      rx_prev : 'a;
      cell_bit : 'a;
      man_pending : 'a;
      se0 : 'a;
      remaining : 'a;
      trailing_stuff : 'a;
      stuff_run : 'a;
      stuff_last : 'a;
      stuff_error : 'a;
      arbitration_lost : 'a;
      crc_state : 'a;
      crc_preset : 'a;
    }
    [@@deriving sexp_of]

    let port_names_and_widths =
      { run = "line_run", 1; phase = "line_phase", 1; frac = "line_frac", 8;
        acc = "line_acc", 8; boundary_seen = "line_boundary_seen", 1; cfg = "line_cfg", 10;
        level = "line_level", 1; rx_prev = "line_rx_prev", 1; cell_bit = "line_cell_bit", 1;
        man_pending = "line_man_pending", 1; se0 = "line_se0", 1;
        remaining = "line_remaining", 6; trailing_stuff = "line_trailing_stuff", 1;
        stuff_run = "stuff_run", 4; stuff_last = "stuff_last", 1; stuff_error = "stuff_error", 1;
        arbitration_lost = "arbitration_lost", 1; crc_state = "crc_state", Line_unit.crc_width;
        crc_preset = "crc_preset", 2 }

    let map t ~f =
      { run = f t.run; phase = f t.phase; frac = f t.frac; acc = f t.acc;
        boundary_seen = f t.boundary_seen; cfg = f t.cfg; level = f t.level;
        rx_prev = f t.rx_prev; cell_bit = f t.cell_bit; man_pending = f t.man_pending;
        se0 = f t.se0; remaining = f t.remaining; trailing_stuff = f t.trailing_stuff;
        stuff_run = f t.stuff_run; stuff_last = f t.stuff_last; stuff_error = f t.stuff_error;
        arbitration_lost = f t.arbitration_lost; crc_state = f t.crc_state;
        crc_preset = f t.crc_preset }

    let map2 a b ~f =
      { run = f a.run b.run; phase = f a.phase b.phase; frac = f a.frac b.frac;
        acc = f a.acc b.acc; boundary_seen = f a.boundary_seen b.boundary_seen;
        cfg = f a.cfg b.cfg; level = f a.level b.level; rx_prev = f a.rx_prev b.rx_prev;
        cell_bit = f a.cell_bit b.cell_bit; man_pending = f a.man_pending b.man_pending;
        se0 = f a.se0 b.se0; remaining = f a.remaining b.remaining;
        trailing_stuff = f a.trailing_stuff b.trailing_stuff;
        stuff_run = f a.stuff_run b.stuff_run; stuff_last = f a.stuff_last b.stuff_last;
        stuff_error = f a.stuff_error b.stuff_error;
        arbitration_lost = f a.arbitration_lost b.arbitration_lost;
        crc_state = f a.crc_state b.crc_state; crc_preset = f a.crc_preset b.crc_preset }

    let iter t ~f = ignore (map t ~f : unit t)
    let iter2 a b ~f = ignore (map2 a b ~f : unit t)

    let to_list t =
      [ t.run; t.phase; t.frac; t.acc; t.boundary_seen; t.cfg; t.level; t.rx_prev;
        t.cell_bit; t.man_pending; t.se0; t.remaining; t.trailing_stuff; t.stuff_run;
        t.stuff_last; t.stuff_error; t.arbitration_lost; t.crc_state; t.crc_preset ]
  end

  include Pre
  include Interface.Make (Pre)

  let create spec =
    let registers = Of_always.reg spec in
    Of_always.apply_names registers;
    registers
end

type t = {
  registers : Always.Variable.t Registers.t;
  values_now : Signal.t;
  every_cycle : Always.t list;
  ltim : Always.t list;
  lcfg : Always.t list;
  crc : Always.t list;
  lstat : Always.t list;
  on_start : Always.t list;
  transfer : Always.t list;
  crc_step : msb:Signal.t -> Signal.t -> Always.t list;
}

let create ?mutation (l : Always.Variable.t Registers.t) (d : Engine_datapath.t)
    ~(fields : Engine_decode.fields) ~source ~finish ~pins ~tx_valid ~rx_ready =
  let open Always in
  let mutated m = mutation = Some m in
  let { Engine_decode.word; b; c; dest; _ } = fields in
  let write_register = Engine_datapath.write_register d ~dest in
  let tx = Engine_datapath.tx d and rx = Engine_datapath.rx d in
  let values = d.values and xtick = d.transfer_tick and xperiod = d.transfer_period in
  let xremaining = d.transfer_edges and xmode = d.transfer_mode in
  let cfg = l.cfg.value in
  let code = select cfg 1 0 in
  let is_nrzi = code ==:. 1 and is_man = code ==:. 2 in
  let stuff_en = bit cfg 2 and stuff_any = bit cfg 3 in
  let run_n = uresize (select cfg 6 4) 4 +:. (if mutated Line_unit.Stuff_run then 2 else 1) in
  let rx_run_n = if mutated Line_unit.Rx_destuff_run then run_n +:. 1 else run_n in
  let pair = bit cfg 7 and arb = bit cfg 8 and se0_end = bit cfg 9 in
  let ck_pin = select d.transfer_pins.value 2 0 and out_pin = select d.transfer_pins.value 5 3
  and in_pin = select d.transfer_pins.value 8 6 in
  let pin_of p = mux p (List.init 8 (bit pins)) in
  (* Ticker: a tick in every cycle in which the count is 1; ticks alternate
     bit boundary (phase 0) and mid-bit (phase 1). *)
  let tick = l.run.value &: (xtick.value ==:. 1) in
  let boundary_tick = tick &: ~:(l.phase.value) and mid_tick = tick &: l.phase.value in
  (* Drive the data pin, and with the pair set its complement on the pair pin. *)
  let write_pair v level =
    let v1 = Engine_datapath.write_pin v out_pin level in
    let v1 =
      if mutated Line_unit.Pin_leak then Engine_datapath.write_pin v1 (out_pin +:. 1) level
      else v1 in
    mux2 pair (Engine_datapath.write_pin v1 ck_pin (~:level)) v1 in
  let flip = mid_tick &: l.man_pending.value in
  let flipped = write_pair values.value l.cell_bit.value in
  let values_now = mux2 flip flipped values.value in
  let crc_step ~msb d =
    [l.crc_state <-- Line_unit.crc_next ?mutation ~msb
       ~poly:(Line_unit.preset_polynomial ~msb l.crc_preset.value) l.crc_state.value d] in
  let sum = uresize l.acc.value 9 +: uresize l.frac.value 9 in
  let ticker = [when_ l.run.value
      [if_ (xtick.value <=:. 1)
         [xtick <-- xperiod.value
                    +: uresize (if mutated Line_unit.Fraction_carry then gnd else bit sum 8) 8;
          l.phase <-- ~:(l.phase.value);
          l.acc <-- select sum 7 0]
         [xtick <-- xtick.value -:. 1]]] in
  let man_flip = [when_ flip [l.level <-- l.cell_bit.value; values <-- flipped; l.man_pending <--. 0]] in
  (* Execute bodies of opcodes 30-33. *)
  let status = concat_msb [zero 18; l.remaining.value; gnd; l.run.value; l.level.value;
                           rx_ready; tx_valid; l.stuff_error.value; l.arbitration_lost.value;
                           l.se0.value] in
  let p = select word 7 0 and q = select word 15 8 and first = select word 23 16 in
  let ltim = finish @ [xperiod <-- p; xtick <-- mux2 (first ==:. 0) p first; l.frac <-- q;
                       l.run <-- (p <>:. 0); l.acc <--. 0; l.phase <--. 0] in
  let lcfg = finish @ [l.cfg <-- select word 9 0; l.level <-- bit word 10;
                       l.rx_prev <-- bit word 10; l.stuff_run <--. 0; l.stuff_last <-- bit word 10;
                       l.se0 <--. 0; l.stuff_error <--. 0; l.arbitration_lost <--. 0;
                       l.remaining <--. 0; l.man_pending <--. 0; l.cell_bit <--. 0] in
  let crc = finish @ [when_ (select c 1 0 ==:. 1) [l.crc_state <-- select source (Line_unit.crc_width-1) 0];
                      when_ (select c 1 0 ==:. 2)
                        (write_register (uresize l.crc_state.value (width tx.value)));
                      when_ (select c 1 0 ==:. 3) [l.crc_preset <-- select b 1 0]] in
  let lstat = finish @ write_register status in
  (* Line-mode XFER in progress: [xremaining] counts data bits. *)
  let xcrc = bit xmode.value 6 in
  let l_msb = bit xmode.value 2 and l_drive = bit xmode.value 3 and l_samp = bit xmode.value 4 in
  let tx_only = l_drive &: ~:l_samp in
  let runv = l.stuff_run.value and lastv = l.stuff_last.value and lost = l.arbitration_lost.value in
  (* Run length after line bit [x]: either polarity (CAN) counts equal bits,
     ones mode (USB, HDLC) counts 1s. *)
  let run_after x = mux2 stuff_any (mux2 (x ==: lastv) (runv +:. 1) (of_int ~width:4 1))
      (mux2 x (runv +:. 1) (zero 4)) in
  let stuff_value = stuff_any &: ~:lastv in
  let run_reset = mux2 stuff_any (of_int ~width:4 1) (zero 4) in
  (* One data bit done: the XFER ends after its last data bit unless a stuff
     bit is due, which it then carries as a trailing stuff bit. *)
  let count_data threshold run_new =
    let remaining = xremaining.value -:. 1 in
    [if_ (remaining ==:. 0)
       [if_ (stuff_en &: (run_new ==: threshold)) [xremaining <--. 1; l.trailing_stuff <--. 1]
          ([xremaining <--. 0] @ finish)]
       [xremaining <-- remaining]] in
  let end_stuff = [when_ l.trailing_stuff.value ([xremaining <--. 0; l.trailing_stuff <--. 0] @ finish)] in
  let is_stuff = stuff_en &: (runv ==: run_n) in
  let rx_is_stuff = stuff_en &: (runv ==: rx_run_n) in
  let data = mux2 lost vdd (Engine_datapath.tx_bit ~msb:l_msb tx.value) in
  let line_bit = mux2 lost vdd (mux2 is_stuff stuff_value data) in
  let level = l.level.value in
  let encoded = mux code
      [line_bit; mux2 line_bit level (~:level);
       (if mutated Line_unit.Manchester_halves then line_bit else ~:line_bit); line_bit] in
  let boundary = [when_ (boundary_tick &: l_drive)
      [l.level <-- encoded; values <-- write_pair values_now encoded; l.man_pending <-- is_man;
       l.cell_bit <-- line_bit; l.boundary_seen <--. 1;
       when_ (~:is_stuff) [tx <-- Engine_datapath.shift_tx d ~msb:l_msb];
       when_ tx_only
         [if_ is_stuff ([l.stuff_run <-- run_reset; l.stuff_last <-- stuff_value] @ end_stuff)
            ([when_ xcrc (crc_step ~msb:l_msb data); l.stuff_run <-- run_after data;
              l.stuff_last <-- data]
             @ count_data run_n (run_after data))]]] in
  let raw = pin_of in_pin and pair_raw = pin_of ck_pin in
  let se0 = se0_end &: pair &: ~:raw &: ~:pair_raw in
  let decoded = mux2 is_nrzi
      (if mutated Line_unit.Nrzi_decode then raw ^: l.rx_prev.value
       else ~:(raw ^: l.rx_prev.value)) raw in
  let middle = [when_ (mid_tick &: l_samp &: (~:l_drive |: l.boundary_seen.value))
      [if_ se0
         ([l.se0 <--. 1; l.remaining <-- select xremaining.value 5 0; xremaining <--. 0;
           l.trailing_stuff <--. 0] @ finish)
         [l.rx_prev <-- raw;
          when_ ((if mutated Line_unit.Arbitration_off then gnd else arb)
                 &: l_drive &: l.cell_bit.value &: ~:raw) [l.arbitration_lost <--. 1];
          if_ rx_is_stuff
            ([when_ (decoded <>: stuff_value) [l.stuff_error <--. 1]; l.stuff_run <-- run_reset;
              l.stuff_last <-- decoded] @ end_stuff)
            ([rx <-- Engine_datapath.sample d ~msb:l_msb decoded;
              when_ xcrc (crc_step ~msb:l_msb decoded);
              l.stuff_run <-- run_after decoded; l.stuff_last <-- decoded]
             @ count_data rx_run_n (run_after decoded))]]] in
  let on_start =
    let clear = Registers.map l ~f:(fun v -> [v <--. 0]) in
    let clear = if mutated Line_unit.Start_keeps_crc then { clear with crc_state = [] } else clear in
    List.concat (Registers.to_list clear) in
  { registers = l; values_now; every_cycle = ticker @ man_flip; ltim; lcfg; crc; lstat;
    on_start; transfer = boundary @ middle; crc_step }
