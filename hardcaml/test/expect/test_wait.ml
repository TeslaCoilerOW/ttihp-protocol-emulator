(* Exact timing and bounded waits (docs/info.md, "Engines" and the ISA table):
   WAIT n advances the PC and then holds for n more cycles; WAITPIN waits for
   an input pin, bounded by LIMIT, and faults with code 3 when the bound runs
   out. *)

open! Base
open! Stdio
open Harness

let runs trace =
  let edges =
    List.filter (List.range 1 (Array.length trace)) ~f:(fun k -> trace.(k) <> trace.(k - 1))
  in
  List.map2_exn (List.drop_last_exn edges) (List.tl_exn edges) ~f:(fun a b -> b - a)
;;

let%expect_test "WAIT n: pin 0 toggles by SET with WAIT 0, 1, 2 and 3 in between" =
  let t = create () in
  let owned = 0b0000_0001 in
  let i = instruction ~owned in
  load
    t
    ~engine:0
    ~owned
    [ i ~imm:owned Dir
    ; i ~imm:1 Set
    ; i ~imm:0 Wait
    ; i ~imm:0 Set
    ; i ~imm:1 Wait
    ; i ~imm:1 Set
    ; i ~imm:2 Wait
    ; i ~imm:0 Set
    ; i ~imm:3 Wait
    ; i ~imm:1 Set
    ; i Halt
    ];
  command_exn t Command.Start 0b0001;
  let start = t.cycle - 1 in
  let trace = Array.init 20 ~f:(fun _ -> (cycle t).uio_out.(0)) in
  printf
    "cycles between the edges of pin 0: %s\n"
    (List.take (runs trace) 4 |> List.map ~f:Int.to_string |> String.concat ~sep:" ");
  print t ~start_cycle:start [ bit "uio_out0"; bit "uio_oe0"; bit "running0" ];
  [%expect {|
    cycles between the edges of pin 0: 2 3 4 5
    ┌Signals─┐┌Waves─────────────────────────────────────┐
    │clock   ││┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐│
    │        ││ └┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└│
    │uio_out0││      ┌───┐     ┌───────┐         ┌─┐     │
    │        ││──────┘   └─────┘       └─────────┘ └─────│
    │uio_oe0 ││    ┌───────────────────────────────┐     │
    │        ││────┘                               └─────│
    │running0││  ┌─────────────────────────────────┐     │
    │        ││──┘                                 └─────│
    └────────┘└──────────────────────────────────────────┘
    |}]
;;

let waitpin_program () =
  let i = instruction ~owned:0 in
  [ i ~imm:6 Limit; i ~a:1 ~b:1 Waitpin (* until pin 1 is high *); i Halt ]
;;

let status word =
  Printf.sprintf
    "0x%08x: running %d, fault %d, code %d"
    word
    (word land 1)
    ((word lsr 3) land 1)
    ((word lsr 8) land 0xff)
;;

let%expect_test "WAITPIN with LIMIT 6 and pin 1 held low: fault code 3" =
  let t = create () in
  load t ~engine:0 ~owned:0 (waitpin_program ());
  command_exn t Command.Start 0b0001;
  let start = t.cycle - 1 in
  let running = Array.init 14 ~f:(fun _ -> (cycle t).running.(0)) in
  printf
    "engine 0 ran for %d cycles\n"
    (Array.count running ~f:(fun r -> r = 1));
  print t ~start_cycle:start [ bit "uio_in1"; bit "running0"; bit "fault" ];
  printf "status (READ_SELECT 0) %s\n" (status (read_status t 0));
  [%expect {|
    engine 0 ran for 7 cycles
    ┌Signals─┐┌Waves─────────────────────────┐
    │clock   ││┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐│
    │        ││ └┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└│
    │uio_in1 ││                              │
    │        ││──────────────────────────────│
    │running0││  ┌─────────────┐             │
    │        ││──┘             └─────────────│
    │fault   ││                ┌─────────────│
    │        ││────────────────┘             │
    └────────┘└──────────────────────────────┘
    status (READ_SELECT 0) 0x0000030a: running 0, fault 1, code 3
    |}]
;;

let%expect_test "the same WAITPIN when pin 1 rises in time: no fault" =
  let t = create () in
  load t ~engine:0 ~owned:0 (waitpin_program ());
  command_exn t Command.Start 0b0001;
  let start = t.cycle - 1 in
  t.environment <- (fun _ -> if t.cycle >= start + 4 then 0b10 else 0);
  let running = Array.init 14 ~f:(fun _ -> (cycle t).running.(0)) in
  printf
    "engine 0 ran for %d cycles\n"
    (Array.count running ~f:(fun r -> r = 1));
  print t ~start_cycle:start [ bit "uio_in1"; bit "running0"; bit "fault" ];
  printf "status (READ_SELECT 0) %s\n" (status (read_status t 0));
  [%expect {|
    engine 0 ran for 7 cycles
    ┌Signals─┐┌Waves─────────────────────────┐
    │clock   ││┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐│
    │        ││ └┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└│
    │uio_in1 ││        ┌─────────────────────│
    │        ││────────┘                     │
    │running0││  ┌─────────────┐             │
    │        ││──┘             └─────────────│
    │fault   ││                              │
    │        ││──────────────────────────────│
    └────────┘└──────────────────────────────┘
    status (READ_SELECT 0) 0x00000002: running 0, fault 0, code 0
    |}]
;;
