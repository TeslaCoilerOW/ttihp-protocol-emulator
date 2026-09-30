open Hardcaml
open Signal

type fields = {
  word : Signal.t;
  op : Signal.t;
  a : Signal.t;
  b : Signal.t;
  c : Signal.t;
  imm24 : Signal.t;
  imm16 : Signal.t;
  low8 : Signal.t;
  dest : Signal.t;
  src : Signal.t;
  pin : Signal.t;
}

let fields word =
  let a = select word 23 16 and b = select word 15 8 in
  { word; op = select word 31 24; a; b; c = select word 7 0;
    imm24 = select word 23 0; imm16 = select word 15 0; low8 = select word 7 0;
    dest = select a 1 0; src = select b 1 0; pin = select a 2 0 }

type line = {
  cfg : Signal.t;
  ticker_running : Signal.t;
  mutation : Line_unit.mutation option;
}

type t = {
  fields : fields;
  valid : Signal.t;
}

let create ~(config : Config.t) ~shift ~split_instruction_decode ?line ~ownership ~transfer_pins
    word =
  let ({ word; op; a; b; c; imm24; imm16; low8; dest = _; src = _; pin } as fields) = fields word in
  let data_width = config.data_width in
  let all_zero = imm24 ==:. 0 and bc_zero = imm16 ==:. 0 in
  let reg_pair = (a <:. 4) &: (b <:. 4) &: (c ==:. 0) in
  let owned pin = (Engine_datapath.bitmask pin &: ownership) <>:. 0 in
  (* Byte-lane shifts: only counts 0, 8, 16, 24 (below the datapath width)
     are valid; any other count is an invalid operand (fault code 1). *)
  let shift_valid c =
    match (shift : Variant_options.shift) with
    | Byte_lane -> (c <:. data_width) &: ((c &: of_int ~width:8 0xe7) ==:. 0)
    | Barrel -> c <:. data_width in
  (* XFER: a bit count 1..width, a half period b > 0, flags c; the clock pin
     owned and, when driving (c bit 3), the data-out pin owned and distinct
     from it. With the line unit, c bit 5 selects a line XFER and bit 6 feeds
     the CRC (also legal in a classic XFER), bit 7 must be zero. A line XFER:
     b = 0, c[1:0] = 0, the ticker running, no sampling in Manchester, the
     data pin owned when driving, and with the pair set the pair pin owned
     and distinct. *)
  let xfer () =
    let ck = select transfer_pins 2 0 and out = select transfer_pins 5 3 in
    let classic flags_valid =
      reduce ~f:( &: )
        ([ a <>:. 0; a <=:. data_width; b <>:. 0 ] @ flags_valid
         @ [ owned ck; (~:(bit c 3)) |: owned out; (~:(bit c 3)) |: (ck <>: out) ]) in
    match line with
    | None -> classic [ c <:. 32 ]
    | Some l ->
      let manchester = select l.cfg 1 0 ==:. 2 and pair = bit l.cfg 7 in
      let line_rule = (a <>:. 0) &: (a <=:. data_width) &: (b ==:. 0) &: (select c 1 0 ==:. 0)
        &: l.ticker_running &: ~:(manchester &: bit c 4)
        &: ((~:(bit c 3)) |: owned out)
        &: ((~:(bit c 3 &: pair)) |: (owned ck &: (ck <>: out))) in
      (~:(bit c 7)) &: mux2 (bit c 5) line_rule (classic []) in
  let with_line f = Option.map f line in
  (* The validity rule of each opcode. [None]: not implemented in this
     configuration. Each call builds the rule's logic afresh. *)
  let rule : Opcode.t -> Signal.t option = function
    | Nop | Halt | Pull | Waitevent -> Some all_zero
    | Push -> Some ((a <:. 2) &: bc_zero)
    | Set | Dir -> Some ((select word 23 8 ==:. 0) &: ((low8 &: ~:ownership) ==:. 0))
    | Wait | Jmp | Loop -> Some vdd
    | Out -> Some ((a <:. 8) &: (b ==:. 0) &: (c <:. 2) &: owned pin)
    | In -> Some ((a <:. 8) &: (b ==:. 0) &: (c <:. 2))
    | Count -> Some (a ==:. 0)
    | Limit -> Some (imm24 <>:. 0)
    | Waitpin -> Some ((a <:. 8) &: (b <:. 2) &: (c ==:. 0))
    | Signal -> Some (imm24 <:. (1 lsl config.engine_count))
    | Pins -> Some (select word 23 9 ==:. 0)
    | Xfer -> (match config.issue with Fused -> Some (xfer ()) | Scalar -> None)
    | Mov | Add | Xor | And | Or -> Some reg_pair
    | Load | Jz -> Some (a <:. 4)
    | Shl | Shr -> Some ((a <:. 4) &: (b ==:. 0) &: shift_valid c)
    | Not | Time -> Some ((a <:. 4) &: bc_zero)
    | Fault -> Some ((select word 23 8 ==:. 0) &: (low8 <>:. 0))
    (* LTIM: any value except P = 255 with a fraction (P + carry must fit). *)
    | Ltim ->
      with_line (fun l ->
        if l.mutation = Some Line_unit.Ltim_overflow then vdd
        else ~:((select word 7 0 ==:. 255) &: (select word 15 8 <>:. 0)))
    (* LCFG: bits 23..11 zero, line code 3 invalid, and no stuffing on runs
       of either polarity of length 1 (the stuff bit itself would complete
       the next run, so no data bit would ever follow). *)
    | Lcfg ->
      with_line (fun _ ->
        (select word 23 11 ==:. 0) &: (select word 1 0 <>:. 3)
        &: ~:(bit word 2 &: bit word 3 &: (select word 6 4 ==:. 0)))
    (* CRC: c = 1 set from register b, c = 2 read into register a, c = 3 preset b. *)
    | Crc ->
      with_line (fun _ ->
        ((c ==:. 1) &: (a ==:. 0) &: (b <:. 4)) |: ((c ==:. 2) &: (a <:. 4) &: (b ==:. 0))
        |: ((c ==:. 3) &: (a ==:. 0) &: (b <:. 4)))
    | Lstat -> with_line (fun _ -> (a <:. 4) &: bc_zero) in
  let valid =
    if not split_instruction_decode then
      mux op (List.init 256 (fun code ->
        Option.value (Option.bind (Opcode.of_int code) rule) ~default:gnd))
    else
      (* Timing knob split_instruction_decode: the 256-way multiplexer on the
         opcode equals the OR over implemented opcodes of (op = code) & rule;
         every other opcode is invalid. *)
      List.fold_left (fun acc opcode ->
          match rule opcode with
          | None -> acc
          | Some r -> acc |: ((op ==:. Opcode.to_int opcode) &: r))
        gnd Opcode.all in
  { fields; valid }
