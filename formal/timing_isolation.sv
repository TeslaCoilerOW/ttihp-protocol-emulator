// Timing isolation (non-interference) of one engine's protocol pins.
//
// Two copies A and B of the SRAM-refinement processor (protocol_processor_fv:
// the formal debug circuit plus plain observation ports, see gen/) form a
// miter. They share clk, rst_n, ena and the pin inputs uio_in. Each copy has
// its own, unconstrained host port ui_in, except that commands which control
// engine K must be the same in both copies:
//   * START/STOP/CLEAR-FAULT of engine K happen in the same cycles, and
//   * neither copy reloads, commits or re-owns engine K (BEGIN_LOAD, COMMIT,
//     OWNERSHIP while engine K is selected).
// Everything else is free and independent between the copies: the other
// engines' programs, starts, stops, loads and ownership; mover/ROUTE
// descriptors; host FIFO traffic to and from every engine (engine K
// included); SIGNAL events and pin triggers; read-back; flushes.
//
// Initial state (no reset is assumed): arbitrary, except that engine K's
// execution state, its SRAM output latch, its program/ownership/open-drain
// configuration, the timestamp and the pin synchronisers are equal in the
// two copies; each copy satisfies ownership disjointness and open-drain
// containment; engine K is not in the middle of a program load. Engine K's
// program image is shared: whenever both copies read the same address of
// engine K's instruction SRAM in the same cycle, they read the same word.
// FIFO contents, mailboxes and all other engines' state start independent.
//
// Claim: while engine K has not moved a word through its TX/RX FIFO or
// consumed an event (PULL/PUSH/WAITEVENT completing, observed in either copy)
// since the last synchronised START or reset, engine K's owned uio_out and
// uio_oe bits are identical in both copies on every cycle. Blocked
// PULL/PUSH/WAITEVENT that block identically in both copies do not end the
// window, so this is stronger than exempting every executed PULL/PUSH/WAIT.
//
// Defines:
//   NI_NO_EXEMPTION  negative control: assert pin equality even after
//                    interaction; must produce a counterexample.
//   NI_PINS_ONLY     assert only the pin property and the environment
//                    sanity checks (used for the mutant negative control).
//   RESET_SYNC       design variant with a two-flop reset synchronizer: its
//                    flops are shared state (equal initially, and invariantly,
//                    since both copies see the same rst_n/ena).
// Parameters IW (image_length/image_loaded width) and PCW (PC width) follow the
// design variant (formal/run.sh --variant); the defaults are the design of record.
module ti_copy #(parameter K=0, WIDTH=32, ENGINES=4, DEPTH=8,
    LEVEL_WIDTH=$clog2(DEPTH)+1, IW=16, PCW=24) (
    input wire clk, rst_n, ena,
    input wire [7:0] ui_in, uio_in,
    output wire [7:0] uo_out, uio_out, uio_oe,
    output wire [ENGINES*8-1:0] owners, drains,
    output wire [ENGINES-1:0] running_all, grant,
    output wire [ENGINES*LEVEL_WIDTH-1:0] tx_level, rx_level,
    output wire clear,
    output wire [182+PCW+4*WIDTH-1:0] engine_state,
    output wire running, fault_free, dbg_running_k,
    output wire [2*IW+17:0] config_state,
    output wire [1:0] reset_sync,
    output wire writing,
    output wire [55:0] shared_state,
    output wire [31:0] dout,
    output wire read_lo, read_hi, write_any,
    output wire [5:0] addr_lo, addr_hi,
    output wire touch, start, stop, clear_fault, reconfigure,
    output wire accepted, other_command, xfer_active);
    wire [ENGINES*PCW-1:0] pc;
    wire [ENGINES*24-1:0] wait_timer, wait_limit, blocked;
    wire [ENGINES*8-1:0] fault, values, enables, tick, period;
    wire [ENGINES*WIDTH-1:0] tx, rx, x, y;
    wire [ENGINES*16-1:0] repeat_count;
    wire [ENGINES*IW-1:0] image_loaded, image_length;
    wire [ENGINES*9-1:0] pins;
    wire [ENGINES*32-1:0] completed, sram_dout;
    wire [ENGINES*7-1:0] edges;
    wire [ENGINES*5-1:0] mode;
    wire [ENGINES-1:0] fv_running, image_writing, image_valid;
    wire [ENGINES-1:0] men_lo, men_hi, wen_lo, wen_hi, ren_lo, ren_hi;
    wire [ENGINES*6-1:0] a_lo, a_hi;
    wire [ENGINES-1:0] starts, stops, tx_pop, rx_push, event_clear;
    wire [31:0] timestamp;
    wire [7:0] sync1, synced, previous, code;
    wire [23:0] payload;
    wire [1:0] selected;
    protocol_processor_fv dut(.clk(clk), .rst_n(rst_n), .ena(ena), .ui_in(ui_in),
        .uio_in(uio_in), .uo_out(uo_out), .uio_out(uio_out), .uio_oe(uio_oe),
        .dbg_ownership(owners), .dbg_open_drain(drains), .dbg_running(running_all),
        .dbg_clear(clear), .dbg_start(starts), .dbg_stop(stops),
        .dbg_dma_grant(grant), .dbg_tx_pop(tx_pop), .dbg_rx_push(rx_push),
        .dbg_event_clear(event_clear), .dbg_tx_level(tx_level), .dbg_rx_level(rx_level),
        .dbg_synced_pins(synced), .dbg_previous_pins(previous),
        .dbg_host_selected(selected), .dbg_command_accepted(accepted),
        .dbg_command_code(code), .dbg_command_payload(payload),
        .dbg_image_valid(image_valid), .dbg_image_length(image_length),
        .fv_pc(pc), .fv_running(fv_running), .fv_fault_code(fault), .fv_tx(tx), .fv_rx(rx),
        .fv_x(x), .fv_y(y), .fv_repeat_count(repeat_count), .fv_wait_timer(wait_timer),
        .fv_wait_limit(wait_limit), .fv_blocked_cycles(blocked),
        .fv_logical_output(values), .fv_logical_enable(enables),
        .fv_transfer_pins(pins), .fv_completed_instructions(completed),
        .fv_transfer_edges(edges), .fv_transfer_tick(tick), .fv_transfer_period(period),
        .fv_transfer_mode(mode), .fv_image_writing(image_writing),
        .fv_image_loaded(image_loaded), .fv_sram_dout(sram_dout),
        .fv_sram_lo_a_men(men_lo), .fv_sram_hi_a_men(men_hi),
        .fv_sram_lo_a_wen(wen_lo), .fv_sram_hi_a_wen(wen_hi),
        .fv_sram_lo_a_ren(ren_lo), .fv_sram_hi_a_ren(ren_hi),
        .fv_sram_lo_a_addr(a_lo), .fv_sram_hi_a_addr(a_hi),
`ifdef RESET_SYNC
        .fv_reset_sync(reset_sync),
`endif
        .fv_timestamp(timestamp), .fv_sync1(sync1));
