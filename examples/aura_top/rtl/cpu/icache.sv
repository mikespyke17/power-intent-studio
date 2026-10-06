// ---------------------------------------------------------------------------
// icache -- direct-mapped, one word per line instruction cache.
//
// Tag and data arrays are separate single-port SRAM hard macros.  On a miss
// the controller issues a single AXI4-Lite read and refills the line.
// ---------------------------------------------------------------------------
module icache
  import aura_pkg::*;
  import aura_axi_pkg::*;
#(
  parameter int unsigned LINES = aura_pkg::IC_LINES,
  parameter int unsigned IDX_W = $clog2(LINES),
  parameter int unsigned TAG_W = ADDR_W - IDX_W - 2
) (
  input  logic            clk,
  input  logic            rst_n,
  input  logic            flush,

  input  logic            req,
  input  logic [ADDR_W-1:0] addr,
  output logic [31:0]     rdata,
  output logic            rvalid,
  output logic            rerr,

  axi_lite_if.mst         refill,

  input  logic            ret_n,
  input  logic            pd_n
);

  typedef enum logic [2:0] {
      IC_IDLE, IC_LOOKUP, IC_CHECK, IC_AR, IC_R, IC_FILL, IC_DONE
  } icst_e;

  icst_e             st_q;
  logic [ADDR_W-1:0] addr_q;
  logic [31:0]       fill_data_q;
  logic              fill_err_q;
  logic              valid_q [LINES];

  logic [IDX_W-1:0]  index;
  logic [TAG_W-1:0]  tag;

  assign index = addr_q[IDX_W+1:2];
  assign tag   = addr_q[ADDR_W-1:IDX_W+2];

  // ---- arrays --------------------------------------------------------------
  sram_if #(.AW(IDX_W), .DW(32)) tag_bus  (.clk(clk), .rst_n(rst_n));
  sram_if #(.AW(IDX_W), .DW(32)) data_bus (.clk(clk), .rst_n(rst_n));

  sram_1rw_wrap #(
    .WORDS (LINES),
    .BITS  (32),
    .AW    (IDX_W)
  ) u_tag_array (
    .bus   (tag_bus),
    .ret_n (ret_n),
    .pd_n  (pd_n)
  );

  sram_1rw_wrap #(
    .WORDS (LINES),
    .BITS  (32),
    .AW    (IDX_W)
  ) u_data_array (
    .bus   (data_bus),
    .ret_n (ret_n),
    .pd_n  (pd_n)
  );

  logic              array_rd;
  logic              array_wr;
  logic [31:0]       tag_wdata;

  assign tag_wdata = {{(32-TAG_W){1'b0}}, tag};

  assign tag_bus.req    = array_rd | array_wr;
  assign tag_bus.we     = array_wr;
  assign tag_bus.addr   = index;
  assign tag_bus.wdata  = tag_wdata;
  assign tag_bus.be     = '1;

  assign data_bus.req   = array_rd | array_wr;
  assign data_bus.we    = array_wr;
  assign data_bus.addr  = index;
  assign data_bus.wdata = fill_data_q;
  assign data_bus.be    = '1;

  assign array_rd = (st_q == IC_LOOKUP);
  assign array_wr = (st_q == IC_FILL);

  logic hit;
  assign hit = valid_q[index] &&
               (tag_bus.rdata[TAG_W-1:0] == tag) &&
               tag_bus.rvalid;

  // ---- refill master -------------------------------------------------------
  assign refill.awaddr  = '0;
  assign refill.awprot  = 3'b000;
  assign refill.awvalid = 1'b0;
  assign refill.wdata   = '0;
  assign refill.wstrb   = '0;
  assign refill.wvalid  = 1'b0;
  assign refill.bready  = 1'b1;
  assign refill.araddr  = {addr_q[ADDR_W-1:2], 2'b00};
  assign refill.arprot  = 3'b000;
  assign refill.arvalid = (st_q == IC_AR);
  assign refill.rready  = (st_q == IC_R);

  assign rdata  = fill_err_q ? 32'h0000_0013 : fill_data_q;
  assign rvalid = (st_q == IC_DONE);
  assign rerr   = fill_err_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      st_q        <= IC_IDLE;
      addr_q      <= '0;
      fill_data_q <= 32'h0000_0013;
      fill_err_q  <= 1'b0;
      for (int unsigned l = 0; l < LINES; l++) valid_q[l] <= 1'b0;
    end else begin
      if (flush) begin
        for (int unsigned l = 0; l < LINES; l++) valid_q[l] <= 1'b0;
      end

      unique case (st_q)
        IC_IDLE: begin
          if (req) begin
            addr_q     <= addr;
            fill_err_q <= 1'b0;
            st_q       <= IC_LOOKUP;
          end
        end

        IC_LOOKUP: st_q <= IC_CHECK;

        IC_CHECK: begin
          if (hit) begin
            fill_data_q <= data_bus.rdata;
            st_q        <= IC_DONE;
          end else begin
            st_q <= IC_AR;
          end
        end

        IC_AR: if (refill.arready) st_q <= IC_R;

        IC_R: begin
          if (refill.rvalid) begin
            fill_data_q <= refill.rdata;
            fill_err_q  <= (refill.rresp != AXI_OKAY);
            st_q        <= IC_FILL;
          end
        end

        IC_FILL: begin
          if (!fill_err_q) valid_q[index] <= 1'b1;
          st_q <= IC_DONE;
        end

        IC_DONE: st_q <= IC_IDLE;

        default: st_q <= IC_IDLE;
      endcase
    end
  end

endmodule : icache
