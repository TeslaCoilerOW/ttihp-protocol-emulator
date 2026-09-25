// Program-load safety (docs/isa.md: BEGIN/COMMIT, "No live instruction memory
// writes are allowed", "PC is bounded by committed image length; falling
// outside the image faults").
//
// Whole processor (protocol_processor_fd), exact IHP FUNCTIONAL SRAM models,
// reset on the first edge, all later inputs free. Ghost state per engine:
//   written  64-bit map of instruction-SRAM addresses written since the
//               engine's last BEGIN (cleared by BEGIN or reset).
// Claims, for every engine k:
//   spec_no_live_write   an instruction-SRAM write to engine k happens only
//                        while k is halted, loading (after BEGIN, before
//                        COMMIT) and its image is not valid;
//   spec_run_committed   while k runs, its image is committed, not loading,
//                        1 <= length <= 64, and every address below the
//                        length was written since the last BEGIN;
//   spec_fetch_pc        while k runs, the word it sees was read from its SRAM
//                        at address pc on the previous edge (both halves, no
//                        write in between), i.e. the SRAM refinement fetches
//                        exactly the PC's word;
//   spec_fetch_written   hence: whenever k could issue, pc < length and the
//                        word at pc was written after the last BEGIN;
//   spec_outside_faults  an issue slot with pc >= length faults with code 2,
//                        stops k, releases its output enables and performs no
//                        queue/event side effect;
//   spec_word_*          (symbolic engine E, address A) the word k=E executes
//                        at pc=A is the last word the host wrote to A after
//                        the last BEGIN (data integrity through the macro).
// link_* are induction strengthening (image_loaded/written bookkeeping).
// spec_word_* depends on the SRAM array contents, which no port observes, so
// it is checked by BMC and IC3/PDR only; -DFD_NO_WORD drops it for
// k-induction.
module program_load (input wire clk);
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] ui_in, uio_in;
    (* anyconst *) reg [1:0] E;
    (* anyconst *) reg [5:0] A;
`include "fd_dut.vh"

    wire clear = !rst_n || !ena;
    wire [1:0] sel = dbg_host_selected;
    reg past_valid = 0;
    reg [31:0] word_a = 0;
    reg word_a_valid = 0;

    function automatic [63:0] below(input [15:0] n);
        below = (n >= 64) ? {64{1'b1}} : ((64'd1 << n) - 1);
    endfunction

    wire begin_cmd = dbg_command_accepted && dbg_command_code == 1;

    generate for (genvar k = 0; k < 4; k = k + 1) begin : eng
        wire wr_lo = fv_sram_lo_a_men[k] && fv_sram_lo_a_wen[k];
        wire wr_hi = fv_sram_hi_a_men[k] && fv_sram_hi_a_wen[k];
        wire rd_lo = fv_sram_lo_a_men[k] && fv_sram_lo_a_ren[k] && !fv_sram_lo_a_wen[k];
        wire rd_hi = fv_sram_hi_a_men[k] && fv_sram_hi_a_ren[k] && !fv_sram_hi_a_wen[k];
        wire [5:0] addr = fv_sram_lo_a_addr[6*k+:6];
        wire running = fv_running[k];
        wire [7:0] fault = fv_fault_code[8*k+:8];
        wire [23:0] pc = fv_pc[24*k+:24];
        wire [15:0] length = dbg_image_length[16*k+:16];
        wire [15:0] loaded = fv_image_loaded[16*k+:16];
        wire valid = dbg_image_valid[k];
        wire writing = fv_image_writing[k];
        // An issue slot: the engine will decode the fetched word on this edge.
        wire slot = running && fault == 0 && !dbg_start[k] && !dbg_stop[k] && !clear
                    && fv_wait_timer[24*k+:24] == 0 && fv_transfer_edges[7*k+:7] == 0;
        reg [63:0] written = 0;

        always @(posedge clk) begin
            if (clear) written <= 0;
            else if (begin_cmd && sel == k) written <= 0;
            else if (wr_lo) written <= written | (64'd1 << addr);
        end

        always @(posedge clk) if (past_valid) begin
            if (wr_lo || wr_hi) begin
                assert(!running && writing && !valid);  // spec_no_live_write
                assert(wr_lo && wr_hi && addr == fv_sram_hi_a_addr[6*k+:6]);  // spec_write_pair
            end
            if (running) begin
                assert(valid && !writing && length != 0 && length <= 64  // spec_run_committed
                                           && (written & below(length)) == below(length));
                assert($past(rd_lo && rd_hi && !wr_lo && !wr_hi  // spec_fetch_pc
                                            && addr == fv_sram_hi_a_addr[6*k+:6])
                                      && $past(addr) == pc[5:0]);
            end
            if (slot && pc < length) assert(written[pc[5:0]]);  // spec_fetch_written
            if ($past(slot && pc >= length)) begin
                assert(fault == 2 && !running && fv_logical_enable[8*k+:8] == 0);  // spec_outside_faults
            end
            if (slot && pc >= length)
                assert(!dbg_tx_pop[k] && !dbg_rx_push[k] && !dbg_event_clear[k]);  // spec_outside_no_effect
`ifndef FD_SPEC_ONLY
            assert(!(valid && writing));  // link_valid_writing
            assert(loaded <= 64);  // link_loaded_cap
            assert(!writing || written == below(loaded));  // link_written_loaded
            assert(!valid || (loaded == length && written == below(loaded)  // link_valid_loaded
                                                 && length != 0 && length <= 64));
            assert(!writing || !running);  // link_idle_writing
`endif
        end
    end endgenerate

    // Symbolic (engine E, address A) data integrity.
    wire e_wr = fv_sram_lo_a_men[E] && fv_sram_lo_a_wen[E];
    always @(posedge clk) begin
        if (clear || (begin_cmd && sel == E)) word_a_valid <= 0;
        else if (e_wr && fv_sram_lo_a_addr[6*E+:6] == A) begin
            word_a_valid <= 1;
            word_a <= {fd_sram_hi_a_din[16*E+:16], fd_sram_lo_a_din[16*E+:16]};
        end
    end
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(!rst_n);
`ifndef FD_NO_WORD
        if (past_valid && fv_running[E] && fv_pc[24*E+:24] == A && A < dbg_image_length[16*E+:16]) begin
            spec_word_written: assert(word_a_valid);
            spec_word_value: assert(fv_sram_dout[32*E+:32] == word_a);
        end
`endif
    end

    // Non-vacuity: an engine runs a committed two-word program and reaches
    // pc 1; a COMMIT of an incomplete image is rejected.
    reg rejected_commit = 0;
    always @(posedge clk) begin
        if (past_valid && !$past(clear) && $past(dbg_command_code == 2) && fd_host_fault
            && !$past(fd_host_fault))
            rejected_commit <= 1;
        if (past_valid) begin
            cover_run_pc1: cover(fv_running[0] && fv_pc[23:0] == 1 && dbg_image_length[15:0] >= 2);
            cover_word_checked: cover(fv_running[E] && fv_pc[24*E+:24] == A && A == 1
                                      && A < dbg_image_length[16*E+:16]);
            cover_outside: cover(fv_fault_code[7:0] == 2);
            cover_rejected_commit: cover(rejected_commit);
        end
    end
`include "fd_invariants.vh"
endmodule
