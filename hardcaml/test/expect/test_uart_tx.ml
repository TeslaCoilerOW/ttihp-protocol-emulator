(* One UART byte from the example firmware (Firmware.make "uart-tx", half-period
   8: a bit period of 16 clocks). The host loads the image, writes the byte to
   engine 0's TX queue and STARTs the engine; the waveform shows pin 0. *)

open! Base
open! Stdio
open Harness

let%expect_test "uart-tx firmware sends 0xA5 on pin 0" =
  let t = create () in
  let words, owned = firmware ~half_period:8 "uart-tx" in
  load t ~engine:0 ~owned words;
  ignore (write_word t ~window:2 0xa5 : bool);
  let start = t.cycle in
  command_exn t Command.Start 0b0001;
  let pin = Array.init 180 ~f:(fun _ -> (cycle t).uio_out.(0)) in
  (* Edges of pin 0 after the START command's last nibble. *)
  let edges =
    List.filter (List.range 1 (Array.length pin)) ~f:(fun k -> pin.(k) <> pin.(k - 1))
  in
  let start_bit =
    List.find_exn edges ~f:(fun k -> pin.(k) = 0)
  in
  let sample k = pin.(start_bit + (16 * k) + 8) in
  let byte = List.fold (List.range 0 8) ~init:0 ~f:(fun b k -> b lor (sample (k + 1) lsl k)) in
  printf "image: %d words, owned pins 0x%02x\n" (List.length words) owned;
  printf
    "edges of pin 0, in clocks after the start bit's falling edge: %s\n"
    (List.filter_map edges ~f:(fun k ->
       if k >= start_bit then Some (Int.to_string (k - start_bit)) else None)
     |> String.concat ~sep:" ");
  printf "start bit %d, byte 0x%02x (LSB first), stop bit %d\n" (sample 0) byte (sample 9);
  print
    t
    ~start_cycle:start
    ~wave_width:(-4)
    [ bit "uio_out0"; bit "uio_oe0"; unsigned "tx_level0"; bit "running0" ];
  [%expect {|
    image: 12 words, owned pins 0x01
    edges of pin 0, in clocks after the start bit's falling edge: 0 16 32 48 64 96 112 128
    start bit 0, byte 0xa5 (LSB first), stop bit 1
    ┌Signals──┐┌Waves───────────────────────────────────────────┐
    │clock    ││╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥│
    │         ││╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨│
    │uio_out0 ││  ╥╥   ╥───╥   ╥───╥       ╥───╥   ╥────────────│
    │         ││──╨╨───╨   ╨───╨   ╨───────╨   ╨───╨            │
    │uio_oe0  ││  ╥─────────────────────────────────────────────│
    │         ││──╨                                             │
    │         ││───┬────────────────────────────────────────────│
    │tx_level0││ 1 │0                                           │
    │         ││───┴────────────────────────────────────────────│
    │running0 ││  ╥─────────────────────────────────────────────│
    │         ││──╨                                             │
    └─────────┘└────────────────────────────────────────────────┘
    |}]
;;
