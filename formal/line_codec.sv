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
