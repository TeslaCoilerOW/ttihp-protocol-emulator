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
