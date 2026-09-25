// Public-interface reset/deselection safety over the committed Tiny Tapeout top
// (src/project.v wrapping src/protocol_emulator_core.v) and the exact IHP SRAM
// FUNCTIONAL models. After any clock edge sampled with rst_n=0 or ena=0, every
// protocol pin output enable is released and every output value is masked.
// Inputs and later host transactions are unconstrained; the first edge assumes
// reset. No SRAM contents are initialised or assumed.
//
// Design variants (formal/run.sh --variant; the core is the variant's) state
// the claim for their reset style (docs/isa.md "Configuration variants"),
// with raw = !(rst_n && ena):
//   RESET_SYNC_REGISTERED     raw passes two flops before it clears: outputs
//                             are released after every edge that follows an
//                             edge sampled with raw two edges earlier. The
//                             first three edges are reset.
//   RESET_ASYNC               as the design of record, and in addition outputs
//                             are released combinationally while raw is high.
//   RESET_ASYNC_SYNC_RELEASE  outputs are released while raw is high and after
//                             every edge within two edges of a raw edge.
module reset_safety(input wire clk);
    (* anyseq *) reg rst_n;
    (* anyseq *) reg ena;
    (* anyseq *) reg [7:0] ui_in;
    (* anyseq *) reg [7:0] uio_in;
    wire [7:0] uo_out, uio_out, uio_oe;
    tt_um_teslacoilerow_protocol_emulator dut(
        .clk(clk), .rst_n(rst_n), .ena(ena), .ui_in(ui_in), .uo_out(uo_out),
        .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe)
    );
    reg past_valid = 0;
`ifdef RESET_SYNC_REGISTERED
    wire raw = !(rst_n && ena);
    reg [1:0] age = 0;
    always @(posedge clk) begin
        if (age != 3) age <= age + 1;
        if (age != 3) assume(!rst_n);
        if (age == 3 && $past(raw, 3)) begin
            assert(uio_oe == 8'b0);
            assert(uio_out == 8'b0);
        end
    end
`elsif RESET_ASYNC_SYNC_RELEASE
    wire raw = !(rst_n && ena);
    reg [1:0] age = 0;
    always @(posedge clk) begin
        if (age != 3) age <= age + 1;
        if (age == 0) assume(!rst_n);
        if (raw) begin
            assert(uio_oe == 8'b0);
            assert(uio_out == 8'b0);
        end
        if ((age >= 1 && $past(raw)) || (age >= 2 && $past(raw, 2)) || (age == 3 && $past(raw, 3))) begin
            assert(uio_oe == 8'b0);
            assert(uio_out == 8'b0);
        end
    end
`else
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(!rst_n);
        if (past_valid && (!$past(rst_n) || !$past(ena))) begin
            assert(uio_oe == 8'b0);
            assert(uio_out == 8'b0);
        end
`ifdef RESET_ASYNC
        if (!rst_n || !ena) begin
            assert(uio_oe == 8'b0);
            assert(uio_out == 8'b0);
        end
`endif
    end
`endif
endmodule
