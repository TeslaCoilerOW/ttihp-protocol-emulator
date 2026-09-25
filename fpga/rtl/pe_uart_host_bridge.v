/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * UART-to-nibble host bridge (FPGA prototype only, not part of the ASIC).
 *
 * Lets a PC drive the chip's synchronous nibble host port over the board's
 * USB-UART, with no external host hardware. The bridge runs in the core
 * clock domain and performs the ready/valid nibble transfers of docs/isa.md
 * ("Host interface"). It drives ui_in from registers and registers uo_out
 * at every edge. Each nibble takes two cycles: valid (or ready) is high for
 * one edge, and in the next cycle the registered uo_out shows whether the
 * core accepted it at that edge.
 *
 * Serial format: 8N1 at BAUD (default 1 Mbaud; CLK_HZ/BAUD should be an
 * integer, e.g. 50 at 50 MHz, 12 at 12 MHz). Frames are a command byte plus
 * fixed-length payload; multi-byte values are little endian. A partial frame
 * is dropped after 10 ms without a byte. Replies are sent in command order;
 * commands may be pipelined up to the 64-byte receive FIFO.
 *
 *   'V'                      -> 'V' ver board clock clk_hz[4] baud_div[2]   (10 bytes)
 *   'S'                      -> 'S' uo uio_pad uio_oe uio_out ui flags      (7 bytes)
 *   'C'                      -> 'C' cycle_counter[4]                        (5 bytes)
 *   'L' limit[4]             -> 'K'   set the transfer timeout in cycles (0 = default)
 *   'W' window data[4]       -> 'K' | 'T' nibbles | 'E' | 'D'
 *   'R' window               -> 'K' data[4] | 'T' nibbles | 'E' | 'D'
 *                               window bit 7 set: spend one edge in another
 *                               window first (fresh status snapshot after
 *                               READ_SELECT, like test/harness.py status())
 *   'X' cycles               -> 'K' | 'D'   hold rst_n low for max(cycles,1) clocks, window 0
 *   'N' ena                  -> 'K' | 'D'   drive ena = bit 0
 *   anything else            -> '?'
 *
 *   'W': windows 0..2 (3 is never write-ready -> 'E'); 'R': windows 0 and 3.
 *   'T': no transfer completed within the timeout; the bridge then changes
 *        window (isa.md: a window change abandons the partial word), and the
 *        reply carries the number of nibbles accepted before the timeout.
 *   'D': the external pin host owns the TT host port (enable = 0).
 *   flags: bit0 receive FIFO overflow seen, bit1 UART framing error seen,
 *          bit2 enable (bridge owns the host port), bit3 core rst_n,
 *          bit4 core ena.
 */

