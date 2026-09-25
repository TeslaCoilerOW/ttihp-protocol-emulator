// Host nibble-port atomicity and read snapshots (docs/isa.md "Host interface").
//
// A pin-level model of the host protocol runs beside the whole processor
// (protocol_processor_fd = the SRAM-refinement processor with observation
// ports; exact IHP FUNCTIONAL SRAM models). It sees only ui_in/uo_out:
//   * a write nibble is accepted on an edge with uo[4] & ui[4],
//   * a read nibble is accepted on an edge with uo[5] & ui[5],
//   * the window is ui[7:6]; a window change (or reset/deselect) discards the
//     partial word on both sides.
// It assembles the eighth-nibble word and checks, against the processor's
// observable side effects, that
//   spec_cmd_*    a command is accepted or rejected only on the eighth accepted
//                 window-0 nibble, and it carries exactly the assembled word;
//   spec_prog_*   an instruction-SRAM write happens only on the eighth accepted
//                 window-1 nibble, into the selected engine, at its load
//                 address, with the assembled word (both 16-bit halves);
//   spec_tx_*     a host TX push happens only on the eighth accepted window-2
//                 nibble, with the assembled word;
//   spec_rx_*     a host RX pop happens only on the eighth accepted window-3
//                 nibble, and the popped RX word is exactly the word read;
//   spec_state_*  no command-controlled state changes without an accepted
//                 command (so abandoned partial words have no side effect);
//   spec_read_*   the eight nibbles of a read form the value the selected
//                 source had on the edge before the word was first presented
//                 (status/timestamp/levels/PC/event/count/held RX/version, or
//                 the RX head), whatever the stalls in between; read data is
//                 stable while not accepted; the RX head is reserved (frozen)
//                 while a window-3 read is in progress;
//   spec_bubble   a window change or reset deasserts write-ready and
//                 read-valid in the same cycle; window 3 never accepts writes.
// link_* assertions tie the model to the host registers (fd_host_*); they are
// induction strengthening only. With -DFD_SPEC_ONLY they are left out, which
// is how the mutant negative controls run.
module host_protocol (input wire clk);
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] ui_in, uio_in;
`include "fd_dut.vh"

    wire clear = !rst_n || !ena;
    wire [1:0] win = ui_in[7:6];
    wire [1:0] sel = dbg_host_selected;

    reg past_valid = 0;
    reg [1:0] m_win = 0;           // window on the last edge (0 after reset)
    reg [2:0] m_wcnt = 0, m_rcnt = 0;
    reg [27:0] m_wbuf = 0, m_rbuf = 0;
    reg m_pres = 0;                // current read word has been presented
    reg [31:0] m_first_val = 0, m_first_mask = 32'hffff_ffff;

    wire changed = win != m_win;
    wire wacc = uo_out[4] && ui_in[4];
    wire racc = uo_out[5] && ui_in[5];
    wire wdone = wacc && m_wcnt == 7;
    wire rdone = racc && m_rcnt == 7;
    wire [31:0] wword = {ui_in[3:0], m_wbuf};
    wire [31:0] rword = {uo_out[3:0], m_rbuf};

    function automatic [27:0] mask28(input [2:0] c);
        mask28 = (28'd1 << (4 * c)) - 1;
    endfunction

    // Expected read data, from observables, for the current window/selection.
    reg [31:0] exp_val, exp_mask;
    always @* begin
        exp_mask = 32'hffff_ffff;
        if (win == 3) exp_val = dbg_rx_head[32*sel+:32];
        else case (fd_host_read_select)
            3'd0: begin  // status; bit 2 (stalled) is not observed here
                exp_val = {16'd0, fv_fault_code[8*sel+:8], 4'd0, fv_fault_code[8*sel+:8] != 0,
                           1'b0, dbg_image_valid[sel], fv_running[sel]};
                exp_mask = 32'hffff_fffb;
            end
            3'd1: exp_val = fv_timestamp;
            3'd2: exp_val = {12'd0, dbg_rx_level[4*sel+:4], 12'd0, dbg_tx_level[4*sel+:4]};
            3'd3: exp_val = {8'd0, fv_pc[24*sel+:24]};
            3'd4: exp_val = {31'd0, dbg_events[sel]};
            3'd5: exp_val = fv_completed_instructions[32*sel+:32];
            3'd6: exp_val = fv_rx[32*sel+:32];
            default: exp_val = 32'd2;
        endcase
    end
    reg [31:0] prev_exp_val = 0, prev_exp_mask = 32'hffff_ffff;

    always @(posedge clk) begin
        past_valid <= 1;
        prev_exp_val <= exp_val;
        prev_exp_mask <= exp_mask;
        m_win <= clear ? 2'd0 : win;
        if (clear || changed) begin
            m_wcnt <= 0; m_rcnt <= 0; m_wbuf <= 0; m_rbuf <= 0; m_pres <= 0;
        end else begin
            if (wacc) begin
                m_wcnt <= m_wcnt + 1;
                m_wbuf[4*m_wcnt+:4] <= ui_in[3:0];
            end
            if (uo_out[5] && !m_pres) begin
                m_pres <= 1;
                m_first_val <= prev_exp_val;
                m_first_mask <= prev_exp_mask;
            end
            if (racc) begin
                m_rcnt <= m_rcnt + 1;
                m_rbuf[4*m_rcnt+:4] <= uo_out[3:0];
            end
            if (rdone) m_pres <= 0;
        end
    end

    // The value used at a completed read: latched at first presentation, or
    // (for a one-cycle word) the value from the edge before.
    wire [31:0] first_val = m_pres ? m_first_val : prev_exp_val;
    wire [31:0] first_mask = m_pres ? m_first_mask : prev_exp_mask;

    always @(posedge clk) begin
        if (!past_valid) assume(!rst_n);
        if (past_valid) begin
            sanity_clear: assert(dbg_clear == clear);
            spec_bubble: assert(!((changed || clear) && (uo_out[4] || uo_out[5])));
            spec_no_write_w3: assert(!(win == 3 && uo_out[4]));
            spec_no_read_w12: assert(!((win == 1 || win == 2) && uo_out[5]));

            // ---- window 0: commands
            if (dbg_command_accepted) begin
                spec_cmd_eighth: assert(wdone && win == 0);
                spec_cmd_word: assert({dbg_command_code, dbg_command_payload} == wword);
            end
            if (!$past(clear) && fd_host_fault && !$past(fd_host_fault))
                spec_cmd_reject: assert($past(wdone && win == 0 && !dbg_command_accepted));
            if (!$past(clear) && $past(wdone && win == 0 && !dbg_command_accepted))
                spec_cmd_reject_sets_fault: assert(fd_host_fault);
            if (!$past(clear) && !fd_host_fault && $past(fd_host_fault))
                spec_fault_clear: assert($past(dbg_command_accepted && dbg_command_code == 7
                                               && dbg_command_payload[23]));
            if (dbg_start != 0) spec_start_cmd: assert(dbg_command_accepted && dbg_command_code == 4);
            if (dbg_stop != 0) spec_stop_cmd: assert(dbg_command_accepted
                                && (dbg_command_code == 5 || dbg_command_code == 1));
            if (dbg_fifo_clear != 0) spec_flush_cmd: assert(dbg_command_accepted && dbg_command_code == 10);

            // ---- window 1: program words (per-engine checks in the generate below)
            if (wdone && win == 1) begin
                assert(fv_sram_lo_a_men[sel] && fv_sram_lo_a_wen[sel]);  // spec_prog_written
            end

            // ---- window 2: TX words
            assert(dbg_host_tx == (wdone && win == 2));  // spec_tx_eighth
            if (dbg_host_tx) assert(dbg_tx_push[sel] && dbg_tx_data[32*sel+:32] == wword);  // spec_tx_word

            // ---- no word-level side effects without an accepted command
            if (!$past(clear) && !$past(dbg_command_accepted)) begin
                assert(dbg_ownership == $past(dbg_ownership)  // spec_state_config
                    && dbg_open_drain == $past(dbg_open_drain)
                    && dbg_image_valid == $past(dbg_image_valid)
                    && dbg_image_length == $past(dbg_image_length)
                    && fv_image_writing == $past(fv_image_writing)
                    && dbg_route_destination == $past(dbg_route_destination)
                    && dbg_trigger_config == $past(dbg_trigger_config)
                    && dbg_host_selected == $past(dbg_host_selected)
                    && fd_host_read_select == $past(fd_host_read_select));
            end

            // ---- reads
            assert(dbg_host_rx == (rdone && win == 3));  // spec_rx_eighth
            if (rdone) assert((rword & first_mask) == (first_val & first_mask));  // spec_read_value
            if (rdone && win == 3)
                assert(dbg_rx_pop[sel] && dbg_rx_head[32*sel+:32] == rword);  // spec_rx_pop
            if (win == 3 && uo_out[5])
                assert(dbg_rx_valid[sel] && dbg_rx_head[32*sel+:32] == first_val);  // spec_rx_reserved
            if ($past(uo_out[5] && !ui_in[5]) && !changed && !clear)
                assert(uo_out[5] && uo_out[3:0] == $past(uo_out[3:0]));  // spec_read_stable

`ifndef FD_SPEC_ONLY
            // ---- induction strengthening: model <-> host registers
            assert(m_win == fd_host_prev_window);  // link_window
            assert(m_wcnt == fd_host_write_index);  // link_wcnt
            assert((m_wbuf & mask28(m_wcnt)) ==  // link_wbuf
                              ((fd_host_write_buffer >> (32 - 4 * m_wcnt)) & mask28(m_wcnt)));
            assert(m_rcnt == fd_host_read_index);  // link_rcnt
            assert((m_rbuf & mask28(m_rcnt)) == (fd_host_snapshot[27:0] & mask28(m_rcnt)));  // link_rbuf
            assert(!m_pres || fd_host_presenting);  // link_pres
            assert(m_rcnt == 0 || m_pres);  // link_rcnt_pres
            assert(!m_pres || (fd_host_snapshot & m_first_mask) == (m_first_val & m_first_mask));  // link_snapshot
            assert(uo_out[5] == (fd_host_presenting && !changed && !clear));  // link_uo5
            if (fd_host_presenting && !m_pres)
                assert((fd_host_snapshot & prev_exp_mask) == (prev_exp_val & prev_exp_mask));  // link_fresh
            if (m_pres && m_win == 3)
                assert(dbg_rx_valid[sel] && dbg_rx_head[32*sel+:32] == m_first_val);  // link_rx_head
            assert(!uo_out[5] || uo_out[3:0] == fd_host_snapshot[4*fd_host_read_index+:4]);  // link_nibble
            assert(prev_exp_mask == 32'hffff_ffff || prev_exp_mask == 32'hffff_fffb);  // link_mask_prev
            assert(m_first_mask == 32'hffff_ffff || m_first_mask == 32'hffff_fffb);  // link_mask_first
            if (m_pres && m_win == 3) assert(m_first_mask == 32'hffff_ffff);  // link_mask_w3
            if (fd_host_presenting && !m_pres && m_win == 3)
                assert(prev_exp_mask == 32'hffff_ffff);  // link_mask_fresh_w3
`endif
        end
    end

    generate for (genvar k = 0; k < 4; k = k + 1) begin : eng
        always @(posedge clk) if (past_valid) begin
            if (fv_sram_lo_a_men[k] && fv_sram_lo_a_wen[k]) begin
                assert(fv_sram_hi_a_men[k] && fv_sram_hi_a_wen[k]);  // spec_prog_both_halves
                assert(wdone && win == 1 && sel == k);  // spec_prog_eighth
                assert(fv_sram_lo_a_addr[6*k+:6] == fv_image_loaded[16*k+:6]  // spec_prog_addr
                                       && fv_sram_hi_a_addr[6*k+:6] == fv_image_loaded[16*k+:6]
                                       && fv_image_loaded[16*k+:16] < 64);
                assert({fd_sram_hi_a_din[16*k+:16], fd_sram_lo_a_din[16*k+:16]} == wword);  // spec_prog_word
            end
            if (fv_sram_hi_a_men[k] && fv_sram_hi_a_wen[k])
                assert(fv_sram_lo_a_men[k] && fv_sram_lo_a_wen[k]);  // spec_prog_both_halves_hi
            if (!$past(clear) && fv_image_loaded[16*k+:16] != $past(fv_image_loaded[16*k+:16]))
                assert(  // spec_state_loaded
                    ($past(dbg_command_accepted && dbg_command_code == 1 && sel == k)
                     && fv_image_loaded[16*k+:16] == 0) ||
                    ($past(wdone && win == 1 && sel == k)
                     && fv_image_loaded[16*k+:16] == $past(fv_image_loaded[16*k+:16]) + 1));
        end
    end endgenerate

    // ---- non-vacuity witnesses (from reset)
    reg abandoned = 0, stalled_read = 0;
    always @(posedge clk) begin
        if (!clear && changed && m_wcnt != 0) abandoned <= 1;
        if (uo_out[5] && !ui_in[5] && m_rcnt != 0) stalled_read <= 1;
        if (clear) begin abandoned <= 0; stalled_read <= 0; end
        if (past_valid) begin
            cover_cmd_after_abandon: cover(dbg_command_accepted && abandoned);
            cover_reject: cover(wdone && win == 0 && !dbg_command_accepted);
            cover_prog_write: cover(fv_sram_lo_a_men[sel] && fv_sram_lo_a_wen[sel]);
            cover_tx_write: cover(dbg_host_tx && abandoned);
            cover_ts_read_stalled: cover(rdone && win == 0 && fd_host_read_select == 1 && stalled_read);
            cover_rx_read: cover(rdone && win == 3 && stalled_read);
        end
    end
`include "fd_invariants.vh"
endmodule
