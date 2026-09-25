`default_nettype none
`timescale 1ns / 1ps

/* Testbench top for the independent-peer suite (test_ext/test_*.py).

   The Tiny Tapeout top is wired to a model "board": eight pads with pull-ups
   (tri1), the DUT driving them through 1 ns of wire (plus a skew corner, see
   "Skew corners" below), and third-party protocol
   peers (vendor/, not written by this project) attached to the pads. The
   cocotb host driver (../test/harness.py) drives ui_in/rst_n/ena and compares
   the DUT with the reference model every cycle; it no longer supplies uio_in.
   Instead the DUT's uio_in is the resolved pad value, re-timed on the falling
   clock edge so that peers that change a pad at arbitrary times (the Python
   peers are timer driven) cannot race the DUT's sampling edge. The harness
   reads that same register (uio_in_q) after each rising edge and gives it to
   the reference model, so model and DUT always see identical inputs.

   Contention (two drivers disagreeing on a pad) resolves to X, which the
   harness reports as a failure. Every peer starts disconnected; a test
   enables the peers it needs through the sel_* registers before releasing
   peer_rst.

   Pin map (docs/firmware.md): UART TX0/RX1, SPI SCK2/MOSI3/MISO4/CSn5,
   I2C SCL6/SDA7 (open drain), JTAG TCK0/TDI1/TDO2/TMS3.
*/
module tb_ext ();

`ifndef NO_WAVES
  initial begin
    $dumpfile("tb_ext.fst");
`ifdef DUMP_ALL
    $dumpvars(0, tb_ext);
`else
    $dumpvars(1, tb_ext);
`endif
    #1;
  end
