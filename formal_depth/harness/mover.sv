// FIFO-mover conservation across all engines (docs/architecture.md "Autonomy
// and congestion", docs/isa.md ROUTE/FLUSH/"DMA decrements descriptor count
// only after an accepted RX-to-TX word transfer").
//
// Whole processor (protocol_processor_fd) with the FIFO storage exposed.
// Two symbolic "tagged" words are followed end to end (a data-independence
// argument: the tags are arbitrary words pushed at arbitrary times, so the
// claims hold for every word). A tag is armed when source engine S (any
// constant) pushes a word into its RX FIFO and the free input arm_t is high;
// tag 1 can only be armed after tag 0, so tag 0 is the older word.
//   RX stage: the word stays in RX_S at the slot it was written to, inside the
//             FIFO's occupied window, with its value, until it is popped.
//   Pop:      a pop of that slot is either a host read (the host then owns
//             the word, see host_protocol.sv) or a mover grant, which must
//             push the same value into TX_D, D = S's route destination, in
//             the same cycle, and the push must be accepted.
//   TX stage: the word stays at its TX_D slot with its value until engine D's
//             PULL pops it; the engine's tx register then holds the value.
//   Drop:     only an explicit FLUSH of the FIFO holding it, or reset.
// Claims:
//   spec_tag_*    the above for both tags (no loss, no corruption, the
//                 destination is the route's, the push is accepted);
//   spec_order_*  tag 0 leaves RX_S before tag 1; if both went to the same
//                 TX queue, tag 0 is ahead of tag 1 there and is pulled first;
//   spec_grant_*  every grant pops its source and pushes its destination in
//                 the same cycle (hence no duplication), only with quota left;
//   spec_quota    the route count changes only by ROUTE (set), FLUSH (zero)
//                 or an accepted move (minus one), and never underflows;
//   spec_single   at most one grant per cycle.
// The common invariants (fd_invariants.vh: FIFO pointer/level consistency,
// image and ownership invariants) strengthen the induction. -DFD_FROM_ANY
// starts from an arbitrary state that satisfies them instead of from reset
// (for deep BMC and quick witnesses).
module mover (input wire clk);
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] ui_in, uio_in;
    (* anyseq *) reg arm0, arm1;
    (* anyconst *) reg [1:0] S;
