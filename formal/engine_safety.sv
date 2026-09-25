// Queue handshake and reset/stop/fault safety over the production Engine.create.
//
// Design variants (formal/run.sh --variant) add, with their option set:
//   PCW=7, -DPC_SAT    7-bit PC: JMP, LOOP (repeat count non-zero) and taken
//                      JZ targets >= 128 saturate to 127
//   -DNO_COUNTERS      no completed-instruction counter: `completed` stays 0
//   -DASYNC_RESET      `clear` is an asynchronous reset: while it is asserted
//                      the engine already reads as reset, so next-state checks
//                      apply only while it is low (and the reset state is
//                      asserted while it is high)
//   -DBYTE_LANE        SHL/SHR with a count that is not a byte lane fault with
//                      code 1; byte-lane counts complete normally, and a lane
//                      shift of RX (register 1) gives the shifted value
// Non-vacuity controls for the variant-only assertions (run by hand, each must
// FAIL): -DVNEG_LOOP (a saturating LOOP lands on target[6:0]), -DVNEG_JZ (a JZ
// to 128 or more never branches), -DVNEG_LANE (a lane shift of RX leaves it
// unchanged).
module engine_safety #(parameter WIDTH=32, ENGINES=4, PCW=24) (input wire clk);
    (* anyseq *) reg clear, start, stop, clear_fault;
    (* anyseq *) reg [31:0] instruction, timestamp;
    (* anyseq *) reg [23:0] image_length;
    (* anyseq *) reg [7:0] ownership, pins;
    (* anyseq *) reg tx_valid, rx_ready, event_pending;
    (* anyseq *) reg [WIDTH-1:0] tx_data;
    wire [PCW-1:0] pc;
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
`ifdef ASYNC_RESET
    wire settled = !clear;
`else
    wire settled = 1'b1;
`endif
`ifdef PC_SAT
    function [23:0] saturate(input [23:0] target);
        saturate = target >= (1 << PCW) ? (1 << PCW) - 1 : target;
    endfunction
`endif
`ifdef BYTE_LANE
    // Could issue this cycle (Engine.create's issue without the decode check).
    wire [PCW-1:0] image_limit = image_length[PCW-1:0];
    wire could_issue = running && fault == 0 && !start && !stop && !clear
        && wait_timer == 0 && transfer_edges == 0 && pc < image_limit;
    wire shift_op = opcode == 24 || opcode == 25;
    wire shift_operands = instruction[23:16] < 4 && instruction[15:8] == 0;
    wire lane_count = instruction[2:0] == 0 && instruction[7:0] < WIDTH;
`endif
    reg past_valid=0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(clear);
`ifdef ASYNC_RESET
        if (past_valid && clear) begin
            assert(!running && fault == 0 && pc == 0 && pin_enables == 0);
            assert(completed == 0 && wait_timer == 0 && transfer_edges == 0);
        end
`endif
`ifdef BYTE_LANE
        if (past_valid && settled && !$past(clear) && !$past(start) && !$past(stop)
            && $past(could_issue) && $past(shift_op) && $past(shift_operands)) begin
            if (!$past(lane_count)) begin
                assert(fault == 1 && !running && pin_enables == 0);
                assert(pc == $past(pc));
            end else begin
                assert(fault == 0 && running && pc == $past(pc)+1);
            end
        end
`endif
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
            if (settled && $past(start) && !$past(stop) && !$past(clear)) begin
                assert(running);
                assert(pc == 0 && completed == 0 && pin_enables == 0);
            end
            if (settled && !$past(clear) && !$past(start) && !$past(clear_fault) && $past(fault) != 0) begin
                assert(fault == $past(fault));
                assert(!running && pin_enables == 0);
            end
            if (settled && !$past(clear) && !$past(start) && !$past(stop)) begin
                if ($past(running) && $past(fault) == 0 && $past(wait_timer) != 0) begin
                    assert(wait_timer == $past(wait_timer)-1);
                    assert(pc == $past(pc));
                end
                if ($past(issue)) begin
                    if ($past(strict_push)) begin
                        assert(rx_data == $past(rx_data));
                        if ($past(rx_ready)) begin
                            assert(fault == 0 && running);
`ifdef NO_COUNTERS
                            assert(pc == $past(pc)+1 && completed == 0);
`else
                            assert(pc == $past(pc)+1 && completed == $past(completed)+1);
`endif
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
`ifdef NO_COUNTERS
                        assert(completed == 0);
`else
                        assert(completed == $past(completed)+1);
`endif
                    end
                    if ($past(opcode) == 4) begin
                        assert(pc == $past(pc)+1);
                        assert(wait_timer == $past(instruction[23:0]));
                    end
`ifdef PC_SAT
                    if ($past(opcode) == 5) assert(pc == saturate($past(instruction[23:0])));
                    // LOOP (repeat count non-zero) and taken JZ saturate like JMP.
                    if ($past(opcode) == 11) begin
                        if ($past(repeat_count) != 0) begin
`ifdef VNEG_LOOP
                            assert(pc == $past(instruction[PCW-1:0]));
`else
                            assert(pc == saturate($past(instruction[23:0])));
`endif
                            assert(repeat_count == $past(repeat_count)-1);
                        end else begin
                            assert(pc == $past(pc)+1 && repeat_count == 0);
                        end
                    end
`ifdef VNEG_JZ
                    if ($past(opcode) == 26 && $past(instruction[15:0]) >= 128) assert(pc == $past(pc)+1);
`else
                    if ($past(opcode) == 26)
                        assert(pc == $past(pc)+1 || pc == saturate({8'd0, $past(instruction[15:0])}));
`endif
`else
                    if ($past(opcode) == 5) assert(pc == $past(instruction[23:0]));
`endif
`ifdef BYTE_LANE
                    // A lane shift of RX (register 1): the lane-shifted value.
                    if (($past(opcode) == 24 || $past(opcode) == 25) && $past(instruction[23:16]) == 1) begin
`ifdef VNEG_LANE
                        assert(rx_data == $past(rx_data));
`else
                        if ($past(opcode) == 24) assert(rx_data == ($past(rx_data) << $past(instruction[7:0])));
                        else assert(rx_data == ($past(rx_data) >> $past(instruction[7:0])));
`endif
                    end
`endif
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
