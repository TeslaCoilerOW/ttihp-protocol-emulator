open Hardcaml
open Signal

module I = struct
  module Pre = struct
    type 'a t = {
      clock : 'a; clear : 'a; start : 'a; stop : 'a; clear_fault : 'a;
      instruction : 'a; image_length : 'a; ownership : 'a; pins : 'a; timestamp : 'a;
      tx_valid : 'a; tx_data : 'a; rx_ready : 'a; event : 'a;
    }
    [@@deriving sexp_of]

    let port_names_and_widths =
      { clock = "clk", 1; clear = "clear", 1; start = "start", 1; stop = "stop", 1;
        clear_fault = "clear_fault", 1; instruction = "instruction", 32;
        image_length = "image_length", 24; ownership = "ownership", 8; pins = "pins", 8;
        timestamp = "timestamp", 32; tx_valid = "tx_valid", 1; tx_data = "tx_data", 32;
        rx_ready = "rx_ready", 1; event = "event_pending", 1 }

    let map t ~f =
      { clock = f t.clock; clear = f t.clear; start = f t.start; stop = f t.stop;
        clear_fault = f t.clear_fault; instruction = f t.instruction;
        image_length = f t.image_length; ownership = f t.ownership; pins = f t.pins;
        timestamp = f t.timestamp; tx_valid = f t.tx_valid; tx_data = f t.tx_data;
        rx_ready = f t.rx_ready; event = f t.event }

    let map2 a b ~f =
      { clock = f a.clock b.clock; clear = f a.clear b.clear; start = f a.start b.start;
        stop = f a.stop b.stop; clear_fault = f a.clear_fault b.clear_fault;
        instruction = f a.instruction b.instruction;
        image_length = f a.image_length b.image_length; ownership = f a.ownership b.ownership;
        pins = f a.pins b.pins; timestamp = f a.timestamp b.timestamp;
        tx_valid = f a.tx_valid b.tx_valid; tx_data = f a.tx_data b.tx_data;
        rx_ready = f a.rx_ready b.rx_ready; event = f a.event b.event }

    let iter t ~f = ignore (map t ~f : unit t)
    let iter2 a b ~f = ignore (map2 a b ~f : unit t)

    let to_list t =
      [ t.clock; t.clear; t.start; t.stop; t.clear_fault; t.instruction; t.image_length;
        t.ownership; t.pins; t.timestamp; t.tx_valid; t.tx_data; t.rx_ready; t.event ]
  end

  include Pre
  include Interface.Make (Pre)

  let widths (config : Config.t) = { port_widths with tx_data = config.data_width }
  let ports config = map2 port_names (widths config) ~f:Signal.input
end

type inputs = Signal.t I.t
type t = {
  pc : Signal.t; running : Signal.t; fault : Signal.t; stalled : Signal.t;
  tx_pop : Signal.t; rx_push : Signal.t; rx_data : Signal.t;
  pin_values : Signal.t; pin_enables : Signal.t;
  signal_events : Signal.t; consume_event : Signal.t; completed : Signal.t;
  issue : Signal.t; wait_timer : Signal.t; wait_limit : Signal.t;
  blocked_cycles : Signal.t; repeat_count : Signal.t; transfer_edges : Signal.t;
  queue_observe : Signal.t; line_state : (string * Signal.t) list;
}

let line_register_names = Engine_line.Registers.(to_list port_names)

(* [options] (default: the design of record) selects the variant knobs that
   live inside an engine.  With an asynchronous reset style [i.clear] is the
   chip-wide asynchronous reset net rather than a synchronous clear; it still
   gates [active] combinationally, exactly as in the synchronous design. *)
