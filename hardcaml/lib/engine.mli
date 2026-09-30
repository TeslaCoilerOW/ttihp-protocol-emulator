(** One engine: an instruction stream with its registers, pin outputs and
    timed transfers (docs/isa.md). {!Processor} instantiates one per engine
    and connects it to its instruction memory, queues, mailbox and pins.

    [create] is split by stage:
    - {!Engine_datapath}: the architectural registers and register-transfer
      helpers;
    - {!Engine_decode}: operand fields and the validity rule of each
      {!Opcode.t};
    - the execute stage (here): one body per {!Opcode.t}, selected by a
      [switch] on the opcode;
    - {!Engine_transfer}: XFER issue and transfer;
    - {!Engine_line}: the line unit, only with {!Line_options.enabled}.

    All registers are written from one [Always] tree compiled here: a fault
    clear, then STOP, then START, then (when running without a fault) the
    WAIT countdown, a transfer in progress or the next instruction. The
    exception is [completed] with {!Timing_options.t.split_instruction_decode}:
    it then has a second [compile] of its own, with the same STOP and START
    priority and an increment enable computed from the decoded opcode. *)

(** The engine's inputs, as a Hardcaml interface. [port_names] are the
    port names of the standalone engine circuits (generate_formal, the
    formal and test wrappers): the field names, except [clk] and
    [event_pending]. Written out by hand (the library has no ppx_hardcaml). *)
module I : sig
  type 'a t = {
    clock : 'a;
    clear : 'a;  (** chip clear: synchronous clear, or asynchronous reset in the async variants *)
    start : 'a;  (** host START for this engine *)
    stop : 'a;  (** host STOP or BEGIN *)
    clear_fault : 'a;  (** host CLEAR *)
    instruction : 'a;  (** the 32-bit word at the PC *)
    image_length : 'a;  (** committed image length; a PC at or above it faults (code 2) *)
    ownership : 'a;  (** OWN mask: pins this engine may drive *)
    pins : 'a;  (** synchronized pad inputs *)
    timestamp : 'a;  (** the chip's 32-bit cycle counter (TIME) *)
    tx_valid : 'a;  (** TX queue not empty *)
    tx_data : 'a;  (** TX queue head, [data_width] bits *)
    rx_ready : 'a;  (** RX queue not full *)
    event : 'a;  (** mailbox pending (port [event_pending]) *)
  }

  include Hardcaml.Interface.S with type 'a t := 'a t

  (** Input ports named [port_names], [port_widths] bits wide except
      [tx_data], which is [data_width] bits wide ([port_widths] has the 32-bit
      design of record). *)
  val ports : Config.t -> Hardcaml.Signal.t t
end

type inputs = Hardcaml.Signal.t I.t

type t = {
  pc : Hardcaml.Signal.t; running : Hardcaml.Signal.t; fault : Hardcaml.Signal.t;
  stalled : Hardcaml.Signal.t; tx_pop : Hardcaml.Signal.t;
  rx_push : Hardcaml.Signal.t; rx_data : Hardcaml.Signal.t;
  pin_values : Hardcaml.Signal.t; pin_enables : Hardcaml.Signal.t;
  signal_events : Hardcaml.Signal.t; consume_event : Hardcaml.Signal.t;
  completed : Hardcaml.Signal.t;
  issue : Hardcaml.Signal.t; wait_timer : Hardcaml.Signal.t;
  wait_limit : Hardcaml.Signal.t; blocked_cycles : Hardcaml.Signal.t;
  repeat_count : Hardcaml.Signal.t; transfer_edges : Hardcaml.Signal.t;
  queue_observe : Hardcaml.Signal.t;
  (** LSTAT issues this cycle: the engine reads its queue status bits
      (always 0 without the line unit). *)
  line_state : (string * Hardcaml.Signal.t) list;
  (** The line-unit registers by name ([line_register_names]); empty without
      the unit. *)
}

(** Register names of the line unit, in [line_state] order
    ({!Engine_line.Registers.port_names}). *)
val line_register_names : string list

(** [options] (default [Variant_options.default]) selects the engine-level
    variant knobs: reset style (synchronous clear or asynchronous reset from
    [clear]), debug counters, PC width and shift implementation. [timing]
    (default {!Timing_options.default}) applies [split_engine_issue] and
    [split_instruction_decode]. [gate] (default [clear]) is the clear that
    gates the next-state enable and the issue outputs; the registers always
    take [clear]. [line] (default {!Line_options.default}, none) adds the
    line unit of docs/extension.md; [line_mutation] seeds one defect into it
    for the formal negative controls and is never used by the production
    generators. *)
val create : ?options:Variant_options.t -> ?timing:Timing_options.t ->
  ?line:Line_options.t -> ?line_mutation:Line_unit.mutation -> ?gate:Hardcaml.Signal.t ->
  Config.t -> inputs -> t

(** Actual compiled PC next value (24 bits) including the synchronous clear or
    asynchronous reset.  Rejects any other clock/reset/enable contract rather
    than duplicating the control decoder. *)
val next_pc : t -> Hardcaml.Signal.t