`include "fd_dut.vh"

    wire clear = !rst_n || !ena;
    wire [1:0] sel = dbg_host_selected;
    reg past_valid = 0;

    wire [1023:0] rx_mem = {fd_rx_mem_3_flat, fd_rx_mem_2_flat, fd_rx_mem_1_flat, fd_rx_mem_0_flat};
    wire [1023:0] tx_mem = {fd_tx_mem_3_flat, fd_tx_mem_2_flat, fd_tx_mem_1_flat, fd_tx_mem_0_flat};
    function automatic [31:0] word(input [1023:0] mem, input [1:0] e, input [2:0] slot);
        word = mem[256*e + 32*slot +: 32];
    endfunction

    // Accepted FIFO operations (FIFO clear = reset or FLUSH of that engine).
    wire [3:0] rx_full, tx_full, rx_empty, tx_empty, rx_put, rx_take, tx_put, tx_take;
    generate for (genvar k = 0; k < 4; k = k + 1) begin : fifo
        wire [3:0] rxl = dbg_rx_level[4*k+:4], txl = dbg_tx_level[4*k+:4];
        assign rx_full[k] = rxl == 8; assign tx_full[k] = txl == 8;
        assign rx_empty[k] = rxl == 0; assign tx_empty[k] = txl == 0;
        wire fclr = clear || dbg_fifo_clear[k];
        assign rx_put[k] = dbg_rx_push[k] && !rx_full[k] && !fclr;
        assign rx_take[k] = dbg_rx_pop[k] && !rx_empty[k] && !fclr;
        assign tx_put[k] = dbg_tx_push[k] && !tx_full[k] && !fclr;
        assign tx_take[k] = dbg_tx_pop[k] && !tx_empty[k] && !fclr;
    end endgenerate

    // ---------------- tags
    localparam IDLE = 0, IN_RX = 1, IN_TX = 2, PULLED = 3, HOST = 4, DROPPED = 5;
    reg [2:0] st0 = IDLE, st1 = IDLE;
    reg [2:0] slot0 = 0, slot1 = 0;
    reg [1:0] d0 = 0, d1 = 0;
    reg [31:0] v0 = 0, v1 = 0;
    reg moved0 = 0, moved1 = 0;

    wire [2:0] rx_wr_s = fd_rx_wr[3*S+:3], rx_rd_s = fd_rx_rd[3*S+:3];
    wire [31:0] push_val = dbg_rx_data[32*S+:32];
    wire arming0 = st0 == IDLE && arm0 && rx_put[S];
    wire arming1 = st1 == IDLE && st0 != IDLE && arm1 && rx_put[S];
    wire [1:0] dest = dbg_dma_destination;

    // Next state of one tag, packed {st, slot, d, v, moved}; one function keeps
    // both tags identical.
    function automatic [40:0] advance(input [2:0] st, input [2:0] slot, input [1:0] d,
                                      input [31:0] v, input moved, input arming);
        reg [2:0] nst, nslot; reg [1:0] nd; reg [31:0] nv; reg nmoved;
        begin
            nst = st; nslot = slot; nd = d; nv = v; nmoved = moved;
            // Reset drops a tag in flight; an unarmed tag stays armable (the
            // first cycle is a reset, so dropping IDLE tags would make every
            // tag claim vacuous from reset).
            if (clear) nst = (st == IDLE) ? IDLE : DROPPED;
            else case (st)
                IDLE: if (arming) begin nst = IN_RX; nslot = rx_wr_s; nv = push_val; end
                IN_RX:
                    if (dbg_fifo_clear[S]) nst = DROPPED;
                    else if (rx_take[S] && rx_rd_s == slot) begin
                        if (dbg_dma_grant[S]) begin
                            nst = IN_TX; nd = dest; nslot = fd_tx_wr[3*dest+:3]; nmoved = 1;
                        end else nst = HOST;
                    end
                IN_TX:
                    if (dbg_fifo_clear[d]) nst = DROPPED;
                    else if (tx_take[d] && fd_tx_rd[3*d+:3] == slot) nst = PULLED;
                default: ;
            endcase
            advance = {nst, nslot, nd, nv, nmoved};
        end
    endfunction
    always @(posedge clk) begin
        {st0, slot0, d0, v0, moved0} <= advance(st0, slot0, d0, v0, moved0, arming0);
        {st1, slot1, d1, v1, moved1} <= advance(st1, slot1, d1, v1, moved1, arming1);
    end

    function automatic [2:0] rank(input [2:0] slot, input [2:0] rd);
        rank = slot - rd;
    endfunction
    wire [2:0] rxrank0 = rank(slot0, rx_rd_s), rxrank1 = rank(slot1, rx_rd_s);
    wire [2:0] txrank0 = rank(slot0, fd_tx_rd[3*d0+:3]), txrank1 = rank(slot1, fd_tx_rd[3*d1+:3]);

    // ---------------- properties
    always @(posedge clk) begin
        past_valid <= 1;
`ifndef FD_FROM_ANY
        if (!past_valid) assume(!rst_n);
`endif
    end

    // Per-tag claims (identical for both tags).
    always @(posedge clk) if (past_valid) begin
        if (st0 == IN_RX) begin
            spec_tag0_rx_value: assert(word(rx_mem, S, slot0) == v0);
            spec_tag0_rx_window: assert({1'b0, rxrank0} < dbg_rx_level[4*S+:4]);
            if (rx_rd_s == slot0) spec_tag0_rx_head: assert(dbg_rx_head[32*S+:32] == v0);
            if (!clear && !dbg_fifo_clear[S] && rx_take[S] && rx_rd_s == slot0 && dbg_dma_grant[S]) begin
                spec_tag0_move_dest: assert(dest == dbg_route_destination[2*S+:2]);
                spec_tag0_move_push: assert(tx_put[dest] && dbg_tx_data[32*dest+:32] == v0);
            end
            if (!clear && !dbg_fifo_clear[S] && rx_take[S] && rx_rd_s == slot0 && !dbg_dma_grant[S])
                spec_tag0_host_read: assert(dbg_host_rx && sel == S);
        end
        if (st0 == IN_TX) begin
            spec_tag0_tx_value: assert(word(tx_mem, d0, slot0) == v0);
            spec_tag0_tx_window: assert({1'b0, txrank0} < dbg_tx_level[4*d0+:4]);
            if (fd_tx_rd[3*d0+:3] == slot0) spec_tag0_tx_head: assert(fd_tx_head[32*d0+:32] == v0);
        end
    end
    always @(posedge clk) if (past_valid) begin
        if (st1 == IN_RX) begin
            spec_tag1_rx_value: assert(word(rx_mem, S, slot1) == v1);
            spec_tag1_rx_window: assert({1'b0, rxrank1} < dbg_rx_level[4*S+:4]);
            if (rx_rd_s == slot1) spec_tag1_rx_head: assert(dbg_rx_head[32*S+:32] == v1);
            if (!clear && !dbg_fifo_clear[S] && rx_take[S] && rx_rd_s == slot1 && dbg_dma_grant[S]) begin
                spec_tag1_move_dest: assert(dest == dbg_route_destination[2*S+:2]);
                spec_tag1_move_push: assert(tx_put[dest] && dbg_tx_data[32*dest+:32] == v1);
            end
            if (!clear && !dbg_fifo_clear[S] && rx_take[S] && rx_rd_s == slot1 && !dbg_dma_grant[S])
                spec_tag1_host_read: assert(dbg_host_rx && sel == S);
        end
        if (st1 == IN_TX) begin
            spec_tag1_tx_value: assert(word(tx_mem, d1, slot1) == v1);
            spec_tag1_tx_window: assert({1'b0, txrank1} < dbg_tx_level[4*d1+:4]);
            if (fd_tx_rd[3*d1+:3] == slot1) spec_tag1_tx_head: assert(fd_tx_head[32*d1+:32] == v1);
        end
    end
    // Delivery: the engine's PULL leaves the tagged value in its tx register.
    always @(posedge clk) if (past_valid) begin
        if ($past(st0 == IN_TX && !clear && !dbg_fifo_clear[d0] && tx_take[d0] && fd_tx_rd[3*d0+:3] == slot0))
            spec_tag0_pulled: assert(fv_tx[32*d0+:32] == v0);
        if ($past(st1 == IN_TX && !clear && !dbg_fifo_clear[d1] && tx_take[d1] && fd_tx_rd[3*d1+:3] == slot1))
            spec_tag1_pulled: assert(fv_tx[32*d1+:32] == v1);
    end

    // Order between the two tags (tag 0 is older).
    always @(posedge clk) if (past_valid) begin
`ifndef FD_SPEC_ONLY
        link_tag_encoding: assert(st0 <= DROPPED && st1 <= DROPPED);
        link_tag0_moved: assert(moved0 == (st0 == IN_TX || st0 == PULLED || (st0 == DROPPED && moved0)));
        link_tag1_moved: assert(moved1 == (st1 == IN_TX || st1 == PULLED || (st1 == DROPPED && moved1)));
