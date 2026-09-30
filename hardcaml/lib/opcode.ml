type t =
  | Nop
  | Halt
  | Set
  | Dir
  | Wait
  | Jmp
  | Pull
  | Push
  | Out
  | In
  | Count
  | Loop
  | Limit
  | Waitpin
  | Signal
  | Waitevent
  | Pins
  | Xfer
  | Mov
  | Load
  | Add
  | Xor
  | And
  | Or
  | Shl
  | Shr
  | Jz
  | Not
  | Time
  | Fault
  | Ltim
  | Lcfg
  | Crc
  | Lstat
[@@deriving compare, enumerate, equal, sexp_of]

let to_int = function
  | Nop -> 0
  | Halt -> 1
  | Set -> 2
  | Dir -> 3
  | Wait -> 4
  | Jmp -> 5
  | Pull -> 6
  | Push -> 7
  | Out -> 8
  | In -> 9
  | Count -> 10
  | Loop -> 11
  | Limit -> 12
  | Waitpin -> 13
  | Signal -> 14
  | Waitevent -> 15
  | Pins -> 16
  | Xfer -> 17
  | Mov -> 18
  | Load -> 19
  | Add -> 20
  | Xor -> 21
  | And -> 22
  | Or -> 23
  | Shl -> 24
  | Shr -> 25
  | Jz -> 26
  | Not -> 27
  | Time -> 28
  | Fault -> 29
  | Ltim -> 30
  | Lcfg -> 31
  | Crc -> 32
  | Lstat -> 33

let mnemonic = function
  | Nop -> "NOP"
  | Halt -> "HALT"
  | Set -> "SET"
  | Dir -> "DIR"
  | Wait -> "WAIT"
  | Jmp -> "JMP"
  | Pull -> "PULL"
  | Push -> "PUSH"
  | Out -> "OUT"
  | In -> "IN"
  | Count -> "COUNT"
  | Loop -> "LOOP"
  | Limit -> "LIMIT"
  | Waitpin -> "WAITPIN"
  | Signal -> "SIGNAL"
  | Waitevent -> "WAITEVENT"
  | Pins -> "PINS"
  | Xfer -> "XFER"
  | Mov -> "MOV"
  | Load -> "LOAD"
  | Add -> "ADD"
  | Xor -> "XOR"
  | And -> "AND"
  | Or -> "OR"
  | Shl -> "SHL"
  | Shr -> "SHR"
  | Jz -> "JZ"
  | Not -> "NOT"
  | Time -> "TIME"
  | Fault -> "FAULT"
  | Ltim -> "LTIM"
  | Lcfg -> "LCFG"
  | Crc -> "CRC"
  | Lstat -> "LSTAT"

let of_int n = List.find_opt (fun t -> to_int t = n) all
let of_mnemonic s = List.find_opt (fun t -> String.equal (mnemonic t) s) all

let requires_line_unit = function
  | Ltim | Lcfg | Crc | Lstat -> true
  | Nop | Halt | Set | Dir | Wait | Jmp | Pull | Push | Out | In | Count | Loop | Limit
  | Waitpin | Signal | Waitevent | Pins | Xfer | Mov | Load | Add | Xor | And | Or | Shl
  | Shr | Jz | Not | Time | Fault -> false