`ifndef RESET_SYNC
    assign reset_sync = 2'b00;
`endif
    assign engine_state = {pc[PCW*K+:PCW], fv_running[K], fault[8*K+:8],
        tx[WIDTH*K+:WIDTH], rx[WIDTH*K+:WIDTH], x[WIDTH*K+:WIDTH], y[WIDTH*K+:WIDTH],
        repeat_count[16*K+:16], wait_timer[24*K+:24], wait_limit[24*K+:24],
        blocked[24*K+:24], values[8*K+:8], enables[8*K+:8], pins[9*K+:9],
        completed[32*K+:32], edges[7*K+:7], tick[8*K+:8], period[8*K+:8], mode[5*K+:5]};
    assign running = fv_running[K];
    assign dbg_running_k = running_all[K];
    assign fault_free = fault[8*K+:8] == 0;
    assign xfer_active = edges[7*K+:7] != 0;
    assign config_state = {image_length[IW*K+:IW], image_valid[K], image_writing[K],
        image_loaded[IW*K+:IW], owners[8*K+:8], drains[8*K+:8]};
    assign writing = image_writing[K];
    assign shared_state = {timestamp, sync1, synced, previous};
    assign dout = sram_dout[32*K+:32];
    assign read_lo = men_lo[K] && ren_lo[K] && !wen_lo[K];
    assign read_hi = men_hi[K] && ren_hi[K] && !wen_hi[K];
    assign write_any = (men_lo[K] && wen_lo[K]) || (men_hi[K] && wen_hi[K]);
    assign addr_lo = a_lo[6*K+:6];
    assign addr_hi = a_hi[6*K+:6];
    assign touch = tx_pop[K] || rx_push[K] || event_clear[K];
    assign start = starts[K];
    assign stop = stops[K];
    assign clear_fault = accepted && code == 7 && payload[K];
    assign reconfigure = accepted && selected == K && (code == 1 || code == 2 || code == 3);
    assign other_command = accepted && !(code == 4 && payload[K]) && !(code == 5 && payload[K])
        && !(code == 7 && payload[K]) && !(selected == K && code == 1);
