// Property harness for the actual emitted adapter and exact FUNCTIONAL macro.
// No assumption initializes SRAM. One arbitrary watched word checks retained
// data after writes; all tags/controls remain unconstrained. This source alone
// is not a proof result. The native job must state its solver/depth/induction.
// Qualification additionally requires an elaborated macro connection gate for
// DLY/BM/BIST/clock ties: FUNCTIONAL behavior alone cannot prove unused DLY.
module payload_sram_properties #(
  parameter integer ADDRESS_BITS = 9
)(
  input wire clk, clear, request, write,
  input wire [ADDRESS_BITS-1:0] address,
  input wire [31:0] write_data,
  input wire [3:0] tag
);
  wire ready, accepted, read_valid, men, wen, ren;
  wire [31:0] read_data;
  wire [3:0] read_tag;
  protocol_payload_sram_adapter dut(.*);

  (* anyconst *) reg [ADDRESS_BITS-1:0] watched_address;
  reg past_valid = 0;
  reg word_written = 0;
  reg cleared_written = 0;
  reg [31:0] watched_data;
  reg previous_read;
  reg [3:0] previous_tag;
  reg previous_checked;
  reg [31:0] previous_expected;

  always @* begin
    assert(ready == !clear);
    assert(accepted == (request && !clear));
    assert(men == accepted);
    assert(wen == (accepted && write));
    assert(ren == (accepted && !write));
    assert(!(wen && ren));
    if (clear) begin
      assert(!read_valid);
      assert(read_data == 0);
      assert(read_tag == 0);
    end
  end

  always @(posedge clk) begin
    if (past_valid) begin
      assert(read_valid == (previous_read && !clear));
      assert(read_tag == ((previous_read && !clear) ? previous_tag : 4'b0));
      if (!read_valid) assert(read_data == 0);
      if (previous_checked && !clear) assert(read_data == previous_expected);
      cover(previous_checked && !clear && read_valid);
      cover(previous_checked && cleared_written && !clear && read_valid);
    end
    past_valid <= 1;
    previous_read <= request && !clear && !write;
    previous_tag <= tag;
    previous_checked <= request && !clear && !write &&
                        address == watched_address && word_written;
    previous_expected <= watched_data;
    if (clear && word_written) cleared_written <= 1;
    if (request && !clear && write && address == watched_address) begin
      watched_data <= write_data;
      word_written <= 1;
      cleared_written <= 0;
    end
    // Reset invalidates response metadata but cannot erase a written word.
    // In particular, do not clear word_written or watched_data here.
  end
endmodule
