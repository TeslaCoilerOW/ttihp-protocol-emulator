// Integration invariants over debug observations of the production processor.
// Packed engine arrays put engine zero in the least-significant slice.
//
// Design variants (formal/run.sh --variant): -DASYNC_RESET (every register is
// asynchronously reset by the chip clear, so state already reads as reset
// while `clear` is asserted and next-state checks apply only while it is low);
// -DCLEAR_AT_START (reset synchronizer: the first edge is a clearing edge,
// assumed through `clear` itself because rst_n reaches the clear two edges
// late); DEPTH follows the variant's fifo_words.
module processor_invariants #(parameter WIDTH=32, ENGINES=4, DEPTH=8,
    LEVEL_WIDTH=$clog2(DEPTH)+1, RR_WIDTH=$clog2(ENGINES)) (input wire clk);
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] ui_in, uio_in;
    wire [7:0] uo_out,uio_out,uio_oe;
    wire [ENGINES*8-1:0] owners, drains;
    wire [ENGINES-1:0] events,event_set,event_clear,running,starts,stops,fifo_clear;
    wire clear;
    wire [ENGINES-1:0] grant,eligible,tx_push,tx_pop,rx_push,rx_pop,tx_ready,rx_valid;
    wire [1:0] destination,selected;
    wire [WIDTH-1:0] dma_data;
    wire [WIDTH*ENGINES-1:0] tx_data,rx_head;
    wire [RR_WIDTH-1:0] rr;
    wire [ENGINES*LEVEL_WIDTH-1:0] tx_level,rx_level;
    wire host_tx,host_rx,rx_reserved;
    protocol_processor_debug dut(.clk(clk),.rst_n(rst_n),.ena(ena),.ui_in(ui_in),
        .uio_in(uio_in),.uo_out(uo_out),.uio_out(uio_out),.uio_oe(uio_oe),
        .dbg_ownership(owners),.dbg_open_drain(drains),.dbg_events(events),
        .dbg_event_set(event_set),.dbg_event_clear(event_clear),.dbg_running(running),
        .dbg_clear(clear),.dbg_start(starts),.dbg_stop(stops),.dbg_fifo_clear(fifo_clear),
        .dbg_dma_grant(grant),.dbg_dma_eligible(eligible),.dbg_dma_destination(destination),
        .dbg_dma_data(dma_data),.dbg_round_robin(rr),.dbg_tx_push(tx_push),.dbg_tx_pop(tx_pop),
        .dbg_tx_data(tx_data),.dbg_rx_push(rx_push),.dbg_rx_pop(rx_pop),
        .dbg_tx_ready(tx_ready),.dbg_rx_valid(rx_valid),.dbg_rx_head(rx_head),
        .dbg_tx_level(tx_level),.dbg_rx_level(rx_level),.dbg_host_tx(host_tx),
        .dbg_host_rx(host_rx),.dbg_host_rx_reserved(rx_reserved),.dbg_host_selected(selected));
`ifdef ASYNC_RESET
    wire settled = !clear;
`else
    wire settled = 1'b1;
`endif
    reg past_valid=0;
    reg [7:0] all_owners, all_drains;
    integer i,j;
    always @* begin
        all_owners=0;
        all_drains=0;
        for (integer k=0;k<ENGINES;k=k+1) begin
            all_owners=all_owners | owners[8*k+:8];
            all_drains=all_drains | drains[8*k+:8];
        end
    end
    always @(posedge clk) begin
        past_valid<=1;
`ifdef CLEAR_AT_START
        if (!past_valid) assume(clear);
`else
        if (!past_valid) assume(!rst_n);
`endif
        if (past_valid) begin
            assert((grant & (grant-1)) == 0);
            assert((grant & ~eligible) == 0);
            assert((uio_oe & ~all_owners) == 0);
            assert((uio_out & all_drains) == 0);
            if (clear) assert(uio_oe == 0 && uio_out == 0);
            if ($past(clear)) assert(events == 0);
            else if (settled) assert(events == (($past(events) & ~$past(event_clear)) | $past(event_set)));
`ifdef ASYNC_RESET
            if (clear) assert(events == 0 && running == 0);
`endif
            for (i=0;i<ENGINES;i=i+1) begin
                assert((drains[8*i+:8] & ~owners[8*i+:8]) == 0);
                for (j=i+1;j<ENGINES;j=j+1) assert((owners[8*i+:8] & owners[8*j+:8]) == 0);
                assert(tx_level[LEVEL_WIDTH*i+:LEVEL_WIDTH] <= DEPTH);
                assert(rx_level[LEVEL_WIDTH*i+:LEVEL_WIDTH] <= DEPTH);
                if (grant[i]) begin
                    assert(rx_valid[i] && rx_pop[i]);
                    assert(tx_ready[destination] && tx_push[destination]);
                    assert(!(rx_reserved && selected == i));
                    assert(!(host_tx && selected == destination));
                    assert(dma_data == rx_head[WIDTH*i+:WIDTH]);
                end
                if (tx_push[i] && !(host_tx && selected == i)) begin
                    assert(grant != 0 && destination == i);
                    assert(tx_data[WIDTH*i+:WIDTH] == dma_data);
                end
                if (rx_pop[i] && !(host_rx && selected == i)) assert(grant[i]);
                if ($past(grant[i]) && !$past(clear) && settled) assert(rr == ((i+1) % ENGINES));
            end
        end
    end
endmodule