endmodule

module timing_isolation #(parameter K=0, WIDTH=32, ENGINES=4, DEPTH=8,
    LEVEL_WIDTH=$clog2(DEPTH)+1, IW=16, PCW=24) (input wire clk);
    localparam SW = 182+PCW+4*WIDTH;
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] uio_in, ui_a, ui_b;
    wire [7:0] a_uo, a_out, a_oe, b_uo, b_out, b_oe;
    wire [ENGINES*8-1:0] a_owners, a_drains, b_owners, b_drains;
    wire [ENGINES-1:0] a_run_all, a_grant, b_run_all, b_grant;
    wire [ENGINES*LEVEL_WIDTH-1:0] a_txl, a_rxl, b_txl, b_rxl;
    wire [SW-1:0] a_state, b_state;
    wire [2*IW+17:0] a_cfg, b_cfg;
    wire [1:0] a_rsync, b_rsync;
    wire [55:0] a_shared, b_shared;
    wire [31:0] a_dout, b_dout;
    wire [5:0] a_addr_lo, a_addr_hi, b_addr_lo, b_addr_hi;
    wire a_writing, b_writing;
    wire a_clear, a_run, a_ok, a_dbg_run, a_rd_lo, a_rd_hi, a_wr, a_touch, a_start, a_stop,
         a_clrf, a_reconf, a_acc, a_other, a_xfer;
    wire b_clear, b_run, b_ok, b_dbg_run, b_rd_lo, b_rd_hi, b_wr, b_touch, b_start, b_stop,
         b_clrf, b_reconf, b_acc, b_other, b_xfer;
    ti_copy #(.K(K), .WIDTH(WIDTH), .ENGINES(ENGINES), .DEPTH(DEPTH), .IW(IW), .PCW(PCW)) a(
        .clk(clk), .rst_n(rst_n), .ena(ena), .ui_in(ui_a), .uio_in(uio_in),
        .uo_out(a_uo), .uio_out(a_out), .uio_oe(a_oe), .owners(a_owners), .drains(a_drains),
        .running_all(a_run_all), .grant(a_grant), .tx_level(a_txl), .rx_level(a_rxl),
        .clear(a_clear), .engine_state(a_state), .running(a_run), .fault_free(a_ok),
        .dbg_running_k(a_dbg_run), .config_state(a_cfg), .reset_sync(a_rsync),
        .writing(a_writing), .shared_state(a_shared),
        .dout(a_dout), .read_lo(a_rd_lo), .read_hi(a_rd_hi), .write_any(a_wr),
        .addr_lo(a_addr_lo), .addr_hi(a_addr_hi), .touch(a_touch), .start(a_start),
        .stop(a_stop), .clear_fault(a_clrf), .reconfigure(a_reconf), .accepted(a_acc),
        .other_command(a_other), .xfer_active(a_xfer));
    ti_copy #(.K(K), .WIDTH(WIDTH), .ENGINES(ENGINES), .DEPTH(DEPTH), .IW(IW), .PCW(PCW)) b(
        .clk(clk), .rst_n(rst_n), .ena(ena), .ui_in(ui_b), .uio_in(uio_in),
        .uo_out(b_uo), .uio_out(b_out), .uio_oe(b_oe), .owners(b_owners), .drains(b_drains),
        .running_all(b_run_all), .grant(b_grant), .tx_level(b_txl), .rx_level(b_rxl),
        .clear(b_clear), .engine_state(b_state), .running(b_run), .fault_free(b_ok),
        .dbg_running_k(b_dbg_run), .config_state(b_cfg), .reset_sync(b_rsync),
        .writing(b_writing), .shared_state(b_shared),
        .dout(b_dout), .read_lo(b_rd_lo), .read_hi(b_rd_hi), .write_any(b_wr),
        .addr_lo(b_addr_lo), .addr_hi(b_addr_hi), .touch(b_touch), .start(b_start),
        .stop(b_stop), .clear_fault(b_clrf), .reconfigure(b_reconf), .accepted(b_acc),
        .other_command(b_other), .xfer_active(b_xfer));

    wire [7:0] own = a_owners[8*K+:8];
    reg past_valid = 0;
    // Set once engine K completes a FIFO or event interaction in either copy;
    // cleared by a reset/deselect edge or a synchronised START (which reset
    // every engine register).
    reg interacted = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (a_clear || (a_start && !a_stop)) interacted <= 0;
        else if (a_touch || b_touch) interacted <= 1;
    end

    function automatic valid_masks(input [ENGINES*8-1:0] o, input [ENGINES*8-1:0] d);
        integer i, j;
        begin
            valid_masks = 1;
            for (i = 0; i < ENGINES; i = i + 1) begin
                if ((d[8*i+:8] & ~o[8*i+:8]) != 0) valid_masks = 0;
                for (j = i + 1; j < ENGINES; j = j + 1)
                    if ((o[8*i+:8] & o[8*j+:8]) != 0) valid_masks = 0;
            end
        end
    endfunction

    // ---------------- environment ----------------
    always @(posedge clk) begin
        if (!past_valid) begin
            assume(a_state == b_state);
            assume(a_cfg == b_cfg);
            assume(a_shared == b_shared);
            assume(a_rsync == b_rsync);
            assume(a_dout == b_dout);
            assume(!a_writing);  // engine K is not mid-load
            assume(valid_masks(a_owners, a_drains));
            assume(valid_masks(b_owners, b_drains));
        end
        // Host commands that control engine K are synchronised.
        assume(a_start == b_start);
        assume(a_stop == b_stop);
        assume(a_clrf == b_clrf);
        assume(!a_reconf && !b_reconf);
        // Shared program image: equal addresses read in the same cycle
        // return equal words (the vendor model's registered read).
        if (past_valid && $past(a_rd_lo && b_rd_lo && a_addr_lo == b_addr_lo))
            assume(a_dout[15:0] == b_dout[15:0]);
        if (past_valid && $past(a_rd_hi && b_rd_hi && a_addr_hi == b_addr_hi))
            assume(a_dout[31:16] == b_dout[31:16]);
    end

    // ---------------- properties ----------------
    always @(posedge clk) begin
        // Observation-port sanity: the structurally attributed register is
        // the one the production debug export reports for engine K.
        assert(a_run == a_dbg_run && b_run == b_dbg_run);
