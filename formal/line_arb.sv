// Line unit (docs/isa.md, "Arbitration"; docs/extension.md): the
// arbitration monitor of a drive-and-sample line XFER.
//
// The production engine with the unit (protocol_engine_line) runs PULL
// (DATA), LTIM (P, Q, D), LCFG C, PINS S, XFER N line|drive|sample (MSB or
// LSB first), then JMP to itself. All operands are free constants: any valid
// LTIM, any legal LCFG with the arbitration monitor on and without Manchester
// (NRZ or NRZI; any stuffing; pair; SE0 end; initial level), any PINS
// selection (distinct data and pair pins with the pair), N 1..32. The input
// pins (the bus) are free in every cycle.
//
// docs/isa.md: "With the monitor on, a drive-and-sample XFER that reads 0
// while it drives 1 sets the lost flag; from then on it drives 1 in every
// data and stuff cell and keeps receiving." The harness takes the tick cycles
// from the engine's ticker (line_tick proves their schedule) and what the XFER
// drives from its cell bit (the line bit of the current cell, which line_tx
// relates to the ISA for a driving XFER). Claims (target of: line_arb_neg):
//   B1  the lost flag (LSTAT bit 1) equals a harness flag that is cleared by
//       LCFG and set at a mid-bit tick of the running XFER after its first
//       bit boundary, without SE0, at which the RX pin reads 0 while the
//       current cell bit is 1;
//   B2  after the flag is set, every cell the XFER drives is a 1.
// Lemmas (lines marked L): the program state. The tick counter and period
// register are read through ports that the .sby script adds with `expose`
// (observation only). -DNO_LEMMAS (the negative control, bounded) leaves the
// lemmas out; -DARB_COVER adds non-vacuity covers.
`ifdef PC_SAT
`define LINE_PCW 7
`else
`define LINE_PCW 24
`endif

module line_arb #(parameter PCW=`LINE_PCW) (input wire clk);
    (* anyconst *) reg [31:0] data;
    (* anyconst *) reg [4:0] n_code;
    (* anyconst *) reg msb;
    (* anyconst *) reg [10:0] cfg;
    (* anyconst *) reg [7:0] tp, tq, td;
    (* anyconst *) reg [8:0] sel;
    (* anyseq *) reg [7:0] pins_in;
    wire [5:0] n = {1'b0, n_code} + 6'd1;
    wire [1:0] code = cfg[1:0];
    wire stuff_en = cfg[2], stuff_any = cfg[3], pair = cfg[7], se0_end = cfg[9];
    wire [2:0] ck = sel[2:0], out = sel[5:3], in = sel[8:6];
    always @* begin
        assume(code != 2'd3 && code != 2'd2 && cfg[8]);    // legal, no Manchester, monitor on
        assume(!(stuff_en && stuff_any && cfg[6:4] == 3'd0));
        assume(tp != 0 && !(tp == 8'd255 && tq != 0));      // valid LTIM
        assume(!(pair && ck == out));                       // distinct pair pin
`ifdef ARB_BOUNDED
        assume(tp <= 8'd2 && tq == 0 && td == 0 && n <= `ARB_BOUNDED);
`endif
    end
    reg past_valid = 0;
    reg [7:0] cycle = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (cycle != 8'hff) cycle <= cycle + 1;
    end
    wire clear = !past_valid;
    wire start = cycle == 1;
    always @* assert(past_valid == (cycle != 0));  // harness: reset only in cycle 0
    wire [PCW-1:0] pc;
    wire running, issue;
    wire [7:0] fault;
    wire [6:0] edges, mode;
    wire [23:0] wait_timer;
    wire [8:0] pins_reg;
    wire [9:0] l_cfg;
    wire l_run, phase, cell, seen, lost;
    wire [7:0] e_tick, e_period, l_frac;
    wire [7:0] xfer_c = {1'b0, 1'b0, 1'b1, 1'b1, 1'b1, msb, 2'b00};
    reg [31:0] instruction;
    always @* begin
        case (pc)
            0: instruction = 32'h06000000;                  // PULL (DATA)
            1: instruction = {8'd30, td, tq, tp};           // LTIM P, Q, D
            2: instruction = {8'd31, 13'd0, cfg};           // LCFG
            3: instruction = {8'd16, 15'd0, sel};           // PINS
            4: instruction = {8'd17, 2'd0, n, 8'd0, xfer_c};  // XFER N line|drive|sample
            default: instruction = {8'd5, 24'd5};           // JMP 5
        endcase
    end
    protocol_engine_line dut(.clk(clk), .clear(clear), .start(start), .stop(1'b0),
        .clear_fault(1'b0), .instruction(instruction), .image_length(24'd6),
        .ownership(8'hff), .pins(pins_in), .timestamp(32'd0), .tx_valid(1'b1),
        .tx_data(data), .rx_ready(1'b1), .event_pending(1'b0),
        .pc(pc), .running(running), .fault(fault), .issue(issue),
        .transfer_edges(edges), .transfer_mode(mode), .wait_timer(wait_timer),
        .transfer_pins(pins_reg), .ls_line_cfg(l_cfg), .ls_line_run(l_run),
        .ls_line_phase(phase), .ls_line_cell_bit(cell), .ls_line_boundary_seen(seen),
        .ls_arbitration_lost(lost), .ls_line_frac(l_frac),
        .transfer_tick(e_tick), .transfer_period(e_period));

    wire active = running && fault == 0 && !start && !clear;
    wire tick = active && l_run && e_tick == 8'd1;
    wire in_xfer = pc == 4 && edges != 0;
    wire raw = pins_in[in];
    wire se0 = se0_end && pair && !raw && !pins_in[ck];
    // the harness flag (B1)
    reg h_lost = 0;
    always @(posedge clk)
        if (start || issue && pc == 2) h_lost <= 0;
        else if (tick && phase && in_xfer && seen && !se0 && cell && !raw) h_lost <= 1;
    always @(posedge clk) begin
        if (past_valid && cycle >= 2) begin
            assert(fault == 0 && pc <= 5);
            assert(lost == h_lost);                                    // B1, target of: line_arb_neg
            if (past_valid && $past(h_lost && tick && !phase && in_xfer && !start))
                assert(cell);                                          // B2, target of: line_arb_neg
`ifndef NO_LEMMAS
            assert(running && wait_timer == 0);                        // L
            if (pc >= 2) assert(l_run && e_tick != 0 && e_period == tp && l_frac == tq);  // L
            if (pc >= 3) assert(l_cfg == cfg[9:0]);                    // L
            if (pc >= 4) assert(pins_reg == sel);                      // L
            if (pc != 4) assert(edges == 0);                           // L
            if (in_xfer) assert(mode == xfer_c[6:0]);                  // L
            if (pc < 3) assert(!h_lost);                               // L
`endif
        end
    end
`ifdef ARB_COVER
    always @(posedge clk) if (past_valid && cycle >= 2) begin
        cover(pc == 5 && h_lost && stuff_en && n >= 3);                // lost, then completes
        cover(pc == 5 && !h_lost && code == 2'd1 && n >= 2);           // NRZI, not lost
    end
`endif
endmodule
