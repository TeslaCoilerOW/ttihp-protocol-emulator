// Line unit (docs/extension.md): the CRC against an independent reference.
//
// line_crc_equiv: the combinational CRC step of the unit (line_crc_step,
// generated from Line_unit.crc_next and Line_unit.preset_polynomial, the
// functions Engine.create uses) equals a bitwise reference for every register
// value, input bit, preset and bit order. The reference is written from the
// published generator polynomials and widths (line_ref.vh). A combinational
// identity for all inputs, so the check is unbounded.
//
// line_crc_e2e: the production engine with the line unit (protocol_engine_line)
// runs a fixed program: PULL (INIT), CRC set, CRC preset P, PULL (DATA),
// LTIM 1, LCFG C, PINS, XFER N line|drive|crc (MSB or LSB first), HALT. INIT,
// DATA, P, C (NRZ/NRZI/Manchester, stuffing off or runs of 4..8), N (1..10) and
// the bit order are free constants. When the XFER has completed, the CRC
// register equals the reference over the first N data bits in shift order
// (stuff bits are never fed). A bounded check (every N up to 10).
// The reference step is line_ref.vh.
`ifdef PC_SAT
`define LINE_PCW 7
`else
`define LINE_PCW 24
`endif

module line_crc_equiv (input wire clk);
    (* anyseq *) reg [15:0] crc;
    (* anyseq *) reg b, msb;
    (* anyseq *) reg [1:0] preset;
    wire [15:0] next;
    line_crc_step dut(.crc(crc), .data(b), .preset(preset), .msb(msb), .next(next));
`include "line_ref.vh"
    always @* assert(next == crc_ref_step(crc, b, preset, msb));  // target of: line_crc_equiv_neg
endmodule

