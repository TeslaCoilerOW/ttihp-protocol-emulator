// Instruction-level specification of the protocol engine, one module per
// instruction of the design of record (docs/isa.md, "Instructions"; ISA 2,
// configs/instruction-sram-32.json: 4 engines, 32-bit datapath, fused issue).
//
// Written from docs/isa.md only. Each module maps the architectural state
// before an issue edge and the image word at PC to the architectural state
// after the edge (interface: isa_insn.vh). formal/isa/isa_spec.sv checks the
// production RTL against these modules through the observation ports of
// formal/build/rtl/processor_fv.v. The quoted rows are docs/isa.md.
//
// Rules that apply to every instruction (docs/isa.md, "Machine" and
// "Instructions"):
//   * "Every instruction is 32 bits, op=[31:24], a=[23:16], b=[15:8],
//     c=[7:0], imm24=[23:0], imm16=[15:0]." "Unused operands are zero."
//     A non-zero unused operand is an invalid operand: fault 1.
//   * "Registers tx,rx,x,y have datapath width; arithmetic wraps."
//   * "A nonblocking instruction costs one cycle": it completes on its issue
//     edge (PC + 1 unless it jumps; the completed-instruction count + 1).
//   * "Pin numbers are 0..7." "Pin writes outside ownership and output
//     enables outside ownership fault." "Reading any pin is legal."
//   * "Faults stop execution and release output enables." Fault 1 is
//     "invalid opcode/operand/ownership". A faulting instruction does not
//     complete (ISA_FAULT in isa_insn.vh).
// PC outside the image (fault 2) is checked by isa_spec.sv before any word is
// decoded.

