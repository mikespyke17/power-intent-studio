// ---------------------------------------------------------------------------
// rv_core -- multi-cycle RV32IM-subset core.
//
//   FETCH -> DECODE -> EXECUTE -> MEM -> WRITEBACK
//
// Instructions are fetched through the instruction cache; data accesses go out
// on a separate AXI4-Lite master which is merged with the cache refill port
// one level up in cpu_subsys.
// ---------------------------------------------------------------------------
module rv_core
  import aura_pkg::*;
#(
  parameter logic [XLEN-1:0] RESET_PC = 32'h0000_0000
) (
  input  logic            clk,
  input  logic            rst_n,

  // instruction port (to the cache)
  output logic            ifetch_req,
  output logic [XLEN-1:0] ifetch_addr,
  input  logic [31:0]     ifetch_data,
  input  logic            ifetch_valid,
  input  logic            ifetch_err,

  // data port
  axi_lite_if.mst         dbus,

  output logic            illegal_instr,
  output logic            core_active
);

  typedef enum logic [2:0] {
      C_FETCH, C_WAIT_I, C_EXEC, C_MUL, C_MEM, C_WB
  } cst_e;

  cst_e            st_q;
  logic [31:0]     instr_q;
  logic            illegal_q;

  rv_ctrl_t        ctrl;
  logic [XLEN-1:0] pc;
  logic [XLEN-1:0] pc_next;
  logic [XLEN-1:0] rs1_data;
  logic [XLEN-1:0] rs2_data;
  logic [XLEN-1:0] alu_result;
  logic [XLEN-1:0] mul_result;
  logic            mul_done;
  logic            branch_taken;

  logic [XLEN-1:0] alu_result_q;
  logic [XLEN-1:0] mul_result_q;
  logic            branch_taken_q;

  logic [XLEN-1:0] lsu_rdata;
  logic            lsu_done;
  logic            lsu_busy;
  logic            lsu_err;

  logic            pc_advance;
  logic            rf_we;
  logic [XLEN-1:0] rf_wdata;

  // ---- fetch ---------------------------------------------------------------
  rv_if_stage #(.RESET_PC(RESET_PC)) u_if (
    .clk           (clk),
    .rst_n         (rst_n),
    .pc_advance    (pc_advance),
    .take_branch   (branch_taken_q),
    .branch_target (alu_result_q),
    .pc            (pc),
    .pc_next       (pc_next)
  );

  assign ifetch_req  = (st_q == C_FETCH);
  assign ifetch_addr = pc;

  // ---- decode --------------------------------------------------------------
  rv_id_stage u_id (
    .instr (instr_q),
    .ctrl  (ctrl)
  );

  // ---- register file (retention target) -----------------------------------
  rv_regfile u_rf (
    .clk      (clk),
    .rst_n    (rst_n),
    .rs1_addr (ctrl.rs1),
    .rs2_addr (ctrl.rs2),
    .rs1_data (rs1_data),
    .rs2_data (rs2_data),
    .we       (rf_we),
    .rd_addr  (ctrl.rd),
    .rd_data  (rf_wdata)
  );

  // ---- execute -------------------------------------------------------------
  rv_ex_stage u_ex (
    .clk          (clk),
    .rst_n        (rst_n),
    .ctrl         (ctrl),
    .pc           (pc),
    .rs1_data     (rs1_data),
    .rs2_data     (rs2_data),
    .mul_start    ((st_q == C_EXEC) & ctrl.is_mul),
    .alu_result   (alu_result),
    .mul_result   (mul_result),
    .mul_done     (mul_done),
    .branch_taken (branch_taken)
  );

  // ---- load / store --------------------------------------------------------
  rv_lsu u_lsu (
    .dbus   (dbus),
    .req    ((st_q == C_EXEC) & (ctrl.is_load | ctrl.is_store)),
    .we     (ctrl.is_store),
    .addr   (alu_result),
    .wdata  (rs2_data),
    .funct3 (ctrl.funct3),
    .rdata  (lsu_rdata),
    .done   (lsu_done),
    .busy   (lsu_busy),
    .err    (lsu_err)
  );

  // ---- writeback mux -------------------------------------------------------
  always_comb begin
    if (ctrl.is_jump)      rf_wdata = pc_next;
    else if (ctrl.is_load) rf_wdata = lsu_rdata;
    else if (ctrl.is_mul)  rf_wdata = mul_result_q;
    else                   rf_wdata = alu_result_q;
  end

  assign rf_we         = (st_q == C_WB) & ctrl.rf_we & ~ctrl.illegal;
  assign pc_advance    = (st_q == C_WB);
  assign illegal_instr = illegal_q;
  assign core_active   = (st_q != C_FETCH) | ifetch_req;

  // ---- control FSM ---------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      st_q           <= C_FETCH;
      instr_q        <= 32'h0000_0013;   // NOP
      illegal_q      <= 1'b0;
      alu_result_q   <= '0;
      mul_result_q   <= '0;
      branch_taken_q <= 1'b0;
    end else begin
      unique case (st_q)
        C_FETCH: st_q <= C_WAIT_I;

        C_WAIT_I: begin
          if (ifetch_valid) begin
            instr_q <= ifetch_err ? 32'h0000_0013 : ifetch_data;
            st_q    <= C_EXEC;
          end
        end

        C_EXEC: begin
          alu_result_q   <= alu_result;
          branch_taken_q <= branch_taken;
          illegal_q      <= ctrl.illegal;
          if (ctrl.is_mul) begin
            st_q <= C_MUL;
          end else if (ctrl.is_load || ctrl.is_store) begin
            st_q <= C_MEM;
          end else begin
            st_q <= C_WB;
          end
        end

        C_MUL: begin
          if (mul_done) begin
            mul_result_q <= mul_result;
            st_q         <= C_WB;
          end
        end

        C_MEM: begin
          if (lsu_done) st_q <= C_WB;
        end

        C_WB: st_q <= C_FETCH;

        default: st_q <= C_FETCH;
      endcase
    end
  end

endmodule : rv_core
