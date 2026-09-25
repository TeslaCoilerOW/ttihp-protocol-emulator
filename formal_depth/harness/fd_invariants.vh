// Common state invariants of protocol_processor_fd, asserted (never assumed)
// by every formal_depth harness as induction strengthening. Each one is proved
// together with the harness's own claims: in a `prove` task the base case
// checks it from reset and the induction step preserves it. Left out with
// -DFD_SPEC_ONLY (mutant runs), because a mutant may legitimately break them.
// Requires `past_valid` and the fd_dut.vh wires. With -DFD_FROM_ANY the same
// conditions are assumed on the first cycle (an arbitrary valid state), also
// under -DFD_SPEC_ONLY, so that a mutant run from an arbitrary state starts
// from a state the real design can reach.
`ifdef FD_FROM_ANY
`define FD_ASM(c) if (!past_valid) assume(c);
`else
`define FD_ASM(c)
`endif
`ifndef FD_SPEC_ONLY
`define FD_AST(c) if (past_valid) assert(c);
`else
`define FD_AST(c)
`endif
`define FD_INV(c) `FD_ASM(c) `FD_AST(c)
generate for (genvar ik = 0; ik < 4; ik = ik + 1) begin : inv
    wire [3:0] inv_rxl = dbg_rx_level[4*ik+:4], inv_txl = dbg_tx_level[4*ik+:4];
    wire inv_running = fv_running[ik];
    wire inv_valid = dbg_image_valid[ik], inv_writing = fv_image_writing[ik];
    wire [15:0] inv_len = dbg_image_length[16*ik+:16], inv_loaded = fv_image_loaded[16*ik+:16];
    always @(posedge clk) begin
        `FD_INV(inv_rxl <= 8 && inv_txl <= 8)  // link_common_levels
        `FD_INV(((fd_rx_wr[3*ik+:3] - fd_rx_rd[3*ik+:3]) & 3'd7) == inv_rxl[2:0])  // link_common_rx_ptr
        `FD_INV(((fd_tx_wr[3*ik+:3] - fd_tx_rd[3*ik+:3]) & 3'd7) == inv_txl[2:0])  // link_common_tx_ptr
        `FD_INV(fv_fault_code[8*ik+:8] == 0 || !inv_running)  // link_common_fault_stops
        `FD_INV(inv_running || fv_logical_enable[8*ik+:8] == 0)  // link_common_idle_enables
        `FD_INV(inv_running || fv_transfer_edges[7*ik+:7] == 0)  // link_common_idle_xfer
        `FD_INV(!inv_running || (inv_valid && !inv_writing))  // link_common_run_valid
        `FD_INV(!(inv_valid && inv_writing) && inv_loaded <= 64)  // link_common_image_flags
        `FD_INV(!inv_valid || (inv_loaded == inv_len && inv_len != 0 && inv_len <= 64))  // link_common_image_len
        `FD_INV((dbg_open_drain[8*ik+:8] & ~dbg_ownership[8*ik+:8]) == 0)  // link_common_od_owned
    end
end endgenerate
wire inv_disjoint =
    (dbg_ownership[7:0] & dbg_ownership[15:8]) == 0 && (dbg_ownership[7:0] & dbg_ownership[23:16]) == 0
    && (dbg_ownership[7:0] & dbg_ownership[31:24]) == 0 && (dbg_ownership[15:8] & dbg_ownership[23:16]) == 0
    && (dbg_ownership[15:8] & dbg_ownership[31:24]) == 0 && (dbg_ownership[23:16] & dbg_ownership[31:24]) == 0;
always @(posedge clk) begin
    `FD_INV(inv_disjoint)  // link_common_owners_disjoint
end
