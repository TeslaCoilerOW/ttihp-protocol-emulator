module \$_DLATCH_P_ (input E, input D, output Q);
  sg13cmos5l_dlhq_1 _TECHMAP_REPLACE_ (.GATE(E), .D(D), .Q(Q));
endmodule
module \$_DLATCH_PN0_ (input E, input R, input D, output Q);
  sg13cmos5l_dlhrq_1 _TECHMAP_REPLACE_ (.GATE(E), .RESET_B(R), .D(D), .Q(Q));
endmodule
module \$_DLATCH_PP0_ (input E, input R, input D, output Q);
  sg13cmos5l_dlhrq_1 _TECHMAP_REPLACE_ (.GATE(E), .RESET_B(~R), .D(D), .Q(Q));
endmodule