`ifdef NI_NO_EXEMPTION
        assert((a_out & own) == (b_out & own));
        assert((a_oe & own) == (b_oe & own));
`else
        if (!interacted) begin
            assert((a_out & own) == (b_out & own));
            assert((a_oe & own) == (b_oe & own));
        end
`endif
`ifndef NI_PINS_ONLY
        // Auxiliary invariants (make the claim 1-inductive).
        assert(a_cfg == b_cfg);
        assert(!a_writing && !b_writing);
        assert(a_shared == b_shared);
        assert(a_rsync == b_rsync);
        assert(!a_wr && !b_wr);
        assert(valid_masks(a_owners, a_drains));
        assert(valid_masks(b_owners, b_drains));
        if (!interacted) begin
            assert(a_state == b_state);
            if (a_run) assert(a_dout == b_dout);
        end
`endif
    end

    // ---------------- non-vacuity witnesses ----------------
    // Engine K drives and toggles an owned pin while the two copies' host
    // traffic, other engines and mover visibly differ.
    reg a_host_differs = 0;
    always @(posedge clk) begin
        if (a_acc != b_acc || a_run_all != b_run_all || a_grant != b_grant) a_host_differs <= 1;
        if (past_valid) begin
            cover(!interacted && a_run && a_ok && (a_oe & own) != 0
                  && ((a_out ^ $past(a_out)) & own) != 0 && a_host_differs);
            cover(!interacted && a_run && a_ok && (a_oe & own) != 0
                  && ((a_out ^ $past(a_out)) & own) != 0
                  && ((a_run_all ^ b_run_all) & ~(1 << K)) != 0 && a_other && !b_other);
            cover(!interacted && a_run && a_xfer && (a_oe & own) != 0
                  && ((a_out ^ $past(a_out)) & own) != 0 && a_grant != b_grant);
            // Engine K interacts with its FIFOs/events; the property window ends.
            cover(!interacted && a_run && (a_oe & own) != 0 && (a_touch || b_touch));
        end
    end
endmodule
