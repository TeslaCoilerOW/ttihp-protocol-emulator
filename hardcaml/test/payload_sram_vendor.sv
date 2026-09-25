// Independent four-state scoreboard for the emitted adapter plus exact IHP
// FUNCTIONAL models. This test does not supply a replacement SRAM model.
// Every reset here spans a rising clock edge, as the adapter contract requires.
`timescale 1ns/1ps
module tb;
  parameter integer ADDRESS_BITS = 9;
  localparam integer WORDS = 1 << ADDRESS_BITS;
  reg clk = 0, clear = 1, request = 0, write = 0;
  reg [ADDRESS_BITS-1:0] address = 0;
  reg [31:0] write_data = 0;
  reg [3:0] tag = 0;
  wire ready, accepted, read_valid, men, wen, ren;
  wire [31:0] read_data;
  wire [3:0] read_tag;
  protocol_payload_sram_adapter dut(.*);

  reg [31:0] expected_memory [0:WORDS-1];
  reg previous_valid = 0;
  reg [3:0] previous_tag = 0;
  reg [31:0] previous_data = 0, previous_raw = 32'bx;
  reg [31:0] rng = 32'h15a4f902;
  integer cycles = 0, reads = 0, writes = 0, i;

  task automatic step(input reg reset_request, input reg access_request,
                      input reg write_request, input integer word_address,
                      input reg [31:0] data, input reg [3:0] opaque_tag);
    reg take, read_request;
    reg [31:0] expected_data;
    begin
      if (word_address < 0 || word_address >= WORDS) $fatal(1, "test address out of range");
      clear = reset_request; request = access_request; write = write_request;
      address = word_address; write_data = data; tag = opaque_tag;
      take = access_request && !reset_request;
      read_request = take && !write_request;
      expected_data = read_request ? expected_memory[word_address] : 32'b0;
      #9;
      if (ready !== !reset_request || accepted !== take || men !== take ||
          wen !== (take && write_request) || ren !== read_request)
        $fatal(1, "access controls cycle %0d", cycles);
      if (read_valid !== (previous_valid && !reset_request) ||
          read_tag !== ((previous_valid && !reset_request) ? previous_tag : 4'b0) ||
          read_data !== ((previous_valid && !reset_request) ? previous_data : 32'b0))
        $fatal(1, "response changed before edge cycle %0d", cycles);
      if (dut.payload_sram.A_DOUT !== previous_raw)
        $fatal(1, "raw macro DOUT changed without read edge");
      if (dut.payload_sram.A_DLY !== 1'b1 || dut.payload_sram.A_BM !== 32'hffffffff ||
          dut.payload_sram.A_CLK !== clk || dut.payload_sram.A_BIST_CLK !== 1'b0 ||
          dut.payload_sram.A_BIST_EN !== 1'b0 || dut.payload_sram.A_BIST_MEN !== 1'b0 ||
          dut.payload_sram.A_BIST_WEN !== 1'b0 || dut.payload_sram.A_BIST_REN !== 1'b0 ||
          dut.payload_sram.A_BIST_ADDR !== {ADDRESS_BITS{1'b0}} ||
          dut.payload_sram.A_BIST_DIN !== 32'b0 || dut.payload_sram.A_BIST_BM !== 32'b0)
        $fatal(1, "normal/BIST macro binding mismatch");
      #1; clk = 1;
      #1;
      if (take && write_request) begin
        expected_memory[word_address] = data;
        writes = writes + 1;
      end
      if (read_request) begin
        previous_raw = expected_data;
        reads = reads + 1;
      end
      if (read_valid !== read_request || read_data !== expected_data ||
          read_tag !== (read_request ? opaque_tag : 4'b0))
        $fatal(1, "read response mismatch cycle %0d address %0d", cycles, word_address);
      if (dut.payload_sram.A_DOUT !== previous_raw)
        $fatal(1, "write/reset/idle changed raw DOUT or read failed");
      previous_valid = read_request; previous_data = expected_data; previous_tag = opaque_tag;
      cycles = cycles + 1;
      #9; clk = 0;
    end
  endtask

  initial begin
    if (ADDRESS_BITS != 9 && ADDRESS_BITS != 10) $fatal(1, "only fixed 2/4 KiB controls");
    #1;
    if (dut.payload_sram.A_DOUT !== 32'bx || read_data !== 32'b0 || read_valid !== 1'b0)
      $fatal(1, "startup must mask uninitialized raw macro output");
    step(1, 1, 1, 0, 32'hbad, 15);
    // Explicit read of untouched storage establishes the four-state boundary:
    // valid means completed request, not initialized data or safe ownership.
    step(0, 1, 0, WORDS-1, 0, 15);
    if (read_data !== 32'bx) $fatal(1, "uninitialized macro read was invented");
    step(1, 0, 0, 0, 0, 0);
    for (i = 0; i < WORDS; i = i + 1)
      step(0, 1, 1, i, 32'h80000000 | (i << 16) | (i ^ 32'ha55a), i[3:0]);
    for (i = WORDS-1; i >= 0; i = i - 1)
      step(0, 1, 0, i, 0, i[3:0]);
    step(0, 1, 0, 0, 0, 9);
    step(1, 1, 1, 0, 32'hbad, 8);
    step(0, 1, 0, 0, 0, 7);
    step(0, 0, 1, 0, 32'hbad, 6);
    step(0, 1, 0, 0, 0, 5);
    for (i = 0; i < 2000; i = i + 1) begin
      rng = rng ^ (rng << 13); rng = rng ^ (rng >> 17); rng = rng ^ (rng << 5);
      step((rng % 19) == 0, rng[19], rng[20], rng[ADDRESS_BITS-1:0],
           rng ^ 32'hfebac019, rng[24:21]);
    end
    if (reads < WORDS || writes < WORDS || cycles != 2*WORDS+2008)
      $fatal(1, "incomplete vendor control denominator");
    $display("PAYLOAD_VENDOR_OK words=%0d cycles=%0d reads=%0d writes=%0d", WORDS, cycles, reads, writes);
    $finish;
  end
endmodule