`endif
        spec_order_armed: assert(st1 == IDLE || st0 != IDLE);
        if (st0 == IN_RX && st1 == IN_RX) spec_order_rx: assert(rxrank0 < rxrank1);
        if (st1 == IN_TX || st1 == PULLED || st1 == HOST) spec_order_left_rx: assert(st0 != IN_RX);
        if (st0 == IN_TX && st1 == IN_TX && d0 == d1) spec_order_tx: assert(txrank0 < txrank1);
        if (st1 == PULLED && moved0 && d0 == d1) spec_order_pulled: assert(st0 == PULLED || st0 == DROPPED);
    end

    // Mover handshake and quota, for every source.
    generate for (genvar k = 0; k < 4; k = k + 1) begin : src
        wire edit = dbg_command_accepted && dbg_command_code == 6 && dbg_command_payload[1:0] == k;
        wire flush = dbg_command_accepted && dbg_command_code == 10
                     && (sel == k || sel == dbg_route_destination[2*k+:2]);
        wire [15:0] count = dbg_route_count[16*k+:16];
        always @(posedge clk) if (past_valid) begin
            if (dbg_dma_grant[k]) begin
                assert(count != 0);  // spec_grant_quota
                assert(rx_take[k]);  // spec_grant_pop
                assert(tx_put[dbg_route_destination[2*k+:2]]  // spec_grant_push
                                        && dest == dbg_route_destination[2*k+:2]);
                assert(dbg_tx_data[32*dest+:32] == dbg_rx_head[32*k+:32]);  // spec_grant_data
            end
            if (rx_take[k] && !(dbg_host_rx && sel == k)) assert(dbg_dma_grant[k]);  // spec_pop_is_grant
            if (tx_put[k] && !(dbg_host_tx && sel == k)) assert(dbg_dma_grant != 0 && dest == k);  // spec_push_is_grant
            if (!$past(clear))
                assert(count ==  // spec_quota
                    ($past(edit) ? ($past(dbg_command_payload[4]) ? $past(dbg_command_payload[20:5]) : 16'd0) :
                     $past(flush) ? 16'd0 :
                     $past(count) - $past(dbg_dma_grant[k])));
        end
    end endgenerate
    always @(posedge clk) if (past_valid) spec_single: assert((dbg_dma_grant & (dbg_dma_grant - 1)) == 0);

    // ---------------- non-vacuity witnesses
    always @(posedge clk) if (past_valid) begin
        cover_tag_moved: cover(st0 == IN_TX);
        cover_tag_pulled: cover(st0 == PULLED && moved0);
        cover_two_tags_pulled_in_order: cover(st1 == PULLED && st0 == PULLED && moved0 && moved1 && d0 == d1);
        cover_quota_exhausted: cover($past(dbg_dma_grant[S]) && dbg_route_count[16*S+:16] == 0);
    end
`include "fd_invariants.vh"
endmodule
