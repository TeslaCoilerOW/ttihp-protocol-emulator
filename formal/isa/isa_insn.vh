// Common interface of the instruction specification modules (isa_insn.sv).
//
// Inputs: the architectural state of one engine before an issue edge, the
// word W of its committed image at PC, and the environment the engine sees on
// that edge. Outputs: the architectural state after the edge, as
// docs/isa.md defines it. Field meanings:
//
//   pc, tx rx x y, rep     PC, the four registers, the COUNT/LOOP repeat counter
//   limit                  architectural LIMIT, 1..2^24-1 (reset default 65535)
//   blk                    consecutive unsuccessful WAITPIN/WAITEVENT samples so far
//   lout, len              logical pin values (SET, OUT) and output enables (DIR)
//   pins                   PINS: [2:0] clock pin, [5:3] TX pin, [8:6] RX pin
//   cnt                    completed-instruction count (READ_SELECT 5)
//   own, sync              the engine's pin ownership; synchronized pin inputs
//                          (the previous registered synchronizer output)
//   ts                     the common 32-bit timestamp
//   tx_empty, rx_full      the engine's TX FIFO is empty / RX FIFO is full,
//                          before the edge
//   evp                    the engine's event mailbox is pending before the edge
//   tx_head, tx_head_known the TX FIFO head word, when the harness knows it
//
//   s_valid                W has this module's opcode
//   s_hold                 remaining WAIT hold cycles after the edge
//   s_pop, s_push          the engine pops its TX FIFO / pushes its RX FIFO on
//   s_push_data            the edge, and the pushed word
//   s_evclr                the engine consumes its mailbox on the edge
//   s_signal               mailboxes that are pending after the edge (SIGNAL)
//   s_busy                 an XFER is in progress after the edge
//   s_dc                   fields docs/isa.md leaves unspecified (ISA_DC_*)

`define ISA_DC_LOUT  0
`define ISA_DC_LEN   1
`define ISA_DC_BLK   2
`define ISA_DC_TX    3

`define ISA_INSN_PORTS \
    input  wire [31:0] insn, \
    input  wire [23:0] pc, \
    input  wire [31:0] tx, rx, x, y, \
    input  wire [15:0] rep, \
    input  wire [23:0] limit, blk, \
    input  wire [7:0]  lout, len, \
    input  wire [8:0]  pins, \
    input  wire [31:0] cnt, \
    input  wire [7:0]  own, sync, \
    input  wire [31:0] ts, \
    input  wire        tx_empty, rx_full, evp, \
    input  wire [31:0] tx_head, \
    input  wire        tx_head_known, \
    output wire        s_valid, \
    output wire [`ISA_RW-1:0] s_out

// The result fields, packed into s_out in this order (isa_spec.sv unpacks it).
`define ISA_RW (24+1+8+4*32+16+3*24+8+8+9+32+1+1+32+1+4+1+4)
`define ISA_RESULT \
    s_pc, s_run, s_fault, s_tx, s_rx, s_x, s_y, s_rep, s_hold, s_limit, s_blk, \
    s_lout, s_len, s_pins, s_cnt, s_pop, s_push, s_push_data, s_evclr, s_signal, \
    s_busy, s_dc
`define ISA_INSN_REGS \
    reg [23:0] s_pc; reg s_run; reg [7:0] s_fault; reg [31:0] s_tx, s_rx, s_x, s_y; \
    reg [15:0] s_rep; reg [23:0] s_hold, s_limit, s_blk; reg [7:0] s_lout, s_len; \
    reg [8:0] s_pins; reg [31:0] s_cnt; reg s_pop, s_push; reg [31:0] s_push_data; \
    reg s_evclr; reg [3:0] s_signal; reg s_busy; reg [3:0] s_dc; \
    assign s_out = {`ISA_RESULT};

// The instruction completes: PC + 1, one more completed instruction, nothing
// else changes. A completed instruction ends any wait (blk = 0).
`define ISA_COMPLETE \
    begin \
        s_pc = pc + 24'd1; s_run = 1'b1; s_fault = 8'd0; \
        s_tx = tx; s_rx = rx; s_x = x; s_y = y; s_rep = rep; \
        s_hold = 24'd0; s_limit = limit; s_blk = 24'd0; \
        s_lout = lout; s_len = len; s_pins = pins; s_cnt = cnt + 32'd1; \
        s_pop = 1'b0; s_push = 1'b0; s_push_data = 32'd0; s_evclr = 1'b0; \
        s_signal = 4'd0; s_busy = 1'b0; s_dc = 4'd0; \
    end

// The instruction does not complete and nothing changes (PULL on an empty
// FIFO, PUSH a=0 on a full one; WAITPIN/WAITEVENT then add one to blk).
`define ISA_BLOCK \
    begin \
        `ISA_COMPLETE \
        s_pc = pc; s_cnt = cnt; s_blk = blk; \
    end

// "Faults stop execution and release output enables." The faulting
// instruction does not complete: PC, count, registers and queues keep their
// values. Release of the output enables is a pin-level fact (isa_pinmap); the
// logical output and enable registers are left unspecified.
`define ISA_FAULT(code) \
    begin \
        `ISA_BLOCK \
        s_run = 1'b0; s_fault = code; \
        s_dc = (4'd1 << `ISA_DC_LOUT) | (4'd1 << `ISA_DC_LEN) | (4'd1 << `ISA_DC_BLK); \
    end

// Register file: 0 = tx, 1 = rx, 2 = x, 3 = y.
`define ISA_REG(i) ((i) == 2'd0 ? tx : (i) == 2'd1 ? rx : (i) == 2'd2 ? x : y)
`define ISA_WRITE(i, v) \
    begin \
        case (i) \
            2'd0: s_tx = v; \
            2'd1: s_rx = v; \
            2'd2: s_x = v; \
            default: s_y = v; \
        endcase \
    end
