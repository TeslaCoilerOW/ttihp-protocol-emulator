(* XFER (docs/info.md, "Timed transfers"): a complete synchronous serial
   transfer from one instruction, with exactly 2 x (bit count) clock
   transitions separated by the half-period. PINS selects SCK = pin 2,
   MOSI = pin 3 and MISO = pin 4. *)

open! Base
open! Stdio
open Harness

let sck = 2
let mosi = 3
let miso = 4
let owned = (1 lsl sck) lor (1 lsl mosi)
let pins = sck lor (mosi lsl 3) lor (miso lsl 6)
let msb_first = 0b00100
let drive = 0b01000
let sample = 0b10000

let edges trace =
  List.filter (List.range 1 (Array.length trace)) ~f:(fun k -> trace.(k) <> trace.(k - 1))
;;

let%expect_test "an 8-bit mode 0 transfer: 0xA5 out on MOSI, 0x3C in from MISO" =
  (* A mode 0 target: it presents the next MISO bit (MSB first) one clock
     after each falling SCK edge. Inputs pass two synchronizer flops, so that
     bit reaches the next rising edge only if the half-period is at least 3
     (with half-period 2 this test receives 0x1E, every bit one bit late). *)
  let target_byte = 0x3c in
  let bit_index = ref 7 in
  let previous_sck = ref 0 in
  let environment (o : int O.t) =
    if !previous_sck = 1 && o.uio_out.(sck) = 0 then Int.decr bit_index;
    previous_sck := o.uio_out.(sck);
    if !bit_index >= 0 && (target_byte lsr !bit_index) land 1 = 1 then 1 lsl miso else 0
  in
  let t = create ~environment () in
  let i = instruction ~owned in
  load
    t
    ~engine:0
    ~owned
    [ i ~imm:pins "PINS"
    ; i ~imm:owned "DIR"
    ; i "PULL"
    ; i ~a:0 ~c:24 "SHL" (* the byte to tx[31:24], sent MSB first *)
    ; i ~a:8 ~b:3 ~c:(msb_first lor drive lor sample) "XFER"
    ; i ~a:0 "PUSH"
    ; i "HALT"
    ];
  ignore (write_word t ~window:2 0xa5 : bool);
  command_exn t Command.Start 0b0001;
  let start = t.cycle - 1 in
  let trace = Array.init 58 ~f:(fun _ -> cycle t) in
  let sck_edges = edges (Array.map trace ~f:(fun o -> o.uio_out.(sck))) in
  let first = List.hd_exn sck_edges in
  printf
    "%d SCK edges, in clocks after the first: %s\n"
    (List.length sck_edges)
    (List.map sck_edges ~f:(fun k -> Int.to_string (k - first)) |> String.concat ~sep:" ");
  print
    t
    ~start_cycle:start
    ~wave_width:(-1)
    [ bit "uio_out2"; bit "uio_out3"; bit "uio_in4"; bit "running0"; unsigned "rx_level0" ];
  printf "received 0x%08x from the RX queue (window 3)\n" (read_word t ~window:3);
  [%expect {|
    16 SCK edges, in clocks after the first: 0 3 6 9 12 15 18 21 24 27 30 33 36 39 42 45
    ┌Signals──┐┌Waves──────────────────────────────────────────────────────┐
    │clock    ││╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥│
    │         ││╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨│
    │uio_out2 ││         ┌──┐  ┌──┐  ┌──┐  ┌──┐  ┌──┐  ┌──┐  ┌──┐  ┌──┐    │
    │         ││─────────┘  └──┘  └──┘  └──┘  └──┘  └──┘  └──┘  └──┘  └────│
    │uio_out3 ││      ┌─────┐     ┌─────┐           ┌─────┐     ┌───────┐  │
    │         ││──────┘     └─────┘     └───────────┘     └─────┘       └──│
    │uio_in4  ││                  ┌───────────────────────┐                │
    │         ││──────────────────┘                       └────────────────│
    │running0 ││ ┌──────────────────────────────────────────────────────┐  │
    │         ││─┘                                                      └──│
    │         ││───────────────────────────────────────────────────────┬───│
    │rx_level0││ 0                                                     │1  │
    │         ││───────────────────────────────────────────────────────┴───│
    └─────────┘└───────────────────────────────────────────────────────────┘
    received 0x0000003c from the RX queue (window 3)
    |}]
