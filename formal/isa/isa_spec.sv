// Instruction-level formal specification harness (docs/isa-spec.md).
//
// The production processor with observation ports (protocol_processor_fv,
// formal/build/rtl/processor_fv.v, with the IHP SRAM FUNCTIONAL models) is
// checked against the per-instruction specification modules of isa_insn.sv and
// the host-command specification of isa_cmd.sv. Those modules are written from
// docs/isa.md only; this file maps the observation ports to the architectural
// state and back.
//
// Symbolic image. Engine k (anyconst, any of the four) and image address A
// (anyconst, 0..63) are arbitrary. W is the word at A of engine k's
// instruction SRAM: a read of address A returns W on the next cycle (the
// vendor model's registered read) and address A is never written. The
// checks therefore cover every engine, every address and every instruction
// word. Program loading writes the SRAM only while the engine is halted and
// not starting (asserted with V below), so a load changes W only in states
// where no instruction of engine k issues.
//
// Observation-port abstraction (formal/README.md, formal/engine_safety.sv):
//   PC pc_k, running run_k, fault code fault_k, registers tx/rx/x/y, repeat
//   rep_k, WAIT hold hold_k = fv_wait_timer, LIMIT = fv_wait_limit with 0
//   meaning the reset default 65535, blocked count blk_k = fv_blocked_cycles,
//   logical outputs/enables fv_logical_output/enable, PINS fv_transfer_pins,
//   completed count fv_completed_instructions, XFER in progress
//   fv_transfer_edges != 0; queue levels, mailboxes, ownership and the
//   synchronized pins from the dbg_* exports.
//
// An issue edge of engine k at A: engine k runs without fault, no WAIT hold
// and no XFER is in progress, PC = A is inside the committed image, and the
// edge carries no reset/deselect, START or STOP of engine k.
//
// The invariant V (inv_*) is the set of states the checks start from: each
// job assumes V in its first state only. CHECK = C_RESET shows that a reset
// edge from any state lands in V. Every other one-step job asserts V as well
// as its own properties and is proved by k-induction (sby prove), so V holds
// in every state reachable from reset and each property holds on every edge
// out of such a state. The negative controls (define ISA_NEG) drop the V
// assertions and run as BMC from V, so they fail only on the checked
// properties. isa_queue_tx, isa_queue_rx and isa_xfer_e2e* are bounded (BMC).
//
// CHECK selects one job (formal/isa/isa.sby). Negative controls use the same
// CHECK on a mutant (formal/isa/mutate_isa.py); the assertion that each must
// fire carries a "target of:" comment naming the job. Define ISA_COVER turns
// the assertions off and the non-vacuity covers on.

`default_nettype none
`include "isa_insn.vh"

