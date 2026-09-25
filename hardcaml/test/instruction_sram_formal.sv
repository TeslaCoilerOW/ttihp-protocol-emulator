// Exact vendor FUNCTIONAL models must be read with these generated wrappers.
// No SRAM contents or output values are initialized/assumed. The watch address
// denotes any of all 64 words; data becomes known only after an accepted write.
module instruction_sram_watch (
    input wire clk,clear,write,
    input wire [5:0] write_address,address,
    input wire [23:0] next_pc,
    input wire [31:0] write_data,instruction,
    input wire memory_enable,write_enable,read_enable,
    output reg written,known,read_seen,
    output wire [5:0] watched_address,
    output reg [5:0] read_address,
    output reg [31:0] last_word);
    (* anyconst *) reg [5:0] watched;
    assign watched_address=watched;
    reg [31:0] expected_word;
    reg past_valid=0;
    initial begin
        written=0;
        known=0;
        read_seen=0;
    end
    always @(posedge clk) begin
        past_valid<=1;
        assert(memory_enable == !clear);
        assert(write_enable == (write && !clear));
        assert(read_enable == !write_enable);
        assert(address == (write_enable ? write_address : next_pc[5:0]));
        assert(!(write_enable && read_enable));
        if (write_enable && address == watched) begin
            last_word<=write_data;
            written<=1;
        end
        if (memory_enable && read_enable) begin
            read_seen<=1;
            read_address<=address;
            known<=written && address == watched;
            expected_word<=last_word;
        end
        if (past_valid) begin
            if (known) assert(instruction == expected_word);
            if (!$past(memory_enable && read_enable))
                assert(instruction == $past(instruction));
        end
    end
endmodule

// Bounded adapter proof, with no restrictions on external controls.
module instruction_sram_adapter_properties(input wire clk,output wire cover_read);
    (* anyseq *) reg clear,write;
    (* anyseq *) reg [5:0] write_address;
    (* anyseq *) reg [23:0] next_pc;
    (* anyseq *) reg [31:0] write_data;
    wire [31:0] instruction,last_word;
    wire [5:0] address,watched_address,read_address;
    wire memory_enable,write_enable,read_enable,written,known,read_seen;
    protocol_instruction_sram_adapter dut(.*);
    instruction_sram_watch watch(.*);
    assign cover_read=known;
endmodule

// RESET_BASE=1: bounded engine + written-instruction proof from first-edge reset.
// RESET_BASE=0: conditional address-alignment preservation only. Initial tag state
// may be arbitrary, constrained solely by the claimed running=>alignment
// invariant. Memory data equivalence is deliberately not assumed or asserted
// in that separate preservation proof.
module instruction_sram_engine_properties #(parameter WIDTH=32,RESET_BASE=1)
    (input wire clk,output reg cover_start,cover_branch,cover_xfer,
     output reg cover_stall,output wire cover_read);
    initial begin
        cover_start=0;
        cover_branch=0;
        cover_xfer=0;
        cover_stall=0;
    end
    (* anyseq *) reg clear,start,stop,clear_fault,write;
    (* anyseq *) reg [5:0] write_address;
    (* anyseq *) reg [31:0] write_data,timestamp;
    (* anyseq *) reg [23:0] image_length;
    (* anyseq *) reg [7:0] ownership,pins;
    (* anyseq *) reg tx_valid,rx_ready,event_pending;
    (* anyseq *) reg [WIDTH-1:0] tx_data;
    wire [23:0] pc,next_pc,wait_timer;
    wire [31:0] instruction,completed;
    wire [5:0] address;
    wire [7:0] fault,pin_values,pin_enables;
    wire [6:0] transfer_edges;
    wire [WIDTH-1:0] rx_data;
    wire [3:0] signal_events;
    wire memory_enable,write_enable,read_enable,running,issue,stalled;
    wire tx_pop,rx_push,consume_event;
    protocol_instruction_sram_engine dut(.*);
    wire written,known,watch_read_seen;
    wire [5:0] watched_address,watch_read_address;
    wire [31:0] last_word;
    generate if (RESET_BASE) begin : data_proof
        instruction_sram_watch watch(.read_seen(watch_read_seen),
            .read_address(watch_read_address),.*);
    end else begin : alignment_only
        assign written=0;
        assign known=0;
        assign watch_read_seen=0;
        assign watched_address=0;
        assign watch_read_address=0;
        assign last_word=0;
    end endgenerate
    assign cover_read=known && running;
    reg tag_valid;
    reg [5:0] fetch_tag;
    reg past_valid=0;
    always @(posedge clk) begin
        past_valid<=1;
        // Exactly the production loader exclusion. START while already running,
        // STOP, faults, pins, events, queues, timers and branches remain free.
        if (write_enable) assume(!running && !start);
        if (!past_valid) begin
            if (RESET_BASE) assume(clear);
            else assume(!running || (tag_valid && fetch_tag == pc[5:0]));
        end
        if (memory_enable && read_enable) begin
            tag_valid<=1;
            fetch_tag<=address;
        end
        if (past_valid) begin
            assert(pc == $past(next_pc));
            assert(memory_enable == !clear);
            assert(write_enable == (write && !clear));
            assert(read_enable == !write_enable);
            assert(address == (write_enable ? write_address : next_pc[5:0]));
            if (running && !clear) begin
                assert(tag_valid && fetch_tag == pc[5:0]);
                if (RESET_BASE) begin
                    assert(watch_read_seen && watch_read_address == pc[5:0]);
                    if (written && watched_address == pc[5:0]) begin
                        assert(known);
                        assert(instruction == last_word);
                    end
                end
            end
            if ($past(clear)) assert(pc == 0 && !running);
            if ($past(start && !clear && !stop)) begin
                assert(pc == 0 && running);
                if (known) cover_start<=1;
            end
            if ($past(issue && instruction[31:24] == 5 &&
                      instruction[23:0] != pc+1) &&
                pc == $past(instruction[23:0]) && known)
                cover_branch<=1;
            if (known && transfer_edges != 0 && $past(transfer_edges) != 0 &&
                !$past(clear || stop || start)) cover_xfer<=1;
            if (running && stalled && known) cover_stall<=1;
        end
    end
endmodule
