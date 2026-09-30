open Hardcaml
open Signal

let mode_width ~line_unit = if line_unit then 7 else 5

let issue (d : Engine_datapath.t) ~(fields : Engine_decode.fields) ~values_now
    ~(line : Engine_line.t option) =
  let open Always in
  let { Engine_decode.a; b; c; _ } = fields in
  let pins = d.transfer_pins.value in
  let mode = select c (width d.transfer_mode.value - 1) 0 in
  let classic =
    [d.transfer_edges <-- sll (uresize a 7) 1;
     d.transfer_tick <-- b; d.transfer_period <-- b; d.transfer_mode <-- mode;
     d.values <-- Engine_datapath.write_pin values_now (select pins 2 0) (bit c 0);
     when_ ((~:(bit c 1)) &: bit c 3)
       [d.values <-- Engine_datapath.write_pin
          (Engine_datapath.write_pin values_now (select pins 2 0) (bit c 0))
          (select pins 5 3)
          (Engine_datapath.tx_bit ~msb:(bit c 2) (Engine_datapath.tx d).value)]] in
  match line with
  | None -> classic
  | Some l ->
    [if_ (bit c 5)
       [d.transfer_edges <-- uresize a 7; d.transfer_mode <-- select c 6 0;
        l.registers.boundary_seen <--. 0; l.registers.trailing_stuff <--. 0]
       (classic @ [l.registers.run <--. 0])]

let transfer (d : Engine_datapath.t) ~pins ~values_now ~finish ~(line : Engine_line.t option) =
  let open Always in
  let tx = Engine_datapath.tx d and rx = Engine_datapath.rx d in
  let xtick = d.transfer_tick and xremaining = d.transfer_edges in
  let mode = d.transfer_mode.value in
  let ck = select d.transfer_pins.value 2 0 and out = select d.transfer_pins.value 5 3
  and inp = select d.transfer_pins.value 8 6 in
  let active_edge = ~:(bit xremaining.value 0) in
  let cpol = bit mode 0 and cpha = bit mode 1 and msb = bit mode 2
  and drive = bit mode 3 and sampling = bit mode 4 in
  let sample_edge = active_edge ^: cpha in
  let new_clock = cpol ^: active_edge in
  let clock_values = Engine_datapath.write_pin values_now ck new_clock in
  let shifting = mux2 cpha active_edge (~:active_edge) &: drive in
  let output_data = mux2 cpha tx.value (Engine_datapath.shift_tx d ~msb) in
  let update_out = shifting &: (cpha |: (xremaining.value <>:. 1)) in
  let input_bit = mux inp (List.init 8 (bit pins)) in
  (* With the line unit, c bit 6 feeds the CRC: a drive-only XFER each bit as
     it is shifted out, a sampling XFER each sampled bit. *)
  let crc_shifted, crc_sampled =
    match line with
    | None -> [], []
    | Some l ->
      let crc = bit mode 6 in
      [when_ (crc &: ~:sampling)
         (l.crc_step ~msb (Engine_datapath.tx_bit ~msb tx.value))],
      [when_ crc (l.crc_step ~msb input_bit)] in
  let classic =
    [if_ (xtick.value >:. 1) [xtick <-- xtick.value -:. 1]
       [xtick <-- d.transfer_period.value; xremaining <-- xremaining.value -:. 1;
        d.values <-- mux2 update_out
          (Engine_datapath.write_pin clock_values out (Engine_datapath.tx_bit ~msb output_data))
          clock_values;
        when_ shifting ((tx <-- Engine_datapath.shift_tx d ~msb) :: crc_shifted);
        when_ (sampling &: sample_edge)
          ((rx <-- Engine_datapath.sample d ~msb input_bit) :: crc_sampled);
        when_ (xremaining.value ==:. 1) finish]] in
  match line with
  | None -> classic
  | Some l -> [if_ (bit mode 5) l.transfer classic]
