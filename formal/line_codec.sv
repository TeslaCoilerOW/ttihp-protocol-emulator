// Line unit (docs/extension.md): the line coder and decoder are inverse.
//
// Two production engines with the line unit (protocol_engine_line) are
// started together. Engine A sends N data bits with a drive-only line XFER on
// pin 0 (pair pin 1); engine B receives them with a sample-only line XFER
// from A's pins (no synchronizer at this level). Both use the same LCFG C and
// LTIM P; for Manchester (TX only) B samples as NRZ. Free constants: DATA, N
// (1..8 at P = 1; with -DCODEC_P2, P = 2 and N 1..4), bit order, CRC feed, and
// C (NRZ, NRZI or Manchester; stuffing off or on, ones or either polarity,
// every legal run length (none with Manchester); pair; SE0 end; initial
// level). Both issue
// LTIM in the same cycle; B issues its XFER one cycle after A, so that its
// first mid-bit sample falls into A's first cell also at P = 1.
//
// When B's XFER has completed: B's RX register holds the N data bits in
// order (their complements for Manchester, whose first half-bit B sees at
// mid-bit), B saw no stuff error and no SE0, and with the CRC feed B's CRC
// equals A's (driven bits against sampled bits). A bounded check.
`ifdef PC_SAT
`define LINE_PCW 7
`else
`define LINE_PCW 24
`endif

module line_codec #(parameter PCW=`LINE_PCW, LIMIT=44) (input wire clk);
    (* anyconst *) reg [31:0] data;
    (* anyconst *) reg [2:0] n_code;
    (* anyconst *) reg msb, crc;
`ifdef CODEC_P2
    wire p2 = 1'b1;
`else
    wire p2 = 1'b0;
