(* The host port (docs/info.md, "Host interface"): nibble-wide ready/valid
   transfers, least significant nibble first, with a bubble cycle whenever the
   window bits change. *)

open! Base
open! Stdio
open Harness

let%expect_test "a command, a TX word and a status read" =
  let t = create () in
  let start = t.cycle in
  (* Window 0 has been selected since reset, so the command needs no bubble. *)
  let accepted = command t Command.Read_select 2 in
  ignore (write_word t ~window:2 0x0000_00a5 : bool);
  let word = read_word t ~window:0 in
  printf "READ_SELECT 2 accepted: %b\n" accepted;
  printf
    "status word 0x%08x: engine 0 TX queue level %d, RX queue level %d\n"
    word
    (word land 0xffff)
    (word lsr 16);
  print
    t
    ~start_cycle:start
    [ unsigned "window"
    ; bit "write_valid"
    ; hex "write_nibble"
    ; bit "write_ready"
    ; bit "read_ready"
    ; bit "read_valid"
    ; hex "read_nibble"
    ; unsigned "tx_level0"
    ];
  [%expect {|
    READ_SELECT 2 accepted: true
    status word 0x00000001: engine 0 TX queue level 1, RX queue level 0
    ┌Signals─────┐┌Waves─────────────────────────────────────────────────┐
    │clock       ││┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐┌┐│
    │            ││ └┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└┘└│
    │            ││────────────────┬─────────────────┬───────────────────│
    │window      ││ 0              │2                │0                  │
    │            ││────────────────┴─────────────────┴───────────────────│
    │write_valid ││────────────────┐ ┌───────────────┐                   │
    │            ││                └─┘               └───────────────────│
    │            ││──┬─────────┬─┬───┬─┬─┬───────────────────────────────│
    │write_nibble││ 2│0        │8│0  │5│A│0                              │
    │            ││──┴─────────┴─┴───┴─┴─┴───────────────────────────────│
    │write_ready ││────────────────┐ ┌───────────────┐ ┌─────────────────│
    │            ││                └─┘               └─┘                 │
    │read_ready  ││                                    ┌─────────────────│
    │            ││────────────────────────────────────┘                 │
    │read_valid  ││  ┌─────────────┐                     ┌───────────────│
    │            ││──┘             └─────────────────────┘               │
    │            ││──────────────────────────────────────┬─┬─────────────│
    │read_nibble ││ 0                                    │1│0            │
    │            ││──────────────────────────────────────┴─┴─────────────│
    │            ││──────────────────────────────────┬───────────────────│
    │tx_level0   ││ 0                                │1                  │
    │            ││──────────────────────────────────┴───────────────────│
    └────────────┘└──────────────────────────────────────────────────────┘
    |}]
;;
