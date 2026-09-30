// Area-study sketch (not verified RTL): latch-array FIFO, DFFRAM-style.
// Write enable per word is captured by an integrated clock gate during the push
// cycle; the latch word is transparent in the high phase of the next cycle and
// captures a flopped copy of the write data (CAPTURE=1) or the source register
// directly (CAPTURE=0; legal only if the source is a register held stable for
// the cycle after the push, e.g. engine rx).  Read is a plain mux, as before.
// ARST=1: pointers and capture register take 'clear' as an asynchronous reset
// (comparable with the async-reset flop FIFO block blk_fifo32x*_afr).
module fifo_latch_block #(parameter W=32, D=8, B=3, CAPTURE=1, LRESET=0, ARST=0)
  (input clk, input clear, input rst_n, input push, input pop, input [W-1:0] din,
   output ready, output valid, output [W-1:0] dout, output [B:0] level);
  reg [B:0] count; reg [B-1:0] rd, wr;
  assign valid = count != 0;
  assign ready = count != D;
  wire put = push & ready & ~clear;
  wire take = pop & valid & ~clear;
  generate if (ARST) begin : ptr_a
    always @(posedge clk or posedge clear) begin
      if (clear) begin count <= 0; rd <= 0; wr <= 0; end
      else begin
        if (put) wr <= wr + 1'b1;
        if (take) rd <= rd + 1'b1;
        if (put ^ take) count <= put ? count + 1'b1 : count - 1'b1;
      end
    end
  end else begin : ptr_s
    always @(posedge clk) begin
      if (clear) begin count <= 0; rd <= 0; wr <= 0; end
      else begin
        if (put) wr <= wr + 1'b1;
        if (take) rd <= rd + 1'b1;
        if (put ^ take) count <= put ? count + 1'b1 : count - 1'b1;
      end
    end
  end endgenerate
  wire [W-1:0] wdata;
  generate if (CAPTURE && ARST) begin : cap_a
    reg [W-1:0] q; always @(posedge clk or posedge clear) if (clear) q <= 0; else q <= din; assign wdata = q;
  end else if (CAPTURE) begin : cap
    reg [W-1:0] q; always @(posedge clk) q <= din; assign wdata = q;
  end else begin : nocap
    assign wdata = din;
  end endgenerate
  wire [W*D-1:0] flat;
  genvar i;
  generate for (i = 0; i < D; i = i + 1) begin : words
    wire gclk;
    sg13cmos5l_lgcp_1 icg (.CLK(clk), .GATE(put & (wr == i)), .GCLK(gclk));
    reg [W-1:0] word;
    if (LRESET) begin : lr
      always @* if (!rst_n) word = 0; else if (gclk) word = wdata;
    end else begin : ln
      always @* if (gclk) word = wdata;
    end
    assign flat[W*i +: W] = word;
  end endgenerate
  assign dout = flat[W*rd +: W];
  assign level = count;
endmodule
module fl_w32d8_cap (input clk, clear, rst_n, push, pop, input [31:0] din, output ready, valid, output [31:0] dout, output [3:0] level);
  fifo_latch_block #(32,8,3,1,0) u (clk, clear, rst_n, push, pop, din, ready, valid, dout, level);
endmodule
module fl_w32d8_nocap (input clk, clear, rst_n, push, pop, input [31:0] din, output ready, valid, output [31:0] dout, output [3:0] level);
  fifo_latch_block #(32,8,3,0,0) u (clk, clear, rst_n, push, pop, din, ready, valid, dout, level);
endmodule
module fl_w32d8_nocap_rst (input clk, clear, rst_n, push, pop, input [31:0] din, output ready, valid, output [31:0] dout, output [3:0] level);
  fifo_latch_block #(32,8,3,0,1) u (clk, clear, rst_n, push, pop, din, ready, valid, dout, level);
endmodule
module fl_w32d4_nocap (input clk, clear, rst_n, push, pop, input [31:0] din, output ready, valid, output [31:0] dout, output [2:0] level);
  fifo_latch_block #(32,4,2,0,0) u (clk, clear, rst_n, push, pop, din, ready, valid, dout, level);
endmodule
module fl_w32d4_cap (input clk, clear, rst_n, push, pop, input [31:0] din, output ready, valid, output [31:0] dout, output [2:0] level);
  fifo_latch_block #(32,4,2,1,0) u (clk, clear, rst_n, push, pop, din, ready, valid, dout, level);
endmodule
module fl_w32d8_nocap_a (input clk, clear, rst_n, push, pop, input [31:0] din, output ready, valid, output [31:0] dout, output [3:0] level);
  fifo_latch_block #(32,8,3,0,0,1) u (clk, clear, rst_n, push, pop, din, ready, valid, dout, level);
endmodule
module fl_w32d8_cap_a (input clk, clear, rst_n, push, pop, input [31:0] din, output ready, valid, output [31:0] dout, output [3:0] level);
  fifo_latch_block #(32,8,3,1,0,1) u (clk, clear, rst_n, push, pop, din, ready, valid, dout, level);
endmodule
module fl_w32d4_nocap_a (input clk, clear, rst_n, push, pop, input [31:0] din, output ready, valid, output [31:0] dout, output [2:0] level);
  fifo_latch_block #(32,4,2,0,0,1) u (clk, clear, rst_n, push, pop, din, ready, valid, dout, level);
endmodule
module fl_w32d4_cap_a (input clk, clear, rst_n, push, pop, input [31:0] din, output ready, valid, output [31:0] dout, output [2:0] level);
  fifo_latch_block #(32,4,2,1,0,1) u (clk, clear, rst_n, push, pop, din, ready, valid, dout, level);
endmodule
