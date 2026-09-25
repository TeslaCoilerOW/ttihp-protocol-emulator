// Arbitrary-valid-state preservation over production processor debug observations.
// Packed engine arrays put engine zero in the least-significant slice.
module processor_inductive #(parameter WIDTH=32, ENGINES=4, DEPTH=8,
    LEVEL_WIDTH=$clog2(DEPTH)+1, RR_WIDTH=$clog2(ENGINES)) (input wire clk, output wire cover_dma, output wire cover_event);
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] ui_in, uio_in;
    wire [7:0] uo_out,uio_out,uio_oe;
    wire [ENGINES*8-1:0] owners, drains;
    wire [ENGINES-1:0] events,event_set,event_clear,running,starts,stops,fifo_clear;
    wire clear;
    wire [ENGINES-1:0] grant,eligible,tx_push,tx_pop,rx_push,rx_pop,tx_ready,rx_valid;
    wire [1:0] destination,selected;
    wire [2*ENGINES-1:0] route_destination;
    wire [WIDTH-1:0] dma_data;
    wire [WIDTH*ENGINES-1:0] tx_data,rx_head;
    wire [RR_WIDTH-1:0] rr;
    wire [ENGINES*LEVEL_WIDTH-1:0] tx_level,rx_level;
    wire host_tx,host_rx,rx_reserved;
    wire [6*ENGINES-1:0] trigger_config;
    wire [ENGINES-1:0] trigger_event,trigger_update,trigger_detect;
    wire [7:0] synced_pins,previous_pins,command_code;
    wire [23:0] command_payload;
    wire command_accepted;
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
        .dbg_host_rx(host_rx),.dbg_host_rx_reserved(rx_reserved),.dbg_host_selected(selected),
        .dbg_route_destination(route_destination),.dbg_trigger_config(trigger_config),
        .dbg_trigger_event(trigger_event),.dbg_synced_pins(synced_pins),
        .dbg_previous_pins(previous_pins),.dbg_command_accepted(command_accepted),
        .dbg_command_code(command_code),.dbg_command_payload(command_payload));
    generate for (genvar k=0;k<ENGINES;k=k+1) begin : trigger_observations
        wire pin_now=synced_pins[trigger_config[6*k+:3]];
        wire pin_before=previous_pins[trigger_config[6*k+:3]];
        wire [1:0] mode=trigger_config[6*k+3+:2];
        assign trigger_detect[k]=mode == 0 ? (pin_now && !pin_before) :
                                 mode == 1 ? (!pin_now && pin_before) :
                                 mode == 2 ? pin_now : !pin_now;
        assign trigger_update[k]=command_accepted && command_code == 11 && selected == k;
    end endgenerate
    assign cover_dma=(grant != 0);
    assign cover_event=(event_clear != 0);
    // Active-context witness (formerly `sat -set-at 1 cover_dma 1 -set-at 1
    // cover_event 1`): a mover grant and an event consumption in one cycle.
    always @(posedge clk) if (past_valid) cover(cover_dma && cover_event);
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
        // Induction hypotheses constrain only the invariants being preserved.
        // The two-bit host/route engine indices must also denote present engines.
        // Reset initializes them to zero; SELECT/ROUTE reject absent engines.
        // Other program, running, FIFO-word, event, host and route-count state is
        // arbitrary, so active transactions exist at the first edge.
        if (!past_valid) begin
            assume(selected < ENGINES);
            for (i=0;i<ENGINES;i=i+1) begin
                assume(route_destination[2*i+:2] < ENGINES);
                assume((drains[8*i+:8] & ~owners[8*i+:8]) == 0);
                for (j=i+1;j<ENGINES;j=j+1) assume((owners[8*i+:8] & owners[8*j+:8]) == 0);
                assume(tx_level[LEVEL_WIDTH*i+:LEVEL_WIDTH] <= DEPTH);
                assume(rx_level[LEVEL_WIDTH*i+:LEVEL_WIDTH] <= DEPTH);
            end
        end
        if (past_valid) begin
            assert(selected < ENGINES);
            assert((grant & (grant-1)) == 0);
            assert((grant & ~eligible) == 0);
            assert((uio_oe & ~all_owners) == 0);
            assert((uio_out & all_drains) == 0);
            if (clear) assert(uio_oe == 0 && uio_out == 0);
            assert((trigger_event & ~event_set) == 0);
            if (clear) assert(trigger_event == 0);
            if ($past(clear)) begin
                assert(events == 0);
                assert(trigger_config == 0 && previous_pins == 0 && synced_pins == 0);
            end else begin
                assert(events == (($past(events) & ~$past(event_clear)) | $past(event_set)));
                assert(previous_pins == $past(synced_pins));
            end
            for (i=0;i<ENGINES;i=i+1) begin
                assert(trigger_event[i] == (!clear && trigger_config[6*i+5] && trigger_detect[i] && !trigger_update[i]));
                if (!$past(clear)) begin
                    if ($past(trigger_update[i])) assert(trigger_config[6*i+:6] == $past(command_payload[5:0]));
                    else assert(trigger_config[6*i+:6] == $past(trigger_config[6*i+:6]));
                end
                if (trigger_update[i]) begin
                    assert(!running[i]);
                    assert(command_payload[23:6] == 0);
                end
                assert(route_destination[2*i+:2] < ENGINES);
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
                if ($past(grant[i]) && !$past(clear)) assert(rr == ((i+1) % ENGINES));
            end
        end
    end
endmodule
