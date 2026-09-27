// Reference CRC step for the line-unit harnesses (formal/line_crc.sv,
// formal/line_codec.sv), written from the published generator polynomials:
// CRC-5/USB x^5+x^2+1 (0x05, width 5), CRC-16 x^16+x^15+x^2+1 (0x8005),
// CRC-15/CAN (0x4599, width 15), CRC-16/CCITT x^16+x^12+x^5+1 (0x1021).
// MSB first: the polynomial left-aligned in 16 bits, left shift.
// LSB first: the polynomial bit-reversed over its own width, right shift.
function automatic [15:0] crc_ref_step(input [15:0] crc, input b, input [1:0] preset, input msb);
    reg [15:0] normal, poly;
    integer width, k;
    begin
        case (preset)
            2'd0: begin normal = 16'h0005; width = 5; end
            2'd1: begin normal = 16'h8005; width = 16; end
            2'd2: begin normal = 16'h4599; width = 15; end
            default: begin normal = 16'h1021; width = 16; end
        endcase
        if (msb) begin
            poly = normal << (16 - width);
            crc_ref_step = {crc[14:0], 1'b0} ^ ((crc[15] ^ b) ? poly : 16'h0000);
        end else begin
            poly = 16'h0000;
            for (k = 0; k < 16; k = k + 1)
                if (k < width) poly[k] = normal[width - 1 - k];
            crc_ref_step = {1'b0, crc[15:1]} ^ ((crc[0] ^ b) ? poly : 16'h0000);
        end
    end
endfunction