`endif

  // --------------------------------------------------------------- TT ports
  reg clk = 1'b0;
  reg rst_n = 1'b0;
  reg ena = 1'b1;
  reg [7:0] ui_in = 8'h00;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;
  // ../test/harness.py writes its pin supplier's value to tb.uio_in every cycle.
  // Here that register is deliberately left unconnected: the DUT's uio_in is
  // uio_in_q, the resolved pads (see the header comment).
  reg [7:0] uio_in = 8'h00;
  reg [7:0] uio_in_q = 8'hff;        // pads sampled at the falling edge -> DUT uio_in

  tt_um_teslacoilerow_protocol_emulator user_project (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in_q),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  // ------------------------------------------------------------------ board
  // Skew corners. The DUT changes every output on a rising clk edge, so a
  // clock pin (JTAG TCK pad0, SPI SCK pad2, I2C SCL pad6) and a data pin that
  // change on the same edge would reach a peer at the same simulation time,
  // and an edge-triggered peer would see the new data value: the simulator
  // would silently forgive a controller that changes data on the peer's
  // sampling edge (the wrong SPI CPHA, say). On a real board that is a
  // set-up/hold violation with an undefined outcome. The board therefore
  // delays one group of DUT outputs by SKEW_NS more than the other:
  //   skew_sel 1 "data":  clock pads 1 ns, every other DUT output 1 + SKEW_NS
  //                       (a same-edge data change arrives after the clock
  //                       edge: the peer samples the old value, as with a
  //                       hold-time requirement);
  //   skew_sel 2 "clock": clock pads 1 + SKEW_NS, the others 1 ns (the data
  //                       change arrives first: catches a chip select or data
  //                       change on the same edge as the preceding clock edge);
  //   skew_sel 0 "none":  every DUT output 1 ns (simulator race; for
  //                       comparison only).
  // A design whose outputs never change on the edge that the peer samples at
  // behaves the same in both corners. Every delay is below half a clock
  // period, so the pads sampled at the falling edge (uio_in_q, the DUT's and
  // the reference model's input) are the same in every corner.
  localparam integer SKEW_NS = 4;
  localparam [7:0] CLOCK_PADS = 8'b0100_0101;  // TCK0, SCK2, SCL6
  reg [1:0] skew_sel = 2'd1;
  wire [7:0] out_early, oe_early, out_late, oe_late;
  assign #1 out_early = uio_out;
  assign #1 oe_early  = uio_oe;
  assign #(1 + SKEW_NS) out_late = uio_out;
  assign #(1 + SKEW_NS) oe_late  = uio_oe;
  wire [7:0] late_pads = (skew_sel == 2'd1) ? ~CLOCK_PADS : (skew_sel == 2'd2) ? CLOCK_PADS : 8'h00;
  wire [7:0] dut_out = (late_pads & out_late) | (~late_pads & out_early);
  wire [7:0] dut_oe  = (late_pads & oe_late)  | (~late_pads & oe_early);

  tri1 [7:0] pad;  // every pad has a pull-up resistor
  genvar gi;
  generate
    for (gi = 0; gi < 8; gi = gi + 1) begin : dut_drive
      assign pad[gi] = (dut_oe[gi] === 1'b1) ? dut_out[gi] : 1'bz;
    end
  endgenerate

  always @(negedge clk) uio_in_q <= pad;

  // Scalar views of the pads (cocotb handles cannot index packed vectors).
  wire pad0 = pad[0], pad1 = pad[1], pad2 = pad[2], pad3 = pad[3];
  wire pad4 = pad[4], pad5 = pad[5], pad6 = pad[6], pad7 = pad[7];

  reg peer_rst = 1'b1;  // active-high reset of every third-party peer

  // Edge-coincidence counters (the tests log and check them): how often a DUT
  // data pin moved on the same clk edge as a DUT clock pin rose or fell. A pin
  // "moved" when the DUT changed its drive (driven value, or 1 when released)
  // and the pad followed, so clock stretching on a line that is already low
  // and pin-ownership changes on an idle bus do not count. The SPI MOSI
  // counters only count inside a frame (CSn low before and after the edge).
  // Pairs: SPI SCK2 with MOSI3 and CSn5, JTAG TCK0 with TDI1 and TMS3, I2C
  // SCL6 with SDA7. Sampled at the falling clk edge, when the DUT outputs and
  // the pads are stable (gate-level flops update in the active region of the
  // rising edge).
  wire [7:0] dut_level = (uio_oe & uio_out) | ~uio_oe;
  reg [7:0] dut_level_q = 8'hff;
  reg [7:0] pad_q = 8'hff;
  reg [15:0] co_sck_mosi_rise = 16'd0, co_sck_mosi_fall = 16'd0;
  reg [15:0] co_sck_csn_rise = 16'd0, co_sck_csn_fall = 16'd0;
  reg [15:0] co_tck_tdi_rise = 16'd0, co_tck_tdi_fall = 16'd0;
  reg [15:0] co_tck_tms_rise = 16'd0, co_tck_tms_fall = 16'd0;
  reg [15:0] co_scl_sda_rise = 16'd0, co_scl_sda_fall = 16'd0;
  wire [7:0] moved = (dut_level ^ dut_level_q) & (pad ^ pad_q);
  wire [7:0] rose = moved & pad;
  wire [7:0] fell = moved & ~pad;
  wire spi_frame = (pad[5] === 1'b0) && (pad_q[5] === 1'b0);
  always @(negedge clk) begin
    dut_level_q <= dut_level;
    pad_q <= pad;
    if (peer_rst) begin
      co_sck_mosi_rise <= 16'd0; co_sck_mosi_fall <= 16'd0;
      co_sck_csn_rise <= 16'd0; co_sck_csn_fall <= 16'd0;
      co_tck_tdi_rise <= 16'd0; co_tck_tdi_fall <= 16'd0;
      co_tck_tms_rise <= 16'd0; co_tck_tms_fall <= 16'd0;
      co_scl_sda_rise <= 16'd0; co_scl_sda_fall <= 16'd0;
    end else begin
      if (spi_frame && moved[3] && rose[2]) co_sck_mosi_rise <= co_sck_mosi_rise + 16'd1;
      if (spi_frame && moved[3] && fell[2]) co_sck_mosi_fall <= co_sck_mosi_fall + 16'd1;
      if (moved[5] && rose[2]) co_sck_csn_rise <= co_sck_csn_rise + 16'd1;
      if (moved[5] && fell[2]) co_sck_csn_fall <= co_sck_csn_fall + 16'd1;
      if (moved[1] && rose[0]) co_tck_tdi_rise <= co_tck_tdi_rise + 16'd1;
      if (moved[1] && fell[0]) co_tck_tdi_fall <= co_tck_tdi_fall + 16'd1;
      if (moved[3] && rose[0]) co_tck_tms_rise <= co_tck_tms_rise + 16'd1;
      if (moved[3] && fell[0]) co_tck_tms_fall <= co_tck_tms_fall + 16'd1;
      if (moved[7] && rose[6]) co_scl_sda_rise <= co_scl_sda_rise + 16'd1;
      if (moved[7] && fell[6]) co_scl_sda_fall <= co_scl_sda_fall + 16'd1;
    end
  end

  // Open-drain discipline: the DUT must never actively drive an I2C pad high.
  reg [15:0] od_violations = 16'd0;
  always @(posedge clk)
    if (peer_rst) od_violations <= 16'd0;
    else if ((uio_oe & uio_out & 8'hc0) != 8'h00) od_violations <= od_violations + 16'd1;

  // =============================================================== UART
  // alexforencich/verilog-uart. prescale = f_clk / (baud * 8); the firmware
  // uses 64 clocks per bit (781.25 kbaud at 50 MHz) -> 8.
  reg [15:0] uart_prescale = 16'd8;

  // uart_rx decodes pad0 (DUT UART TX). Receive-only: always attached.
  wire [7:0] vurx_tdata;
  wire vurx_tvalid, vurx_busy, vurx_overrun, vurx_frame_error;
  uart_rx #(.DATA_WIDTH(8)) vuart_rx (
      .clk(clk), .rst(peer_rst),
      .m_axis_tdata(vurx_tdata), .m_axis_tvalid(vurx_tvalid), .m_axis_tready(1'b1),
      .rxd(pad[0]),
      .busy(vurx_busy), .overrun_error(vurx_overrun), .frame_error(vurx_frame_error),
      .prescale(uart_prescale));
  reg [7:0] vurx_log [0:63];
  reg [7:0] vurx_count = 8'd0;
  reg [7:0] vurx_frame_errors = 8'd0;
  always @(posedge clk) begin
    if (peer_rst) begin
      vurx_count <= 8'd0;
      vurx_frame_errors <= 8'd0;
    end else begin
      if (vurx_tvalid) begin
        vurx_log[vurx_count[5:0]] <= vurx_tdata;
        vurx_count <= vurx_count + 8'd1;
      end
      if (vurx_frame_error) vurx_frame_errors <= vurx_frame_errors + 8'd1;
    end
  end

  // uart_tx drives pad1 (DUT UART RX) when sel_vuart_tx. A small feeder sends
  // vutx_mem[0..vutx_len-1] after vutx_go, with vutx_gap idle clocks between
  // frames (0 = back to back).
  reg sel_vuart_tx = 1'b0;
  reg [7:0] vutx_mem [0:63];
  reg [6:0] vutx_len = 7'd0;
  reg [15:0] vutx_gap = 16'd0;
  reg vutx_go = 1'b0;
  reg [6:0] vutx_idx = 7'd0;
  reg [15:0] vutx_wait = 16'd0;
  reg vutx_tvalid = 1'b0;
  wire vutx_tready, vutx_busy, vutx_txd;
  uart_tx #(.DATA_WIDTH(8)) vuart_tx (
      .clk(clk), .rst(peer_rst),
      .s_axis_tdata(vutx_mem[vutx_idx[5:0]]), .s_axis_tvalid(vutx_tvalid), .s_axis_tready(vutx_tready),
      .txd(vutx_txd), .busy(vutx_busy), .prescale(uart_prescale));
  always @(posedge clk) begin
    if (peer_rst) begin
      vutx_idx <= 7'd0;
      vutx_wait <= 16'd0;
      vutx_tvalid <= 1'b0;
    end else if (vutx_tvalid) begin
      if (vutx_tready) begin
        vutx_tvalid <= 1'b0;
        vutx_idx <= vutx_idx + 7'd1;
        vutx_wait <= vutx_gap;
      end
    end else if (vutx_go && vutx_idx < vutx_len && !vutx_busy) begin
      if (vutx_wait != 16'd0) vutx_wait <= vutx_wait - 16'd1;
      else vutx_tvalid <= 1'b1;
    end
  end
  assign pad[1] = sel_vuart_tx ? vutx_txd : 1'bz;

  // cocotbext-uart UartSource drives pad1 through py_uart_rxd.
  reg sel_py_uart_tx = 1'b0;
  reg py_uart_rxd = 1'b1;
  assign pad[1] = sel_py_uart_tx ? py_uart_rxd : 1'bz;

  // =============================================================== SPI
  // DUT as controller: SCK pad2, MOSI pad3, MISO pad4, CSn pad5.

  // YosysHQ picorv32 spiflash.v (modes 0/3). Memory image: +firmware=<hex>.
  reg sel_flash = 1'b0;
  wire flash_io1;
  tri1 flash_io2, flash_io3;  // WP#/HOLD# pulled up
  spiflash flash (
      .csb(sel_flash ? pad[5] : 1'b1), .clk(pad[2]),
      .io0(pad[3]), .io1(flash_io1), .io2(flash_io2), .io3(flash_io3));
  assign pad[4] = sel_flash ? flash_io1 : 1'bz;

  // nandland spi-slave SPI_Slave, one instance per mode. All instances listen;
  // nslave_miso_en connects the selected mode's MISO to pad4. Each received
  // byte is logged and the next response byte from nslave_tx is loaded.
  reg [1:0] nslave_mode = 2'd0;
  reg nslave_miso_en = 1'b0;
  reg [7:0] nslave_tx [0:63];
  reg [6:0] nslave_tx_idx = 7'd0;
  reg [7:0] nslave_log [0:63];
  reg [7:0] nslave_count = 8'd0;
  wire [3:0] nslave_rx_dv;
  wire [7:0] nslave_rx_byte [0:3];
  wire [3:0] nslave_miso;
  reg nslave_tx_dv = 1'b0;
  generate
    for (gi = 0; gi < 4; gi = gi + 1) begin : nslave
      SPI_Slave #(.SPI_MODE(gi)) u (
          .i_Rst_L(~peer_rst), .i_Clk(clk),
          .o_RX_DV(nslave_rx_dv[gi]), .o_RX_Byte(nslave_rx_byte[gi]),
          .i_TX_DV(nslave_tx_dv), .i_TX_Byte(nslave_tx[nslave_tx_idx[5:0]]),
          .i_SPI_Clk(pad[2]), .o_SPI_MISO(nslave_miso[gi]), .i_SPI_MOSI(pad[3]),
          .i_SPI_CS_n(peer_rst ? 1'b0 : pad[5]));
    end
  endgenerate
  // SPI_Slave initialises its SPI-clock-domain registers only on a rising
  // CS_n edge; holding CS_n low during peer_rst provides one at release.
  // Load response byte 0 after reset and byte n+1 after the n-th received
  // byte (i_TX_DV sees the advanced index on the following edge).
  reg nslave_primed = 1'b0;
  always @(posedge clk) begin
    nslave_tx_dv <= 1'b0;
    if (peer_rst) begin
      nslave_primed <= 1'b0;
      nslave_tx_idx <= 7'd0;
      nslave_count <= 8'd0;
    end else if (!nslave_primed) begin
      nslave_primed <= 1'b1;
      nslave_tx_dv <= 1'b1;
    end else if (nslave_rx_dv[nslave_mode]) begin
      nslave_log[nslave_count[5:0]] <= nslave_rx_byte[nslave_mode];
      nslave_count <= nslave_count + 8'd1;
      nslave_tx_idx <= nslave_tx_idx + 7'd1;
      nslave_tx_dv <= 1'b1;
    end
  end
  assign pad[4] = (nslave_miso_en && !peer_rst) ? nslave_miso[nslave_mode] : 1'bz;

  // DUT as target: nandland spi-master SPI_Master_With_Single_CS, one instance
  // per mode, one byte per CS assertion. nmaster_sel (one-hot) connects an
  // instance's SCK/MOSI/CSn to the pads; nmaster_tx[0..len-1] are sent after
  // nmaster_go with nmaster_gap idle clocks between frames.
  localparam integer NM_HALF = 32;  // CLKS_PER_HALF_BIT: 32 clocks per SCK phase
  reg [3:0] nmaster_sel = 4'd0;
  reg [7:0] nmaster_tx [0:63];
  reg [6:0] nmaster_len = 7'd0;
  reg [15:0] nmaster_gap = 16'd64;
  reg nmaster_go = 1'b0;
  reg [6:0] nmaster_idx = 7'd0;
  reg [15:0] nmaster_wait = 16'd0;
  reg nmaster_dv = 1'b0;
  reg [7:0] nmaster_log [0:63];
  reg [7:0] nmaster_count = 8'd0;
  wire [3:0] nm_ready, nm_rx_dv, nm_sck, nm_mosi, nm_csn;
  wire [7:0] nm_rx_byte [0:3];
  generate
    for (gi = 0; gi < 4; gi = gi + 1) begin : nmaster
      wire rx_count;
      // CS_INACTIVE_CLKS must not be a power of two: the upstream counter is
      // $clog2(CS_INACTIVE_CLKS) bits wide.
      SPI_Master_With_Single_CS #(.SPI_MODE(gi), .CLKS_PER_HALF_BIT(NM_HALF), .MAX_BYTES_PER_CS(1),
                                  .CS_INACTIVE_CLKS(100)) u (
          .i_Rst_L(~peer_rst), .i_Clk(clk),
          .i_TX_Count(1'b1), .i_TX_Byte(nmaster_tx[nmaster_idx[5:0]]), .i_TX_DV(nmaster_dv & nmaster_sel[gi]),
          .o_TX_Ready(nm_ready[gi]), .o_RX_Count(rx_count), .o_RX_DV(nm_rx_dv[gi]), .o_RX_Byte(nm_rx_byte[gi]),
          .o_SPI_Clk(nm_sck[gi]), .i_SPI_MISO(pad[4]), .o_SPI_MOSI(nm_mosi[gi]), .o_SPI_CS_n(nm_csn[gi]));
      assign pad[2] = nmaster_sel[gi] ? nm_sck[gi] : 1'bz;
      assign pad[3] = nmaster_sel[gi] ? nm_mosi[gi] : 1'bz;
      assign pad[5] = nmaster_sel[gi] ? nm_csn[gi] : 1'bz;
    end
  endgenerate
  wire nm_ready_sel = |(nm_ready & nmaster_sel);
  wire nm_rx_dv_sel = |(nm_rx_dv & nmaster_sel);
  wire [7:0] nm_rx_byte_sel = nmaster_sel[0] ? nm_rx_byte[0] : nmaster_sel[1] ? nm_rx_byte[1] :
                              nmaster_sel[2] ? nm_rx_byte[2] : nm_rx_byte[3];
  wire nm_csn_sel = |(nm_csn & nmaster_sel);
  always @(posedge clk) begin
    nmaster_dv <= 1'b0;
    if (peer_rst) begin
      nmaster_idx <= 7'd0;
      nmaster_wait <= 16'd0;
      nmaster_count <= 8'd0;
    end else if (nmaster_dv) begin
      nmaster_idx <= nmaster_idx + 7'd1;
      nmaster_wait <= nmaster_gap;
    end else if (nmaster_go && nmaster_idx < nmaster_len && nm_ready_sel && nm_csn_sel) begin
      if (nmaster_wait != 16'd0) nmaster_wait <= nmaster_wait - 16'd1;
      else nmaster_dv <= 1'b1;
    end
    if (!peer_rst && nm_rx_dv_sel) begin
      nmaster_log[nmaster_count[5:0]] <= nm_rx_byte_sel;
      nmaster_count <= nmaster_count + 8'd1;
    end
  end

  // cocotbext-spi SpiMaster (DUT target) and SpiSlave (DUT controller).
  reg sel_py_spi_master = 1'b0;
  reg py_spi_sclk = 1'b0, py_spi_mosi = 1'b1, py_spi_cs = 1'b1;
  assign pad[2] = sel_py_spi_master ? py_spi_sclk : 1'bz;
  assign pad[3] = sel_py_spi_master ? py_spi_mosi : 1'bz;
  assign pad[5] = sel_py_spi_master ? py_spi_cs : 1'bz;
  reg sel_py_spi_slave = 1'b0;
  reg py_spi_miso = 1'b1;
  assign pad[4] = sel_py_spi_slave ? py_spi_miso : 1'bz;

  // =============================================================== I2C
  // SCL pad6, SDA pad7; every I2C peer is open drain (pulls low or releases).

  // alexforencich/verilog-i2c i2c_slave (DUT controller). Received bytes are
  // logged with their tlast flag; read data comes from vi2cs_tx and is
  // presented vi2cs_delay clocks after the slave requests it (the slave
  // stretches SCL meanwhile).
  reg sel_vi2c_slave = 1'b0;
  reg [6:0] vi2cs_address = 7'h42;
  reg [7:0] vi2cs_tx [0:15];
  reg [4:0] vi2cs_tx_idx = 5'd0;
  reg [15:0] vi2cs_delay = 16'd0;
  reg [15:0] vi2cs_wait = 16'd0;
  reg [8:0] vi2cs_log [0:31];  // {tlast, byte}
  reg [7:0] vi2cs_count = 8'd0;
  reg [7:0] vi2cs_stops = 8'd0;  // STOP conditions seen by i2c_slave (bus_active falling)
  reg vi2cs_active_q = 1'b0;
  // Set when i2c_slave holds SCL low while waiting for read data (its
  // STATE_READ_1 = 5 with scl_o low), and when it detects a STOP condition
  // while it is itself sending read data (stop_bit in STATE_READ_1): see
  // docs/independent-peers.md.
  reg vi2cs_read_stretched = 1'b0;
  reg vi2cs_self_stop = 1'b0;
  wire vi2cs_s_tready, vi2cs_m_tvalid, vi2cs_m_tlast;
  wire [7:0] vi2cs_m_tdata;
  wire vi2cs_scl_o, vi2cs_scl_t, vi2cs_sda_o, vi2cs_sda_t, vi2cs_busy, vi2cs_addressed, vi2cs_active;
  wire [6:0] vi2cs_bus_address;
  wire vi2cs_s_tvalid = vi2cs_s_tready && vi2cs_wait == vi2cs_delay;
  i2c_slave #(.FILTER_LEN(4)) vi2c_slave (
      .clk(clk), .rst(peer_rst), .release_bus(1'b0),
      .s_axis_data_tdata(vi2cs_tx[vi2cs_tx_idx[3:0]]), .s_axis_data_tvalid(vi2cs_s_tvalid),
      .s_axis_data_tready(vi2cs_s_tready), .s_axis_data_tlast(1'b1),
      .m_axis_data_tdata(vi2cs_m_tdata), .m_axis_data_tvalid(vi2cs_m_tvalid),
      .m_axis_data_tready(1'b1), .m_axis_data_tlast(vi2cs_m_tlast),
      .scl_i(pad[6]), .scl_o(vi2cs_scl_o), .scl_t(vi2cs_scl_t),
      .sda_i(pad[7]), .sda_o(vi2cs_sda_o), .sda_t(vi2cs_sda_t),
      .busy(vi2cs_busy), .bus_address(vi2cs_bus_address), .bus_addressed(vi2cs_addressed),
      .bus_active(vi2cs_active),
      .enable(sel_vi2c_slave), .device_address(vi2cs_address), .device_address_mask(7'h7f));
  always @(posedge clk) begin
    if (peer_rst) begin
      vi2cs_tx_idx <= 5'd0;
      vi2cs_wait <= 16'd0;
      vi2cs_count <= 8'd0;
      vi2cs_stops <= 8'd0;
      vi2cs_active_q <= 1'b0;
      vi2cs_read_stretched <= 1'b0;
      vi2cs_self_stop <= 1'b0;
    end else begin
      if (vi2c_slave.state_reg == 5'd5 && !vi2cs_scl_o) vi2cs_read_stretched <= 1'b1;
      if (vi2c_slave.state_reg == 5'd5 && vi2c_slave.stop_bit) vi2cs_self_stop <= 1'b1;
      if (vi2cs_s_tvalid) begin
        vi2cs_tx_idx <= vi2cs_tx_idx + 5'd1;
        vi2cs_wait <= 16'd0;
      end else if (vi2cs_s_tready && vi2cs_wait != vi2cs_delay) begin
        vi2cs_wait <= vi2cs_wait + 16'd1;
      end
      if (vi2cs_m_tvalid) begin
        vi2cs_log[vi2cs_count[4:0]] <= {vi2cs_m_tlast, vi2cs_m_tdata};
        vi2cs_count <= vi2cs_count + 8'd1;
      end
      vi2cs_active_q <= vi2cs_active;
      if (vi2cs_active_q && !vi2cs_active) vi2cs_stops <= vi2cs_stops + 8'd1;
    end
  end
  assign pad[6] = (sel_vi2c_slave && !vi2cs_scl_o) ? 1'b0 : 1'bz;
  assign pad[7] = (sel_vi2c_slave && !vi2cs_sda_o) ? 1'b0 : 1'bz;

  // alexforencich/verilog-i2c i2c_master (DUT target). One command per
  // vi2cm_go toggle: START, address, one byte (write vi2cm_wdata, or read
  // with NACK), STOP.
  reg sel_vi2c_master = 1'b0;
  reg [15:0] vi2cm_prescale = 16'd32;  // SCL quarter period in clocks
  reg [6:0] vi2cm_address = 7'h42;
  reg vi2cm_read = 1'b0;
  reg [7:0] vi2cm_wdata = 8'h00;
  reg vi2cm_go = 1'b0;
  reg vi2cm_go_seen = 1'b0;
  reg vi2cm_cmd_valid = 1'b0;
  reg vi2cm_data_valid = 1'b0;
  reg [7:0] vi2cm_log [0:15];
  reg [7:0] vi2cm_count = 8'd0;
  reg [7:0] vi2cm_missed_acks = 8'd0;
  reg [7:0] vi2cm_done = 8'd0;  // commands accepted
  wire vi2cm_cmd_ready, vi2cm_data_ready, vi2cm_m_tvalid, vi2cm_m_tlast;
  wire [7:0] vi2cm_m_tdata;
  wire vi2cm_scl_o, vi2cm_scl_t, vi2cm_sda_o, vi2cm_sda_t, vi2cm_busy, vi2cm_bus_control, vi2cm_active, vi2cm_missed_ack;
  i2c_master vi2c_master (
      .clk(clk), .rst(peer_rst),
      .s_axis_cmd_address(vi2cm_address), .s_axis_cmd_start(1'b1), .s_axis_cmd_read(vi2cm_read),
      .s_axis_cmd_write(!vi2cm_read), .s_axis_cmd_write_multiple(1'b0), .s_axis_cmd_stop(1'b1),
      .s_axis_cmd_valid(vi2cm_cmd_valid), .s_axis_cmd_ready(vi2cm_cmd_ready),
      .s_axis_data_tdata(vi2cm_wdata), .s_axis_data_tvalid(vi2cm_data_valid),
      .s_axis_data_tready(vi2cm_data_ready), .s_axis_data_tlast(1'b1),
      .m_axis_data_tdata(vi2cm_m_tdata), .m_axis_data_tvalid(vi2cm_m_tvalid),
      .m_axis_data_tready(1'b1), .m_axis_data_tlast(vi2cm_m_tlast),
      .scl_i(pad[6]), .scl_o(vi2cm_scl_o), .scl_t(vi2cm_scl_t),
      .sda_i(pad[7]), .sda_o(vi2cm_sda_o), .sda_t(vi2cm_sda_t),
      .busy(vi2cm_busy), .bus_control(vi2cm_bus_control), .bus_active(vi2cm_active),
      .missed_ack(vi2cm_missed_ack),
      .prescale(vi2cm_prescale), .stop_on_idle(1'b0));
  always @(posedge clk) begin
    if (peer_rst) begin
      vi2cm_go_seen <= vi2cm_go;
      vi2cm_cmd_valid <= 1'b0;
      vi2cm_data_valid <= 1'b0;
      vi2cm_count <= 8'd0;
      vi2cm_missed_acks <= 8'd0;
      vi2cm_done <= 8'd0;
    end else begin
      if (vi2cm_go != vi2cm_go_seen) begin
        vi2cm_go_seen <= vi2cm_go;
        vi2cm_cmd_valid <= 1'b1;
        vi2cm_data_valid <= !vi2cm_read;
      end
      if (vi2cm_cmd_valid && vi2cm_cmd_ready) begin
        vi2cm_cmd_valid <= 1'b0;
        vi2cm_done <= vi2cm_done + 8'd1;
      end
      if (vi2cm_data_valid && vi2cm_data_ready) vi2cm_data_valid <= 1'b0;
      if (vi2cm_m_tvalid) begin
        vi2cm_log[vi2cm_count[3:0]] <= vi2cm_m_tdata;
        vi2cm_count <= vi2cm_count + 8'd1;
      end
      if (vi2cm_missed_ack) vi2cm_missed_acks <= vi2cm_missed_acks + 8'd1;
    end
  end
  assign pad[6] = (sel_vi2c_master && !vi2cm_scl_o) ? 1'b0 : 1'bz;
  assign pad[7] = (sel_vi2c_master && !vi2cm_sda_o) ? 1'b0 : 1'bz;

  // cocotbext-i2c I2cMaster / I2cMemory: *_o = 0 pulls the line low.
  reg sel_py_i2c = 1'b0;
  reg py_i2c_scl_o = 1'b1, py_i2c_sda_o = 1'b1;
  assign pad[6] = (sel_py_i2c && !py_i2c_scl_o) ? 1'b0 : 1'bz;
  assign pad[7] = (sel_py_i2c && !py_i2c_sda_o) ? 1'b0 : 1'bz;
  // A second, independent cocotbext-i2c device on the same bus.
  reg sel_py_i2c_b = 1'b0;
  reg py_i2c_b_scl_o = 1'b1, py_i2c_b_sda_o = 1'b1;
  assign pad[6] = (sel_py_i2c_b && !py_i2c_b_scl_o) ? 1'b0 : 1'bz;
  assign pad[7] = (sel_py_i2c_b && !py_i2c_b_sda_o) ? 1'b0 : 1'bz;

  // =============================================================== JTAG
  // Hazard3 RISC-V JTAG-DTM: the IEEE 1149.1 TAP state machine with a 5-bit
  // IR (IDCODE 0x01, DTMCS 0x10, DMI 0x11, others BYPASS). Not a fully
  // 1149.1-conformant TAP: Capture-IR loads the current IR value instead of
  // the ...01 pattern the standard requires (hazard3_jtag_dtm.v line 110).
  // DUT = controller: TCK pad0,
  // TDI pad1, TDO pad2, TMS pad3. The DMI port is tied off.
  localparam [31:0] JTAG_IDCODE = 32'h1dec_a0e1;  // arbitrary; bit0 = 1 as IEEE 1149.1 requires
  reg sel_jtag = 1'b0;
  wire jtag_tdo;
  wire jtag_dmihardreset_req, jtag_psel, jtag_penable, jtag_pwrite;
  wire [8:0] jtag_paddr;
  wire [31:0] jtag_pwdata;
  // trst_n is registered so that every peer_rst assertion (including the one
  // present from time 0) produces a real falling edge: the TAP's TDO flop is
  // reset only by that edge or a falling TCK edge.
  reg jtag_trst_n = 1'b1;
  always @(posedge clk) jtag_trst_n <= ~peer_rst;
  hazard3_jtag_dtm #(.IDCODE(JTAG_IDCODE)) jtag_tap (
      .tck(pad[0]), .trst_n(jtag_trst_n), .tms(pad[3]), .tdi(pad[1]), .tdo(jtag_tdo),
      .dmihardreset_req(jtag_dmihardreset_req),
      .clk_dmi(clk), .rst_n_dmi(jtag_trst_n),
      .dmi_psel(jtag_psel), .dmi_penable(jtag_penable), .dmi_pwrite(jtag_pwrite),
      .dmi_paddr(jtag_paddr), .dmi_pwdata(jtag_pwdata),
      .dmi_prdata(32'h0), .dmi_pready(1'b1), .dmi_pslverr(1'b0));
  assign pad[2] = sel_jtag ? jtag_tdo : 1'bz;
  wire [3:0] jtag_tap_state = jtag_tap.tap_state;
  wire [4:0] jtag_ir = jtag_tap.ir;

  // Peer data tables and logs start as zeros (never X).
  integer mi;
  initial begin
    for (mi = 0; mi < 64; mi = mi + 1) begin
      vutx_mem[mi] = 8'h00; nslave_tx[mi] = 8'h00; nmaster_tx[mi] = 8'h00;
      vurx_log[mi] = 8'h00; nslave_log[mi] = 8'h00; nmaster_log[mi] = 8'h00;
    end
    for (mi = 0; mi < 32; mi = mi + 1) vi2cs_log[mi] = 9'h000;
    for (mi = 0; mi < 16; mi = mi + 1) begin
      vi2cs_tx[mi] = 8'h00; vi2cm_log[mi] = 8'h00;
    end
  end

endmodule

// Restore the default for the third-party sources compiled after this file
// (verilog-i2c relies on implicit nets).
`default_nettype wire
