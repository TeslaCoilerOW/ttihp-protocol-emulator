// Round-robin grant bound of the FIFO mover (docs/architecture.md: "Continuously
// eligible sources are served within at most four accepted grants in the
// four-engine machine").
//
// Whole processor (protocol_processor_fd). No reset is assumed: the claims
// hold from every state of the registers (the prove task's base case starts
// from an arbitrary state).
//   spec_work_conserving  if any source is eligible, exactly one is granted;
//   spec_bound            a source that has been eligible and not granted for
//                         three consecutive cycles is granted on the fourth if
//                         it is still eligible: it never waits four cycles, so
//                         at most three grants go to other sources while it is
//                         continuously eligible;
//   link_distance         the induction invariant: each cycle a waiting source
//                         loses to another source, the cursor moves strictly
//                         closer to it, so waits + distance(cursor, k) <= 3.
// cover_worst_case shows the bound is tight (a source granted after exactly
// three other grants).
module rr_bound (input wire clk);
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] ui_in, uio_in;
`include "fd_dut.vh"

    reg past_valid = 0;
    always @(posedge clk) past_valid <= 1;
    always @(posedge clk) if (past_valid) begin
        spec_work_conserving: assert((dbg_dma_eligible != 0) == (dbg_dma_grant != 0));
        spec_single_grant: assert((dbg_dma_grant & (dbg_dma_grant - 1)) == 0);
    end

    generate for (genvar k = 0; k < 4; k = k + 1) begin : src
        // Consecutive cycles in which source k was eligible but not granted,
        // ending with the previous cycle.
        reg [2:0] waits = 0;
        reg [2:0] others = 0;   // grants to other sources during those cycles
        wire [1:0] distance = k - dbg_round_robin;
        always @(posedge clk) begin
            if (dbg_dma_eligible[k] && !dbg_dma_grant[k]) begin
                waits <= waits + 1;
                others <= others + (dbg_dma_grant != 0);
            end else begin
                waits <= 0;
                others <= 0;
            end
        end
        always @(posedge clk) if (past_valid) begin
            assert(waits <= 3 && others <= 3);  // spec_bound
            if (waits == 3 && dbg_dma_eligible[k]) assert(dbg_dma_grant[k]);  // spec_bound_grant
`ifndef FD_SPEC_ONLY
            assert(waits == 0 || {1'b0, waits} + distance <= 3);  // link_distance
            assert(others == waits);  // link_others
`endif
            cover(waits == 3 && dbg_dma_grant[k]);  // cover_worst_case
        end
    end endgenerate
endmodule
