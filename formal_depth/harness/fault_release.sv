// Fault stickiness and output-enable release (docs/isa.md: "Faults stop
// execution and release output enables", "Fault4 remains until explicit
// clear/reset", HALT "stop and release output enables", "Reset/deselection
// releases all output enables"; uo[7] fault and uo[6] IRQ pins).
//
// Whole processor (protocol_processor_fd), reset on the first edge, all later
// inputs free. For every engine k:
//   spec_sticky          a nonzero fault code stays unchanged until an
//                        accepted CLEAR of k while k is halted (then it is
//                        zero) or reset; START is never accepted while k is
//                        faulted;
//   spec_fault_only_running  a fault is raised only by a running engine;
//   spec_fault_stops     a faulted engine is not running;
//   spec_oe_released     while k is faulted or halted (or in reset), no pin
//                        that k owns is driven: its uio_oe and uio_out bits are
//                        zero (owners are disjoint, so nobody else drives them);
//   spec_enables_cleared while k is halted its logical output enables are zero
//                        (HALT, STOP, BEGIN, a fault and reset all clear them),
//                        so a later START cannot re-drive stale enables;
//   spec_halt            an issued HALT (0x01000000) stops k with no fault and
//                        releases its enables on that edge;
//   spec_reset           after a reset/deselect edge every fault is zero, no
//                        engine runs and uio_oe is zero;
//   spec_fault_pin       uo[7] = host fault OR any engine fault;
//   spec_irq_pin         uo[6] = any event mailbox pending OR any RX nonempty.
module fault_release (input wire clk);
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] ui_in, uio_in;
`include "fd_dut.vh"

    wire clear = !rst_n || !ena;
    reg past_valid = 0;
    always @(posedge clk) begin
        past_valid <= 1;
`ifndef FD_FROM_ANY
        if (!past_valid) assume(!rst_n);
`endif
    end

    wire any_fault = fv_fault_code != 0;
    always @(posedge clk) if (past_valid) begin
        spec_fault_pin: assert(uo_out[7] == (fd_host_fault || any_fault));
        spec_irq_pin: assert(uo_out[6] == (dbg_events != 0 || dbg_rx_level != 0));
        if ($past(clear)) spec_reset: assert(fv_fault_code == 0 && fv_running == 0 && uio_oe == 0);
    end

    generate for (genvar k = 0; k < 4; k = k + 1) begin : eng
        wire [7:0] fault = fv_fault_code[8*k+:8];
        wire running = fv_running[k];
        wire [7:0] own = dbg_ownership[8*k+:8];
        wire clear_cmd = dbg_command_accepted && dbg_command_code == 7 && dbg_command_payload[k];
        wire slot = running && fault == 0 && !dbg_start[k] && !dbg_stop[k] && !clear
                    && fv_wait_timer[24*k+:24] == 0 && fv_transfer_edges[7*k+:7] == 0
                    && fv_pc[24*k+:24] < dbg_image_length[16*k+:16];
        always @(posedge clk) if (past_valid) begin
            if (!$past(clear) && $past(fault) != 0)
                assert(fault == $past(fault)  // spec_sticky
                                    || ($past(clear_cmd && !running) && fault == 0));
            if (dbg_start[k]) assert(fault == 0);  // spec_no_start_faulted
            if (!$past(clear) && $past(fault) == 0 && fault != 0)
                assert($past(running));  // spec_fault_only_running
            if (fault != 0) assert(!running);  // spec_fault_stops
            if (fault != 0 || !running || clear)
                assert((uio_oe & own) == 0 && (uio_out & own) == 0);  // spec_oe_released
            if (!running) assert(fv_logical_enable[8*k+:8] == 0);  // spec_enables_cleared
            if ($past(slot && fv_sram_dout[32*k+:32] == 32'h01000000))
                assert(!running && fault == 0 && fv_logical_enable[8*k+:8] == 0);  // spec_halt
            cover($past(fault) != 0 && fault == 0 && !$past(clear));  // cover_fault_then_clear
            cover($past((uio_oe & own) != 0) && fault != 0);  // cover_driving_then_fault
            cover($past(slot && fv_sram_dout[32*k+:32] == 32'h01000000));  // cover_halt
        end
    end endgenerate
`include "fd_invariants.vh"
endmodule
