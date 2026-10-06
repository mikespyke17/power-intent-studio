// ---------------------------------------------------------------------------
// spi_master -- mode 0 SPI master with a TX FIFO.
//
//   0x00 DATA    write pushes a byte, read returns the last received byte
//   0x04 STATUS  [0] busy, [1] tx empty, [2] tx full
//   0x08 CTRL    [0] enable, [8:1] clock divisor, [12] chip select
// ---------------------------------------------------------------------------
module spi_master
  import aura_pkg::*;
#(
  parameter int unsigned FIFO_DEPTH = 4
) (
  apb_if.cmp   apb,
  output logic sclk,
  output logic mosi,
  input  logic miso,
  output logic csn,
  output logic irq
);

  logic [7:0] div_q;
  logic       enable_q;
  logic       cs_q;
  logic [7:0] rx_q;

  logic       sel_access;
  logic       wr_stb;
  logic [1:0] reg_idx;

  assign sel_access  = apb.psel & apb.penable;
  assign wr_stb      = sel_access & apb.pwrite & apb.pready;
  assign reg_idx     = apb.paddr[3:2];
  assign apb.pready  = 1'b1;
  assign apb.pslverr = 1'b0;

  // ---- shift engine --------------------------------------------------------
  typedef enum logic [1:0] { S_IDLE, S_LOW, S_HIGH, S_LAST } sst_e;

  sst_e       st_q;
  logic [7:0] sh_q;
  logic [2:0] bit_q;
  logic [7:0] cnt_q;
  logic       sclk_q;

  logic [7:0] tx_dout;
  logic       tx_empty;
  logic       tx_full;
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

  assign tx_pop = enable_q & ~tx_empty & (st_q == S_IDLE);

  logic tick;
  assign tick = (cnt_q == 8'd0);

  always_ff @(posedge apb.clk or negedge apb.rst_n) begin
    if (!apb.rst_n) begin
      st_q   <= S_IDLE;
      sh_q   <= 8'h00;
      bit_q  <= 3'd0;
      cnt_q  <= 8'd0;
      sclk_q <= 1'b0;
      rx_q   <= 8'h00;
    end else begin
      if (cnt_q != 8'd0) cnt_q <= cnt_q - 8'd1;

      unique case (st_q)
        S_IDLE: begin
          sclk_q <= 1'b0;
          if (tx_pop) begin
            sh_q  <= tx_dout;
            bit_q <= 3'd7;
            cnt_q <= div_q;
            st_q  <= S_LOW;
          end
        end

        S_LOW: begin
          if (tick) begin
            sclk_q <= 1'b1;
            rx_q   <= {rx_q[6:0], miso};
            cnt_q  <= div_q;
            st_q   <= S_HIGH;
          end
        end

        S_HIGH: begin
          if (tick) begin
            sclk_q <= 1'b0;
            cnt_q  <= div_q;
            if (bit_q == 3'd0) begin
              st_q <= S_LAST;
            end else begin
              bit_q <= bit_q - 3'd1;
              sh_q  <= {sh_q[6:0], 1'b0};
              st_q  <= S_LOW;
            end
          end
        end

        S_LAST: st_q <= S_IDLE;

        default: st_q <= S_IDLE;
      endcase
    end
  end

  assign sclk = sclk_q;
  assign mosi = sh_q[7];
  assign csn  = ~cs_q | (st_q == S_IDLE);
  assign irq  = tx_empty & (st_q == S_IDLE);

  always_ff @(posedge apb.clk or negedge apb.rst_n) begin
    if (!apb.rst_n) begin
      div_q    <= 8'd8;
      enable_q <= 1'b0;
      cs_q     <= 1'b0;
    end else if (wr_stb && (reg_idx == 2'd2)) begin
      enable_q <= apb.pwdata[0];
      div_q    <= apb.pwdata[8:1];
      cs_q     <= apb.pwdata[12];
    end
  end

  always_comb begin
    unique case (reg_idx)
      2'd0:    apb.prdata = {24'd0, rx_q};
      2'd1:    apb.prdata = {29'd0, tx_full, tx_empty, (st_q != S_IDLE)};
      default: apb.prdata = {19'd0, cs_q, 3'd0, div_q, enable_q};
    endcase
  end

endmodule : spi_master


// ---------------------------------------------------------------------------
// timer_unit -- 32-bit down counter with auto-reload.
//
//   0x00 LOAD    reload value
//   0x04 VALUE   current count, read only
//   0x08 CTRL    [0] enable, [1] auto reload, [2] interrupt enable
//   0x0C ISR     [0] pending, write 1 to clear
// ---------------------------------------------------------------------------
module timer_unit
  import aura_pkg::*;
(
  apb_if.cmp   apb,
  output logic irq
);

  logic [DATA_W-1:0] load_q;
  logic [DATA_W-1:0] value_q;
  logic              enable_q;
  logic              reload_q;
  logic              irq_en_q;
  logic              pending_q;

  logic       sel_access;
  logic       wr_stb;
  logic [1:0] reg_idx;

  assign sel_access  = apb.psel & apb.penable;
  assign wr_stb      = sel_access & apb.pwrite & apb.pready;
  assign reg_idx     = apb.paddr[3:2];
  assign apb.pready  = 1'b1;
  assign apb.pslverr = 1'b0;

  always_ff @(posedge apb.clk or negedge apb.rst_n) begin
    if (!apb.rst_n) begin
      load_q    <= '0;
      value_q   <= '0;
      enable_q  <= 1'b0;
      reload_q  <= 1'b0;
      irq_en_q  <= 1'b0;
      pending_q <= 1'b0;
    end else begin
      if (enable_q) begin
        if (value_q == '0) begin
          pending_q <= 1'b1;
          value_q   <= reload_q ? load_q : '0;
          if (!reload_q) enable_q <= 1'b0;
        end else begin
          value_q <= value_q - DATA_W'(1);
        end
      end

      if (wr_stb) begin
        unique case (reg_idx)
          2'd0: begin
            load_q  <= apb.pwdata;
            value_q <= apb.pwdata;
          end
          2'd2: begin
            enable_q <= apb.pwdata[0];
            reload_q <= apb.pwdata[1];
            irq_en_q <= apb.pwdata[2];
          end
          2'd3: if (apb.pwdata[0]) pending_q <= 1'b0;
          default: ;
        endcase
      end
    end
  end

  always_comb begin
    unique case (reg_idx)
      2'd0:    apb.prdata = load_q;
      2'd1:    apb.prdata = value_q;
      2'd2:    apb.prdata = {29'd0, irq_en_q, reload_q, enable_q};
      default: apb.prdata = {31'd0, pending_q};
    endcase
  end

  assign irq = pending_q & irq_en_q;

endmodule : timer_unit


// ---------------------------------------------------------------------------
// gpio -- bidirectional general purpose I/O with edge detection.
//
//   0x00 OUT   output value
//   0x04 IN    synchronised input value, read only
//   0x08 OE    output enable, per bit
//   0x0C ISR   edge flags, write 1 to clear
// ---------------------------------------------------------------------------
module gpio
  import aura_pkg::*;
#(
  parameter int unsigned N_IO = 16
) (
  apb_if.cmp                apb,
  input  logic [N_IO-1:0]   gpio_in,
  output logic [N_IO-1:0]   gpio_out,
  output logic [N_IO-1:0]   gpio_oe,
  output logic              irq
);

  logic [N_IO-1:0] out_q;
  logic [N_IO-1:0] oe_q;
  logic [N_IO-1:0] sync0_q;
  logic [N_IO-1:0] sync1_q;
  logic [N_IO-1:0] sync2_q;
  logic [N_IO-1:0] isr_q;

  logic       sel_access;
  logic       wr_stb;
  logic [1:0] reg_idx;

  assign sel_access  = apb.psel & apb.penable;
  assign wr_stb      = sel_access & apb.pwrite & apb.pready;
  assign reg_idx     = apb.paddr[3:2];
  assign apb.pready  = 1'b1;
  assign apb.pslverr = 1'b0;

  always_ff @(posedge apb.clk or negedge apb.rst_n) begin
    if (!apb.rst_n) begin
      out_q   <= '0;
      oe_q    <= '0;
      sync0_q <= '0;
      sync1_q <= '0;
      sync2_q <= '0;
      isr_q   <= '0;
    end else begin
      sync0_q <= gpio_in;
      sync1_q <= sync0_q;
      sync2_q <= sync1_q;
      isr_q   <= isr_q | (sync1_q ^ sync2_q);

      if (wr_stb) begin
        unique case (reg_idx)
          2'd0:    out_q <= apb.pwdata[N_IO-1:0];
          2'd2:    oe_q  <= apb.pwdata[N_IO-1:0];
          2'd3:    isr_q <= isr_q & ~apb.pwdata[N_IO-1:0];
          default: ;
        endcase
      end
    end
  end

  assign gpio_out = out_q;
  assign gpio_oe  = oe_q;
  assign irq      = |isr_q;

  always_comb begin
    unique case (reg_idx)
      2'd0:    apb.prdata = {{(DATA_W-N_IO){1'b0}}, out_q};
      2'd1:    apb.prdata = {{(DATA_W-N_IO){1'b0}}, sync1_q};
      2'd2:    apb.prdata = {{(DATA_W-N_IO){1'b0}}, oe_q};
      default: apb.prdata = {{(DATA_W-N_IO){1'b0}}, isr_q};
    endcase
  end

endmodule : gpio