let create ?(options=Variant_options.default) ?(timing=Timing_options.default)
    ?(line=Line_options.default) ?line_mutation ?gate
    (config:Config.t) (i:inputs) =
  let open Always in
  (* [gate]: the clear that gates the next-state enable and the issue outputs
     (constant 0 with the timing knob clear_outputs_only); [i.clear] when
     absent. The registers always take [i.clear]. *)
  let gate = match gate with Some g -> g | None -> i.clear in
  let split_decode = timing.Timing_options.split_instruction_decode in
  let spec = if Variant_options.asynchronous options
    then Reg_spec.create ~clock:i.clock ~reset:i.clear ()
    else Reg_spec.create ~clock:i.clock ~clear:i.clear () in
  (* ---------------- state ---------------- *)
  let line_registers =
    if Line_options.enabled line then Some (Engine_line.Registers.create spec) else None in
  let pcw = Variant_options.pc_width options in
  let d = Engine_datapath.create spec ~pc_width:pcw ~data_width:config.data_width
      ~transfer_mode_width:(Engine_transfer.mode_width ~line_unit:(Option.is_some line_registers)) in
  let { Engine_datapath.pc; running; fault; registers = regs; repeat_count; timer; limit; blocked;
        values; enables; transfer_pins = pins; completed; transfer_edges = xremaining;
        transfer_tick = xtick; _ } = d in
  let tx = Engine_datapath.tx d and rx = Engine_datapath.rx d in
  (* ---------------- decode ---------------- *)
  let decode =
    Engine_decode.create ~config ~shift:options.shift ~split_instruction_decode:split_decode
      ?line:(Option.map (fun (l : Variable.t Engine_line.Registers.t) ->
        { Engine_decode.cfg = l.cfg.value; ticker_running = l.run.value;
          mutation = line_mutation }) line_registers)
      ~ownership:i.ownership ~transfer_pins:pins.value i.instruction in
  let fields = decode.fields and valid = decode.valid in
  let { Engine_decode.word; op; a; b; c; imm24; imm16; low8; dest; src; pin } = fields in
  let is (opcode : Opcode.t) = op ==:. Opcode.to_int opcode in
  let data_width = config.data_width in
  let image_length = uresize i.image_length pcw in
  (* Saturating PC: a branch target >= 2^pcw becomes the all-ones PC (127),
     which is outside every image (<= 64 words) and so still faults with
     code 2.  Identity for the 24-bit PC. *)
  let sat v = if pcw = 24 then v
    else mux2 (select v 23 pcw <>:. 0) (ones pcw) (select v (pcw-1) 0) in
  (* Byte-lane shifts (shift=byte_lane): a multiplexer over the byte shifts,
     selected by c[4:3] (c[3] with 16-bit data). *)
  let shift op value count = match options.shift with
    | Barrel -> log_shift op value count
    | Byte_lane ->
      let lane_bits = Config.log2 (data_width / 8) in
      mux (select count (2 + lane_bits) 3)
        (List.init (data_width / 8) (fun k -> if k = 0 then value else op value (8 * k))) in
  let register_values = Array.to_list (Array.map (fun (r:Variable.t) -> r.value) regs) in
  let source = mux src register_values and destination = mux dest register_values in
  let pin_input = mux pin (List.init 8 (bit i.pins)) in
  (* Timing knob keep_counter_increments: the incremented or decremented value
     of each wide counter carries a keep attribute. Synthesis then keeps it as
     a net of its own, so the counter's enable selects between two finished
     values instead of being merged into the carry chain (ABC's area mapping
     otherwise starts the chain with the enable). Identity without the knob. *)
  let step s = if timing.Timing_options.keep_counter_increments
    then Signal.add_attribute s (Rtl_attribute.create "keep" ~value:(Rtl_attribute.Value.Bool true))
    else s in
  (* ---------------- issue ---------------- *)
  let active, issue =
    if not timing.Timing_options.split_engine_issue then
      let active = running.value &: (fault.value ==:. 0) &: ~:(i.start) &: ~:(i.stop) &: ~:gate in
      let issue = active &: (timer.value ==:. 0) &: (xremaining.value ==:. 0)
                  &: (pc.value <: image_length) &: valid in
      active, issue
    else
      (* Timing knob split_engine_issue. The next-state logic below sits in the
         else branches of STOP and START, where both are low, so enabling it
         with [running & fault = 0 & ~clear] computes the same next state as
         [active]; START and STOP (host commands, late in the cycle) then gate
         only the issue-derived outputs that act outside the engine. *)
      let executing = running.value &: (fault.value ==:. 0) &: ~:gate in
      let ready = executing &: (timer.value ==:. 0) &: (xremaining.value ==:. 0)
                  &: (pc.value <: image_length) &: valid in
      let gate = ~:(i.start) &: ~:(i.stop) in
      executing, ready &: gate in
  let waiting_pin = pin_input <>: bit b 0 in
  let stalled = issue
                &: (((is Pull) &: ~:(i.tx_valid)) |: ((is Push) &: (a ==:. 0) &: ~:(i.rx_ready))
                    |: ((is Waitpin) &: waiting_pin) |: ((is Waitevent) &: ~:(i.event))) in
  let tx_pop = issue &: (is Pull) &: i.tx_valid in
  let rx_push = issue &: (is Push) &: i.rx_ready in
  let consume_event = issue &: (is Waitevent) &: i.event in
  let signal_events = mux2 (issue &: (is Signal))
      (uresize imm24 config.engine_count) (zero config.engine_count) in
  (* ---------------- execute ---------------- *)
  (* [retire next_pc]: the instruction completes and the PC moves on. With
     split_instruction_decode the completed-instruction counter is not
     assigned here but by its own enable (see the end of this function).
     [finish] is [retire] of PC + 1, built once and shared by every body. *)
  let retire next_pc =
    [pc <-- next_pc]
    @ (if split_decode then [] else [completed <-- step (completed.value +:. 1)])
    @ [blocked <--. 0] in
  let finish = retire (step (pc.value +:. 1)) in
  let fail code = [fault <-- code; running <--. 0; enables <--. 0; xremaining <--. 0] in
  let write_reg = Engine_datapath.write_register d ~dest in
  let tx_bit = Engine_datapath.tx_bit and shift_tx msb = Engine_datapath.shift_tx d ~msb in
  let sample msb data = Engine_datapath.sample d ~msb data in
  let blocked_step condition = if_ condition finish
      [if_ (blocked.value +:. 1 >=: mux2 (limit.value ==:. 0)
                                      (of_int ~width:24 65535) limit.value)
          (fail (of_int ~width:8 3)) [blocked <-- step (blocked.value +:. 1)]] in
  let line_unit =
    Option.map (fun registers ->
      Engine_line.create ?mutation:line_mutation registers d ~fields ~source ~finish
        ~pins:i.pins ~tx_valid:i.tx_valid ~rx_ready:i.rx_ready) line_registers in
  let values_now = match line_unit with None -> values.value | Some l -> l.values_now in
  let line_body body = Option.map body line_unit in
  (* The execute body of every opcode. [None]: not implemented in this
     configuration (the line-unit opcodes without the unit); the decoder
     rejects those. XFER has a body with scalar issue too, which the decoder
     also rejects. *)
  let execute : Opcode.t -> Always.t list option = function
    | Nop -> Some finish
    | Halt -> Some (finish @ [running <--. 0; enables <--. 0])
    | Set -> Some (finish @ [values <-- low8])
    | Dir -> Some (finish @ [enables <-- low8])
    | Wait -> Some (finish @ [timer <-- imm24])
    | Jmp -> Some (retire (sat imm24))
    | Pull -> Some [when_ i.tx_valid (finish @ [tx <-- i.tx_data])]
    | Push -> Some [if_ i.rx_ready finish [when_ (a ==:. 1) (fail (of_int ~width:8 4))]]
    | Out -> Some (finish @ [values <-- Engine_datapath.write_pin values_now pin
                                          (tx_bit ~msb:(bit c 0) tx.value);
                             tx <-- shift_tx (bit c 0)])
    | In -> Some (finish @ [rx <-- sample (bit c 0) pin_input])
    | Count -> Some (finish @ [repeat_count <-- imm16])
    | Loop -> Some (finish @ [when_ (repeat_count.value <>:. 0)
                               [repeat_count <-- step (repeat_count.value -:. 1); pc <-- sat imm24]])
    | Limit -> Some (finish @ [limit <-- imm24])
    | Waitpin -> Some [blocked_step (~:waiting_pin)]
    | Signal -> Some finish
    | Waitevent -> Some [blocked_step i.event]
    | Pins -> Some (finish @ [pins <-- select word 8 0])
    | Xfer -> Some (Engine_transfer.issue d ~fields ~values_now ~line:line_unit)
    | Mov -> Some (finish @ write_reg source)
    | Load -> Some (finish @ write_reg (uresize imm16 data_width))
    | Add -> Some (finish @ write_reg (destination +: source))
    | Xor -> Some (finish @ write_reg (destination ^: source))
    | And -> Some (finish @ write_reg (destination &: source))
    | Or -> Some (finish @ write_reg (destination |: source))
    | Shl -> Some (finish @ write_reg (shift sll destination c))
    | Shr -> Some (finish @ write_reg (shift srl destination c))
    | Jz -> Some (finish @ [when_ (destination ==:. 0) [pc <-- sat (uresize imm16 24)]])
    | Not -> Some (finish @ write_reg (~:destination))
    | Time -> Some (finish @ write_reg (uresize i.timestamp data_width))
    | Fault -> Some (fail low8)
    | Ltim -> line_body (fun l -> l.ltim)
    | Lcfg -> line_body (fun l -> l.lcfg)
    | Crc -> line_body (fun l -> l.crc)
    | Lstat -> line_body (fun l -> l.lstat) in
  let ordinary = [if_ ((pc.value >=: image_length) |: ~:valid)
      (fail (mux2 (pc.value >=: image_length) (of_int ~width:8 2) (of_int ~width:8 1)))
      [switch op (List.filter_map (fun opcode ->
         Option.map (fun body -> of_int ~width:8 (Opcode.to_int opcode), body) (execute opcode))
         Opcode.all)]] in
  let transfer =
    Engine_transfer.transfer d ~pins:i.pins ~values_now ~finish ~line:line_unit in
  (* ---------------- control ---------------- *)
  let every_cycle, on_start = match line_unit with
    | None -> [], []
    | Some l -> l.every_cycle, l.on_start in
  compile [when_ (i.clear_fault &: ~:(running.value)) [fault <--. 0];
    if_ i.stop [running <--. 0; enables <--. 0; xremaining <--. 0]
      [if_ i.start
        (Engine_datapath.start d ~clear_completed:(not split_decode) @ on_start)
        [when_ active
           (every_cycle @
           [if_ (timer.value <>:. 0) [timer <-- step (timer.value -:. 1)]
              [if_ (xremaining.value <>:. 0) transfer ordinary]])]]];
  if split_decode then begin
    (* The counter increments exactly where the branches above execute
       [finish] or JMP: in the else branches of STOP and START, with the
       engine running without a fault and no clear, no WAIT timer pending, and
       either the last edge of a transfer (tick <= 1, one edge left) or an
       in-image valid instruction that completes (every opcode except XFER and
       FAULT, PULL with TX valid, PUSH with RX ready, WAITPIN with the pin at
       its level, WAITEVENT with an event pending). *)
    let completes = ~:((is Xfer) |: (is Fault))
      &: ((op <>:. Opcode.to_int Pull) |: i.tx_valid)
      &: ((op <>:. Opcode.to_int Push) |: i.rx_ready)
      &: ((op <>:. Opcode.to_int Waitpin) |: ~:waiting_pin)
      &: ((op <>:. Opcode.to_int Waitevent) |: i.event) in
    let increment = running.value &: (fault.value ==:. 0) &: ~:gate &: (timer.value ==:. 0)
      &: mux2 (xremaining.value <>:. 0)
           ((xtick.value <=:. 1) &: (xremaining.value ==:. 1))
           ((pc.value <: image_length) &: valid &: completes) in
    compile [if_ i.stop [] [if_ i.start [completed <--. 0]
                               [when_ increment [completed <-- step (completed.value +:. 1)]]]]
  end;
  {pc=pc.value; running=running.value; fault=fault.value; stalled;
   tx_pop; rx_push; rx_data=rx.value; pin_values=values.value;
   pin_enables=enables.value; signal_events; consume_event;
   (* Without debug counters the completed register has no reader and is not
      emitted; READ_SELECT 5 reads zero. *)
   completed=(if options.debug_counters then completed.value else zero 32);
   issue; wait_timer=timer.value; wait_limit=limit.value; blocked_cycles=blocked.value;
   repeat_count=repeat_count.value; transfer_edges=xremaining.value;
   (* LSTAT issues this cycle: it reads the queue status bits. *)
   queue_observe=(match line_unit with None -> gnd | Some _ -> issue &: (is Lstat));
   line_state=(match line_unit with
       | None -> []
       | Some l ->
         Engine_line.Registers.(to_list (map2 port_names l.registers
                                            ~f:(fun name (v:Variable.t) -> name, v.value))))}

