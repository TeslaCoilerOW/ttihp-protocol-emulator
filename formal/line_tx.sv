// Line unit (docs/isa.md, "Line XFER" and "Stuffing"; docs/extension.md):
// a driving line XFER puts on its pins the cells that the ISA text
// prescribes, for any valid ticker, any legal LCFG and any data.
//
// The production engine with the unit (protocol_engine_line) runs PULL
// (DATA), LTIM (P, Q, D), LCFG C, PINS S, XFER N line|drive (MSB or LSB
// first, with or without the CRC feed), then JMP to itself (the ticker runs
// on). All operands are free constants: any valid LTIM (P 1..255, any
// fraction Q and delay D), any legal LCFG (NRZ, NRZI or Manchester; stuffing
// off, or on runs of 1s or of either polarity with every legal run length;
// pair; arbitration, SE0-end and initial-level bits), any PINS selection
// (distinct data and pair pins with the pair), N 1..32.
//
// A reference encoder written from docs/isa.md runs beside the engine. It
// takes the tick cycles from the engine's ticker (the cycles in which its tick
// counter reaches 1, whose schedule line_tick proves against the ISA formula;
// the phase register tells bit boundary from mid-bit) and, at each
// bit-boundary tick while its XFER runs, makes the next cell: a stuff bit
// when the run of equal line bits (either polarity) or of 1s has reached the
// run length, the complement of the last bit or 0 for runs of 1s; otherwise
// the next data bit in shift order. It encodes the cell (NRZ: the bit; NRZI: keep the level for a 1,
// toggle it for a 0; Manchester: the complement, and the bit itself at the
// next mid-bit tick). After the last data bit it sends a trailing stuff bit
// when one is due, and then ends. Claims (target of: line_tx_neg_stuff
// line_tx_neg_manchester):
//   X1  from the XFER's first cell on, the data pin carries the reference
//       line level, and with the pair the pair pin its complement;
//   X2  the line level that LSTAT reports (bit 5) is the reference level;
//   X3  the XFER completes (the PC moves on) exactly when the reference has
//       sent its last cell.
// Lemmas (lines marked L) make the claims inductive: the program state, the
// engine's stuffing run, last bit, cell bit, Manchester flag and bit counter
// equal the reference's, and tx holds DATA shifted by the data bits sent. The
// tick counter, period and tx registers are read through ports that the .sby
// script adds with `expose` (observation only). -DNO_LEMMAS (the negative
// controls, bounded) leaves the lemmas out; -DTX_BOUNDED=K limits the
// operands (P <= 2, no fraction or delay, N <= K); -DTX_COVER adds
// non-vacuity covers.
`ifdef PC_SAT
`define LINE_PCW 7
`else
`define LINE_PCW 24
`endif

module line_tx #(parameter PCW=`LINE_PCW) (input wire clk);
    (* anyconst *) reg [31:0] data;
    (* anyconst *) reg [4:0] n_code;
    (* anyconst *) reg msb, crc;
    (* anyconst *) reg [10:0] cfg;
    (* anyconst *) reg [7:0] tp, tq, td;
    (* anyconst *) reg [8:0] sel;
    (* anyseq *) reg [7:0] pins_in;
    wire [5:0] n = {1'b0, n_code} + 6'd1;
    wire [1:0] code = cfg[1:0];
    wire stuff_en = cfg[2], stuff_any = cfg[3], pair = cfg[7], ilevel = cfg[10];
    wire [3:0] run_n = {1'b0, cfg[6:4]} + 4'd1;
    wire [2:0] ck = sel[2:0], out = sel[5:3];
    always @* begin
        assume(code != 2'd3);                               // legal LCFG
        assume(!(stuff_en && stuff_any && cfg[6:4] == 3'd0));
        assume(tp != 0 && !(tp == 8'd255 && tq != 0));      // valid LTIM
        assume(!(pair && ck == out));                       // distinct pair pin
`ifdef TX_BOUNDED
        assume(tp <= 8'd2 && tq == 0 && td == 0 && n <= `TX_BOUNDED);
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
    wire [7:0] fault, values;
    wire [6:0] edges, mode;
    wire [23:0] wait_timer;
    wire [8:0] pins_reg;
    wire [9:0] l_cfg;
    wire l_run, phase, level, cell, man, trail, s_last, lost, seen;
    wire [3:0] s_run;
    wire [7:0] e_tick, e_period, l_frac;
`ifndef NO_LEMMAS
    wire [31:0] e_tx;
`endif
    wire [7:0] xfer_c = {1'b0, crc, 1'b1, 1'b0, 1'b1, msb, 2'b00};
    reg [31:0] instruction;
    always @* begin
        case (pc)
            0: instruction = 32'h06000000;                  // PULL (DATA)
            1: instruction = {8'd30, td, tq, tp};           // LTIM P, Q, D
            2: instruction = {8'd31, 13'd0, cfg};           // LCFG
            3: instruction = {8'd16, 15'd0, sel};           // PINS
            4: instruction = {8'd17, 2'd0, n, 8'd0, xfer_c};  // XFER N line|drive
            default: instruction = {8'd5, 24'd5};           // JMP 5
        endcase
    end
    protocol_engine_line dut(.clk(clk), .clear(clear), .start(start), .stop(1'b0),
        .clear_fault(1'b0), .instruction(instruction), .image_length(24'd6),
        .ownership(8'hff), .pins(pins_in), .timestamp(32'd0), .tx_valid(1'b1),
        .tx_data(data), .rx_ready(1'b1), .event_pending(1'b0),
        .pc(pc), .running(running), .fault(fault), .issue(issue), .pin_values(values),
        .transfer_edges(edges), .transfer_mode(mode), .wait_timer(wait_timer),
        .transfer_pins(pins_reg), .ls_line_cfg(l_cfg), .ls_line_run(l_run),
        .ls_line_phase(phase), .ls_line_level(level), .ls_line_cell_bit(cell),
        .ls_line_man_pending(man), .ls_line_trailing_stuff(trail), .ls_stuff_run(s_run),
        .ls_stuff_last(s_last), .ls_arbitration_lost(lost),
        .transfer_tick(e_tick), .transfer_period(e_period), .ls_line_frac(l_frac),
`ifndef NO_LEMMAS
        .tx(e_tx),
`endif
        .ls_line_boundary_seen(seen));

    // Ticks: the cycles in which the engine's tick counter reaches 1 while
    // its ticker runs and the engine is active (line_tick proves that these
    // follow the ISA schedule); the phase says boundary (0) or mid-bit (1).
    wire active = running && fault == 0 && !start && !clear;
    wire tick = active && l_run && e_tick == 8'd1;
    wire boundary = tick && !phase, middle = tick && phase;

    // Reference encoder (docs/isa.md). r_state: 0 before the XFER is armed,
    // 1 while it runs, 2 after its last cell.
    reg [1:0] r_state = 0;
    reg [5:0] r_k = 0;             // data bits sent
    reg [3:0] r_run = 0;
    reg r_last = 0, r_level = 0, r_cell = 0, r_man = 0, r_trail = 0, r_sent = 0;
    wire r_stuff = stuff_en && r_run == run_n;
    wire r_data = msb ? data[31 - r_k[4:0]] : data[r_k[4:0]];
    wire r_bit = r_stuff ? (stuff_any && !r_last) : r_data;
    wire [3:0] r_after = stuff_any ? (r_data == r_last ? r_run + 4'd1 : 4'd1) : (r_data ? r_run + 4'd1 : 4'd0);
    always @(posedge clk) begin
        if (start) begin
            r_state <= 0; r_k <= 0; r_run <= 0; r_last <= 0; r_level <= 0; r_cell <= 0;
            r_man <= 0; r_trail <= 0; r_sent <= 0;
        end else if (issue && pc == 2) begin              // LCFG: line state from C
            r_run <= 0; r_last <= ilevel; r_level <= ilevel; r_cell <= 0; r_man <= 0;
        end else begin
            if (middle && r_man) begin                    // Manchester second half
                r_level <= r_cell;
                r_man <= 0;
            end
            if (issue && pc == 4) begin                   // the XFER is armed
                r_state <= 1; r_k <= 0; r_trail <= 0; r_sent <= 0;
            end
            if (boundary && r_state == 1) begin           // the next cell
                r_cell <= r_bit;
                r_level <= code == 2'd0 ? r_bit : code == 2'd1 ? (r_bit ? r_level : !r_level) : !r_bit;
                r_man <= code == 2'd2;
                r_sent <= 1;
                if (r_stuff) begin
                    r_run <= stuff_any ? 4'd1 : 4'd0;
                    r_last <= stuff_any && !r_last;
                    if (r_trail) begin r_trail <= 0; r_state <= 2; end
                end else begin
                    r_run <= r_after;
                    r_last <= r_data;
                    r_k <= r_k + 6'd1;
                    if (r_k + 6'd1 == n) begin
                        if (stuff_en && r_after == run_n) r_trail <= 1;
                        else r_state <= 2;
                    end
                end
            end
        end
    end
    wire in_xfer = pc == 4 && edges != 0;
    always @(posedge clk) begin
        if (past_valid && cycle >= 2) begin
            assert(fault == 0 && pc <= 5);
            if (r_sent) assert(values[out] == r_level && (!pair || values[ck] == !r_level));  // X1, target of: line_tx_neg_stuff line_tx_neg_manchester
            if (pc >= 3) assert(level == r_level);                                  // X2, target of: line_tx_neg_stuff line_tx_neg_manchester
            assert((pc == 5) == (r_state == 2));                                    // X3, target of: line_tx_neg_stuff line_tx_neg_manchester
`ifndef NO_LEMMAS
            assert(running && wait_timer == 0 && !lost);                            // L
            if (pc >= 2) assert(l_run && e_tick != 0 && e_period == tp && l_frac == tq);  // L
            if (pc >= 3) assert(l_cfg == cfg[9:0]);                                 // L
            if (pc >= 4) assert(pins_reg == sel);                                   // L
            if (pc != 4) assert(edges == 0);                                        // L
            assert(!trail || in_xfer);                                              // L
            assert(in_xfer == (r_state == 1));                                      // L
            assert(r_state != 3 && (pc == 4 || r_state != 1) && (pc >= 4 || r_state == 0));  // L
            if (r_state == 0) assert(!r_sent && r_k == 0 && !r_trail);              // L
            if (!r_sent) assert(r_k == 0 && !r_man && !r_trail);                    // L
            if (r_man) assert(code == 2'd2);                                        // L
            if (pc >= 3) assert(s_run == r_run && s_last == r_last && man == r_man
                                && cell == r_cell && trail == r_trail);             // L
            if (in_xfer) assert(mode == xfer_c[6:0] && edges == {1'b0, n - r_k} + {6'd0, r_trail}
                                && r_k <= n && (!r_trail || r_k == n && stuff_en && r_run == run_n));  // L
            if (pc >= 1) assert(e_tx == (msb ? data << r_k : data >> r_k));         // L
            if (!r_sent) assert(values == 0 && !seen);                              // L
            if (r_sent) assert(seen);                                               // L
            if (r_state == 2) assert(r_k == n && !r_trail);                         // L
`endif
        end
    end
`ifdef TX_COVER
    always @(posedge clk) if (past_valid && cycle >= 2 && pc == 5) begin
        cover(stuff_en && stuff_any && code == 2'd1 && n >= 3 && tq != 0);  // NRZI, either polarity, fraction
        cover(stuff_en && !stuff_any && cfg[6:4] == 3'd0 && pair && n >= 3);              // runs of 1s of length 1, pair
        cover(code == 2'd2 && td != 0 && n >= 2 && pair);                                 // Manchester, delay, pair
    end
`endif
endmodule
