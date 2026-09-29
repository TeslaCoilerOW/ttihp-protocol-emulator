(* Loading a program (docs/info.md, "Loading and starting"): SELECT and BEGIN
   open window 1, the words are written there with an auto-incrementing
   address, and COMMIT accepts the image only if its length is exactly the
   number of words written. *)

open! Base
open! Stdio
open Harness

let%expect_test "BEGIN, three words, OWN, a rejected and an accepted COMMIT" =
  let t = create () in
  let owned = 0b0000_0001 in
  let program =
    [ "DIR", instruction ~owned ~imm:owned "DIR"
    ; "SET", instruction ~owned ~imm:owned "SET"
    ; "HALT", instruction ~owned "HALT"
    ]
  in
  let start = t.cycle in
  let log = Queue.create () in
  let host name ok = Queue.enqueue log (t.cycle - 1 - start, name, ok) in
  host "SELECT 0" (command t Command.Select 0);
  host "BEGIN" (command t Command.Begin 0);
  List.iter program ~f:(fun (_, word) -> ignore (write_word t ~window:1 word : bool));
  host "OWN 0x01" (command t Command.Own owned);
  host "COMMIT 2" (command t Command.Commit 2);
  host "CLEAR bit 23 (host fault)" (command t Command.Clear (1 lsl 23));
  host "COMMIT 3" (command t Command.Commit 3);
  (* Window 1 is closed after COMMIT: write-ready stays low. *)
  enter_window t 1;
  idle t 3;
  List.iteri program ~f:(fun address (mnemonic, word) ->
    printf "word %d: 0x%08x %s\n" address word mnemonic);
  Queue.iter log ~f:(fun (cycle, name, ok) ->
    printf "cycle %2d: %-26s %s\n" cycle name (if ok then "accepted" else "rejected"));
  print
    t
    ~start_cycle:start
    ~wave_width:(-1)
    [ unsigned "window"
    ; bit "write_valid"
    ; bit "write_ready"
    ; bit "command_accepted"
    ; bit "image_valid0"
    ; bit "fault"
    ];
  [%expect {|
    word 0: 0x03000001 DIR
    word 1: 0x02000001 SET
    word 2: 0x01000000 HALT
    cycle  7: SELECT 0                   accepted
    cycle 15: BEGIN                      accepted
    cycle 49: OWN 0x01                   accepted
    cycle 57: COMMIT 2                   rejected
    cycle 65: CLEAR bit 23 (host fault)  accepted
    cycle 73: COMMIT 3                   accepted
    ┌Signals─────────┐┌Waves─────────────────────────────────────────────────────────────────────────┐
    │clock           ││╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥╥│
    │                ││╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨╨│
    │                ││────────────────┬────────────────────────┬────────────────────────────────┬───│
    │window          ││ 0              │1                       │0                               │1  │
    │                ││────────────────┴────────────────────────┴────────────────────────────────┴───│
    │write_valid     ││────────────────┐┌───────────────────────┐┌───────────────────────────────┐   │
    │                ││                └┘                       └┘                               └───│
    │write_ready     ││────────────────┐┌───────────────────────┐┌───────────────────────────────┐   │
    │                ││                └┘                       └┘                               └───│
    │command_accepted││       ┌┐      ┌┐                                ┌┐              ┌┐      ┌┐   │
    │                ││───────┘└──────┘└────────────────────────────────┘└──────────────┘└──────┘└───│
    │image_valid0    ││                                                                          ┌───│
    │                ││──────────────────────────────────────────────────────────────────────────┘   │
    │fault           ││                                                          ┌───────┐           │
    │                ││──────────────────────────────────────────────────────────┘       └───────────│
    └────────────────┘└──────────────────────────────────────────────────────────────────────────────┘
    |}]
;;
