// ---------------------------------------------------------------------------
// sync_fifo -- small synchronous FIFO shared by the peripherals.
// ---------------------------------------------------------------------------
module sync_fifo #(
  parameter int unsigned WIDTH = 8,
  parameter int unsigned DEPTH = 8,
  parameter int unsigned PTR_W = $clog2(DEPTH)
) (
  input  logic             clk,
  input  logic             rst_n,
  input  logic             push,
  input  logic [WIDTH-1:0] din,
  input  logic             pop,
  output logic [WIDTH-1:0] dout,
  output logic             empty,
  output logic             full,
  output logic [PTR_W:0]   level
);

  logic [WIDTH-1:0] mem_q [DEPTH];
  logic [PTR_W-1:0] rd_q;
  logic [PTR_W-1:0] wr_q;
  logic [PTR_W:0]   cnt_q;

  assign empty = (cnt_q == '0);
  assign full  = (cnt_q == (PTR_W+1)'(DEPTH));
  assign dout  = mem_q[rd_q];
  assign level = cnt_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_q  <= '0;
      wr_q  <= '0;
      cnt_q <= '0;
    end else begin
      if (push && !full) begin
        mem_q[wr_q] <= din;
        wr_q        <= (wr_q == PTR_W'(DEPTH-1)) ? '0 : (wr_q + PTR_W'(1));
      end
      if (pop && !empty) begin
        rd_q <= (rd_q == PTR_W'(DEPTH-1)) ? '0 : (rd_q + PTR_W'(1));
      end
      unique case ({push && !full, pop && !empty})
        2'b10:   cnt_q <= cnt_q + (PTR_W+1)'(1);
        2'b01:   cnt_q <= cnt_q - (PTR_W+1)'(1);
        default: cnt_q <= cnt_q;
      endcase
    end
  end

endmodule : sync_fifo


// ---------------------------------------------------------------------------
// baud_gen -- fractional-free integer baud tick generator.
// ---------------------------------------------------------------------------
module baud_gen #(
  parameter int unsigned DIV_W = 16
) (
  input  logic             clk,
  input  logic             rst_n,
  input  logic             enable,
  input  logic [DIV_W-1:0] divisor,
  output logic             tick,     // one pulse per bit period
  output logic             tick_x16  // one pulse per 1/16 bit period
);

  logic [DIV_W-1:0] cnt_q;
  logic [3:0]       ovs_q;
  logic             tick_x16_q;
  logic             tick_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cnt_q      <= '0;
      ovs_q      <= 4'd0;
      tick_x16_q <= 1'b0;
      tick_q     <= 1'b0;
    end else begin
      tick_x16_q <= 1'b0;
      tick_q     <= 1'b0;
      if (!enable) begin
        cnt_q <= '0;
        ovs_q <= 4'd0;
      end else if (cnt_q >= divisor) begin
        cnt_q      <= '0;
        tick_x16_q <= 1'b1;
        ovs_q      <= ovs_q + 4'd1;
        if (ovs_q == 4'd15) tick_q <= 1'b1;
      end else begin
        cnt_q <= cnt_q + DIV_W'(1);
      end
    end
  end

  assign tick     = tick_q;
  assign tick_x16 = tick_x16_q;

endmodule : baud_gen


// ---------------------------------------------------------------------------
// uart_tx -- 8N1 transmitter.
// ---------------------------------------------------------------------------
module uart_tx (
  input  logic       clk,
  input  logic       rst_n,
  input  logic       tick,
  input  logic       start,
  input  logic [7:0] data,
  output logic       busy,
  output logic       txd
);

  typedef enum logic [1:0] { T_IDLE, T_START, T_DATA, T_STOP } tst_e;

  tst_e       st_q;
  logic [7:0] sh_q;
  logic [2:0] bit_q;
  logic       txd_q;

  assign busy = (st_q != T_IDLE);
  assign txd  = txd_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      st_q  <= T_IDLE;
      sh_q  <= 8'h00;
      bit_q <= 3'd0;
      txd_q <= 1'b1;
    end else begin
      unique case (st_q)
        T_IDLE: begin
          txd_q <= 1'b1;
          if (start) begin
            sh_q <= data;
            st_q <= T_START;
          end
        end

        T_START: if (tick) begin
                   txd_q <= 1'b0;
                   bit_q <= 3'd0;
                   st_q  <= T_DATA;
                 end

        T_DATA: if (tick) begin
                  txd_q <= sh_q[0];
                  sh_q  <= {1'b0, sh_q[7:1]};
                  if (bit_q == 3'd7) st_q  <= T_STOP;
                  else               bit_q <= bit_q + 3'd1;
                end

        T_STOP: if (tick) begin
                  txd_q <= 1'b1;
                  st_q  <= T_IDLE;
                end

        default: st_q <= T_IDLE;
      endcase
    end
  end

endmodule : uart_tx


// ---------------------------------------------------------------------------
// uart_rx -- 8N1 receiver with 16x oversampling and mid-bit sampling.
// ---------------------------------------------------------------------------
module uart_rx (
  input  logic       clk,
  input  logic       rst_n,
  input  logic       tick_x16,
  input  logic       rxd,
  output logic [7:0] data,
  output logic       valid,
  output logic       frame_err
);

  typedef enum logic [1:0] { R_IDLE, R_START, R_DATA, R_STOP } rst_e;

  rst_e       st_q;
  logic [3:0] ovs_q;
  logic [2:0] bit_q;
  logic [7:0] sh_q;
  logic       valid_q;
  logic       ferr_q;
  logic [1:0] sync_q;

  assign data      = sh_q;
  assign valid     = valid_q;
  assign frame_err = ferr_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      st_q    <= R_IDLE;
      ovs_q   <= 4'd0;
      bit_q   <= 3'd0;
      sh_q    <= 8'h00;
      valid_q <= 1'b0;
      ferr_q  <= 1'b0;
      sync_q  <= 2'b11;
    end else begin
      valid_q <= 1'b0;
      sync_q  <= {sync_q[0], rxd};

      if (tick_x16) begin
        unique case (st_q)
          R_IDLE: begin
            if (!sync_q[1]) begin
              ovs_q <= 4'd0;
              st_q  <= R_START;
            end
          end

          R_START: begin
            if (ovs_q == 4'd7) begin
              if (!sync_q[1]) begin
                ovs_q <= 4'd0;
                bit_q <= 3'd0;
                st_q  <= R_DATA;
              end else begin
                st_q <= R_IDLE;      // false start
              end
            end else begin
              ovs_q <= ovs_q + 4'd1;
            end
          end

          R_DATA: begin
            if (ovs_q == 4'd15) begin
              ovs_q <= 4'd0;
              sh_q  <= {sync_q[1], sh_q[7:1]};
              if (bit_q == 3'd7) st_q  <= R_STOP;
              else               bit_q <= bit_q + 3'd1;
            end else begin
              ovs_q <= ovs_q + 4'd1;
            end
          end

          R_STOP: begin
            if (ovs_q == 4'd15) begin
              ovs_q   <= 4'd0;
              valid_q <= 1'b1;
              ferr_q  <= ~sync_q[1];
              st_q    <= R_IDLE;
            end else begin
              ovs_q <= ovs_q + 4'd1;
            end
          end

          default: st_q <= R_IDLE;
        endcase
      end
    end
  end

endmodule : uart_rx


// ---------------------------------------------------------------------------
// uart -- APB completer wrapping the baud generator, both FSMs and two FIFOs.
//
//   0x00 DATA    write pushes TX, read pops RX
//   0x04 STATUS  [0] tx full, [1] tx empty, [2] rx empty, [3] rx full,
//                [4] frame error, [5] tx busy
//   0x08 CTRL    [0] enable
//   0x0C DIV     baud divisor (clock cycles per 1/16 bit)
// ---------------------------------------------------------------------------
module uart
  import aura_pkg::*;
#(
  parameter int unsigned FIFO_DEPTH = 8,
  parameter int unsigned DIV_W      = 16
) (
  apb_if.cmp   apb,
  input  logic rxd,
  output logic txd,
  output logic irq
);

  logic [DIV_W-1:0] div_q;
  logic             enable_q;
  logic             ferr_q;

  logic tick;
  logic tick_x16;

  baud_gen #(.DIV_W(DIV_W)) u_baud (
    .clk      (apb.clk),
    .rst_n    (apb.rst_n),
    .enable   (enable_q),
    .divisor  (div_q),
    .tick     (tick),
    .tick_x16 (tick_x16)
  );

  // ---- APB decode ----------------------------------------------------------
  logic       sel_access;
  logic       wr_stb;
  logic       rd_stb;
  logic [1:0] reg_idx;

  assign sel_access = apb.psel & apb.penable;
  assign wr_stb     = sel_access &  apb.pwrite & apb.pready;
  assign rd_stb     = sel_access & ~apb.pwrite & apb.pready;
  assign reg_idx    = apb.paddr[3:2];

  assign apb.pready  = 1'b1;
  assign apb.pslverr = 1'b0;

  // ---- TX ------------------------------------------------------------------
  logic [7:0] tx_dout;
  logic       tx_empty;
  logic       tx_full;
  logic       tx_busy;
  logic       tx_pop;
  logic [$clog2(FIFO_DEPTH):0] tx_level;

  sync_fifo #(.WIDTH(8), .DEPTH(FIFO_DEPTH)) u_tx_fifo (
    .clk   (apb.clk),
    .rst_n (apb.rst_n),
    .push  (wr_stb & (reg_idx == 2'd0)),
    .din   (apb.pwdata[7:0]),
    .pop   (tx_pop),
    .dout  (tx_dout),
    .empty (tx_empty),
    .full  (tx_full),
    .level (tx_level)
  );

  assign tx_pop = ~tx_empty & ~tx_busy & enable_q;

  uart_tx u_tx (
    .clk   (apb.clk),
    .rst_n (apb.rst_n),
    .tick  (tick),
    .start (tx_pop),
    .data  (tx_dout),
    .busy  (tx_busy),
    .txd   (txd)
  );

  // ---- RX ------------------------------------------------------------------
  logic [7:0] rx_data;
  logic       rx_valid;
  logic       rx_ferr;
  logic [7:0] rx_dout;
  logic       rx_empty;
  logic       rx_full;
  logic [$clog2(FIFO_DEPTH):0] rx_level;

  uart_rx u_rx (
    .clk       (apb.clk),
    .rst_n     (apb.rst_n),
    .tick_x16  (tick_x16),
    .rxd       (rxd),
    .data      (rx_data),
    .valid     (rx_valid),
    .frame_err (rx_ferr)
  );

  sync_fifo #(.WIDTH(8), .DEPTH(FIFO_DEPTH)) u_rx_fifo (
    .clk   (apb.clk),
    .rst_n (apb.rst_n),
    .push  (rx_valid & ~rx_ferr),
    .din   (rx_data),
    .pop   (rd_stb & (reg_idx == 2'd0)),
    .dout  (rx_dout),
    .empty (rx_empty),
    .full  (rx_full),
    .level (rx_level)
  );

  // ---- registers -----------------------------------------------------------
  always_ff @(posedge apb.clk or negedge apb.rst_n) begin
    if (!apb.rst_n) begin
      div_q    <= DIV_W'(27);       // ~115200 baud from a 50 MHz clock
      enable_q <= 1'b1;
      ferr_q   <= 1'b0;
    end else begin
      if (rx_valid && rx_ferr) ferr_q <= 1'b1;
      if (wr_stb) begin
        unique case (reg_idx)
          2'd2: begin
            enable_q <= apb.pwdata[0];
          end
          2'd3: div_q <= apb.pwdata[DIV_W-1:0];
          default: ferr_q <= ferr_q & ~apb.pwdata[4];
        endcase
      end
    end
  end

  always_comb begin
    unique case (reg_idx)
      2'd0:    apb.prdata = {24'd0, rx_dout};
      2'd1:    apb.prdata = {26'd0, tx_busy, ferr_q, rx_full, rx_empty,
                             tx_empty, tx_full};
      2'd2:    apb.prdata = {31'd0, enable_q};
      default: apb.prdata = {{(DATA_W-DIV_W){1'b0}}, div_q};
    endcase
  end

  assign irq = ~rx_empty | ferr_q;

endmodule : uart
