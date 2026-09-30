(** Host commands: window-0 writes, with the command in bits 31:24 of the word
    and the payload in bits 23:0 (docs/info.md, "Commands"). Codes 12..255 are
    invalid and set the sticky host fault.

    [Processor]'s acceptance rule matches on [t] without a wildcard, so a new
    command fails the build until it has one. *)

type t =
  | Select  (** 0: select the engine for windows 1-3 and per-engine commands *)
  | Begin  (** 1: stop the selected engine and start writing a new image *)
  | Commit  (** 2: accept a fully written image of the given length *)
  | Own  (** 3: pin ownership and open-drain mask (halted only) *)
  | Start  (** 4: start the engines in the mask *)
  | Stop  (** 5: stop the engines in the mask *)
  | Route  (** 6: mover descriptor of one source engine *)
  | Clear  (** 7: clear the faults in the mask; bit 23 clears the host fault *)
  | Read_select  (** 8: the status word read in window 0 *)
  | Event  (** 9: set the mailboxes in the mask *)
  | Flush  (** 10: flush the selected engine's queues (halted only) *)
  | Trigger  (** 11: the selected engine's input trigger (halted only) *)
[@@deriving compare, enumerate, equal, sexp_of]

(** [all] is in encoding order: [List.map to_int all = [0; 1; ...; 11]]. *)

(** Bits 31:24 of the command word. *)
val to_int : t -> int

val of_int : int -> t option

(** The name used in docs/info.md, e.g. ["READ_SELECT"]. *)
val name : t -> string