module line_crc_e2e #(parameter PCW=`LINE_PCW, LIMIT=40) (input wire clk);
    (* anyconst *) reg [31:0] init_word, data;
    (* anyconst *) reg [1:0] preset;
    (* anyconst *) reg msb;
    (* anyconst *) reg [3:0] n_code;
    (* anyconst *) reg [10:0] cfg;
    wire [4:0] n = {1'b0, n_code} + 5'd1;
    reg past_valid = 0;
    reg [7:0] cycle = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (cycle != 8'hff) cycle <= cycle + 1;
        // at most 10 bits; LCFG: code 0..2, stuffing off or runs of 4..8
        // (either polarity or ones), no pair (PINS names one pin), any
        // arbitration/SE0/initial-level bits
        assume(n <= 10);
        assume(cfg[1:0] != 2'd3 && !cfg[7]);
        assume(!cfg[2] || cfg[6:4] >= 3'd3);
    end
    wire clear = !past_valid;
    wire start = cycle == 1;
    wire [PCW-1:0] pc;
    wire running, stalled, tx_pop, rx_push, consume_event, issue;
    wire [7:0] fault, pin_values, pin_enables;
    wire [31:0] rx_data, completed;
    wire [3:0] signal_events;
    wire [23:0] wait_timer, wait_limit, blocked_cycles;
    wire [15:0] repeat_count;
    wire [6:0] transfer_edges, transfer_mode;
    wire [8:0] transfer_pins;
    wire [15:0] crc_state;
    reg [31:0] instruction;
    always @* begin
        case (pc)
            0: instruction = 32'h06000000;                         // PULL (INIT)
            1: instruction = {8'd32, 8'd0, 8'd0, 8'd1};            // CRC set from tx
            2: instruction = {8'd32, 8'd0, 6'd0, preset, 8'd3};    // CRC preset
            3: instruction = 32'h06000000;                         // PULL (DATA)
            4: instruction = {8'd30, 24'd1};                       // LTIM 1
            5: instruction = {8'd31, 13'd0, cfg};                  // LCFG
            6: instruction = {8'd16, 24'd0};                       // PINS 0
            7: instruction = {8'd17, 3'd0, n, 8'd0, 1'b0, 1'b1, 1'b1, 1'b0, 1'b1, msb, 2'b00};
            default: instruction = 32'h01000000;                   // HALT
        endcase
    end
    protocol_engine_line dut(.clk(clk), .clear(clear), .start(start), .stop(1'b0),
        .clear_fault(1'b0), .instruction(instruction), .image_length(24'd9),
        .ownership(8'hff), .pins(8'h00), .timestamp(32'd0), .tx_valid(1'b1),
        .tx_data(pc == 0 ? init_word : data), .rx_ready(1'b1), .event_pending(1'b0),
        .pc(pc), .running(running), .fault(fault), .stalled(stalled), .tx_pop(tx_pop),
        .rx_push(rx_push), .rx_data(rx_data), .pin_values(pin_values),
        .pin_enables(pin_enables), .signal_events(signal_events),
        .consume_event(consume_event), .completed(completed), .issue(issue),
        .wait_timer(wait_timer), .wait_limit(wait_limit), .blocked_cycles(blocked_cycles),
        .repeat_count(repeat_count), .transfer_edges(transfer_edges),
        .ls_crc_state(crc_state), .transfer_pins(transfer_pins), .transfer_mode(transfer_mode));
`include "line_ref.vh"
    function automatic [15:0] reference(input [15:0] init, input [31:0] d, input [4:0] bits,
                                        input [1:0] p, input m);
        reg [15:0] c;
        integer k;
        begin
            c = init;
            for (k = 0; k < 12; k = k + 1)
                if (k < bits) c = crc_ref_step(c, m ? d[31 - k] : d[k], p, m);
            reference = c;
        end
    endfunction
    always @(posedge clk) begin
        if (past_valid && cycle >= 2) begin
            assert(fault == 0);
            if (pc >= 8) assert(crc_state == reference(init_word[15:0], data, n, preset, msb));  // target of: line_crc_e2e_neg
        end
        // the XFER completes within the bound (the check above is not vacuous)
        if (cycle == LIMIT) assert(pc >= 8);
    end
endmodule

// line_crc_tx, line_crc_rx (module line_crc_line, SAMPLE = 0 or 1): the CRC
// of a line XFER against the reference step, unbounded. The production engine
// runs a fixed program: PULL (INIT), CRC set from tx, CRC preset PRESET, PULL
// (DATA), LTIM (P, Q, D), LCFG C, PINS S, XFER N line|crc (drive only for
// _tx; sample, or drive and sample, for _rx; MSB or LSB first), HALT. All
// operands are free constants: any valid LTIM (P 1..255, any fraction Q and
// delay D), any legal LCFG (line code, stuffing on runs of 1s or of either
// polarity with every legal run length, pair, arbitration, SE0 end, initial
// level; for _rx no Manchester, which a sampling XFER may not use), any PINS
// selection (distinct data and pair pins when driving with the pair), N
// 1..32, any preset, initial value and bit order; for _rx the input pins are
// free in every cycle.
//
// The harness folds the reference step (line_ref.vh) over the data bits in
// order: h_crc starts at INIT, and whenever the number of data bits done
// grows by one it takes one step with the next data bit, for _tx bit h_k of
// DATA in shift order, for _rx the bit that entered rx in that cycle. The
// number of data bits done is 0 before the XFER, N minus the XFER's bit
// counter (plus 1 while a trailing stuff bit is pending) during it, and after
// it N for _tx and N minus the count LSTAT reports as remaining at SE0 for
// _rx. Claims:
//   C1  the number of data bits done grows by at most one per cycle and never
//       shrinks;
//   C2  from the CRC set on, the CRC register equals the fold: every data bit
//       is fed once, in order, with the preset's polynomial in the transfer's
//       bit order, and no stuff bit is fed; after the XFER the fold spans the
//       N data bits for _tx ("a driving XFER feeds the data bits it sends");
//   C3  for _rx, rx shifts exactly in the cycles in which the count grows, so
//       that after the XFER the fold spans the bits in rx, in arrival order
//       ("a sampling XFER feeds the data bits it receives").
// C1 excludes one case for _rx: the end on SE0 in the cell of a trailing
// stuff bit. LSTAT then reports 1 data bit remaining, although all N were
// received and fed to the CRC (docs/isa.md: "data bits remaining at SE0");
// -DSE0_STUFF_COVER shows the case from reset.
// Lemmas (lines marked L) make the claims inductive: the program state, the
// XFER mode, and for _tx that tx holds DATA shifted by the bits done; _tx
// reads the tx register through a port that the .sby script adds with
// `expose` (observation only). -DNO_LEMMAS (bounded tasks) leaves them out.
// -DCRC_CLOSED also checks the CRC after the XFER against the closed form
// over DATA or rx (bounded tasks, as line_crc_e2e); -DCRC_BOUNDED=K limits
// the operands (P <= 2, no fraction or delay, N <= K) and asserts that the
// XFER completes by cycle CRC_LIMIT; -DCRC_COVER adds non-vacuity covers.
module line_crc_line #(parameter PCW=`LINE_PCW, SAMPLE=0) (input wire clk);
    (* anyconst *) reg [31:0] init_word, data;
    (* anyconst *) reg [1:0] preset;
    (* anyconst *) reg msb, drive_too;
    (* anyconst *) reg [4:0] n_code;
    (* anyconst *) reg [10:0] cfg;
    (* anyconst *) reg [7:0] tp, tq, td;
    (* anyconst *) reg [8:0] sel;
    (* anyseq *) reg [7:0] pins_in;
    wire [5:0] n = {1'b0, n_code} + 6'd1;
    wire drive = !SAMPLE || drive_too;
    wire pair = cfg[7];
    always @* begin
        assume(cfg[1:0] != 2'd3);                           // legal LCFG
        assume(!(cfg[2] && cfg[3] && cfg[6:4] == 3'd0));
        assume(!SAMPLE || cfg[1:0] != 2'd2);                // no Manchester when sampling
        assume(tp != 0 && !(tp == 8'd255 && tq != 0));      // valid LTIM, ticker running
        assume(!(drive && pair && sel[2:0] == sel[5:3]));   // distinct pair pin
`ifdef CRC_BOUNDED
        assume(tp <= 8'd2 && tq == 0 && td == 0 && n <= `CRC_BOUNDED);
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
    wire running;
    wire [7:0] fault;
    wire [31:0] rx;
    wire [6:0] edges, mode;
    wire [15:0] crc;
    wire [1:0] crc_preset;
    wire trail, se0, lost, l_run;
    wire [5:0] rem;
    wire [23:0] wait_timer;
    wire [8:0] pins_reg;
    wire [9:0] l_cfg;
    wire [3:0] s_run;
`ifndef NO_LEMMAS
    wire [31:0] e_tx;
`endif
    wire [7:0] xfer_c = {1'b0, 1'b1, 1'b1, SAMPLE != 0, drive, msb, 2'b00};
    reg [31:0] instruction;
    always @* begin
        case (pc)
            0: instruction = 32'h06000000;                         // PULL (INIT)
            1: instruction = {8'd32, 8'd0, 8'd0, 8'd1};            // CRC set from tx
            2: instruction = {8'd32, 8'd0, 6'd0, preset, 8'd3};    // CRC preset
            3: instruction = 32'h06000000;                         // PULL (DATA)
            4: instruction = {8'd30, td, tq, tp};                  // LTIM P, Q, D
            5: instruction = {8'd31, 13'd0, cfg};                  // LCFG
            6: instruction = {8'd16, 15'd0, sel};                  // PINS
            7: instruction = {8'd17, 2'd0, n, 8'd0, xfer_c};       // XFER N line|crc
            default: instruction = 32'h01000000;                   // HALT
        endcase
    end
    protocol_engine_line dut(.clk(clk), .clear(clear), .start(start), .stop(1'b0),
        .clear_fault(1'b0), .instruction(instruction), .image_length(24'd9),
        .ownership(8'hff), .pins(pins_in), .timestamp(32'd0), .tx_valid(1'b1),
        .tx_data(pc == 0 ? init_word : data), .rx_ready(1'b1), .event_pending(1'b0),
        .pc(pc), .running(running), .fault(fault), .rx_data(rx), .transfer_edges(edges),
        .transfer_mode(mode), .ls_crc_state(crc), .ls_crc_preset(crc_preset),
        .ls_line_trailing_stuff(trail), .ls_line_se0(se0), .ls_line_remaining(rem),
        .wait_timer(wait_timer), .transfer_pins(pins_reg), .ls_line_run(l_run), .ls_line_cfg(l_cfg),
        .ls_stuff_run(s_run),
`ifndef NO_LEMMAS
        .tx(e_tx),
`endif
        .ls_arbitration_lost(lost));
`include "line_ref.vh"
    wire [15:0] init = init_word[15:0];
    wire in_xfer = pc == 7 && edges != 0;
    wire [5:0] done_bits = pc < 7 || (pc == 7 && edges == 0) ? 6'd0
                         : in_xfer ? n - edges[5:0] + {5'd0, trail}
                         : SAMPLE ? n - rem : n;
    // the reference fold
    reg [15:0] h_crc = 0;
    reg [5:0] h_k = 0;
    reg [31:0] h_rx = 0;
    wire grows = done_bits == h_k + 6'd1;
    wire next_bit = SAMPLE ? (msb ? rx[0] : rx[31]) : (msb ? data[31 - h_k] : data[h_k]);
    wire [15:0] h_now = grows ? crc_ref_step(h_crc, next_bit, preset, msb) : h_crc;
    always @(posedge clk) begin
        h_crc <= start ? init : h_now;
        h_k <= start ? 6'd0 : done_bits;
        h_rx <= rx;
    end
    // The XFER ended on SE0 in the cell of a trailing stuff bit (C1 excludes it).
    reg se0_on_stuff = 0;
    always @(posedge clk)
        if (start) se0_on_stuff <= 0;
        else if (in_xfer && trail && SAMPLE && cfg[9] && cfg[7]) se0_on_stuff <= 1;
    wire se0_stuff_end = se0 && se0_on_stuff;
    always @(posedge clk) begin
        if (past_valid && cycle >= 2) begin
            assert(fault == 0 && pc <= 9);
            if (!se0_stuff_end) assert(done_bits == h_k || grows);                   // C1
            if (pc >= 2) assert(crc == h_now);  // C2, target of: line_crc_tx_neg line_crc_rx_neg
            if (SAMPLE) begin
                if (!grows) assert(rx == h_rx);                                      // C3
                else if (msb) assert(rx[31:1] == h_rx[30:0]);                        // C3
                else assert(rx[30:0] == h_rx[31:1]);                                 // C3
            end
`ifndef NO_LEMMAS
            assert(running == (pc <= 8));                                            // L
            if (pc >= 3) assert(crc_preset == preset);                               // L
            assert(wait_timer == 0);                                                 // L
            if (pc >= 5) assert(l_run);                                              // L
            if (pc >= 6) assert(l_cfg == cfg[9:0] && (se0 || rem == 0) && (SAMPLE || !lost));  // L
            if (pc >= 7) assert(pins_reg == sel);                                    // L
            if (pc != 7) assert(edges == 0);                                         // L
            if (pc >= 1 && pc <= 3) assert(e_tx == init_word);                       // L
            if (pc >= 4 && pc <= 7 && !in_xfer) assert(e_tx == data);                // L
            if (in_xfer) begin
                assert(mode == xfer_c[6:0] && edges <= {1'b0, n} && !se0);           // L
                assert(!trail || (edges == 1 && cfg[2] && s_run == {1'b0, cfg[6:4]} + 4'd1));  // L
                if (!SAMPLE) assert(e_tx == (msb ? data << done_bits : data >> done_bits));  // L
            end
`endif
        end
`ifdef CRC_BOUNDED
        if (cycle == `CRC_LIMIT) assert(pc >= 8);  // the XFER completes within the bound
`endif
    end
`ifdef CRC_CLOSED
    // closed forms: the reference over the first k data bits in shift order,
    // and over the last k bits shifted into r in arrival order (LSB first they
    // enter at bit 31, MSB first at bit 0)
    function automatic [15:0] ref_sent(input [15:0] c0, input [31:0] d, input [5:0] k,
                                       input [1:0] p, input m);
        reg [15:0] c;
        integer j;
        begin
            c = c0;
            for (j = 0; j < 32; j = j + 1)
                if (j < k) c = crc_ref_step(c, m ? d[31 - j] : d[j], p, m);
            ref_sent = c;
        end
    endfunction
    function automatic [15:0] ref_received(input [15:0] c0, input [31:0] r, input [5:0] k,
                                           input [1:0] p, input m);
        reg [15:0] c;
        integer j;
        begin
            c = c0;
            for (j = 0; j < 32; j = j + 1)
                if (j < k) c = crc_ref_step(c, m ? r[k - 1 - j] : r[32 - k + j], p, m);
            ref_received = c;
        end
    endfunction
`ifdef CRC_BOUNDED
    wire closed_check = cycle == `CRC_LIMIT;   // once, after the XFER (it completes by then)
`else
    wire closed_check = pc >= 8;
`endif
    always @(posedge clk)
        if (past_valid && cycle >= 2 && closed_check && !se0_stuff_end)
            assert(crc == (SAMPLE ? ref_received(init, rx, n - rem, preset, msb)
                                  : ref_sent(init, data, n, preset, msb)));
`endif
`ifdef CRC_COVER
    wire done = past_valid && cycle >= 2 && pc >= 8;
    generate if (SAMPLE) begin : cover_rx
        always @(posedge clk) if (done) begin
            cover(se0 && rem != 0 && cfg[1:0] == 2'd1);                  // ended on SE0, NRZI
            cover(drive_too && pair && cfg[2] && tq != 0 && n >= 3);    // drive and sample, pair, stuffing, fraction
            cover(n >= 20 && msb && cfg[2]);                            // 20 bits or more, stuffing
        end
    end else begin : cover_tx
        always @(posedge clk) if (done) begin
            cover(pair && cfg[2] && cfg[6:4] <= 3'd2 && tp == 8'd2 && n >= 4);  // pair, runs 1-3, P = 2
            cover(tq != 0 && cfg[1:0] == 2'd1 && cfg[2] && cfg[3] && n >= 3);  // fraction, NRZI, either polarity
            cover(n >= 20 && cfg[1:0] == 2'd2);                                // 20 bits or more, Manchester
        end
    end endgenerate
`endif
`ifdef SE0_STUFF_COVER
    // The case C1 excludes, from reset: SE0 in a trailing stuff cell; the
    // CRC holds all N data bits, LSTAT reports 1 remaining.
    always @(posedge clk) if (past_valid && cycle >= 2 && pc >= 8 && SAMPLE)
        cover(se0_stuff_end && rem == 1 && h_k == n && crc == h_now);
`endif
endmodule