`endif
    (* anyconst *) reg [10:0] cfg;
    wire [4:0] n = {2'd0, n_code} + 5'd1;
    wire [7:0] period = p2 ? 8'd2 : 8'd1;
    reg past_valid = 0;
    reg [7:0] cycle = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (cycle != 8'hff) cycle <= cycle + 1;
        assume(cfg[1:0] != 2'd3);                         // legal line code
        assume(!(cfg[2] && cfg[3] && cfg[6:4] == 3'd0));  // legal stuffing
        assume(!p2 || n <= 4);
        assume(!(cfg[1:0] == 2'd2 && cfg[2]));           // Manchester without stuffing
    end
    wire manchester = cfg[1:0] == 2'd2;
    // B: the same configuration, NRZ instead of Manchester, no arbitration
    wire [10:0] cfg_b = {cfg[10:9], 1'b0, cfg[7:2], manchester ? 2'd0 : cfg[1:0]};
    wire clear = !past_valid;
    wire start = cycle == 1;
    wire [PCW-1:0] pc_a, pc_b;
    wire [7:0] fault_a, fault_b, values_a;
    wire [31:0] rx_b;
    wire [15:0] crc_a, crc_b;
    wire serr_b, se0_b;
    reg [31:0] ins_a, ins_b;
    wire [7:0] xfer_c = {1'b0, crc, 1'b1, 1'b0, 1'b0, msb, 2'b00};  // + drive (A) or sample (B)
    always @* begin
        case (pc_a)
            0: ins_a = 32'h06000000;                        // PULL (DATA)
            1: ins_a = {8'd16, 24'd1};                      // PINS clock/pair 1, data 0, in 0
            2: ins_a = {8'd31, 13'd0, cfg};                 // LCFG
            3: ins_a = {8'd30, 16'd0, period};              // LTIM P
            4: ins_a = {8'd17, 3'd0, n, 8'd0, xfer_c | 8'h08};
            5: ins_a = {8'd4, 24'd12};                      // WAIT (hold the last cell)
            default: ins_a = 32'h01000000;                  // HALT
        endcase
        case (pc_b)
            0: ins_b = 32'h00000000;                        // NOP
            1: ins_b = {8'd16, 24'd1};                      // PINS
            2: ins_b = {8'd31, 13'd0, cfg_b};               // LCFG
            3: ins_b = {8'd30, 16'd0, period};              // LTIM P
            4: ins_b = 32'h00000000;                        // NOP
            5: ins_b = {8'd17, 3'd0, n, 8'd0, xfer_c | 8'h10};
            default: ins_b = 32'h01000000;                  // HALT
        endcase
    end
    protocol_engine_line a(.clk(clk), .clear(clear), .start(start), .stop(1'b0),
        .clear_fault(1'b0), .instruction(ins_a), .image_length(24'd7), .ownership(8'hff),
        .pins(8'h00), .timestamp(32'd0), .tx_valid(1'b1), .tx_data(data), .rx_ready(1'b1),
        .event_pending(1'b0), .pc(pc_a), .fault(fault_a), .pin_values(values_a),
        .ls_crc_state(crc_a));
    protocol_engine_line b(.clk(clk), .clear(clear), .start(start), .stop(1'b0),
        .clear_fault(1'b0), .instruction(ins_b), .image_length(24'd7), .ownership(8'h00),
        .pins(values_a), .timestamp(32'd0), .tx_valid(1'b0), .tx_data(32'd0), .rx_ready(1'b1),
        .event_pending(1'b0), .pc(pc_b), .fault(fault_b), .rx_data(rx_b),
        .ls_crc_state(crc_b), .ls_stuff_error(serr_b), .ls_line_se0(se0_b));
    function automatic [31:0] field(input [31:0] rx, input [4:0] bits, input m);
        // the received bits in arrival order: LSB first they fill rx from the top
        field = m ? (rx & ((32'd1 << bits) - 1)) : (rx >> (32 - bits));
    endfunction
    function automatic [31:0] sent(input [31:0] d, input [4:0] bits, input m);
        // the bits A sends: LSB first d[bits-1:0]; MSB first d[31:32-bits] (in arrival order)
        sent = m ? (d >> (32 - bits)) : (d & ((32'd1 << bits) - 1));
    endfunction
    wire [31:0] mask = (32'd1 << n) - 1;
    always @(posedge clk) begin
        if (past_valid && cycle >= 2) begin
            assert(fault_a == 0 && fault_b == 0);
            if (pc_b >= 6) begin
                if (manchester)
                    assert(field(rx_b, n, msb) == (~sent(data, n, msb) & mask));  // target of: line_codec_neg_manchester
                else
                    assert(field(rx_b, n, msb) == sent(data, n, msb));  // target of: line_codec_neg_nrzi line_codec_neg_destuff
                assert(!serr_b && !se0_b);  // target of: line_codec_neg_nrzi line_codec_neg_destuff
                if (crc && !manchester) assert(crc_b == crc_a);  // target of: line_codec_neg_nrzi line_codec_neg_destuff
            end
        end
        if (cycle == LIMIT) assert(pc_b >= 6);
    end
endmodule

// line_codec_line: the same claim, unbounded (k-induction), for any valid
// ticker (P 1..255 with any fraction Q; the delay D is 0), N 1..32, and every
// legal LCFG except stuffing with Manchester, as for line_codec. Engine A
// runs PULL (DATA), PINS, LCFG C, LTIM (P, Q), XFER N line|drive, then JMP to
// itself (so it keeps its pins and its ticker runs on); engine B runs NOP,
// PINS, LCFG C_B (C with NRZ for Manchester and the arbitration bit cleared),
// LTIM (P, Q), NOP, XFER N line|sample, HALT. Both have the CRC feed or not
// (a free constant) and preset 0.
//
// Claims (the same as line_codec's; target of: line_codec_prove_neg_nrzi,
// line_codec_prove_neg_destuff, line_codec_prove_neg_manchester):
//   when B's XFER has completed, B's RX register holds the N data bits in
//   arrival order (their complements for Manchester), B saw no stuff error and
//   no SE0, and with the CRC feed (not Manchester) B's CRC equals A's.
//
// Lemmas (lines marked L) make the claims inductive. The harness counts the
// cells A has driven and B has sampled; they differ by at most one: after A
// drives a cell at a bit boundary, B samples it at the next mid-bit tick.
// With equal counts ("in step", phase 0 or no XFER armed) A's and B's stuffing
// run, last bit, bits done, trailing stuff flag and CRC are equal and B's
// previous sample is A's line level; with a cell pending (phase 1) A's state
// is one encoder step ahead of B's, as docs/isa.md describes the step
// (stuff bit when the run reached the run length, run and last-bit update,
// NRZ/NRZI/Manchester first-half level, data bit counting). Further lemmas:
// both tickers are equal while B runs (B's LTIM issues in the same cycle as
// A's), the program state, A's tx register holds DATA shifted by the bits
// done, and B's RX register holds the bits done. The tick counters, period
// registers and tx registers are read through ports that the .sby script adds
// with `expose` (observation only). -DNO_LEMMAS (the negative controls and
// the covers) leaves the lemmas out; -DCODEC_BOUNDED=K limits P to 1..2 and N
// to K; -DCODEC_COVER adds non-vacuity covers.
module line_codec_line #(parameter PCW=`LINE_PCW) (input wire clk);
    (* anyconst *) reg [31:0] data;
    (* anyconst *) reg [4:0] n_code;
    (* anyconst *) reg msb, crc;
    (* anyconst *) reg [10:0] cfg;
    (* anyconst *) reg [7:0] tp, tq;
    wire [5:0] n = {1'b0, n_code} + 6'd1;
    always @* begin
        assume(cfg[1:0] != 2'd3);                         // legal line code
        assume(!(cfg[2] && cfg[3] && cfg[6:4] == 3'd0));  // legal stuffing
        assume(!(cfg[1:0] == 2'd2 && cfg[2]));            // Manchester without stuffing
        assume(tp != 0 && !(tp == 8'd255 && tq != 0));    // valid LTIM
`ifdef CODEC_BOUNDED
        assume(tp <= 8'd2 && n <= `CODEC_BOUNDED);
`endif
    end
    wire manchester = cfg[1:0] == 2'd2, nrzi = cfg[1:0] == 2'd1;
    wire pair = cfg[7], stuff_en = cfg[2], stuff_any = cfg[3], ilevel = cfg[10];
    wire [3:0] run_n = {1'b0, cfg[6:4]} + 4'd1;
    wire [10:0] cfg_b = {cfg[10:9], 1'b0, cfg[7:2], manchester ? 2'd0 : cfg[1:0]};
    reg past_valid = 0;
    reg [7:0] cycle = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (cycle != 8'hff) cycle <= cycle + 1;
    end
    wire clear = !past_valid;
    wire start = cycle == 1;
    always @* assert(past_valid == (cycle != 0));  // harness: reset only in cycle 0
    wire [PCW-1:0] pc_a, pc_b;
    wire run_a, run_b, issue_a, issue_b;
    wire [7:0] fault_a, fault_b, values_a;
    wire [31:0] rx_b;
    wire [15:0] crc_a, crc_b;
    wire [6:0] e_a, e_b, mode_a, mode_b;
    wire [23:0] wt_a, wt_b;
    wire [8:0] pins_a, pins_b;
    wire [9:0] lcfg_a, lcfg_b;
    wire lrun_a, lrun_b, ph_a, ph_b, seen_a, lev_a, rxp_b, cell_a, man_a, trail_a, trail_b;
    wire slast_a, slast_b, serr_b, se0_b, lost_a, lost_b, man_b;
    wire [7:0] frac_a, frac_b, acc_a, acc_b;
    wire [3:0] srun_a, srun_b;
    wire [1:0] preset_a, preset_b;
`ifndef NO_LEMMAS
    wire [7:0] tick_a, tick_b, per_a, per_b;
    wire [31:0] tx_a;
`endif
    reg [31:0] ins_a, ins_b;
    wire [7:0] xfer_c = {1'b0, crc, 1'b1, 1'b0, 1'b0, msb, 2'b00};  // + drive (A) or sample (B)
    always @* begin
        case (pc_a)
            0: ins_a = 32'h06000000;                        // PULL (DATA)
            1: ins_a = {8'd16, 24'd1};                      // PINS clock/pair 1, data 0, in 0
            2: ins_a = {8'd31, 13'd0, cfg};                 // LCFG
            3: ins_a = {8'd30, 8'd0, tq, tp};               // LTIM P, Q
            4: ins_a = {8'd17, 2'd0, n, 8'd0, xfer_c | 8'h08};
            default: ins_a = {8'd5, 24'd5};                 // JMP 5
        endcase
        case (pc_b)
            0: ins_b = 32'h00000000;                        // NOP
            1: ins_b = {8'd16, 24'd1};                      // PINS
            2: ins_b = {8'd31, 13'd0, cfg_b};               // LCFG
            3: ins_b = {8'd30, 8'd0, tq, tp};               // LTIM P, Q
            4: ins_b = 32'h00000000;                        // NOP
            5: ins_b = {8'd17, 2'd0, n, 8'd0, xfer_c | 8'h10};
            default: ins_b = 32'h01000000;                  // HALT
        endcase
    end
    protocol_engine_line a(.clk(clk), .clear(clear), .start(start), .stop(1'b0),
        .clear_fault(1'b0), .instruction(ins_a), .image_length(24'd6), .ownership(8'hff),
        .pins(8'h00), .timestamp(32'd0), .tx_valid(1'b1), .tx_data(data), .rx_ready(1'b1),
        .event_pending(1'b0), .pc(pc_a), .running(run_a), .fault(fault_a), .pin_values(values_a),
        .issue(issue_a), .wait_timer(wt_a), .transfer_edges(e_a), .transfer_mode(mode_a),
        .transfer_pins(pins_a), .ls_line_cfg(lcfg_a), .ls_line_run(lrun_a), .ls_line_phase(ph_a),
        .ls_line_frac(frac_a), .ls_line_acc(acc_a), .ls_line_boundary_seen(seen_a),
        .ls_line_level(lev_a), .ls_line_cell_bit(cell_a), .ls_line_man_pending(man_a),
        .ls_line_trailing_stuff(trail_a), .ls_stuff_run(srun_a), .ls_stuff_last(slast_a),
        .ls_arbitration_lost(lost_a), .ls_crc_preset(preset_a),
`ifndef NO_LEMMAS
        .transfer_tick(tick_a), .transfer_period(per_a), .tx(tx_a),
`endif
        .ls_crc_state(crc_a));
    protocol_engine_line b(.clk(clk), .clear(clear), .start(start), .stop(1'b0),
        .clear_fault(1'b0), .instruction(ins_b), .image_length(24'd7), .ownership(8'h00),
        .pins(values_a), .timestamp(32'd0), .tx_valid(1'b0), .tx_data(32'd0), .rx_ready(1'b1),
        .event_pending(1'b0), .pc(pc_b), .running(run_b), .fault(fault_b), .rx_data(rx_b),
        .issue(issue_b), .wait_timer(wt_b), .transfer_edges(e_b), .transfer_mode(mode_b),
        .transfer_pins(pins_b), .ls_line_cfg(lcfg_b), .ls_line_run(lrun_b), .ls_line_phase(ph_b),
        .ls_line_frac(frac_b), .ls_line_acc(acc_b), .ls_line_rx_prev(rxp_b),
        .ls_line_trailing_stuff(trail_b), .ls_stuff_run(srun_b), .ls_stuff_last(slast_b),
        .ls_stuff_error(serr_b), .ls_line_se0(se0_b), .ls_arbitration_lost(lost_b),
        .ls_line_man_pending(man_b), .ls_crc_preset(preset_b),
`ifndef NO_LEMMAS
        .transfer_tick(tick_b), .transfer_period(per_b),
`endif
        .ls_crc_state(crc_b));
`include "line_ref.vh"
    function automatic [31:0] field(input [31:0] rx, input [5:0] bits, input m);
        // the received bits in arrival order: LSB first they fill rx from the top
        field = m ? (rx & ((32'd1 << bits) - 1)) : (bits == 0 ? 32'd0 : rx >> (32 - bits));
    endfunction
    function automatic [31:0] sent(input [31:0] d, input [5:0] bits, input m);
        // the bits A sends: LSB first d[bits-1:0]; MSB first d[31:32-bits] (in arrival order)
        sent = m ? (bits == 0 ? 32'd0 : d >> (32 - bits)) : (d & ((32'd1 << bits) - 1));
    endfunction
    wire [31:0] mask = (32'd1 << n) - 1;
    // data bits done: 0 before the XFER, N - counter (+1 with a trailing stuff
    // bit pending) during it, N after it
    wire in_a = pc_a == 4 && e_a != 0, in_b = pc_b == 5 && e_b != 0;
    wire [5:0] done_a = in_a ? n - e_a[5:0] + {5'd0, trail_a} : (pc_a >= 5 ? n : 6'd0);
    wire [5:0] done_b = in_b ? n - e_b[5:0] + {5'd0, trail_b} : (pc_b >= 6 ? n : 6'd0);
    // cells driven by A minus cells sampled by B (harness count)
    reg pend = 0;
`ifndef NO_LEMMAS
    wire drive_a = in_a && lrun_a && tick_a == 8'd1 && !ph_a && run_a;
    wire sample_b = in_b && lrun_b && tick_b == 8'd1 && ph_b && run_b;
    always @(posedge clk)
        if (start) pend <= 0;
        else pend <= pend + drive_a - sample_b;
    // A's state after its next boundary, from B's state (docs/isa.md, "Line
    // XFER" and "Stuffing"): the cell is a stuff bit when the run reached the
    // run length; otherwise it carries the next data bit
    wire st = stuff_en && srun_b == run_n;
    wire sv = stuff_any && !slast_b;
    wire d = msb ? data[31 - done_b] : data[done_b];
    wire lb = st ? sv : d;
    wire [3:0] run_after = stuff_any ? (d == slast_b ? srun_b + 4'd1 : 4'd1) : (d ? srun_b + 4'd1 : 4'd0);
    wire [3:0] x_srun = st ? {3'd0, stuff_any} : run_after;
    wire x_slast = st ? sv : d;
    wire x_level = manchester ? !lb : nrzi ? (lb ? rxp_b : !rxp_b) : lb;
    wire [5:0] x_done = st ? done_b : done_b + 6'd1;
    wire x_trail = !st && x_done == n && stuff_en && run_after == run_n;
    wire x_in = !(x_done == n && !x_trail);
    wire [15:0] x_crc = st ? crc_b : crc_ref_step(crc_b, d, 2'd0, msb);
`endif
    always @(posedge clk) begin
        if (past_valid && cycle >= 2) begin
            assert(fault_a == 0 && fault_b == 0);
            if (pc_b >= 6) begin
                if (manchester)
                    assert(field(rx_b, n, msb) == (~sent(data, n, msb) & mask));  // target of: line_codec_prove_neg_manchester
                else
                    assert(field(rx_b, n, msb) == sent(data, n, msb));  // target of: line_codec_prove_neg_nrzi line_codec_prove_neg_destuff
                assert(!serr_b && !se0_b);  // target of: line_codec_prove_neg_nrzi line_codec_prove_neg_destuff
                if (crc && !manchester) assert(crc_b == crc_a);  // target of: line_codec_prove_neg_nrzi line_codec_prove_neg_destuff
            end
`ifndef NO_LEMMAS
            // program
            assert(pc_a <= 5 && pc_b <= 7 && run_a && run_b == (pc_b <= 6));                   // L
            assert(wt_a == 0 && wt_b == 0 && !lost_a && !lost_b && preset_a == 0 && preset_b == 0);  // L
            assert(pc_a < 4 ? pc_b == pc_a : pc_a == 4 ? pc_b == (e_a == 0 ? 4 : 5) : pc_b >= 5);  // L
            if (pc_a >= 2) assert(pins_a == 9'd1);                                             // L
            if (pc_b >= 2) assert(pins_b == 9'd1);                                             // L
            if (pc_a >= 3) assert(lcfg_a == cfg[9:0]);                                         // L
            if (pc_b >= 3) assert(lcfg_b == cfg_b[9:0]);                                       // L
            if (pc_a != 4) assert(e_a == 0);                                                   // L
            if (pc_b != 5) assert(e_b == 0);                                                   // L
            assert((!trail_a || in_a) && (!trail_b || in_b));                                  // L
            if (in_a) assert(mode_a == (xfer_c[6:0] | 7'h08) && e_a <= {1'b0, n}
                             && (!trail_a || (e_a == 1 && stuff_en && srun_a == run_n)));      // L
            if (in_b) assert(mode_b == (xfer_c[6:0] | 7'h10) && e_b <= {1'b0, n}
                             && (!trail_b || (e_b == 1 && stuff_en && srun_b == run_n)));      // L
            if (pc_a >= 1) assert(tx_a == (msb ? data << done_a : data >> done_a));            // L
            // tickers: equal while B runs; running after LTIM with P, Q
            if (run_b) assert(tick_a == tick_b && per_a == per_b && ph_a == ph_b
                              && acc_a == acc_b && frac_a == frac_b && lrun_a == lrun_b);      // L
            if (pc_a >= 4) assert(lrun_a && tick_a != 0 && per_a == tp && frac_a == tq);       // L
            if (pc_a < 4) assert(!lrun_a);                                                     // L
            // cells: A is at most one cell ahead of B, and only between a
            // boundary and the next mid-bit tick, while B's XFER is armed
            if (pend) assert(ph_a && in_b && seen_a);                                          // L
            if (!pend && ph_a) assert(!in_b);                                                  // L
            // the ticker in the cycles in which A's and B's XFERs issue (LTIM
            // issued one and two cycles before, D = 0)
            if (pc_a == 4 && e_a == 0) assert(!ph_a && tick_a == tp && acc_a == 0);           // L
            if (pc_b == 5 && e_b == 0) assert(tp == 1 ? ph_a && tick_a == 1 && acc_a == tq
                                                      : !ph_a && tick_a == tp - 1 && acc_a == 0);  // L
            if (!in_b && pc_b <= 5) assert(!pend && !seen_a && done_a == 0);                   // L
            // A's pins
            if (seen_a) assert(values_a[0] == lev_a && (!pair || values_a[1] == !lev_a));     // L
            else assert(values_a == 0);                                                        // L
            // B's receiver
            assert(!serr_b && !se0_b && !man_b);                                               // L
            if (manchester) assert(field(rx_b, done_b, msb) == (~sent(data, done_b, msb) & ((32'd1 << done_b) - 1)));  // L
            else assert(field(rx_b, done_b, msb) == sent(data, done_b, msb));                  // L
            // in step
            if (!pend && pc_a >= 3) begin
                assert(done_a == done_b && trail_a == trail_b && !man_a);                      // L
                if (!manchester) assert(srun_a == srun_b && slast_a == slast_b && lev_a == rxp_b
                                        && crc_a == crc_b);                                   // L
                else if (seen_a) assert(lev_a == cell_a);                                      // L
            end
            // one cell pending: A is one encoder step ahead of B
            if (pend) begin
                assert(done_a == x_done && in_a == x_in && trail_a == (x_in && x_trail));     // L
                assert(lev_a == x_level && man_a == manchester && cell_a == lb);               // L
                if (!manchester) assert(srun_a == x_srun && slast_a == x_slast && (!crc || crc_a == x_crc)
                                        && (crc || crc_a == crc_b));                          // L
            end
            if (!crc || pc_a < 4 || pc_a == 4 && e_a == 0) assert(crc_a == 0 && crc_b == 0);   // L
`endif
        end
    end
`ifdef CODEC_COVER
    always @(posedge clk) if (past_valid && cycle >= 2 && pc_b >= 6) begin
        cover(tq != 0 && nrzi && stuff_en && stuff_any && n >= 3);   // fraction, NRZI, stuffing (either polarity)
        cover(nrzi && stuff_en && !stuff_any && tp == 8'd2 && n >= 3);  // NRZI, stuffing (ones), P = 2
        cover(manchester && pair && tq != 0 && n >= 2);              // Manchester, pair, fraction
    end
`endif
endmodule
