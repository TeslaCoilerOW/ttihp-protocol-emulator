(* PUSH with a = 1 (strict) on a full RX queue faults with code 4 instead of
   blocking (docs/info.md, "Queues" and "Faults and reset"). The fault stops
   the engine and releases its output enables; the rejected word stays in rx. *)

open! Base
open! Stdio
open Harness

let program ~strict =
  let owned = 0b0000_0001 in
  let i = instruction ~owned in
  ( owned
  , [ i ~imm:owned "SET"
    ; i ~imm:owned "DIR"
    ; i ~a:1 ~imm:0x42 "LOAD" (* rx := 0x42 *)
    ; i ~a:(if strict then 1 else 0) "PUSH"
    ; i ~imm:3 "JMP"
    ] )
;;

let status word =
  Printf.sprintf
    "0x%08x: running %d, committed %d, stalled %d, fault %d, code %d"
    word
    (word land 1)
    ((word lsr 1) land 1)
    ((word lsr 2) land 1)
    ((word lsr 3) land 1)
    ((word lsr 8) land 0xff)
;;

let%expect_test "a strict PUSH on a full RX queue: fault code 4" =
  let t = create () in
  let owned, words = program ~strict:true in
  load t ~engine:0 ~owned words;
  command_exn t Command.Start 0b0001;
  (* From the cycle in which START's last nibble is accepted. *)
  let start = t.cycle - 1 in
  idle t 24;
  print
    t
    ~start_cycle:start
    [ bit "uio_out0"; bit "uio_oe0"; unsigned "rx_level0"; bit "running0"; bit "irq"; bit "fault" ];
  printf "status (READ_SELECT 0) %s\n" (status (read_status t 0));
  printf "held rx (READ_SELECT 6) 0x%08x\n" (read_status t 6);
  printf "queue levels (READ_SELECT 2) 0x%08x\n" (read_status t 2);
  [%expect {|
    ┌Signals──┐┌Waves─────────────────────────────────────────────┐
    │clock    ││┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐│
    │         ││ └┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└│
    │uio_out0 ││    ┌─────────────────────────────────────┐       │
    │         ││────┘                                     └───────│
    │uio_oe0  ││      ┌───────────────────────────────────┐       │
    │         ││──────┘                                   └───────│
    │         ││──────────┬───┬───┬───┬───┬───┬───┬───┬───────────│
    │rx_level0││ 0        │1  │2  │3  │4  │5  │6  │7  │8          │
    │         ││──────────┴───┴───┴───┴───┴───┴───┴───┴───────────│
    │running0 ││  ┌───────────────────────────────────────┐       │
    │         ││──┘                                       └───────│
    │irq      ││          ┌───────────────────────────────────────│
    │         ││──────────┘                                       │
    │fault    ││                                          ┌───────│
    │         ││──────────────────────────────────────────┘       │
    └─────────┘└──────────────────────────────────────────────────┘
    status (READ_SELECT 0) 0x0000040a: running 0, committed 1, stalled 0, fault 1, code 4
    held rx (READ_SELECT 6) 0x00000042
    queue levels (READ_SELECT 2) 0x00080000
    |}]
;;

let%expect_test "the same program with a blocking PUSH (a = 0) stalls instead" =
  let t = create () in
  let owned, words = program ~strict:false in
  load t ~engine:0 ~owned words;
  command_exn t Command.Start 0b0001;
  idle t 24;
  printf "status (READ_SELECT 0) %s\n" (status (read_status t 0));
  [%expect {| status (READ_SELECT 0) 0x00000007: running 1, committed 1, stalled 1, fault 0, code 0 |}]
;;