(* Observe the D input compiled from the single Always control tree above.
   Synchronous clear and asynchronous reset are register metadata, so include
   them explicitly.  Do not reproduce instruction decoding here: every branch,
   hold, fault and START must use precisely the same next state as the actual
   PC register.  Two register forms are accepted: the design of record
   (synchronous active-high clear to zero, no reset) and the asynchronous
   variants (active-high asynchronous reset to zero, no clear).  With an
   asynchronous reset the value after an edge at which reset is asserted is
   the reset value.  A 7-bit (saturating) PC is zero-extended to 24 bits. *)
let next_pc (engine:t) =
  match engine.pc with
  | Signal.Type.Reg {register;d;_} ->
    let is_zero = function
      | Signal.Type.Const {constant;_} -> Bits.to_int constant = 0
      | _ -> false in
    let clear_is_zero = is_zero register.reg_clear_value in
    let pc_width = width engine.pc in
    if is_empty register.reg_reset then begin
      if pc_width <> 24 && pc_width <> 7 || register.reg_clock_edge <> Edge.Rising
         || is_empty register.reg_clear
         || register.reg_clear_level <> Level.High || not clear_is_zero
         || not (is_vdd register.reg_enable)
      then invalid_arg "Engine.next_pc: unsupported PC register semantics";
      uresize (mux2 register.reg_clear register.reg_clear_value d) 24
    end else begin
      if pc_width <> 24 && pc_width <> 7 || register.reg_clock_edge <> Edge.Rising
         || not (is_empty register.reg_clear)
         || register.reg_reset_edge <> Edge.Rising || not (is_zero register.reg_reset_value)
         || not (is_vdd register.reg_enable)
      then invalid_arg "Engine.next_pc: unsupported PC register semantics";
      uresize (mux2 register.reg_reset register.reg_reset_value d) 24
    end
  | _ -> invalid_arg "Engine.next_pc: PC must remain a register"