`include "isa_insn.vh"

`define ISA_FIELDS \
    wire [7:0] op = insn[31:24]; \
    wire [7:0] a = insn[23:16]; \
    wire [7:0] b = insn[15:8]; \
    wire [7:0] c = insn[7:0]; \
    wire [23:0] imm24 = insn[23:0]; \
    wire [15:0] imm16 = insn[15:0]; \
    `ISA_INSN_REGS

// |0|NOP|none|
module isa_insn_nop (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd0;
    always @* begin
        `ISA_COMPLETE
        if (imm24 != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |1|HALT|stop and release output enables; preserve queues and diagnostic registers|
// HALT stops the engine without a fault. docs/isa.md does not say whether
// HALT itself advances PC and the completed-instruction count. The RTL
// completes it (PC + 1, count + 1); the spec follows that resolution
// (docs/isa-spec.md, disagreement D1). "Preserve diagnostic registers": HALT
// changes no register, counter, queue or configuration.
module isa_insn_halt (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd1;
    always @* begin
        `ISA_COMPLETE
        s_run = 1'b0;
        s_dc = (4'd1 << `ISA_DC_LOUT) | (4'd1 << `ISA_DC_LEN);
        if (imm24 != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |2|SET|imm24 low8 sets logical pin values; high16 zero|
// "SET/DIR are whole-eight-bit logical values and may contain only owned bits."
module isa_insn_set (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd2;
    always @* begin
        `ISA_COMPLETE
        s_lout = imm24[7:0];
        if (imm24[23:8] != 0 || (imm24[7:0] & ~own) != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |3|DIR|imm24 low8 sets logical output enables; high16 zero|
module isa_insn_dir (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd3;
    always @* begin
        `ISA_COMPLETE
        s_len = imm24[7:0];
        if (imm24[23:8] != 0 || (imm24[7:0] & ~own) != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |4|WAIT|imm24 additional cycles|
// "WAIT n advances PC on issue, then holds execution for n additional cycles;
// WAIT 0 therefore costs one cycle." The hold countdown is isa_hold.
module isa_insn_wait (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd4;
    always @* begin
        `ISA_COMPLETE
        s_hold = imm24;
    end
endmodule

// |5|JMP|imm24 target PC|
// A target outside the image is legal here; the next issue faults with code 2.
module isa_insn_jmp (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd5;
    always @* begin
        `ISA_COMPLETE
        s_pc = imm24;
    end
endmodule

// |6|PULL|tx := TX FIFO head; block without changing state when empty|
// "An empty FIFO does not allow a pop on the same edge as its first push":
// admission uses the level before the edge. The head word is known to the
// harness only in the tagged-word job (isa_queue_tx); elsewhere tx is
// unspecified after a completing PULL.
module isa_insn_pull (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd6;
    always @* begin
        `ISA_COMPLETE
        s_pop = 1'b1;
        s_tx = tx_head;
        if (!tx_head_known) s_dc = 4'd1 << `ISA_DC_TX;
        if (imm24 != 0) `ISA_FAULT(8'd1)
        else if (tx_empty) `ISA_BLOCK
    end
endmodule

// |7|PUSH|a=0 block when full; a=1 fault4 when full; b=c=0. When space exists,
// append rx to RX FIFO and advance in one cycle|
// "Strict overflow preserves the rejected RX word, PC and completed-instruction
// count, halts only that engine, and releases its output enables. It performs
// no enqueue. Space freed on the same edge does not change pre-edge admission."
module isa_insn_push (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd7;
    always @* begin
        `ISA_COMPLETE
        s_push = 1'b1;
        s_push_data = rx;
        if (a > 8'd1 || b != 0 || c != 0) `ISA_FAULT(8'd1)
        else if (rx_full && a == 8'd0) `ISA_BLOCK
        else if (rx_full) `ISA_FAULT(8'd4)
    end
endmodule

// |8|OUT|a pin, b=0, c direction 0=LSB/right shift, 1=MSB/left shift; update
// that output bit and shift tx|
// LSB/right: the pin takes tx[0] and tx shifts right; MSB/left: the pin takes
// tx[31] and tx shifts left. The pin must be owned.
module isa_insn_out (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd8;
    always @* begin
        `ISA_COMPLETE
        s_lout[a[2:0]] = c[0] ? tx[31] : tx[0];
        s_tx = c[0] ? (tx << 1) : (tx >> 1);
        if (a > 8'd7 || b != 0 || c > 8'd1 || !own[a[2:0]]) `ISA_FAULT(8'd1)
    end
endmodule

// |9|IN|a pin, b=0, c direction 0=right shift and insert input at MSB, 1=left
// shift and insert input at LSB|
// "Each engine observes the previous registered synchronizer output on an edge."
module isa_insn_in (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd9;
    always @* begin
        `ISA_COMPLETE
        s_rx = c[0] ? {rx[30:0], sync[a[2:0]]} : {sync[a[2:0]], rx[31:1]};
        if (a > 8'd7 || b != 0 || c > 8'd1) `ISA_FAULT(8'd1)
    end
endmodule

// |10|COUNT|imm16 repeat counter; a=0|
module isa_insn_count (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd10;
    always @* begin
        `ISA_COMPLETE
        s_rep = imm16;
        if (a != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |11|LOOP|imm24 target: if repeat !=0 decrement and jump, otherwise next PC;
// COUNT n yields n+1 loop iterations|
module isa_insn_loop (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd11;
    always @* begin
        `ISA_COMPLETE
        if (rep != 0) begin
            s_rep = rep - 16'd1;
            s_pc = imm24;
        end
    end
endmodule

// |12|LIMIT|imm24 nonzero maximum blocked cycles for WAITPIN/WAITEVENT (reset
// default 65535)|
module isa_insn_limit (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd12;
    always @* begin
        `ISA_COMPLETE
        s_limit = imm24;
        if (imm24 == 0) `ISA_FAULT(8'd1)
    end
endmodule

// |13|WAITPIN|a pin, b expected bit 0/1, c=0; consume one issue edge if
// already true; otherwise block, fault after LIMIT consecutive unsuccessful
// samples|
// The sample of this edge is unsuccessful sample number blk + 1; when that is
// LIMIT the engine faults with code 3 ("3 bounded-wait timeout").
module isa_insn_waitpin (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd13;
    always @* begin
        `ISA_COMPLETE
        if (a > 8'd7 || b > 8'd1 || c != 0) `ISA_FAULT(8'd1)
        else if (sync[a[2:0]] != b[0]) begin
            if ({1'b0, blk} + 25'd1 >= {1'b0, limit}) `ISA_FAULT(8'd3)
            else begin
                `ISA_BLOCK
                s_blk = blk + 24'd1;
            end
        end
    end
endmodule

// |14|SIGNAL|imm24 low engine-count bits sets recipients' event mailboxes|
// "Immediate masks must not address absent engines": bits 23:4 are zero.
module isa_insn_signal (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd14;
    always @* begin
        `ISA_COMPLETE
        s_signal = imm24[3:0];
        if (imm24[23:4] != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |15|WAITEVENT|none; consume own pending mailbox bit or wait bounded by
// LIMIT; simultaneous delivery wins over clear|
// The mailbox after a consuming edge is pending exactly when a delivery
// arrives on that edge (checked in isa_spec.sv).
module isa_insn_waitevent (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd15;
    always @* begin
        `ISA_COMPLETE
        s_evclr = 1'b1;
        if (imm24 != 0) `ISA_FAULT(8'd1)
        else if (!evp) begin
            if ({1'b0, blk} + 25'd1 >= {1'b0, limit}) `ISA_FAULT(8'd3)
            else begin
                `ISA_BLOCK
                s_blk = blk + 24'd1;
            end
        end
    end
endmodule

// |16|PINS|imm24 bits2:0 clock pin,5:3 TX pin,8:6 RX pin; high15 zero|
module isa_insn_pins (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd16;
    always @* begin
        `ISA_COMPLETE
        s_pins = imm24[8:0];
        if (imm24[23:9] != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |17|XFER|a bit count 1..datapath width, b half-period 1..255 cycles, c bits0
// CPOL,1 CPHA,2 MSB-first,3 drive,4 sample; other bits zero; fused only|
// This module is the issue edge only; the bounded jobs isa_xfer_e2e* check
// the whole transfer. "XFER sets clock idle on issue, prepares CPHA0 first
// data bit on issue, then waits b clocks to its first transition." "PC
// advances at final idle edge." "When drive is enabled, the configured clock
// and TX pins must differ; a collision faults with invalid-operand code 1
// before any transfer transition." The clock pin is written, and with drive
// the TX pin is written, so both must be owned. Whether the prepared bit
// already shifts tx is not stated: tx is unspecified after the issue edge.
module isa_insn_xfer (`ISA_INSN_PORTS);
    `ISA_FIELDS
    wire [2:0] ck = pins[2:0], tp = pins[5:3];
    wire drive = c[3], cpha = c[1], msb = c[2];
    always @* begin
        `ISA_BLOCK
        s_busy = 1'b1;
        s_dc = 4'd1 << `ISA_DC_TX;
        s_lout[ck] = c[0];
        if (!cpha && drive) s_lout[tp] = msb ? tx[31] : tx[0];
        if (a == 0 || a > 8'd32 || b == 0 || c[7:5] != 0 || !own[ck]
            || (drive && (!own[tp] || tp == ck))) `ISA_FAULT(8'd1)
    end
    assign s_valid = op == 8'd17;
endmodule

// |18|MOV|a dest register, b source register, c=0; registers 0=tx,1=rx,2=x,3=y|
module isa_insn_mov (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd18;
    always @* begin
        `ISA_COMPLETE
        `ISA_WRITE(a[1:0], `ISA_REG(b[1:0]))
        if (a > 8'd3 || b > 8'd3 || c != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |19|LOAD|a dest register, imm16 zero-extended literal|
module isa_insn_load (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd19;
    always @* begin
        `ISA_COMPLETE
        `ISA_WRITE(a[1:0], {16'd0, imm16})
        if (a > 8'd3) `ISA_FAULT(8'd1)
    end
endmodule

// |20|ADD|a dest, b source, c=0|  |21|XOR|  |22|AND|  |23|OR|
// dest := dest OP source.
module isa_insn_alu #(parameter [7:0] OPCODE = 8'd20) (`ISA_INSN_PORTS);
    `ISA_FIELDS
    wire [31:0] d = `ISA_REG(a[1:0]), s = `ISA_REG(b[1:0]);
    wire [31:0] result = OPCODE == 8'd20 ? d + s : OPCODE == 8'd21 ? d ^ s :
                         OPCODE == 8'd22 ? d & s : d | s;
    assign s_valid = op == OPCODE;
    always @* begin
        `ISA_COMPLETE
        `ISA_WRITE(a[1:0], result)
        if (a > 8'd3 || b > 8'd3 || c != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |24|SHL|a register, b=0, c shift count < datapath width|
// |25|SHR|a register, b=0, c shift count < datapath width|
// Logical shifts (zero fill).
module isa_insn_shift #(parameter [7:0] OPCODE = 8'd24) (`ISA_INSN_PORTS);
    `ISA_FIELDS
    wire [31:0] d = `ISA_REG(a[1:0]);
    assign s_valid = op == OPCODE;
    always @* begin
        `ISA_COMPLETE
        `ISA_WRITE(a[1:0], OPCODE == 8'd24 ? d << c[4:0] : d >> c[4:0])
        if (a > 8'd3 || b != 0 || c > 8'd31) `ISA_FAULT(8'd1)
    end
endmodule

// |26|JZ|a register, imm16 target PC|
module isa_insn_jz (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd26;
    always @* begin
        `ISA_COMPLETE
        if (`ISA_REG(a[1:0]) == 32'd0) s_pc = {8'd0, imm16};
        if (a > 8'd3) `ISA_FAULT(8'd1)
    end
endmodule

// |27|NOT|a register, b=c=0|
module isa_insn_not (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd27;
    always @* begin
        `ISA_COMPLETE
        `ISA_WRITE(a[1:0], ~`ISA_REG(a[1:0]))
        if (a > 8'd3 || b != 0 || c != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |28|TIME|a register := common 32-bit timestamp truncated to datapath width; b=c=0|
// Reading: the timestamp value before the issue edge (the state the edge samples).
module isa_insn_time (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd28;
    always @* begin
        `ISA_COMPLETE
        `ISA_WRITE(a[1:0], ts)
        if (a > 8'd3 || b != 0 || c != 0) `ISA_FAULT(8'd1)
    end
endmodule

// |29|FAULT|imm24 low8 nonzero explicit fault code; high16 zero|
// "FAULT uses its explicit nonzero code."
module isa_insn_fault (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op == 8'd29;
    always @* begin
        `ISA_FAULT(imm24[7:0])
        if (imm24[23:8] != 0 || imm24[7:0] == 0) `ISA_FAULT(8'd1)
    end
endmodule

// "Other opcodes fault." (30..255 in the design of record, which has no line
// unit.)
module isa_insn_invalid (`ISA_INSN_PORTS);
    `ISA_FIELDS
    assign s_valid = op >= 8'd30;
    always @* begin
        `ISA_FAULT(8'd1)
    end
endmodule
