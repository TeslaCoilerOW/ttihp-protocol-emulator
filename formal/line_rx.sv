// Line unit (docs/isa.md, "Line XFER" and "Stuffing"; docs/extension.md):
// a sampling line XFER decodes its input pins as the ISA text prescribes, for
// any valid ticker, any legal LCFG, any input waveform.
//
// The production engine with the unit (protocol_engine_line) runs LTIM (P,
// Q, D), LCFG C, PINS S, XFER N line|sample (MSB or LSB first, with or without
// the CRC feed), then JMP to itself. All operands are free constants: any
// valid LTIM (P 1..255, any fraction Q and delay D), any legal LCFG without
// Manchester (which a sampling XFER may not use: NRZ or NRZI; stuffing off,
// or on runs of 1s or of either polarity with every legal run length; pair;
// arbitration; SE0 end; initial level), any PINS selection, N 1..32. The
// input pins are free in every cycle.
//
// A reference decoder written from docs/isa.md runs beside the engine. It
// takes the tick cycles from the engine's ticker (the cycles in which its tick
// counter reaches 1, whose schedule line_tick proves against the ISA formula;
// the phase register tells mid-bit from bit boundary). At each mid-bit tick
// while its XFER runs it ends the XFER on SE0 (SE0 end and the pair set, the
// RX pin and the pair pin low), and otherwise reads the RX pin, decodes it
// (NRZ: the pin; NRZI: 1 when it equals the previous sample, which starts as
// LCFG's initial level) and, when the run of equal line bits (either
// polarity) or of 1s has reached the run length, drops the bit as a stuff bit
// (setting the stuff-error flag when it is not the complement of the last bit,
// or 0 for runs of 1s; the run then restarts as after the expected stuff
// bit); otherwise it shifts the bit into rx, MSB or LSB first, without
// clearing rx. After the last data bit it also takes a trailing stuff bit
// when one is due. Claims (target of: line_rx_neg_nrzi line_rx_neg_destuff):
//   R1  rx equals the reference's shift register (the whole register);
//   R2  the stuff-error and SE0 flags (LSTAT bits 2 and 0) equal the
//       reference's;
//   R3  after an end on SE0, LSTAT's bits 13:8 hold the data bits remaining
//       (N minus the data bits received); excluded: the end on SE0 in the
//       cell of a trailing stuff bit, where the engine reports 1 (the
//       reference 0; line_crc_rx_se0_stuff_cover shows the case from reset);
//   R4  the XFER completes (the PC moves on) exactly when the reference ends.
// Lemmas (lines marked L) make the claims inductive: the program state and
// the engine's stuffing run, last bit, previous sample, trailing-stuff flag
// and bit counter equal the reference's. The tick counter and period register
// are read through ports that the .sby script adds with `expose`
// (observation only). -DNO_LEMMAS (the negative controls, bounded) leaves the
// lemmas out; -DRX_BOUNDED=K limits the operands (P <= 2, no fraction or
// delay, N <= K); -DRX_COVER adds non-vacuity covers.
`ifdef PC_SAT
`define LINE_PCW 7
`else
`define LINE_PCW 24
`endif

module line_rx #(parameter PCW=`LINE_PCW) (input wire clk);
    (* anyconst *) reg [4:0] n_code;
    (* anyconst *) reg msb, crc;
    (* anyconst *) reg [10:0] cfg;
    (* anyconst *) reg [7:0] tp, tq, td;
    (* anyconst *) reg [8:0] sel;
    (* anyseq *) reg [7:0] pins_in;
    wire [5:0] n = {1'b0, n_code} + 6'd1;
    wire [1:0] code = cfg[1:0];
    wire nrzi = code == 2'd1;
    wire stuff_en = cfg[2], stuff_any = cfg[3], pair = cfg[7], se0_end = cfg[9], ilevel = cfg[10];
    wire [3:0] run_n = {1'b0, cfg[6:4]} + 4'd1;
    wire [2:0] ck = sel[2:0], in = sel[8:6];
    always @* begin
        assume(code != 2'd3 && code != 2'd2);               // legal LCFG, no Manchester
        assume(!(stuff_en && stuff_any && cfg[6:4] == 3'd0));
        assume(tp != 0 && !(tp == 8'd255 && tq != 0));      // valid LTIM
`ifdef RX_BOUNDED
        assume(tp <= 8'd2 && tq == 0 && td == 0 && n <= `RX_BOUNDED);
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
    wire [31:0] rx;
    wire [6:0] edges, mode;
    wire [23:0] wait_timer;
    wire [8:0] pins_reg;
    wire [9:0] l_cfg;
    wire l_run, phase, rx_prev, trail, s_last, s_err, se0, lost;
    wire [3:0] s_run;
    wire [5:0] rem;
    wire [7:0] e_tick, e_period, l_frac;
    wire [7:0] xfer_c = {1'b0, crc, 1'b1, 1'b1, 1'b0, msb, 2'b00};
    reg [31:0] instruction;
    always @* begin
        case (pc)
            0: instruction = {8'd30, td, tq, tp};           // LTIM P, Q, D
            1: instruction = {8'd31, 13'd0, cfg};           // LCFG
            2: instruction = {8'd16, 15'd0, sel};           // PINS
            3: instruction = {8'd17, 2'd0, n, 8'd0, xfer_c};  // XFER N line|sample
            default: instruction = {8'd5, 24'd4};           // JMP 4
        endcase
    end
    protocol_engine_line dut(.clk(clk), .clear(clear), .start(start), .stop(1'b0),
        .clear_fault(1'b0), .instruction(instruction), .image_length(24'd5),
        .ownership(8'h00), .pins(pins_in), .timestamp(32'd0), .tx_valid(1'b0),
        .tx_data(32'd0), .rx_ready(1'b1), .event_pending(1'b0),
        .pc(pc), .running(running), .fault(fault), .issue(issue), .rx_data(rx),
        .transfer_edges(edges), .transfer_mode(mode), .wait_timer(wait_timer),
        .transfer_pins(pins_reg), .ls_line_cfg(l_cfg), .ls_line_run(l_run),
        .ls_line_phase(phase), .ls_line_rx_prev(rx_prev), .ls_line_trailing_stuff(trail),
        .ls_stuff_run(s_run), .ls_stuff_last(s_last), .ls_stuff_error(s_err),
        .ls_line_se0(se0), .ls_line_remaining(rem), .ls_arbitration_lost(lost),
        .transfer_tick(e_tick), .transfer_period(e_period), .ls_line_frac(l_frac));

    // Ticks: the cycles in which the engine's tick counter reaches 1 while
    // its ticker runs and the engine is active.
    wire active = running && fault == 0 && !start && !clear;
    wire middle = active && l_run && e_tick == 8'd1 && phase;

    // Reference decoder (docs/isa.md). r_state: 0 before the XFER is armed,
    // 1 while it runs, 2 after it ended.
    reg [1:0] r_state = 0;
    reg [5:0] r_k = 0;             // data bits received
    reg [3:0] r_run = 0;
    reg r_last = 0, r_prev = 0, r_trail = 0, r_err = 0, r_se0 = 0, r_se0_trail = 0;
    reg [5:0] r_rem = 0;
    reg [31:0] r_rx = 0;
    wire raw = pins_in[in];
    wire r_bit = nrzi ? raw == r_prev : raw;
    wire r_is_se0 = se0_end && pair && !raw && !pins_in[ck];
    wire r_stuff = stuff_en && r_run == run_n;
    wire [3:0] r_after = stuff_any ? (r_bit == r_last ? r_run + 4'd1 : 4'd1) : (r_bit ? r_run + 4'd1 : 4'd0);
    always @(posedge clk) begin
        if (start) begin
            r_state <= 0; r_k <= 0; r_run <= 0; r_last <= 0; r_prev <= 0; r_trail <= 0;
            r_err <= 0; r_se0 <= 0; r_se0_trail <= 0; r_rem <= 0; r_rx <= 0;
        end else if (issue && pc == 1) begin              // LCFG: line state and flags
            r_run <= 0; r_last <= ilevel; r_prev <= ilevel; r_err <= 0; r_se0 <= 0; r_rem <= 0;
        end else begin
            if (issue && pc == 3) begin                   // the XFER is armed
                r_state <= 1; r_k <= 0; r_trail <= 0;
            end
            if (middle && r_state == 1) begin
                if (r_is_se0) begin                       // end on SE0
                    r_se0 <= 1; r_rem <= n - r_k; r_se0_trail <= r_trail; r_trail <= 0; r_state <= 2;
                end else begin
                    r_prev <= raw;
                    if (r_stuff) begin                    // a stuff bit: dropped
                        if (r_bit != (stuff_any && !r_last)) r_err <= 1;
                        r_run <= stuff_any ? 4'd1 : 4'd0;
                        r_last <= r_bit;
                        if (r_trail) begin r_trail <= 0; r_state <= 2; end
                    end else begin                        // a data bit
                        r_rx <= msb ? {r_rx[30:0], r_bit} : {r_bit, r_rx[31:1]};
                        r_run <= r_after;
                        r_last <= r_bit;
                        r_k <= r_k + 6'd1;
                        if (r_k + 6'd1 == n) begin
                            if (stuff_en && r_after == run_n) r_trail <= 1;
                            else r_state <= 2;
                        end
                    end
                end
            end
        end
    end
    wire in_xfer = pc == 3 && edges != 0;
    always @(posedge clk) begin
        if (past_valid && cycle >= 2) begin
            assert(fault == 0 && pc <= 4);
            assert(rx == r_rx);                                                     // R1, target of: line_rx_neg_nrzi line_rx_neg_destuff
            if (pc >= 2) assert(s_err == r_err && se0 == r_se0);                    // R2, target of: line_rx_neg_nrzi line_rx_neg_destuff
            if (pc >= 2 && !r_se0_trail) assert(rem == r_rem);                      // R3
            assert((pc == 4) == (r_state == 2));                                    // R4, target of: line_rx_neg_nrzi line_rx_neg_destuff
`ifndef NO_LEMMAS
            assert(running && wait_timer == 0 && !lost);                            // L
            if (pc >= 1) assert(l_run && e_tick != 0 && e_period == tp && l_frac == tq);  // L
            if (pc >= 2) assert(l_cfg == cfg[9:0]);                                 // L
            if (pc >= 3) assert(pins_reg == sel);                                   // L
            if (pc != 3) assert(edges == 0);                                        // L
            assert(!trail || in_xfer);                                              // L
            assert(in_xfer == (r_state == 1));                                      // L
            assert(r_state != 3 && (pc == 3 || r_state != 1) && (pc >= 3 || r_state == 0));  // L
            if (r_state == 0) assert(r_k == 0 && !r_trail && !r_se0_trail);         // L
            if (pc >= 2) assert(s_run == r_run && s_last == r_last && rx_prev == r_prev
                                && trail == r_trail);                               // L
            if (in_xfer) assert(mode == xfer_c[6:0] && edges == {1'b0, n - r_k} + {6'd0, r_trail}
                                && r_k <= n && (!r_trail || r_k == n && stuff_en && r_run == run_n));  // L
            if (r_state != 2 || !r_se0) assert(!r_se0 && rem == 0 && !r_se0_trail);  // L
            if (r_state == 2 && !r_se0) assert(r_k == n && !r_trail);               // L
            if (r_se0_trail) assert(r_se0 && r_k == n);                             // L
`endif
        end
    end
`ifdef RX_COVER
    always @(posedge clk) if (past_valid && cycle >= 2 && pc == 4) begin
        cover(nrzi && stuff_en && stuff_any && r_err && n >= 3 && tq != 0);  // NRZI, either polarity, stuff error, fraction
        cover(r_se0 && r_rem >= 2 && td != 0);                               // SE0 end, delay
        cover(stuff_en && !stuff_any && !r_err && n >= 4 && tp == 8'd2);     // runs of 1s, P = 2
    end
`endif
endmodule
