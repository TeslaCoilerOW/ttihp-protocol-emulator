// Independent shift-queue specification for the production ring-buffer FIFO.
// Default matches the flagship; override both parameters for another generation.
module fifo_conservation #(parameter WIDTH=32, DEPTH=8, LEVEL_WIDTH=$clog2(DEPTH)+1)
    (input wire clk);
    (* anyseq *) reg clear, push, pop;
    (* anyseq *) reg [WIDTH-1:0] data_in;
    wire ready, valid;
    wire [WIDTH-1:0] data_out;
    wire [LEVEL_WIDTH-1:0] level;
    protocol_fifo dut(.clk(clk), .clear(clear), .push(push), .pop(pop),
        .data_in(data_in), .ready(ready), .valid(valid), .data_out(data_out), .level(level));
    reg past_valid=0;
    reg [LEVEL_WIDTH-1:0] expected_count=0;
    reg [WIDTH-1:0] expected [0:DEPTH-1];
    wire take=pop && valid && !clear;
    wire put=push && ready && !clear;
    integer i;
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(clear);
        if (past_valid) begin
            assert(level == expected_count);
            assert(level <= DEPTH);
            assert(valid == (level != 0));
            assert(ready == (level < DEPTH));
            if (valid) assert(data_out == expected[0]);
        end
        if (clear) expected_count <= 0;
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
