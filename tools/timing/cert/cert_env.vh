// Timing certificates: the certificate run B (docs/timing-certificates.md).
//
// Included after cert_dut.vh. The includer declares
//   localparam IMG_N, IMG_OWN, IMG_OD   image length, owned pins, open-drain mask
//   function [31:0] img(input [5:0] a)  the image words
//   wire k_pre                          engine K's state at the anchor (step 0)
//
// B shares with any real run A exactly what the timing-isolation miter
// (formal/timing_isolation.sv) requires to be equal: engine K's execution
// registers (k_pre, every other register free), its configuration and SRAM
// output latch, the timestamp, the pin synchronisers and the pad inputs
// uio_in (free on every cycle). Everything else is B's own and is chosen
// quiet: host port idle (ui_in = 0, so no command is ever accepted), no reset,
// the other engines halted, every FIFO empty, no routes, no pending events
// and no pin triggers. Engine K's instruction SRAM holds the image: every
// read of an address below IMG_N returns that image word on the next cycle
// (the macro's registered read), and the latch at step 0 holds the word at
// the anchor PC.
    reg past_valid = 0;
    reg prev_rd_lo = 0, prev_rd_hi = 0;
    reg [5:0] prev_addr_lo = 0, prev_addr_hi = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        prev_rd_lo <= k_rd_lo;
        prev_rd_hi <= k_rd_hi;
        prev_addr_lo <= k_addr_lo;
        prev_addr_hi <= k_addr_hi;
    end
    wire [31:0] img_at_pc = img(k_pc[5:0]);
    wire [31:0] img_at_lo = img(prev_addr_lo);
    wire [31:0] img_at_hi = img(prev_addr_hi);

    always @(posedge clk) begin
        if (!past_valid) begin
            assume(k_pre);
            assume(k_len == IMG_N && k_loaded == IMG_N && k_valid && !k_writing);
            assume(k_own == IMG_OWN && k_od == IMG_OD);
            assume(valid_masks(owners, drains));
            assume(k_dout == img_at_pc);
            assume((running_all & ~(4'd1 << K)) == 4'd0);
            assume(tx_level == 16'd0 && rx_level == 16'd0);
            assume(route_count == 64'd0 && mailbox == 4'd0 && trigger_config == 24'd0);
        end
        if (past_valid && prev_rd_lo && prev_addr_lo < IMG_N)
            assume(k_dout[15:0] == img_at_lo[15:0]);
        if (past_valid && prev_rd_hi && prev_addr_hi < IMG_N)
            assume(k_dout[31:16] == img_at_hi[31:16]);
    end

    // Environment sanity (proved, not assumed): B never controls or reloads
    // engine K and never writes its instruction SRAM.
    always @(posedge clk) begin
        env_quiet: assert(!cmd_accepted && !k_start && !k_stop && !k_clear_fault && !k_reconfigure);
        env_no_sram_write: assert(!k_wr);
    end
