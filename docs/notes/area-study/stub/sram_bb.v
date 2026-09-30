(* blackbox *)
module RM_IHPSG13_1P_64x16_c2 (A_CLK, A_MEN, A_WEN, A_REN, A_ADDR, A_DIN, A_DLY, A_DOUT);
  input A_CLK; input A_MEN; input A_WEN; input A_REN;
  input [5:0] A_ADDR; input [15:0] A_DIN; input A_DLY;
  output [15:0] A_DOUT;
endmodule
