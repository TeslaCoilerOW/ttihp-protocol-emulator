// Independent shift-queue specification for the production ring-buffer FIFO.
// Default matches the flagship; override both parameters for another generation.
//
// Design variants (formal/run.sh --variant): with -DASYNC_RESET the FIFO has an
// asynchronous chip reset input `rst` (count and pointers, and storage words
// when they are registers) and `clear` is FLUSH, a synchronous clear. While
// `rst` is asserted the queue must read empty; the specification queue empties
// on either.
module fifo_conservation #(parameter WIDTH=32, DEPTH=8, LEVEL_WIDTH=$clog2(DEPTH)+1)
    (input wire clk);
    (* anyseq *) reg clear, push, pop;
    (* anyseq *) reg [WIDTH-1:0] data_in;
    wire ready, valid;
    wire [WIDTH-1:0] data_out;
    wire [LEVEL_WIDTH-1:0] level;
`ifdef ASYNC_RESET
    (* anyseq *) reg rst;
    wire empty_now = rst;
    protocol_fifo dut(.clk(clk), .rst(rst), .clear(clear), .push(push), .pop(pop),
        .data_in(data_in), .ready(ready), .valid(valid), .data_out(data_out), .level(level));
`else
    wire empty_now = 1'b0;
    protocol_fifo dut(.clk(clk), .clear(clear), .push(push), .pop(pop),
        .data_in(data_in), .ready(ready), .valid(valid), .data_out(data_out), .level(level));
`endif
    reg past_valid=0;
    reg [LEVEL_WIDTH-1:0] expected_count=0;
    reg [WIDTH-1:0] expected [0:DEPTH-1];
    wire take=pop && valid && !clear;
    wire put=push && ready && !clear;
    integer i;
    always @(posedge clk) begin
        past_valid <= 1;
`ifdef ASYNC_RESET
        if (!past_valid) assume(rst);
        if (past_valid && rst) begin
            assert(level == 0);
            assert(!valid && ready);
        end
`else
        if (!past_valid) assume(clear);
`endif
        if (past_valid && !empty_now) begin
            assert(level == expected_count);
            assert(level <= DEPTH);
            assert(valid == (level != 0));
            assert(ready == (level < DEPTH));
            if (valid) assert(data_out == expected[0]);
        end
        if (clear || empty_now) expected_count <= 0;
        else begin
            case ({put,take})
                2'b10: expected_count <= expected_count + 1;
                2'b01: expected_count <= expected_count - 1;
            endcase
            if (take) begin
                for (i=0; i<DEPTH-1; i=i+1) expected[i] <= expected[i+1];
            end
            if (put) expected[expected_count - take] <= data_in;
        end
    end
endmodule