;;

let%expect_test "SPI modes 0 to 3: 4 bits of 1010, half-period 2" =
  List.iter [ 0; 1; 2; 3 ] ~f:(fun mode ->
    (* SPI mode = 2 x CPOL + CPHA; XFER takes CPOL in c bit 0, CPHA in c bit 1. *)
    let cpol = mode lsr 1 in
    let cpha = mode land 1 in
    let t = create () in
    let i = instruction ~owned in
    load
      t
      ~engine:0
      ~owned
      [ i ~imm:pins "PINS"
      ; i ~imm:(cpol lsl sck) "SET" (* SCK at its idle level before DIR *)
      ; i ~imm:owned "DIR"
      ; i ~a:0 ~imm:0xa000 "LOAD"
      ; i ~a:0 ~c:16 "SHL" (* tx = 0xA0000000 *)
      ; i ~a:4 ~b:2 ~c:(msb_first lor drive lor cpol lor (cpha lsl 1)) "XFER"
      ; i ~imm:2 "WAIT"
      ; i "HALT"
      ];
    command_exn t Command.Start 0b0001;
    let start = t.cycle + 4 in
    idle t 30;
    printf "SPI mode %d: CPOL %d, CPHA %d\n" mode cpol cpha;
    print t ~start_cycle:start [ bit "uio_out2"; bit "uio_out3"; bit "uio_oe2" ]);
  [%expect {|
    SPI mode 0: CPOL 0, CPHA 0
    ┌Signals─┐┌Waves───────────────────────────────────────────────┐
    │clock   ││┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐│
    │        ││ └┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└│
    │uio_out2││        ┌───┐   ┌───┐   ┌───┐   ┌───┐               │
    │        ││────────┘   └───┘   └───┘   └───┘   └───────────────│
    │uio_out3││    ┌───────┐       ┌───────┐                       │
    │        ││────┘       └───────┘       └───────────────────────│
    │uio_oe2 ││────────────────────────────────────────────┐       │
    │        ││                                            └───────│
    └────────┘└────────────────────────────────────────────────────┘
    SPI mode 1: CPOL 0, CPHA 1
    ┌Signals─┐┌Waves───────────────────────────────────────────────┐
    │clock   ││┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐│
    │        ││ └┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└│
    │uio_out2││        ┌───┐   ┌───┐   ┌───┐   ┌───┐               │
    │        ││────────┘   └───┘   └───┘   └───┘   └───────────────│
    │uio_out3││        ┌───────┐       ┌───────┐                   │
    │        ││────────┘       └───────┘       └───────────────────│
    │uio_oe2 ││────────────────────────────────────────────┐       │
    │        ││                                            └───────│
    └────────┘└────────────────────────────────────────────────────┘
    SPI mode 2: CPOL 1, CPHA 0
    ┌Signals─┐┌Waves───────────────────────────────────────────────┐
    │clock   ││┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐│
    │        ││ └┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└│
    │uio_out2││────────┐   ┌───┐   ┌───┐   ┌───┐   ┌───────┐       │
    │        ││        └───┘   └───┘   └───┘   └───┘       └───────│
    │uio_out3││    ┌───────┐       ┌───────┐                       │
    │        ││────┘       └───────┘       └───────────────────────│
    │uio_oe2 ││────────────────────────────────────────────┐       │
    │        ││                                            └───────│
    └────────┘└────────────────────────────────────────────────────┘
    SPI mode 3: CPOL 1, CPHA 1
    ┌Signals─┐┌Waves───────────────────────────────────────────────┐
    │clock   ││┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐│
    │        ││ └┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└│
    │uio_out2││────────┐   ┌───┐   ┌───┐   ┌───┐   ┌───────┐       │
    │        ││        └───┘   └───┘   └───┘   └───┘       └───────│
    │uio_out3││        ┌───────┐       ┌───────┐                   │
    │        ││────────┘       └───────┘       └───────────────────│
    │uio_oe2 ││────────────────────────────────────────────┐       │
    │        ││                                            └───────│
    └────────┘└────────────────────────────────────────────────────┘
    |}]
;;
