type t =
  | Select
  | Begin
  | Commit
  | Own
  | Start
  | Stop
  | Route
  | Clear
  | Read_select
  | Event
  | Flush
  | Trigger
[@@deriving compare, enumerate, equal, sexp_of]

let to_int = function
  | Select -> 0
  | Begin -> 1
  | Commit -> 2
  | Own -> 3
  | Start -> 4
  | Stop -> 5
  | Route -> 6
  | Clear -> 7
  | Read_select -> 8
  | Event -> 9
  | Flush -> 10
  | Trigger -> 11

let of_int n = List.find_opt (fun t -> to_int t = n) all

let name = function
  | Select -> "SELECT"
  | Begin -> "BEGIN"
  | Commit -> "COMMIT"
  | Own -> "OWN"
  | Start -> "START"
  | Stop -> "STOP"
  | Route -> "ROUTE"
  | Clear -> "CLEAR"
  | Read_select -> "READ_SELECT"
  | Event -> "EVENT"
  | Flush -> "FLUSH"
  | Trigger -> "TRIGGER"
