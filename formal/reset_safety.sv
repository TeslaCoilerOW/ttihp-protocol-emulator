// Public-interface reset/deselection safety over the committed Tiny Tapeout top
// (src/project.v wrapping src/protocol_emulator_core.v) and the exact IHP SRAM
// FUNCTIONAL models. After any clock edge sampled with rst_n=0 or ena=0, every
// protocol pin output enable is released and every output value is masked.
// Inputs and later host transactions are unconstrained; the first edge assumes
// reset. No SRAM contents are initialised or assumed.
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
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(!rst_n);
        if (past_valid && (!$past(rst_n) || !$past(ena))) begin
            assert(uio_oe == 8'b0);
            assert(uio_out == 8'b0);
        end
    end
endmodule
