// Host-command specification: one property module per window-0 command that
// changes engine or queue state (docs/isa.md, "Host interface"). Written from
// docs/isa.md only.
//
// isa_spec.sv instantiates a module once per engine j and feeds it engine j's
// observed state; each module asserts, on the edge that accepts its command,
// (a) that the command was legal ("Invalid host commands reject atomically"),
// and (b) on the next cycle, engine j's state after the command. An accepted
// command is the processor's dbg_command_accepted with dbg_command_code and
// dbg_command_payload; the nibble transport that assembles the word is not
// part of this specification. READ_SELECT changes only read-back state and has
// no module.

`define ISA_CMD_PORTS \
    input wire clk, input wire past_valid, input wire [1:0] j, \
    input wire accepted, input wire [7:0] code, input wire [23:0] payload, \
    input wire [1:0] sel, \
    input wire run, input wire [7:0] fault, input wire [23:0] pc, \
    input wire [31:0] tx, rx, x, y, input wire [15:0] rep, \
    input wire [23:0] hold, limit, blk, input wire [7:0] lout, len, \
    input wire [8:0] pins, input wire [31:0] cnt, input wire busy, \
    input wire ivalid, iwriting, input wire [15:0] iloaded, ilen, \
    input wire [7:0] own, od, own_others, \
    input wire [3:0] txl, rxl, \
    input wire ev, ev_set, ev_clear, tx_pop, rx_push, fifo_clear, \
    input wire [5:0] trig, input wire [15:0] route_count, input wire [1:0] route_dst

// Engine j's execution state except the fields a command sets.
`define ISA_SAME_EXEC \
    (pc == $past(pc) && tx == $past(tx) && rx == $past(rx) && x == $past(x) \
     && y == $past(y) && rep == $past(rep) && limit == $past(limit) \
     && pins == $past(pins) && cnt == $past(cnt))

// |0|SELECT low2 engine|
module isa_cmd_select (`ISA_CMD_PORTS);
    wire hit = accepted && code == 8'd0;
    always @(posedge clk)
        if (past_valid && $past(hit)) assert(sel == $past(payload[1:0])); // target of: isa_cmd_select_neg
endmodule

// |1|BEGIN selected engine: stop, invalidate image, program write address=0|
module isa_cmd_begin (`ISA_CMD_PORTS);
    wire hit = accepted && code == 8'd1 && sel == j;
    always @(posedge clk)
        if (past_valid && $past(hit)) begin
            assert(!run && !ivalid);
            assert(iwriting && iloaded == 0);                      // target of: isa_cmd_begin_neg
        end
endmodule

// |2|COMMIT low16 length: accept only contiguous fully written image of that
// length, nonzero and <= capacity|
// Reading: the image [0, length) has been written since BEGIN (at least
// `length` words loaded) and loading is still open.
module isa_cmd_commit (`ISA_CMD_PORTS);
    wire hit = accepted && code == 8'd2 && sel == j;
    always @(posedge clk) begin
        if (hit) begin
            assert(iwriting);
            assert(payload[15:0] != 0 && payload[15:0] <= 16'd64);
            assert(iloaded >= payload[15:0]);
        end
        if (past_valid && $past(hit)) begin
            assert(ivalid && ilen == $past(payload[15:0]));        // target of: isa_cmd_commit_neg
            assert(!iwriting);
        end
    end
endmodule

// |3|OWN low8 pin ownership, next8 open-drain mask: halted only; reject
// ownership overlap and open-drain bits outside ownership|
module isa_cmd_own (`ISA_CMD_PORTS);
    wire any = accepted && code == 8'd3;
    wire hit = any && sel == j;
    always @(posedge clk) begin
        if (hit) begin
            assert(!run);
            assert((payload[7:0] & own_others) == 0);
            assert((payload[15:8] & ~payload[7:0]) == 0);
        end
        if (past_valid && $past(hit)) assert(own == $past(payload[7:0]) && od == $past(payload[15:8])); // target of: isa_cmd_own_neg
        if (past_valid && $past(any) && !$past(hit)) assert(own == $past(own) && od == $past(od));
    end
endmodule

