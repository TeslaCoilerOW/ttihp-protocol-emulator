// Timing certificates: image-independent one-step lemmas for engine K.
//
// The segment certificates (gen_cert.py) cover each segment from its anchor
// up to the cycle in which the next boundary instruction is first attempted.
// These lemmas cover what lies between two segments, in *any* environment:
// every chip input is free (host port, pad inputs), and every register outside
// engine K is arbitrary, except that engine K is not stopped, cleared or
// reconfigured on the transition (the premise of the timing claim), there is
// no reset on it, ownership is disjoint and open-drain masks lie inside
// ownership, and a running engine K has a committed image that is not being
// loaded (formal_depth/harness/program_load.sv, spec_run_committed).
//
// Each lemma is a Hoare triple over one clock edge: step 0 is an arbitrary
// state satisfying the assumptions, step 1 its successor. BMC from an
// unconstrained initial state therefore proves it for every state, with no
// bound on how often it is applied (e.g. on every cycle of an unbounded stall).
// Yosys 0.67 samples clocked checks at the clock edge, so the check in sby
// step k sees the values of step k-1: a BMC of depth D checks steps 0..D-2.
// The proofs use depth 4 (steps 0..2, for lemma_sync) and the covers depth 3.
// The one-step lemmas are then also checked from every successor state,
// which adds nothing but costs little.
//
//   lemma_start       an accepted START gives exactly the START state
//                     (pe_timing START_STATE; the START certificate assumes it)
//   lemma_*_done      a boundary instruction that completes advances the PC,
//                     clears the blocked count and changes nothing else in K
//                     except PULL's tx (the successor certificate assumes tx free)
//   lemma_*_hold      one that does not complete changes nothing except the
//                     blocked count of WAITPIN/WAITEVENT
//   lemma_*_timeout   WAITPIN/WAITEVENT fault 3 on the LIMIT-th unsuccessful
//                     attempt; strict PUSH faults 4 on a full RX FIFO
//   lemma_*_pads      K's owned pads do not change in a done/hold step and are
//                     all released in a fault step
//   lemma_*_when      the completion happens exactly on the condition
//                     (TX word present, RX space, synchronized pin level, own
//                     event mailbox)
//   lemma_fetch       while K runs, its SRAM latch holds the word at the PC
//                     (read on the previous edge at the new PC)
//   lemma_stopped     a halted or faulted engine K stays stopped with its pads
//                     released until the host starts it again
//   lemma_sync        the engines see each pad input exactly two edges after
//                     it was present (checked on step 2)
module boundary_lemmas #(parameter K = 0) (input wire clk);
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] ui_in, uio_in;
`include "cert_dut.vh"

    reg past_valid = 0, past2 = 0;
    reg [7:0] u1 = 0, u2 = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        past2 <= past_valid;
        u1 <= uio_in;
        u2 <= u1;
    end

    // Owned-pad view of K: pad states of owned pins, zero elsewhere.
    function automatic [23:0] owned3(input [7:0] own);
        integer p;
        begin
            owned3 = 0;
            for (p = 0; p < 8; p = p + 1)
                if (own[p]) owned3[3*p+:3] = 3'b111;
        end
    endfunction
    wire [23:0] k_opads = k_pads & owned3(k_own);
    wire [23:0] released = 24'o44444444 & owned3(k_own);

    // Step-0 copy of engine K and of the inputs it reads.
    reg [23:0] p_pc, p_timer, p_limit, p_blocked;
    reg [31:0] p_tx, p_rx, p_x, p_y, p_completed, p_dout;
    reg [15:0] p_repeat, p_len;
    reg [7:0] p_fault, p_values, p_enables, p_xtick, p_xperiod, p_synced, p_own;
    reg [8:0] p_xpins;
    reg [6:0] p_xrem;
    reg [4:0] p_xmode;
    reg [3:0] p_tx_level, p_rx_level;
    reg [23:0] p_opads;
    reg p_running, p_slot, p_start, p_mailbox, p_tx_pop, p_rx_push, p_event_clear;
    reg p_rd_lo, p_rd_hi;
    reg [5:0] p_addr_lo, p_addr_hi;
    always @(posedge clk) begin
        p_pc <= k_pc; p_timer <= k_timer; p_limit <= k_limit; p_blocked <= k_blocked;
        p_tx <= k_tx; p_rx <= k_rx; p_x <= k_x; p_y <= k_y; p_completed <= k_completed;
        p_dout <= k_dout; p_repeat <= k_repeat; p_len <= k_len; p_fault <= k_fault;
        p_values <= k_values; p_enables <= k_enables; p_xtick <= k_xtick;
        p_xperiod <= k_xperiod; p_synced <= synced; p_own <= k_own; p_xpins <= k_xpins;
        p_xrem <= k_xrem; p_xmode <= k_xmode; p_tx_level <= k_tx_level;
        p_rx_level <= k_rx_level; p_opads <= k_opads; p_running <= k_running;
        p_slot <= k_slot; p_start <= k_start; p_mailbox <= mailbox[K];
        p_tx_pop <= k_tx_pop; p_rx_push <= k_rx_push; p_event_clear <= k_event_clear;
        p_rd_lo <= k_rd_lo; p_rd_hi <= k_rd_hi; p_addr_lo <= k_addr_lo; p_addr_hi <= k_addr_hi;
    end

    // ---------------- assumptions (step 0 and the transition) ----------------
    always @(posedge clk) begin
        assume(rst_n && ena);
        assume(!k_stop && !k_clear_fault && !k_reconfigure);
        if (!past_valid) begin
            assume(valid_masks(owners, drains));
            assume(!(k_valid && k_writing));
            if (k_running) assume(k_valid && k_len >= 1 && k_len <= 64);
            // FIFO levels never exceed the 8-word depth (processor_inductive.sv).
            assume(tx_level[3:0] <= 8 && tx_level[7:4] <= 8 && tx_level[11:8] <= 8
                   && tx_level[15:12] <= 8);
            assume(rx_level[3:0] <= 8 && rx_level[7:4] <= 8 && rx_level[11:8] <= 8
                   && rx_level[15:12] <= 8);
        end
    end

    // ---------------- one-step claims ----------------
    wire [31:0] w = p_dout;                       // the instruction attempted at step 0
    wire issue = past_valid && p_slot && !p_start && p_pc < {8'd0, p_len};
    wire is_pull = w == 32'h06000000;
    wire is_push0 = w == 32'h07000000;
    wire is_push1 = w == 32'h07010000;
    wire is_waitpin = w[31:24] == 8'd13 && w[23:16] < 8 && w[15:8] < 2 && w[7:0] == 0;
    wire is_waitevent = w == 32'h0f000000;
    wire pin_ok = p_synced[w[18:16]] == w[8];
    wire [23:0] limit_eff = p_limit == 0 ? 24'd65535 : p_limit;
    wire timeout = p_blocked + 24'd1 >= limit_eff;
    // Everything in engine K except pc, tx, blocked and completed is unchanged.
    wire frame = k_running && k_fault == 0 && k_rx == p_rx && k_x == p_x && k_y == p_y
        && k_repeat == p_repeat && k_timer == p_timer && k_limit == p_limit
        && k_values == p_values && k_enables == p_enables && k_xpins == p_xpins
        && k_xrem == p_xrem && k_xtick == p_xtick && k_xperiod == p_xperiod && k_xmode == p_xmode;
    wire done = frame && k_pc == p_pc + 24'd1 && k_blocked == 0 && k_completed == p_completed + 32'd1;
    wire hold = frame && k_pc == p_pc && k_tx == p_tx && k_completed == p_completed;
    wire faulted = !k_running && k_enables == 0 && k_xrem == 0 && k_pc == p_pc && k_tx == p_tx;

    always @(posedge clk) if (past_valid) begin
`ifndef LEMMA_START_ONLY
        if (issue && is_pull) begin
            lemma_pull_when: assert(p_tx_pop == (p_tx_level != 0));
            if (p_tx_pop) lemma_pull_done: assert(done);
            else lemma_pull_hold: assert(hold && k_blocked == p_blocked);
            lemma_pull_pads: assert(k_opads == p_opads);
        end
        if (issue && is_push0) begin
            lemma_push0_when: assert(p_rx_push == (p_rx_level < 4'd8));
            if (p_rx_push) lemma_push0_done: assert(done && k_tx == p_tx);
            else lemma_push0_hold: assert(hold && k_blocked == p_blocked);
            lemma_push0_pads: assert(k_opads == p_opads);
        end
        if (issue && is_push1) begin
            lemma_push1_when: assert(p_rx_push == (p_rx_level < 4'd8));
            if (p_rx_push) begin
                lemma_push1_done: assert(done && k_tx == p_tx);
                lemma_push1_pads: assert(k_opads == p_opads);
            end else begin
                lemma_push1_timeout: assert(faulted && k_fault == 8'd4);
                lemma_push1_release: assert(k_opads == released);
            end
        end
        if (issue && is_waitpin) begin
            if (pin_ok) begin
                lemma_waitpin_done: assert(done && k_tx == p_tx);
                lemma_waitpin_pads: assert(k_opads == p_opads);
            end else if (!timeout) begin
                lemma_waitpin_hold: assert(hold && k_blocked == p_blocked + 24'd1);
                lemma_waitpin_hold_pads: assert(k_opads == p_opads);
            end else begin
                lemma_waitpin_timeout: assert(faulted && k_fault == 8'd3);
                lemma_waitpin_release: assert(k_opads == released);
            end
        end
        if (issue && is_waitevent) begin
            lemma_waitevent_when: assert(p_event_clear == p_mailbox);
            if (p_mailbox) begin
                lemma_waitevent_done: assert(done && k_tx == p_tx);
                lemma_waitevent_pads: assert(k_opads == p_opads);
            end else if (!timeout) begin
                lemma_waitevent_hold: assert(hold && k_blocked == p_blocked + 24'd1);
                lemma_waitevent_hold_pads: assert(k_opads == p_opads);
            end else begin
                lemma_waitevent_timeout: assert(faulted && k_fault == 8'd3);
                lemma_waitevent_release: assert(k_opads == released);
            end
        end
        if ((!p_running || p_fault != 0) && !p_start) begin
            lemma_stopped: assert(!k_running || k_fault != 0);
            lemma_stopped_pads: assert(k_opads == released);
        end
`endif
        if (p_start) begin
            lemma_start: assert(k_pc == 0 && k_running && k_fault == 0 && k_tx == 0 && k_rx == 0
                && k_x == 0 && k_y == 0 && k_repeat == 0 && k_timer == 0 && k_limit == 24'd65535
                && k_blocked == 0 && k_values == 0 && k_enables == 0 && k_xpins == 0 && k_xrem == 0);
            lemma_start_pads: assert(k_opads == released);
        end
        if (k_running)
            lemma_fetch: assert(p_rd_lo && p_rd_hi && p_addr_lo == k_pc[5:0] && p_addr_hi == k_pc[5:0]);
        // Two synchronizer flops: the engine sees the pad input of two cycles earlier.
        if (past2) lemma_sync: assert(synced == u2);
    end

`ifdef LEMMA_NEG
    // Negative control (must fail): claim that a WAITPIN timeout keeps the pads.
    always @(posedge clk) if (past_valid && issue && is_waitpin && !pin_ok && timeout)
        neg_waitpin_timeout_keeps_pads: assert(k_opads == p_opads);
`endif

`ifdef LEMMA_COVER
    // Non-vacuity: every lemma branch is reachable in one step.
    always @(posedge clk) if (past_valid) begin
        cover(issue && is_pull && p_tx_pop);
        cover(issue && is_pull && !p_tx_pop);
        cover(issue && is_push1 && !p_rx_push);
        cover(issue && is_waitpin && pin_ok);
        cover(issue && is_waitpin && !pin_ok && timeout);
        cover(issue && is_waitevent && p_mailbox);
        cover(issue && is_waitevent && !p_mailbox && !timeout);
        cover(p_start);
    end
`endif
endmodule
