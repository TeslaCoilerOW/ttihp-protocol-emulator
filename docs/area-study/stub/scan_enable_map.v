// Experimental: use the scan-mux flop (sdfrbpq_1: next = SCE ? SCD : D) as an
// enable flop by feeding Q back into D.  Area study only.
module \$_DFFE_PP_ (input C, input D, input E, output Q);
  sg13cmos5l_sdfrbpq_1 _TECHMAP_REPLACE_ (.CLK(C), .D(Q), .SCD(D), .SCE(E), .RESET_B(1'b1), .Q(Q));
endmodule
module \$_DFFE_PN0P_ (input C, input D, input E, input R, output Q);
  sg13cmos5l_sdfrbpq_1 _TECHMAP_REPLACE_ (.CLK(C), .D(Q), .SCD(D), .SCE(E), .RESET_B(R), .Q(Q));
endmodule
module \$_DFFE_PP0P_ (input C, input D, input E, input R, output Q);
  sg13cmos5l_sdfrbpq_1 _TECHMAP_REPLACE_ (.CLK(C), .D(Q), .SCD(D), .SCE(E), .RESET_B(~R), .Q(Q));
endmodule
module \$_SDFFE_PP0P_ (input C, input D, input E, input R, output Q);
  sg13cmos5l_sdfrbpq_1 _TECHMAP_REPLACE_ (.CLK(C), .D(Q), .SCD(D & ~R), .SCE(E | R), .RESET_B(1'b1), .Q(Q));
endmodule