// |4|START low engine-count-bit mask: all selected engines must have committed
// image and no fault; synchronous start|
// "START resets PC, registers, local timers, repeat state and local logical
// outputs; queues remain available for prefill. START does not change
// ownership, open-drain configuration or image validity." "Start is a
// higher-priority event than ordinary execution and starts execution on the
// following edge." "Local logical outputs" are read as the logical values
// and enables. START's list does not name the completed-instruction count,
// LIMIT or the PINS configuration; the RTL clears the count and PINS and
// restores LIMIT to its reset default 65535, and the spec follows that
// resolution (docs/isa-spec.md, disagreements D2 and D3).
module isa_cmd_start (`ISA_CMD_PORTS);
    wire hit = accepted && code == 8'd4 && payload[j];
    always @(posedge clk) begin
        if (hit) begin
            assert(ivalid && fault == 0);
            assert(!fifo_clear && !tx_pop && !rx_push && !ev_clear);
        end
        if (past_valid && $past(hit)) begin
            assert(run && fault == 0 && pc == 0);
            assert(tx == 0 && rx == 0 && x == 0 && y == 0);        // target of: isa_cmd_start_neg
            assert(hold == 0 && blk == 0 && !busy);
            assert(rep == 0);
            assert(lout == 0 && len == 0);
            assert(limit == 24'd65535);
            assert(pins == 0);
            assert(cnt == 0);
            assert(ivalid && ilen == $past(ilen) && own == $past(own) && od == $past(od));
            assert(ev == ($past(ev) || $past(ev_set)));
        end
    end
endmodule

// |5|STOP low engine-count-bit mask|
// Reading: STOP takes effect instead of any instruction on its edge; the
// engine keeps its execution state (PC, registers, counters, fault).
module isa_cmd_stop (`ISA_CMD_PORTS);
    wire hit = accepted && code == 8'd5 && payload[j];
    always @(posedge clk) begin
        if (hit) assert(!tx_pop && !rx_push && !ev_clear);
        if (past_valid && $past(hit)) begin
            assert(!run);                                          // target of: isa_cmd_stop_neg
            assert(fault == $past(fault));
            assert(`ISA_SAME_EXEC);
            assert(hold == $past(hold) && blk == $past(blk));
        end
    end
endmodule

// |6|ROUTE bits1:0 source engine,3:2 destination engine,bit4 enable, bits20:5
// word count; one descriptor per source; zero count disables; invalid
// endpoints reject|
module isa_cmd_route (`ISA_CMD_PORTS);
    wire hit = accepted && code == 8'd6 && payload[1:0] == j;
    always @(posedge clk)
        if (past_valid && $past(hit)) begin
            assert(route_count == ($past(payload[4]) ? $past(payload[20:5]) : 16'd0)); // target of: isa_cmd_route_neg
            if ($past(payload[4]) && $past(payload[20:5]) != 0) assert(route_dst == $past(payload[3:2]));
        end
endmodule

// |7|CLEAR low engine-count-bit mask: clear engine faults while halted|
module isa_cmd_clear (`ISA_CMD_PORTS);
    wire hit = accepted && code == 8'd7 && payload[j];
    always @(posedge clk) begin
        if (hit) assert(!run);
        if (past_valid && $past(hit)) begin
            assert(fault == 0);                                    // target of: isa_cmd_clear_neg
            assert(!run && `ISA_SAME_EXEC);
        end
    end
endmodule

// |9|EVENT low engine-count-bit destination mask|
module isa_cmd_event (`ISA_CMD_PORTS);
    wire hit = accepted && code == 8'd9 && payload[j];
    always @(posedge clk)
        if (past_valid && $past(hit)) assert(ev);                  // target of: isa_cmd_event_neg
endmodule

// |10|FLUSH selected engine queues while halted; disable routes touching it|
// This instance also checks the route whose source is engine j.
module isa_cmd_flush (`ISA_CMD_PORTS);
    wire any = accepted && code == 8'd10;
    wire hit = any && sel == j;
    always @(posedge clk) begin
        if (hit) assert(!run);
        if (past_valid && $past(hit)) assert(txl == 0 && rxl == 0); // target of: isa_cmd_flush_neg
        if (past_valid && $past(any) && ($past(sel) == j || $past(route_dst) == $past(sel)))
            assert(route_count == 0);
    end
endmodule

// |11|TRIGGER selected engine while halted: bits2:0 observed pin,4:3 mode (0
// rising,1 falling,2 high,3 low),5 enable; higher bits zero|
module isa_cmd_trigger (`ISA_CMD_PORTS);
    wire hit = accepted && code == 8'd11 && sel == j;
    always @(posedge clk) begin
        if (hit) assert(!run && payload[23:6] == 0);
        if (past_valid && $past(hit)) assert(trig == $past(payload[5:0])); // target of: isa_cmd_trigger_neg
    end
endmodule
