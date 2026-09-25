// Queue handshake and reset/stop/fault safety over the production Engine.create.
module engine_safety #(parameter WIDTH=32, ENGINES=4) (input wire clk);
    (* anyseq *) reg clear, start, stop, clear_fault;
    (* anyseq *) reg [31:0] instruction, timestamp;
    (* anyseq *) reg [23:0] image_length;
    (* anyseq *) reg [7:0] ownership, pins;
    (* anyseq *) reg tx_valid, rx_ready, event_pending;
    (* anyseq *) reg [WIDTH-1:0] tx_data;
    wire [23:0] pc;
    wire running, stalled, tx_pop, rx_push, consume_event;
    wire [7:0] fault, pin_values, pin_enables;
    wire [WIDTH-1:0] rx_data;
    wire [ENGINES-1:0] signal_events;
    wire [31:0] completed;
    wire issue;
    wire [23:0] wait_timer, wait_limit, blocked_cycles;
    wire [15:0] repeat_count;
    wire [6:0] transfer_edges;
    protocol_engine dut(.clk(clk),.clear(clear),.start(start),.stop(stop),
        .clear_fault(clear_fault),.instruction(instruction),.image_length(image_length),
        .ownership(ownership),.pins(pins),.timestamp(timestamp),.tx_valid(tx_valid),
        .tx_data(tx_data),.rx_ready(rx_ready),.event_pending(event_pending),.pc(pc),
        .running(running),.fault(fault),.stalled(stalled),.tx_pop(tx_pop),
        .rx_push(rx_push),.rx_data(rx_data),.pin_values(pin_values),
        .pin_enables(pin_enables),.signal_events(signal_events),
        .consume_event(consume_event),.completed(completed),.issue(issue),
        .wait_timer(wait_timer),.wait_limit(wait_limit),.blocked_cycles(blocked_cycles),
        .repeat_count(repeat_count),.transfer_edges(transfer_edges));
    wire [7:0] opcode=instruction[31:24];
    wire strict_push=instruction == 32'h07010000;
    wire legacy_push=instruction == 32'h07000000;
    wire pin_matches=pins[instruction[18:16]] == instruction[8];
    wire waiting_failed=(opcode == 13 && !pin_matches) || (opcode == 15 && !event_pending);
    wire [23:0] effective_limit=wait_limit == 0 ? 24'd65535 : wait_limit;
    reg past_valid=0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(clear);
        if (past_valid) begin
            if (clear || start || stop || !running || fault != 0) begin
                assert(!tx_pop && !rx_push && !consume_event);
                assert(signal_events == 0);
            end
            assert(!tx_pop || tx_valid);
            assert(!rx_push || rx_ready);
            assert(!consume_event || event_pending);
            if (issue && strict_push) begin
                assert(rx_push == rx_ready);
                assert(!stalled);
            end
            if (issue && legacy_push) begin
                assert(rx_push == rx_ready);
                assert(stalled == !rx_ready);
            end
            if (!running || fault != 0) assert(pin_enables == 0);
            if ($past(clear) || $past(stop)) begin
                assert(!running);
                assert(pin_enables == 0);
            end
            if ($past(start) && !$past(stop) && !$past(clear)) begin
                assert(running);
                assert(pc == 0 && completed == 0 && pin_enables == 0);
            end
            if (!$past(clear) && !$past(start) && !$past(clear_fault) && $past(fault) != 0) begin
                assert(fault == $past(fault));
                assert(!running && pin_enables == 0);
            end
            if (!$past(clear) && !$past(start) && !$past(stop)) begin
                if ($past(running) && $past(fault) == 0 && $past(wait_timer) != 0) begin
                    assert(wait_timer == $past(wait_timer)-1);
                    assert(pc == $past(pc));
                end
                if ($past(issue)) begin
                    if ($past(strict_push)) begin
                        assert(rx_data == $past(rx_data));
                        if ($past(rx_ready)) begin
                            assert(fault == 0 && running);
                            assert(pc == $past(pc)+1 && completed == $past(completed)+1);
                            assert(pin_enables == $past(pin_enables));
                        end else begin
                            assert(fault == 4 && !running);
                            assert(pc == $past(pc) && completed == $past(completed));
                            assert(pin_enables == 0 && transfer_edges == 0);
                        end
                    end
                    if ($past(legacy_push) && !$past(rx_ready)) begin
                        assert(fault == 0 && running);
                        assert(rx_data == $past(rx_data));
                        assert(pin_enables == $past(pin_enables));
                    end
                    if ($past(opcode) == 0) begin
                        assert(pc == $past(pc)+1);
                        assert(completed == $past(completed)+1);
                    end
                    if ($past(opcode) == 4) begin
                        assert(pc == $past(pc)+1);
                        assert(wait_timer == $past(instruction[23:0]));
                    end
                    if ($past(opcode) == 5) assert(pc == $past(instruction[23:0]));
                    if (($past(opcode) == 6 && !$past(tx_valid)) ||
                        ($past(opcode) == 7 && !$past(rx_ready))) begin
                        assert(pc == $past(pc));
                        assert(completed == $past(completed));
                    end
                    if ($past(waiting_failed)) begin
                        if ($past(blocked_cycles)+1 >= $past(effective_limit)) begin
                            assert(fault == 3 && !running);
                            assert(pin_enables == 0);
                        end else begin
                            assert(pc == $past(pc));
                            assert(blocked_cycles == $past(blocked_cycles)+1);
                        end
                    end
                end
            end
        end
    end
endmodule