module isa_spec #(parameter WIDTH=32, ENGINES=4, DEPTH=8, LW=4, IW=16, PCW=24,
    CHECK=0, XA=2, XB=2) (input wire clk);
    // ---- job selection -------------------------------------------------
    localparam C_INVALID = 30;          // opcodes 30..255
    localparam C_ALL = 31;              // every opcode in one run (not registered:
                                        // slower than the per-opcode jobs)
    localparam C_FETCH = 40, C_HOLD = 41, C_IDLE = 42, C_PINMAP = 43;
    localparam C_INV = 50, C_RESET = 51;
    localparam C_SELECT = 60, C_BEGIN = 61, C_COMMIT = 62, C_OWN = 63, C_START = 64,
               C_STOP = 65, C_ROUTE = 66, C_CLEAR = 67, C_EVENT = 69, C_FLUSH = 70,
               C_TRIGGER = 71;
    localparam C_QUEUE_TX = 80, C_XFER = 81, C_QUEUE_RX = 82;
    localparam INSN_CHECK = CHECK <= C_ALL;

    // ---- the processor --------------------------------------------------
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] ui_in, uio_in;
    wire [7:0] uo_out, uio_out, uio_oe;
    wire [ENGINES*8-1:0] owners, drains;
    wire [6*ENGINES-1:0] trigger_config;
    wire [ENGINES-1:0] trigger_event, events, event_set, event_clear, dbg_run, starts, stops,
                       fifo_clear, grant, eligible, tx_push, tx_pop, rx_push, rx_pop, tx_ready,
                       rx_valid, image_valid;
    wire [7:0] synced, previous, command_code;
    wire clear, host_tx, host_rx, rx_reserved, command_accepted;
    wire [1:0] dma_destination, round_robin, selected;
    wire [WIDTH-1:0] dma_data;
    wire [16*ENGINES-1:0] route_count;
    wire [2*ENGINES-1:0] route_destination;
    wire [WIDTH*ENGINES-1:0] tx_data, rx_data, rx_head;
    wire [LW*ENGINES-1:0] tx_level, rx_level;
    wire [23:0] command_payload;
    wire [IW*ENGINES-1:0] image_length, image_loaded;
    wire [PCW*ENGINES-1:0] fv_pc;
    wire [ENGINES-1:0] fv_running, image_writing;
    wire [8*ENGINES-1:0] fv_fault, fv_lout, fv_len, fv_tick, fv_period;
    wire [WIDTH*ENGINES-1:0] fv_tx, fv_rx, fv_x, fv_y;
    wire [16*ENGINES-1:0] fv_rep;
    wire [24*ENGINES-1:0] fv_hold, fv_limit, fv_blk;
    wire [9*ENGINES-1:0] fv_pins;
    wire [32*ENGINES-1:0] fv_cnt, sram_dout;
    wire [7*ENGINES-1:0] fv_edges;
    wire [5*ENGINES-1:0] fv_mode;
    wire [ENGINES-1:0] men_lo, men_hi, wen_lo, wen_hi, ren_lo, ren_hi;
    wire [6*ENGINES-1:0] addr_lo, addr_hi;
    wire [31:0] timestamp;
    wire [7:0] sync1;
    protocol_processor_fv dut(.clk(clk), .rst_n(rst_n), .ena(ena), .ui_in(ui_in),
        .uio_in(uio_in), .uo_out(uo_out), .uio_out(uio_out), .uio_oe(uio_oe),
        .dbg_ownership(owners), .dbg_open_drain(drains), .dbg_trigger_config(trigger_config),
        .dbg_trigger_event(trigger_event), .dbg_synced_pins(synced),
        .dbg_previous_pins(previous), .dbg_events(events), .dbg_event_set(event_set),
        .dbg_event_clear(event_clear), .dbg_running(dbg_run), .dbg_clear(clear),
        .dbg_start(starts), .dbg_stop(stops), .dbg_fifo_clear(fifo_clear),
        .dbg_dma_grant(grant), .dbg_dma_eligible(eligible),
        .dbg_dma_destination(dma_destination), .dbg_dma_data(dma_data),
        .dbg_round_robin(round_robin), .dbg_route_count(route_count),
        .dbg_route_destination(route_destination), .dbg_tx_push(tx_push),
        .dbg_tx_pop(tx_pop), .dbg_tx_data(tx_data), .dbg_rx_push(rx_push),
        .dbg_rx_pop(rx_pop), .dbg_rx_data(rx_data), .dbg_tx_ready(tx_ready),
        .dbg_rx_valid(rx_valid), .dbg_tx_level(tx_level), .dbg_rx_level(rx_level),
        .dbg_rx_head(rx_head), .dbg_host_tx(host_tx), .dbg_host_rx(host_rx),
        .dbg_host_rx_reserved(rx_reserved), .dbg_host_selected(selected),
        .dbg_command_accepted(command_accepted), .dbg_command_code(command_code),
        .dbg_command_payload(command_payload), .dbg_image_valid(image_valid),
        .dbg_image_length(image_length),
        .fv_pc(fv_pc), .fv_running(fv_running), .fv_fault_code(fv_fault), .fv_tx(fv_tx),
        .fv_rx(fv_rx), .fv_x(fv_x), .fv_y(fv_y), .fv_repeat_count(fv_rep),
        .fv_wait_timer(fv_hold), .fv_wait_limit(fv_limit), .fv_blocked_cycles(fv_blk),
        .fv_logical_output(fv_lout), .fv_logical_enable(fv_len),
        .fv_transfer_pins(fv_pins), .fv_completed_instructions(fv_cnt),
        .fv_transfer_edges(fv_edges), .fv_transfer_tick(fv_tick),
        .fv_transfer_period(fv_period), .fv_transfer_mode(fv_mode),
        .fv_image_writing(image_writing), .fv_image_loaded(image_loaded),
        .fv_sram_dout(sram_dout), .fv_sram_lo_a_men(men_lo), .fv_sram_hi_a_men(men_hi),
        .fv_sram_lo_a_wen(wen_lo), .fv_sram_hi_a_wen(wen_hi),
        .fv_sram_lo_a_ren(ren_lo), .fv_sram_hi_a_ren(ren_hi),
        .fv_sram_lo_a_addr(addr_lo), .fv_sram_hi_a_addr(addr_hi),
        .fv_timestamp(timestamp), .fv_sync1(sync1));

    function [23:0] arch_limit(input [23:0] r);
        arch_limit = r == 24'd0 ? 24'd65535 : r;
    endfunction

    // ---- engine k and the symbolic image word ----------------------------
    (* anyconst *) reg [1:0] k;
    (* anyconst *) reg [5:0] img_a;
    (* anyconst *) reg [31:0] img_w;
    wire [PCW-1:0] pc_k = fv_pc[PCW*k +: PCW];
    wire run_k = fv_running[k];
    wire [7:0] fault_k = fv_fault[8*k +: 8];
    wire [31:0] tx_k = fv_tx[WIDTH*k +: WIDTH], rx_k = fv_rx[WIDTH*k +: WIDTH];
    wire [31:0] x_k = fv_x[WIDTH*k +: WIDTH], y_k = fv_y[WIDTH*k +: WIDTH];
    wire [15:0] rep_k = fv_rep[16*k +: 16];
    wire [23:0] hold_k = fv_hold[24*k +: 24];
    wire [23:0] limit_k = arch_limit(fv_limit[24*k +: 24]);
    wire [23:0] blk_k = fv_blk[24*k +: 24];
    wire [7:0] lout_k = fv_lout[8*k +: 8], len_k = fv_len[8*k +: 8];
    wire [8:0] pins_k = fv_pins[9*k +: 9];
    wire [31:0] cnt_k = fv_cnt[32*k +: 32];
    wire busy_k = fv_edges[7*k +: 7] != 0;
    wire [7:0] own_k = owners[8*k +: 8], od_k = drains[8*k +: 8];
    wire [LW-1:0] txl_k = tx_level[LW*k +: LW], rxl_k = rx_level[LW*k +: LW];
    wire [IW-1:0] ilen_k = image_length[IW*k +: IW];
    wire [31:0] dout_k = sram_dout[32*k +: 32];
    wire start_k = starts[k], stop_k = stops[k];
    wire rd_lo_k = men_lo[k] && ren_lo[k] && !wen_lo[k];
    wire rd_hi_k = men_hi[k] && ren_hi[k] && !wen_hi[k];
    wire wr_lo_k = men_lo[k] && wen_lo[k], wr_hi_k = men_hi[k] && wen_hi[k];
    wire [5:0] a_lo_k = addr_lo[6*k +: 6], a_hi_k = addr_hi[6*k +: 6];

    wire at_a = pc_k == {{(PCW-6){1'b0}}, img_a};
    wire in_image = {{(32-PCW){1'b0}}, pc_k} < {{(32-IW){1'b0}}, ilen_k};
    wire ready = run_k && fault_k == 0 && hold_k == 0 && !busy_k;
    wire edge_plain = !clear && !start_k && !stop_k;
    wire issue = ready && edge_plain && in_image && at_a;

    reg past_valid = 1'b0;
    always @(posedge clk) past_valid <= 1'b1;

    always @(posedge clk) begin
        // A read of A returns W on the next cycle; A is never written.
        if (past_valid && $past(rd_lo_k && a_lo_k == img_a)) assume(dout_k[15:0] == img_w[15:0]);
        if (past_valid && $past(rd_hi_k && a_hi_k == img_a)) assume(dout_k[31:16] == img_w[31:16]);
        assume(!(wr_lo_k && a_lo_k == img_a));
        assume(!(wr_hi_k && a_hi_k == img_a));
    end

    // ---- the invariant V --------------------------------------------------
    // Per engine: a fault stops the engine; a running engine has a committed
    // image of 1..64 words; the blocked count is below LIMIT; an engine being
    // loaded is halted and uncommitted; a WAIT hold and an XFER are never in
    // progress together. Across engines: ownership disjoint,
    // open-drain inside ownership, queue levels at most DEPTH. For engine k:
    // the fetched word at PC = A is W (fetch), and a non-zero blocked count at
    // A means W is a well-formed WAITPIN or WAITEVENT.
    wire w_is_wait = (img_w[31:24] == 8'd13 && img_w[23:16] < 8'd8 && img_w[15:8] < 8'd2
                      && img_w[7:0] == 8'd0) || img_w == 32'h0f000000;
    reg [ENGINES-1:0] inv_stop, inv_image, inv_blk, inv_load, inv_mask, inv_level, inv_one;
    integer i, j, m;
    always @* begin
        for (i = 0; i < ENGINES; i = i + 1) begin
            inv_stop[i] = fv_fault[8*i +: 8] == 0 || !fv_running[i];
            inv_image[i] = (!fv_running[i] || image_valid[i]) && (!image_valid[i]
                           || (image_length[IW*i +: IW] != 0 && image_length[IW*i +: IW] <= 64));
            inv_blk[i] = fv_blk[24*i +: 24] < arch_limit(fv_limit[24*i +: 24]);
            inv_load[i] = !image_writing[i] || (!image_valid[i] && !fv_running[i]);
            inv_mask[i] = (drains[8*i +: 8] & ~owners[8*i +: 8]) == 0;
            for (j = i + 1; j < ENGINES; j = j + 1)
                if ((owners[8*i +: 8] & owners[8*j +: 8]) != 0) inv_mask[i] = 1'b0;
            inv_level[i] = tx_level[LW*i +: LW] <= DEPTH && rx_level[LW*i +: LW] <= DEPTH;
            inv_one[i] = fv_hold[24*i +: 24] == 0 || fv_edges[7*i +: 7] == 0;
        end
    end
    wire inv_fetch = !(run_k && at_a && in_image) || dout_k == img_w;
    wire inv_waiting = !(run_k && at_a && in_image && blk_k != 0) || w_is_wait;
    wire inv_all = &inv_stop && &inv_image && &inv_blk && &inv_load && &inv_mask && &inv_level && &inv_one
                 && inv_fetch && inv_waiting;

    always @(posedge clk) begin
        if (!past_valid && CHECK != C_RESET) assume(inv_all);
    end

`ifndef ISA_COVER
`ifndef ISA_NEG
    // V is asserted by every job except the reset check and the negative
    // controls, so each one-step job is itself a k-induction proof of V and its
    // own assertions; CHECK = C_INV asserts V alone.
    generate if (CHECK != C_RESET && CHECK != C_QUEUE_TX && CHECK != C_QUEUE_RX && CHECK != C_XFER) begin : inv
        wire [ENGINES-1:0] writes = (men_lo & wen_lo) | (men_hi & wen_hi);
        always @(posedge clk) begin
            assert(&inv_stop);
            assert(&inv_image);
            assert(&inv_blk);
            assert(&inv_load);
            assert(&inv_mask);
            assert(&inv_level);
            assert(&inv_one);
            assert(inv_fetch);
            assert(inv_waiting);
            // program writes only into a halted engine that is not starting
            assert((writes & (fv_running | starts)) == 0);
        end
    end endgenerate
`endif

    generate if (CHECK == C_RESET) begin : reset
        // "Reset/deselection releases all output enables, stops all engines,
        // invalidates program images, and clears queues/mailboxes/control."
        // LIMIT resets to 65535. From an arbitrary state, one reset edge.
        always @(posedge clk) begin
            if (!past_valid) assume(clear);
            if (past_valid) begin
                assert(inv_all);
                assert(fv_running == 0 && image_valid == 0 && image_writing == 0);
                assert(fv_fault == 0);
                assert(tx_level == 0 && rx_level == 0 && events == 0);
                assert(owners == 0 && drains == 0 && trigger_config == 0 && route_count == 0);
                assert(fv_limit == 0);
                assert(uio_oe == 0);
            end
        end
    end endgenerate
`endif

    // ---- the specification of the word at PC ------------------------------
    // Harness knowledge of the TX FIFO head (C_QUEUE_TX only).
    wire [31:0] tag_head;
    wire tag_head_known;
    generate if (CHECK != C_QUEUE_TX) begin : no_tag
        assign tag_head = 32'd0;
        assign tag_head_known = 1'b0;
    end endgenerate
`define ISA_SPEC_INPUTS .insn(img_w), .pc(pc_k), .tx(tx_k), .rx(rx_k), .x(x_k), .y(y_k), \
        .rep(rep_k), .limit(limit_k), .blk(blk_k), .lout(lout_k), .len(len_k), .pins(pins_k), \
        .cnt(cnt_k), .own(own_k), .sync(synced), .ts(timestamp), .tx_empty(txl_k == 0), \
        .rx_full(rxl_k == DEPTH), .evp(events[k]), .tx_head(tag_head), \
        .tx_head_known(tag_head_known)
`define ISA_SPEC(module_name, vname, rname, uname) \
    wire vname; wire [`ISA_RW-1:0] rname; \
    module_name uname(`ISA_SPEC_INPUTS, .s_valid(vname), .s_out(rname));
    `ISA_SPEC(isa_insn_nop, v_nop, r_nop, u_nop)
    `ISA_SPEC(isa_insn_halt, v_halt, r_halt, u_halt)
    `ISA_SPEC(isa_insn_set, v_set, r_set, u_set)
    `ISA_SPEC(isa_insn_dir, v_dir, r_dir, u_dir)
    `ISA_SPEC(isa_insn_wait, v_wait_, r_wait_, u_wait_)
    `ISA_SPEC(isa_insn_jmp, v_jmp, r_jmp, u_jmp)
    `ISA_SPEC(isa_insn_pull, v_pull, r_pull, u_pull)
    `ISA_SPEC(isa_insn_push, v_push, r_push, u_push)
    `ISA_SPEC(isa_insn_out, v_out, r_out, u_out)
    `ISA_SPEC(isa_insn_in, v_in, r_in, u_in)
    `ISA_SPEC(isa_insn_count, v_count, r_count, u_count)
    `ISA_SPEC(isa_insn_loop, v_loop, r_loop, u_loop)
    `ISA_SPEC(isa_insn_limit, v_limit, r_limit, u_limit)
    `ISA_SPEC(isa_insn_waitpin, v_waitpin, r_waitpin, u_waitpin)
    `ISA_SPEC(isa_insn_signal, v_signal, r_signal, u_signal)
    `ISA_SPEC(isa_insn_waitevent, v_waitevent, r_waitevent, u_waitevent)
    `ISA_SPEC(isa_insn_pins, v_pins, r_pins, u_pins)
    `ISA_SPEC(isa_insn_xfer, v_xfer, r_xfer, u_xfer)
    `ISA_SPEC(isa_insn_mov, v_mov, r_mov, u_mov)
    `ISA_SPEC(isa_insn_load, v_load, r_load, u_load)
    `ISA_SPEC(isa_insn_alu #(.OPCODE(8'd20)), v_add, r_add, u_add)
    `ISA_SPEC(isa_insn_alu #(.OPCODE(8'd21)), v_xor_, r_xor_, u_xor_)
    `ISA_SPEC(isa_insn_alu #(.OPCODE(8'd22)), v_and_, r_and_, u_and_)
    `ISA_SPEC(isa_insn_alu #(.OPCODE(8'd23)), v_or_, r_or_, u_or_)
    `ISA_SPEC(isa_insn_shift #(.OPCODE(8'd24)), v_shl, r_shl, u_shl)
    `ISA_SPEC(isa_insn_shift #(.OPCODE(8'd25)), v_shr, r_shr, u_shr)
    `ISA_SPEC(isa_insn_jz, v_jz, r_jz, u_jz)
    `ISA_SPEC(isa_insn_not, v_not_, r_not_, u_not_)
    `ISA_SPEC(isa_insn_time, v_time_, r_time_, u_time_)
    `ISA_SPEC(isa_insn_fault, v_fault, r_fault, u_fault)
    `ISA_SPEC(isa_insn_invalid, v_invalid, r_invalid, u_invalid)
    wire [`ISA_RW-1:0] spec =
        v_nop ? r_nop : v_halt ? r_halt : v_set ? r_set : v_dir ? r_dir : v_wait_ ? r_wait_ :
        v_jmp ? r_jmp : v_pull ? r_pull : v_push ? r_push : v_out ? r_out : v_in ? r_in :
        v_count ? r_count : v_loop ? r_loop : v_limit ? r_limit : v_waitpin ? r_waitpin :
        v_signal ? r_signal : v_waitevent ? r_waitevent : v_pins ? r_pins : v_xfer ? r_xfer :
        v_mov ? r_mov : v_load ? r_load : v_add ? r_add : v_xor_ ? r_xor_ : v_and_ ? r_and_ :
        v_or_ ? r_or_ : v_shl ? r_shl : v_shr ? r_shr : v_jz ? r_jz : v_not_ ? r_not_ :
        v_time_ ? r_time_ : v_fault ? r_fault : r_invalid;
    wire [23:0] s_pc, s_hold, s_limit, s_blk;
    wire s_run, s_pop, s_push, s_evclr, s_busy;
    wire [7:0] s_fault, s_lout, s_len;
    wire [31:0] s_tx, s_rx, s_x, s_y, s_cnt, s_push_data;
    wire [15:0] s_rep;
    wire [8:0] s_pins;
    wire [3:0] s_signal, s_dc;
    assign {`ISA_RESULT} = spec;

    // The opcode this job checks.
    wire [7:0] w_op = img_w[31:24];
    wire selected_op = CHECK == C_ALL || (CHECK == C_INVALID ? w_op >= 8'd30 : w_op == CHECK);
    always @(posedge clk) if (INSN_CHECK) assume(selected_op);

    // ---- one-step instruction checks ---------------------------------------
    // Expected post-state, registered on the issue edge.
    reg e_valid = 1'b0;
    reg [23:0] e_pc, e_hold, e_limit, e_blk;
    reg e_run, e_busy, e_evclr, e_evset;
    reg [7:0] e_fault, e_lout, e_len;
    reg [31:0] e_tx, e_rx, e_x, e_y, e_cnt, e_cnt_pre;
    reg [15:0] e_rep;
    reg [8:0] e_pins;
    reg [3:0] e_signal, e_dc;
    reg [7:0] e_op;
    always @(posedge clk) begin
        e_valid <= INSN_CHECK && issue && selected_op;
        {e_pc, e_run, e_fault, e_tx, e_rx, e_x, e_y, e_rep, e_hold, e_limit, e_blk, e_lout,
         e_len, e_pins, e_cnt, e_busy, e_evclr, e_signal, e_dc}
            <= {s_pc, s_run, s_fault, s_tx, s_rx, s_x, s_y, s_rep, s_hold, s_limit, s_blk,
                s_lout, s_len, s_pins, s_cnt, s_busy, s_evclr, s_signal, s_dc};
        e_evset <= event_set[k];
        e_cnt_pre <= cnt_k;
        e_op <= w_op;
    end

`ifndef ISA_COVER
    always @(posedge clk) begin
        // Queue and mailbox handshakes on the issue edge itself.
        if (INSN_CHECK && issue && selected_op) begin
            assert(tx_pop[k] == s_pop);                            // target of: isa_pull_neg
            assert(rx_push[k] == s_push);                          // target of: isa_push_neg
            if (s_push) assert(rx_data[WIDTH*k +: WIDTH] == s_push_data);
            assert(event_clear[k] == s_evclr);                     // target of: isa_waitevent_neg
        end
        if (past_valid && e_valid) begin
            assert(pc_k == e_pc);                                  // target of: isa_jmp_neg isa_jz_neg
            assert(run_k == e_run);                                // target of: isa_halt_neg
            assert(fault_k == e_fault);                            // target of: isa_set_neg isa_dir_neg isa_fault_neg isa_invalid_neg
            assert((tx_k == e_tx || e_dc[`ISA_DC_TX]) && rx_k == e_rx && x_k == e_x && y_k == e_y); // target of: isa_in_neg isa_mov_neg isa_load_neg isa_add_neg isa_xor_neg isa_and_neg isa_or_neg isa_shl_neg isa_shr_neg isa_not_neg isa_time_neg
            assert(rep_k == e_rep);                                // target of: isa_count_neg isa_loop_neg
            assert(hold_k == e_hold);                              // target of: isa_wait_neg
            assert(limit_k == e_limit);                            // target of: isa_limit_neg
            assert(blk_k == e_blk || e_dc[`ISA_DC_BLK]);           // target of: isa_waitpin_neg
            assert(lout_k == e_lout || e_dc[`ISA_DC_LOUT]);        // target of: isa_out_neg isa_xfer_neg
            assert(len_k == e_len || e_dc[`ISA_DC_LEN]);
            assert(pins_k == e_pins);                              // target of: isa_pins_neg
            assert(cnt_k == e_cnt);                                // target of: isa_nop_neg
            assert(busy_k == e_busy);
            // "simultaneous delivery wins over clear"
            if (e_evclr) assert(events[k] == e_evset);
            for (m = 0; m < ENGINES; m = m + 1)
                if (e_signal[m]) assert(events[m]);                // target of: isa_signal_neg
        end
    end
`else
    // Non-vacuity: the checked instruction issues and completes (PC and count
    // advance, no fault); HALT stops; FAULT and invalid words fault; the
    // blocking paths block, time out or overflow.
    generate if (INSN_CHECK) begin : insn_cover
        wire done = fault_k == 0 && run_k && cnt_k == e_cnt_pre + 32'd1;
        always @(posedge clk) if (past_valid && e_valid) begin
            if (CHECK == 1) cover(!run_k && fault_k == 0);
            else if (CHECK == 29 || CHECK == C_INVALID) cover(fault_k != 0);
            else if (CHECK == 17) cover(busy_k && fault_k == 0);
            else cover(done);
            if (CHECK == 6 || CHECK == 7) cover(run_k && fault_k == 0 && cnt_k == e_cnt_pre);
            if (CHECK == 7) cover(fault_k == 8'd4);
            if (CHECK == 13 || CHECK == 15) begin
                cover(run_k && blk_k != 0);
                cover(fault_k == 8'd3);
            end
        end
    end endgenerate
`endif

    // ---- PC outside the committed image ------------------------------------
    // "PC is bounded by committed image length; falling outside the image
    // faults." Fault 2: "PC outside the committed image". The instruction at
    // PC does not exist, so nothing else changes.
    wire fetch_fault = ready && edge_plain && !in_image;
    generate if (CHECK == C_FETCH) begin : fetch
`ifndef ISA_COVER
        always @(posedge clk) begin
            if (fetch_fault) assert(!tx_pop[k] && !rx_push[k] && !event_clear[k]);  // target of: isa_fetch_neg
            if (past_valid && $past(fetch_fault)) begin
                assert(fault_k == 8'd2 && !run_k);                 // target of: isa_fetch_neg
                assert(pc_k == $past(pc_k) && cnt_k == $past(cnt_k));
                assert(tx_k == $past(tx_k) && rx_k == $past(rx_k) && x_k == $past(x_k) && y_k == $past(y_k));
            end
        end
`else
        always @(posedge clk) if (past_valid) cover($past(fetch_fault) && fault_k == 8'd2);
`endif
    end endgenerate

    // ---- WAIT hold and XFER progress ---------------------------------------
    // "WAIT n advances PC on issue, then holds execution for n additional
    // cycles": while the hold is non-zero each edge only counts it down.
    // "No FIFO operations occur inside XFER"; "PC advances at final idle edge":
    // while a transfer is in progress PC and count stay until it ends, when
    // they advance by one; registers other than tx/rx and the configuration
    // do not change.
    wire holding = run_k && fault_k == 0 && hold_k != 0 && edge_plain;
    wire moving = run_k && fault_k == 0 && hold_k == 0 && busy_k && edge_plain;
    generate if (CHECK == C_HOLD) begin : hold
`ifndef ISA_COVER
        always @(posedge clk) begin
            if (holding || moving) assert(!tx_pop[k] && !rx_push[k] && !event_clear[k]);
            if (past_valid && $past(holding)) begin
                assert(hold_k == $past(hold_k) - 24'd1);           // target of: isa_hold_neg
                assert(run_k && fault_k == 0 && pc_k == $past(pc_k) && cnt_k == $past(cnt_k));
                assert(tx_k == $past(tx_k) && rx_k == $past(rx_k) && x_k == $past(x_k) && y_k == $past(y_k));
                assert(rep_k == $past(rep_k) && limit_k == $past(limit_k) && blk_k == $past(blk_k));
                assert(lout_k == $past(lout_k) && len_k == $past(len_k) && pins_k == $past(pins_k));
                assert(!busy_k);
            end
            if (past_valid && $past(moving)) begin
                assert(run_k && fault_k == 0 && hold_k == 0);
                if (busy_k) assert(pc_k == $past(pc_k) && cnt_k == $past(cnt_k));
                else assert(pc_k == $past(pc_k) + 24'd1 && cnt_k == $past(cnt_k) + 32'd1);
                assert(x_k == $past(x_k) && y_k == $past(y_k) && rep_k == $past(rep_k));
                assert(limit_k == $past(limit_k) && len_k == $past(len_k) && pins_k == $past(pins_k));
            end
        end
`else
        always @(posedge clk) if (past_valid) begin
            cover($past(holding) && hold_k == 0);
            cover($past(moving) && !busy_k);
        end
`endif
    end endgenerate

    // ---- halted engines and the start/stop observations ----------------------
    // A halted (or faulted) engine changes no execution state except through
    // START, CLEAR or reset, and moves no queue word or event. dbg_start and
    // dbg_stop (the observations that exclude an edge from the instruction
    // checks) occur only for START, STOP, BEGIN of the engine or reset.
    wire cmd_start_k = command_accepted && command_code == 8'd4 && command_payload[k];
    wire cmd_stop_k = command_accepted && ((command_code == 8'd5 && command_payload[k])
                                           || (command_code == 8'd1 && selected == k));
    wire cmd_clear_k = command_accepted && command_code == 8'd7 && command_payload[k];
    wire idle = !run_k && !clear && !start_k && !cmd_clear_k;
    generate if (CHECK == C_IDLE) begin : idle_check
`ifndef ISA_COVER
        always @(posedge clk) begin
            assert(start_k == (!clear && cmd_start_k));
            assert(!stop_k || clear || cmd_stop_k);
            if (idle) assert(!tx_pop[k] && !rx_push[k] && !event_clear[k]);
            if (past_valid && $past(idle)) begin
                assert(!run_k && fault_k == $past(fault_k));
                assert(pc_k == $past(pc_k) && cnt_k == $past(cnt_k));
                assert(tx_k == $past(tx_k) && rx_k == $past(rx_k) && x_k == $past(x_k) && y_k == $past(y_k)); // target of: isa_idle_neg
                assert(rep_k == $past(rep_k) && hold_k == $past(hold_k) && limit_k == $past(limit_k));
                assert(blk_k == $past(blk_k) && pins_k == $past(pins_k));
                // STOP/BEGIN of a halted engine may release its logical enables
                if (!$past(stop_k)) assert(lout_k == $past(lout_k) && len_k == $past(len_k));
            end
        end
`else
        always @(posedge clk) if (past_valid) cover($past(idle) && fault_k != 0 && txl_k != 0);
`endif
    end endgenerate

    // ---- pins and host status outputs ---------------------------------------
    // "Outputs are masked by ownership, run, and fault. For an open-drain pin,
    // output is always zero and physical OE = logical OE AND NOT logical output
    // value." "While the chip is in reset its pin outputs and the host
    // ready/valid bits are masked." "IRQ is asserted while any event mailbox is
    // pending or any RX FIFO is nonempty" (uo6); uo7 is the fault output.
    // "Inputs pass through two flip flops": outside reset the first stage
    // takes uio_in and the second (the synchronized pins the engines sample)
    // takes the first. (Added after the specification freeze.)
    reg [7:0] pm_oe, pm_out;
    reg pm_irq, pm_fault;
    integer p, q;
    always @* begin
        pm_oe = 0;
        pm_out = 0;
        pm_irq = events != 0;
        pm_fault = 1'b0;
        for (q = 0; q < ENGINES; q = q + 1) begin
            if (rx_level[LW*q +: LW] != 0) pm_irq = 1'b1;
            if (fv_fault[8*q +: 8] != 0) pm_fault = 1'b1;
            for (p = 0; p < 8; p = p + 1)
                if (owners[8*q + p] && fv_running[q] && fv_fault[8*q +: 8] == 0) begin
                    pm_oe[p] = fv_len[8*q + p] && !(drains[8*q + p] && fv_lout[8*q + p]);
                    pm_out[p] = !drains[8*q + p] && fv_lout[8*q + p];
                end
        end
        if (clear) begin
            pm_oe = 0;
            pm_out = 0;
        end
    end
    generate if (CHECK == C_PINMAP) begin : pinmap
`ifndef ISA_COVER
        always @(posedge clk) begin
            assert(uio_oe == pm_oe);                               // target of: isa_pinmap_neg
            assert(uio_out == pm_out);
            if (!clear) assert(uo_out[6] == pm_irq);
            if (!clear && pm_fault) assert(uo_out[7]);
            if (clear) assert(uo_out[5:4] == 2'b00);
            if (past_valid && !$past(clear)) assert(sync1 == $past(uio_in) && synced == $past(sync1));
        end
`else
        always @(posedge clk) begin
            cover((uio_oe & drains[8*k +: 8]) != 0 && !clear);
            cover((uio_out & uio_oe & ~drains[8*k +: 8]) != 0 && !clear);
        end
`endif
    end endgenerate

    // ---- host commands (isa_cmd.sv), one instance per engine --------------------
    generate for (genvar g = 0; g < ENGINES; g = g + 1) begin : cmd
        localparam [1:0] GJ = g;
        reg [7:0] others;
        integer o;
        always @* begin
            others = 0;
            for (o = 0; o < ENGINES; o = o + 1) if (o != g) others = others | owners[8*o +: 8];
        end
`define ISA_CMD_CONNECT .clk(clk), .past_valid(past_valid), .j(GJ), \
        .accepted(command_accepted), .code(command_code), .payload(command_payload), \
        .sel(selected), .run(fv_running[g]), .fault(fv_fault[8*g +: 8]), .pc(fv_pc[PCW*g +: PCW]), \
        .tx(fv_tx[WIDTH*g +: WIDTH]), .rx(fv_rx[WIDTH*g +: WIDTH]), .x(fv_x[WIDTH*g +: WIDTH]), \
        .y(fv_y[WIDTH*g +: WIDTH]), .rep(fv_rep[16*g +: 16]), .hold(fv_hold[24*g +: 24]), \
        .limit(arch_limit(fv_limit[24*g +: 24])), .blk(fv_blk[24*g +: 24]), \
        .lout(fv_lout[8*g +: 8]), .len(fv_len[8*g +: 8]), .pins(fv_pins[9*g +: 9]), \
        .cnt(fv_cnt[32*g +: 32]), .busy(fv_edges[7*g +: 7] != 0), .ivalid(image_valid[g]), \
        .iwriting(image_writing[g]), .iloaded(image_loaded[IW*g +: IW]), \
        .ilen(image_length[IW*g +: IW]), .own(owners[8*g +: 8]), .od(drains[8*g +: 8]), \
        .own_others(others), .txl(tx_level[LW*g +: LW]), .rxl(rx_level[LW*g +: LW]), \
        .ev(events[g]), .ev_set(event_set[g]), .ev_clear(event_clear[g]), .tx_pop(tx_pop[g]), \
        .rx_push(rx_push[g]), .fifo_clear(fifo_clear[g]), .trig(trigger_config[6*g +: 6]), \
        .route_count(route_count[16*g +: 16]), .route_dst(route_destination[2*g +: 2])
`ifndef ISA_COVER
        if (CHECK == C_SELECT && g == 0) begin : c_select isa_cmd_select u(`ISA_CMD_CONNECT); end
        if (CHECK == C_BEGIN) begin : c_begin isa_cmd_begin u(`ISA_CMD_CONNECT); end
        if (CHECK == C_COMMIT) begin : c_commit isa_cmd_commit u(`ISA_CMD_CONNECT); end
        if (CHECK == C_OWN) begin : c_own isa_cmd_own u(`ISA_CMD_CONNECT); end
        if (CHECK == C_START) begin : c_start isa_cmd_start u(`ISA_CMD_CONNECT); end
        if (CHECK == C_STOP) begin : c_stop isa_cmd_stop u(`ISA_CMD_CONNECT); end
        if (CHECK == C_ROUTE) begin : c_route isa_cmd_route u(`ISA_CMD_CONNECT); end
        if (CHECK == C_CLEAR) begin : c_clear isa_cmd_clear u(`ISA_CMD_CONNECT); end
        if (CHECK == C_EVENT) begin : c_event isa_cmd_event u(`ISA_CMD_CONNECT); end
        if (CHECK == C_FLUSH) begin : c_flush isa_cmd_flush u(`ISA_CMD_CONNECT); end
        if (CHECK == C_TRIGGER) begin : c_trigger isa_cmd_trigger u(`ISA_CMD_CONNECT); end
`endif
    end endgenerate
`ifdef ISA_COVER
    // Each command is accepted (engine k selected or in the mask).
    wire [7:0] cmd_code_of = CHECK == C_SELECT ? 8'd0 : CHECK == C_BEGIN ? 8'd1 :
        CHECK == C_COMMIT ? 8'd2 : CHECK == C_OWN ? 8'd3 : CHECK == C_START ? 8'd4 :
        CHECK == C_STOP ? 8'd5 : CHECK == C_ROUTE ? 8'd6 : CHECK == C_CLEAR ? 8'd7 :
        CHECK == C_EVENT ? 8'd9 : CHECK == C_FLUSH ? 8'd10 : 8'd11;
    generate if (CHECK >= C_SELECT && CHECK <= C_TRIGGER) begin : cmd_cover
        always @(posedge clk) if (past_valid)
            cover($past(command_accepted && command_code == cmd_code_of
                        && (command_payload[k] || selected == k)) && !clear);
    end endgenerate
`endif

    // ---- queue data (bounded) --------------------------------------------------
    // PULL: "tx := TX FIFO head" (C_QUEUE_TX). PUSH: "append rx to RX FIFO"
    // (C_QUEUE_RX). One word pushed into engine k's TX FIFO (by the host or the
    // mover), or one word engine k pushes into its RX FIFO, is tagged and counts
    // the words ahead of it. The tagged TX word is the word that the PULL
    // popping it loads; the tagged RX word is the RX head while nothing is
    // ahead of it. A pop request on an empty queue moves nothing. The FIFO
    // pointers are not observable, so a check starts on a FLUSH of engine k
    // (both queues empty); the host then fills, routes and starts freely.
    generate if (CHECK == C_QUEUE_TX || CHECK == C_QUEUE_RX) begin : queue
        wire tx_side = CHECK == C_QUEUE_TX;
        wire push = tx_side ? tx_push[k] : rx_push[k];
        wire pop = tx_side ? tx_pop[k] : rx_pop[k];
        wire [LW-1:0] level = tx_side ? txl_k : rxl_k;
        wire [31:0] word = tx_side ? tx_data[WIDTH*k +: WIDTH] : rx_data[WIDTH*k +: WIDTH];
        (* anyseq *) reg pick;
        reg q_on = 1'b0;
        reg [LW-1:0] q_pos;
        reg [31:0] q_word;
        always @(posedge clk) begin
            if (!past_valid) assume(fifo_clear[k] && !clear);
            if (clear || fifo_clear[k]) q_on <= 1'b0;
            else if (!q_on && pick && push) begin
                q_on <= 1'b1;
                q_word <= word;
                q_pos <= level - (pop && level != 0);
            end else if (q_on && pop) begin
                if (q_pos == 0) q_on <= 1'b0;
                else q_pos <= q_pos - 1'b1;
            end
        end
        if (CHECK == C_QUEUE_TX) begin : tx_head
            assign tag_head = q_word;
            assign tag_head_known = q_on && q_pos == 0;
        end
`ifndef ISA_COVER
        always @(posedge clk) begin
            if (q_on) assert(q_pos < level);
            if (!tx_side && q_on && q_pos == 0) assert(rx_head[WIDTH*k +: WIDTH] == q_word); // target of: isa_queue_rx_neg
            if (tx_side && past_valid && $past(q_on && q_pos == 0 && pop && !clear))
                assert(tx_k == $past(q_word));                     // target of: isa_queue_tx_neg
        end
`else
        always @(posedge clk) if (past_valid) begin
            if (CHECK == C_QUEUE_TX) cover($past(q_on && q_pos == 0 && pop) && !$past(clear));  // a PULL loads it
            else cover(q_on && q_pos == 0);                                         // it is the RX head
            cover(q_on && q_pos != 0);                                 // it waits behind another word
        end
`endif
    end endgenerate

    // ---- XFER end to end (bounded: bit count <= XA, half-period <= XB) --------
    // "Exactly 2*a transitions occur, each separated by b cycles", the first b
    // cycles after the issue edge. The clock pin starts at CPOL ("sets clock
    // idle on issue") and toggles on every transition; odd transitions are
    // active edges, even ones idle edges. "CPHA0 samples on active edges and
    // shifts output for the next bit on idle edges. CPHA1 drives/shifts on
    // active edges and samples on idle edges. MSB-first samples by shifting rx
    // left; LSB-first samples by shifting rx right." Data bits leave in order
    // from tx (bit 31 first when MSB-first, bit 0 first otherwise). "PC
    // advances at final idle edge. No FIFO operations occur inside XFER."
    generate if (CHECK == C_XFER) begin : xfer
        reg x_on = 1'b0;
        reg [7:0] x_n, x_h, x_ph;
        reg [8:0] x_t;
        reg [4:0] x_c;
        reg [8:0] x_pins;
        reg [31:0] x_tx0, x_rx, x_rx0, x_cnt, x_x, x_y;
        reg [23:0] x_pc;
        reg [7:0] x_lout0, x_len;
        reg [15:0] x_rep;
        wire x_start = issue && w_op == 8'd17 && s_fault == 0;
        wire [2:0] x_ck = x_pins[2:0], x_tp = x_pins[5:3], x_rp = x_pins[8:6];
        wire x_edge = x_ph + 8'd1 == x_h;               // a transition on this edge
        wire [8:0] x_next = x_t + 9'd1;                  // its number
        wire x_samples = x_c[4] && (x_c[1] ? !x_next[0] : x_next[0]);
        wire x_last = x_t == {x_n, 1'b0};                // all 2a transitions done
        wire x_msb = x_c[2];
        function bitof(input [31:0] w, input [8:0] i, input msb);
            bitof = msb ? w[31 - i[4:0]] : w[i[4:0]];
        endfunction
        always @(posedge clk) begin
            if (!x_on && x_start) begin
                x_on <= 1'b1;
                x_n <= img_w[23:16];
                x_h <= img_w[15:8];
                x_c <= img_w[4:0];
                x_ph <= 8'd0;
                x_t <= 9'd0;
                x_pins <= pins_k;
                x_tx0 <= tx_k;
                x_rx <= rx_k;
                x_rx0 <= rx_k;
                x_pc <= pc_k;
                x_cnt <= cnt_k;
                x_lout0 <= lout_k;
                x_len <= len_k;
                x_x <= x_k;
                x_y <= y_k;
                x_rep <= rep_k;
            end else if (x_on) begin
                if (x_last) x_on <= 1'b0;
                else if (x_edge) begin
                    x_ph <= 8'd0;
                    x_t <= x_next;
                    if (x_samples)
                        x_rx <= x_msb ? {x_rx[30:0], synced[x_rp]} : {synced[x_rp], x_rx[31:1]};
                end else x_ph <= x_ph + 8'd1;
            end
        end
        always @(posedge clk) begin
            assume(img_w[31:24] == 8'd17 && img_w[23:16] <= XA && img_w[15:8] <= XB);
            if (x_on) assume(edge_plain);
        end
`ifndef ISA_COVER
        wire [7:0] other = ~((8'd1 << x_ck) | ((x_c[3] ? 8'd1 : 8'd0) << x_tp));
        always @(posedge clk) begin
            if (x_on && !x_last) assert(!tx_pop[k] && !rx_push[k] && !event_clear[k]);
            if (x_on) begin
                assert(run_k && fault_k == 0);
                assert(lout_k[x_ck] == (x_c[0] ^ x_t[0]));         // target of: isa_xfer_e2e_neg
                if (x_c[3] && x_tp != x_ck) begin
                    if (!x_c[1] && !x_last) assert(lout_k[x_tp] == bitof(x_tx0, x_t >> 1, x_msb));
                    if (x_c[1] && x_t == 0) assert(lout_k[x_tp] == x_lout0[x_tp]);
                    if (x_c[1] && x_t != 0) assert(lout_k[x_tp] == bitof(x_tx0, (x_t - 9'd1) >> 1, x_msb));
                end
                assert((lout_k & other) == (x_lout0 & other));
                assert(rx_k == (x_c[4] ? x_rx : x_rx0));
                assert(x_k == x_x && y_k == x_y && rep_k == x_rep && len_k == x_len && pins_k == x_pins);
                if (!x_last) assert(busy_k && pc_k == x_pc && cnt_k == x_cnt);
                else assert(!busy_k && pc_k == x_pc + 24'd1 && cnt_k == x_cnt + 32'd1); // target of: isa_xfer_e2e_neg
            end
        end
`else
        always @(posedge clk) begin
            cover(x_on && x_last && x_c[3] && x_c[4] && x_n == XA && x_h == XB);
            cover(x_on && x_last && x_c[1] && x_c[3] && x_c[4] && x_n == XA);
        end
`endif
    end endgenerate
endmodule
