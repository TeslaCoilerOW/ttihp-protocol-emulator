// Timing certificates: data integrity of one IHP RM_IHPSG13_1P_64x16_c2 macro
// (the vendor FUNCTIONAL model in models/), unbounded.
//
// Every input of the macro is free on every cycle. For a symbolic address A,
// a ghost register holds the last word written to A. Claim: whenever the
// macro performs a read of A (MEN, REN, not WEN), DOUT on the next cycle is
// that last written word; a write with REN also set returns the written word.
// Together with formal_depth's unbounded program-load claims (the
// instruction SRAM is written only between BEGIN and COMMIT, every address
// below the committed length was written since BEGIN, and the engine decodes
// the word read at its PC on the previous edge), this gives the certificates'
// image assumption for a host load, without a bound on the run length.
//
// The claim needs the array contents, which no port observes, so it is proved
// with IC3/PDR on the bit-level model (the memory mapped to flip-flops),
// which finds the invariant memory[A] == last itself.
module sram_lemma (input wire clk);
    (* anyseq *) reg men, wen, ren;
    (* anyseq *) reg [5:0] addr;
    (* anyseq *) reg [15:0] din;
    (* anyconst *) reg [5:0] A;
    wire [15:0] dout;
    RM_IHPSG13_1P_64x16_c2 macro(.A_CLK(clk), .A_DLY(1'b1), .A_MEN(men), .A_WEN(wen),
        .A_REN(ren), .A_ADDR(addr), .A_DIN(din), .A_DOUT(dout));

    reg written = 0;
    reg [15:0] last = 0;
    reg read_a = 0;      // the previous edge read A
    reg [15:0] expect_w = 0;
    reg write_read = 0;  // the previous edge wrote with REN set
    reg [15:0] wdata = 0;
    always @(posedge clk) begin
        read_a <= men && ren && !wen && addr == A;
        write_read <= men && wen && ren;
        wdata <= din;
        if (men && wen && addr == A) begin
            written <= 1;
            last <= din;
        end
    end
    always @(posedge clk) begin
        if (read_a && written) sram_read_last_write: assert(dout == last);
        if (write_read) sram_write_through: assert(dout == wdata);
`ifdef SRAM_NEG
        // Negative control (must fail): a read returns the word written before the last one.
        if (read_a && written) neg_sram_stale: assert(dout != last);
`endif
    end
`ifdef SRAM_COVER
    always @(posedge clk) cover(read_a && written && last == 16'hbeef);
`endif
endmodule