`default_nettype none

module pe_uart_rx #(
    parameter integer DIV = 50
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       rx,
    output reg  [7:0] data = 8'd0,
    output reg        valid = 1'b0,
    output reg        ferr = 1'b0
);
  (* ASYNC_REG = "TRUE" *) reg rx_m = 1'b1;
  (* ASYNC_REG = "TRUE" *) reg rx_s = 1'b1;
  reg        busy = 1'b0;
  reg [15:0] cnt = 16'd0;
  reg [3:0]  bitn = 4'd0;
  reg [7:0]  sh = 8'd0;

  always @(posedge clk) begin
    rx_m  <= rx;
    rx_s  <= rx_m;
    valid <= 1'b0;
    ferr  <= 1'b0;
    if (rst) begin
      busy <= 1'b0;
      cnt  <= 16'd0;
      bitn <= 4'd0;
    end else if (!busy) begin
      if (!rx_s) begin               // start bit edge: go to its middle
        busy <= 1'b1;
        cnt  <= DIV / 2 - 1;
        bitn <= 4'd0;
      end
    end else if (cnt != 16'd0) begin
      cnt <= cnt - 16'd1;
    end else begin
      cnt <= DIV - 1;
      if (bitn == 4'd0) begin
        if (rx_s) busy <= 1'b0;      // glitch, not a start bit
        else bitn <= 4'd1;
      end else if (bitn <= 4'd8) begin
        sh   <= {rx_s, sh[7:1]};     // LSB first
        bitn <= bitn + 4'd1;
      end else begin                 // stop bit
        busy <= 1'b0;
        if (rx_s) begin
          data  <= sh;
          valid <= 1'b1;
        end else begin
          ferr <= 1'b1;
        end
      end
    end
  end
endmodule

module pe_uart_tx #(
    parameter integer DIV = 50
) (
    input  wire       clk,
    input  wire       rst,
    input  wire [7:0] data,
    input  wire       start,   // accepted when !busy
    output wire       busy,
    output reg        tx = 1'b1
);
  reg [9:0]  sh = 10'h3ff;
  reg [3:0]  n = 4'd0;
  reg [15:0] cnt = 16'd0;
  assign busy = (n != 4'd0) || (cnt != 16'd0);

  always @(posedge clk) begin
    if (rst) begin
      tx  <= 1'b1;
      n   <= 4'd0;
      cnt <= 16'd0;
    end else if (n == 4'd0 && cnt == 16'd0) begin
      tx <= 1'b1;
      if (start) begin
        sh <= {1'b1, data, 1'b0};
        n  <= 4'd10;
      end
    end else if (cnt != 16'd0) begin
      cnt <= cnt - 16'd1;
    end else begin
      tx  <= sh[0];
      sh  <= {1'b1, sh[9:1]};
      cnt <= DIV - 1;
      n   <= n - 4'd1;
    end
  end
endmodule

module pe_byte_fifo #(
    parameter integer AW = 6
) (
    input  wire       clk,
    input  wire       rst,
    input  wire [7:0] wdata,
    input  wire       push,
    output wire       full,
    output wire [7:0] rdata,
    input  wire       pop,
    output wire       empty,
    output wire [AW:0] count
);
  reg [7:0]  mem [0:(1 << AW) - 1];
  reg [AW:0] wp = 0;
  reg [AW:0] rp = 0;
  assign count = wp - rp;
  assign empty = (wp == rp);
  assign full  = (count == (1 << AW));
  assign rdata = mem[rp[AW-1:0]];
  always @(posedge clk) begin
    if (rst) begin
      wp <= 0;
      rp <= 0;
    end else begin
      if (push && !full) begin
        mem[wp[AW-1:0]] <= wdata;
        wp <= wp + 1'b1;
      end
      if (pop && !empty) rp <= rp + 1'b1;
    end
  end
endmodule

module pe_uart_host_bridge #(
    parameter integer CLK_HZ   = 50_000_000,
    parameter integer BAUD     = 1_000_000,
    parameter [7:0]   BOARD_ID = 8'h00,
    parameter [7:0]   CLOCK_ID = 8'h00
) (
    input  wire       clk,
    input  wire       rst,        // synchronous, active high
    input  wire       enable,     // 1: the bridge owns the TT host port
    input  wire       uart_rx,
    output wire       uart_tx,
    output reg  [7:0] ui = 8'd0,
    input  wire [7:0] uo,
    output reg        core_rst_n = 1'b1,
    output reg        core_ena = 1'b1,
    input  wire [7:0] uio_pad,
    input  wire [7:0] uio_out,
    input  wire [7:0] uio_oe,
    output wire       activity
);
  localparam integer DIV           = (CLK_HZ + BAUD / 2) / BAUD;
  localparam integer FRAME_TIMEOUT = CLK_HZ / 100;          // 10 ms
  localparam [31:0]  DEF_LIMIT     = CLK_HZ / 50;           // 20 ms per word
  localparam [7:0]   VERSION       = 8'd1;
  localparam [31:0]  CLK_HZ_W      = CLK_HZ;
  localparam [15:0]  DIV_W         = DIV;
  localparam [23:0]  FRAME_TO_W    = FRAME_TIMEOUT;

  // ---- serial side -------------------------------------------------------
  wire [7:0] rx_byte;
  wire       rx_valid, rx_ferr;
  pe_uart_rx #(.DIV(DIV)) u_rx (
      .clk(clk), .rst(rst), .rx(uart_rx), .data(rx_byte), .valid(rx_valid), .ferr(rx_ferr));

  wire [7:0] rxf_data;
  wire       rxf_empty, rxf_full;
  reg        rxf_pop = 1'b0;
  pe_byte_fifo #(.AW(6)) u_rxf (
      .clk(clk), .rst(rst), .wdata(rx_byte), .push(rx_valid), .full(rxf_full),
      .rdata(rxf_data), .pop(rxf_pop), .empty(rxf_empty), .count());

  reg  [7:0] txf_wdata = 8'd0;
  reg        txf_push = 1'b0;
  wire [7:0] txf_data;
  wire       txf_empty, txf_full;
  wire [6:0] txf_count;
  wire       tx_busy;
  wire       tx_start = !txf_empty && !tx_busy;
  pe_byte_fifo #(.AW(6)) u_txf (
      .clk(clk), .rst(rst), .wdata(txf_wdata), .push(txf_push), .full(txf_full),
      .rdata(txf_data), .pop(tx_start), .empty(txf_empty), .count(txf_count));
  pe_uart_tx #(.DIV(DIV)) u_tx (
      .clk(clk), .rst(rst), .data(txf_data), .start(tx_start), .busy(tx_busy), .tx(uart_tx));

  // ---- status ------------------------------------------------------------
  reg [7:0]  uo_q = 8'd0;   // uo_out captured at every edge
  reg        ovf_seen = 1'b0;
  reg        ferr_seen = 1'b0;
  reg [31:0] cycles = 32'd0;
  reg [23:0] act_stretch = 24'd0;
  always @(posedge clk) begin
    cycles <= cycles + 32'd1;
    uo_q   <= uo;
    if (rst) begin
      ovf_seen  <= 1'b0;
      ferr_seen <= 1'b0;
    end else begin
      if (rx_valid && rxf_full) ovf_seen <= 1'b1;
      if (rx_ferr) ferr_seen <= 1'b1;
    end
    if (rx_valid || tx_start) act_stretch <= {24{1'b1}};
    else if (act_stretch != 24'd0) act_stretch <= act_stretch - 24'd1;
  end
  assign activity = act_stretch[23];

  // ---- command engine ----------------------------------------------------
  localparam [3:0] S_IDLE = 4'd0, S_ARGS = 4'd1, S_EXEC = 4'd2, S_WRITE = 4'd3,
                   S_READ = 4'd4, S_RESET = 4'd5, S_REPLY = 4'd6, S_BOUNCE = 4'd7;

  reg [3:0]  state = S_IDLE;
  reg [7:0]  cmd = 8'd0;
  reg [2:0]  nargs = 3'd0;
  reg [2:0]  argi = 3'd0;
  reg [7:0]  arg [0:4];
  reg [23:0] gap = 24'd0;
  reg [1:0]  win = 2'd0;
  reg [31:0] word = 32'd0;
  reg [2:0]  nib = 3'd0;
  reg        phase = 1'b0;
  reg [31:0] limit = DEF_LIMIT;
  reg [31:0] tcount = 32'd0;
  reg [7:0]  rcnt = 8'd0;
  reg [7:0]  rbuf [0:9];
  reg [3:0]  rlen = 4'd0;
  reg [3:0]  ridx = 4'd0;

  integer k;
  initial for (k = 0; k < 10; k = k + 1) rbuf[k] = 8'd0;
  initial for (k = 0; k < 5; k = k + 1) arg[k] = 8'd0;

  function [2:0] payload_len(input [7:0] c);
    case (c)
      "W": payload_len = 3'd5;
      "L": payload_len = 3'd4;
      "R", "X", "N": payload_len = 3'd1;
      default: payload_len = 3'd0;
    endcase
  endfunction

  wire [31:0] arg32 = {arg[3], arg[2], arg[1], arg[0]};
  wire [31:0] warg  = {arg[4], arg[3], arg[2], arg[1]};

  always @(posedge clk) begin
    rxf_pop  <= 1'b0;
    txf_push <= 1'b0;
    if (rst) begin
      state      <= S_IDLE;
      ui         <= 8'd0;
      win        <= 2'd0;
      core_rst_n <= 1'b1;
      core_ena   <= 1'b1;
      limit      <= DEF_LIMIT;
      rlen       <= 4'd0;
      ridx       <= 4'd0;
    end else begin
      case (state)
        S_IDLE: begin
          if (!rxf_empty && !rxf_pop) begin
            rxf_pop <= 1'b1;
            cmd     <= rxf_data;
            nargs   <= payload_len(rxf_data);
            argi    <= 3'd0;
            gap     <= 24'd0;
            state   <= (payload_len(rxf_data) == 3'd0) ? S_EXEC : S_ARGS;
          end
        end

        S_ARGS: begin
          if (!rxf_empty && !rxf_pop) begin
            rxf_pop   <= 1'b1;
            arg[argi] <= rxf_data;
            argi      <= argi + 3'd1;
            gap       <= 24'd0;
            if (argi + 3'd1 == nargs) state <= S_EXEC;
          end else if (gap == FRAME_TO_W) begin
            state <= S_IDLE;                  // drop the partial frame
          end else begin
            gap <= gap + 24'd1;
          end
        end

        S_EXEC: begin
          ridx  <= 4'd0;
          state <= S_REPLY;
          case (cmd)
            "V": begin
              rbuf[0] <= "V";          rbuf[1] <= VERSION;
              rbuf[2] <= BOARD_ID;     rbuf[3] <= CLOCK_ID;
              rbuf[4] <= CLK_HZ_W[7:0];   rbuf[5] <= CLK_HZ_W[15:8];
              rbuf[6] <= CLK_HZ_W[23:16]; rbuf[7] <= CLK_HZ_W[31:24];
              rbuf[8] <= DIV_W[7:0];      rbuf[9] <= DIV_W[15:8];
              rlen <= 4'd10;
            end
            "S": begin
              rbuf[0] <= "S"; rbuf[1] <= uo_q; rbuf[2] <= uio_pad; rbuf[3] <= uio_oe;
              rbuf[4] <= uio_out; rbuf[5] <= ui;
              rbuf[6] <= {3'b000, core_ena, core_rst_n, enable, ferr_seen, ovf_seen};
              rlen <= 4'd7;
            end
            "C": begin
              rbuf[0] <= "C"; rbuf[1] <= cycles[7:0]; rbuf[2] <= cycles[15:8];
              rbuf[3] <= cycles[23:16]; rbuf[4] <= cycles[31:24];
              rlen <= 4'd5;
            end
            "L": begin
              limit   <= (arg32 == 32'd0) ? DEF_LIMIT : arg32;
              rbuf[0] <= "K";
              rlen    <= 4'd1;
            end
            "W": begin
              rlen <= 4'd1;
              if (!enable) rbuf[0] <= "D";
              else if (arg[0] > 8'd2) rbuf[0] <= "E";
              else begin
                win    <= arg[0][1:0];
                word   <= warg;
                nib    <= 3'd0;
                tcount <= 32'd0;
                ui     <= {arg[0][1:0], 2'b01, warg[3:0]};
                phase  <= 1'b0;
                state  <= S_WRITE;
              end
            end
            "R": begin
              rlen <= 4'd1;
              if (!enable) rbuf[0] <= "D";
              else if (arg[0][6:0] != 7'd0 && arg[0][6:0] != 7'd3) rbuf[0] <= "E";
              else begin
                win    <= arg[0][1:0];
                word   <= 32'd0;
                nib    <= 3'd0;
                tcount <= 32'd0;
                if (arg[0][7]) begin         // bounce: one edge in another window first
                  ui    <= {arg[0][1:0] ^ 2'b01, 6'b000000};
                  state <= S_BOUNCE;
                end else begin
                  ui    <= {arg[0][1:0], 2'b10, 4'b0000};
                  phase <= 1'b0;
                  state <= S_READ;
                end
              end
            end
            "X": begin
              rlen <= 4'd1;
              if (!enable) rbuf[0] <= "D";
              else begin
                core_rst_n <= 1'b0;
                ui         <= 8'd0;
                win        <= 2'd0;
                rcnt       <= (arg[0] == 8'd0) ? 8'd1 : arg[0];
                state      <= S_RESET;
              end
            end
            "N": begin
              rlen <= 4'd1;
              if (!enable) rbuf[0] <= "D";
              else begin
                core_ena <= arg[0][0];
                rbuf[0]  <= "K";
              end
            end
            default: begin
              rbuf[0] <= "?";
              rlen    <= 4'd1;
            end
          endcase
        end

        // Two cycles per nibble. "Present": write-valid (ui[4]) is high for
        // exactly one edge, and uo_q captures the pre-edge uo_out at that
        // edge. "Check": write-valid is low and uo_q[4] tells whether the
        // core accepted the nibble at that edge (ready AND valid). Only a
        // register looks at uo_out, so no bridge logic is added to the
        // core's combinational uo_out paths.
        S_WRITE: begin
          tcount <= tcount + 32'd1;
          if (!phase) begin
            phase <= 1'b1;
            ui[4] <= 1'b0;
          end else begin
            phase <= 1'b0;
            if (uo_q[4]) begin
              if (nib == 3'd7) begin
                ui      <= {win, 6'b000000};
                rbuf[0] <= "K";
                rlen    <= 4'd1;
                state   <= S_REPLY;
              end else begin
                nib <= nib + 3'd1;
                ui  <= {win, 2'b01, word[4 * (nib + 3'd1) +: 4]};
              end
            end else if (tcount >= limit) begin
              win     <= win ^ 2'b01;          // abandon the partial word
              ui      <= {win ^ 2'b01, 6'b000000};
              rbuf[0] <= "T";
              rbuf[1] <= {5'd0, nib};
              rlen    <= 4'd2;
              state   <= S_REPLY;
            end else begin
              ui[4] <= 1'b1;                   // present the same nibble again
            end
          end
        end

        // Window bounce before a read: drops a read word captured before the
        // preceding READ_SELECT (docs/isa.md: snapshot at first presentation).
        S_BOUNCE: begin
          ui    <= {win, 2'b10, 4'b0000};
          phase <= 1'b0;
          state <= S_READ;
        end

        // Same two-cycle scheme with read-ready (ui[5]) and read-valid (uo[5]);
        // the nibble is uo_q[3:0] from the accepting edge.
        S_READ: begin
          tcount <= tcount + 32'd1;
          if (!phase) begin
            phase <= 1'b1;
            ui[5] <= 1'b0;
          end else begin
            phase <= 1'b0;
            if (uo_q[5]) begin
              word[4 * nib +: 4] <= uo_q[3:0];
              if (nib == 3'd7) begin
                ui      <= {win, 6'b000000};
                rbuf[0] <= "K";
                rbuf[1] <= word[7:0];
                rbuf[2] <= word[15:8];
                rbuf[3] <= word[23:16];
                rbuf[4] <= {uo_q[3:0], word[27:24]};
                rlen    <= 4'd5;
                state   <= S_REPLY;
              end else begin
                nib   <= nib + 3'd1;
                ui[5] <= 1'b1;
              end
            end else if (tcount >= limit) begin
              win     <= win ^ 2'b01;
              ui      <= {win ^ 2'b01, 6'b000000};
              rbuf[0] <= "T";
              rbuf[1] <= {5'd0, nib};
              rlen    <= 4'd2;
              state   <= S_REPLY;
            end else begin
              ui[5] <= 1'b1;
            end
          end
        end

        S_RESET: begin
          if (rcnt == 8'd1) begin
            core_rst_n <= 1'b1;
            rbuf[0]    <= "K";
            rlen       <= 4'd1;
            state      <= S_REPLY;
          end else begin
            rcnt <= rcnt - 8'd1;
          end
        end

        // Queue the reply; one byte per clock while the transmit FIFO has room.
        S_REPLY: begin
          if (ridx == rlen) begin
            state <= S_IDLE;
          end else if (!txf_full && !txf_push) begin
            txf_wdata <= rbuf[ridx];
            txf_push  <= 1'b1;
            ridx      <= ridx + 4'd1;
          end
        end

        default: state <= S_IDLE;
      endcase
    end
  end

endmodule

`default_nettype wire
