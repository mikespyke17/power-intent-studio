// ---------------------------------------------------------------------------
// rv_regfile -- 32 x XLEN architectural register file.
//
// This is the retention target of PD_CPU: set_retention in the UPF names this
// instance so the architectural state survives a SLEEP_RET power collapse.
// ---------------------------------------------------------------------------
module rv_regfile
  import aura_pkg::*;
#(
  parameter int unsigned N_REG = 32
) (
  input  logic            clk,
  input  logic            rst_n,
  input  logic [4:0]      rs1_addr,
  input  logic [4:0]      rs2_addr,
  output logic [XLEN-1:0] rs1_data,
  output logic [XLEN-1:0] rs2_data,
  input  logic            we,
  input  logic [4:0]      rd_addr,
  input  logic [XLEN-1:0] rd_data
);

  logic [XLEN-1:0] regs_q [N_REG];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int unsigned r = 0; r < N_REG; r++) regs_q[r] <= '0;
    end else if (we && (rd_addr != 5'd0)) begin
      regs_q[rd_addr] <= rd_data;
    end
  end

  assign rs1_data = (rs1_addr == 5'd0) ? '0 : regs_q[rs1_addr];
  assign rs2_data = (rs2_addr == 5'd0) ? '0 : regs_q[rs2_addr];

endmodule : rv_regfile


// ---------------------------------------------------------------------------
// rv_alu -- combinational arithmetic and logic unit.
// ---------------------------------------------------------------------------
module rv_alu
  import aura_pkg::*;
(
  input  alu_op_e         op,
  input  logic [XLEN-1:0] a,
  input  logic [XLEN-1:0] b,
  output logic [XLEN-1:0] y,
  output logic            zero
);

  logic signed [XLEN-1:0] a_s;
  logic signed [XLEN-1:0] b_s;
  logic [4:0]             sh;

  assign a_s = a;
  assign b_s = b;
  assign sh  = b[4:0];

  always_comb begin
    unique case (op)
      ALU_ADD:   y = a + b;
      ALU_SUB:   y = a - b;
      ALU_AND:   y = a & b;
      ALU_OR:    y = a | b;
      ALU_XOR:   y = a ^ b;
      ALU_SLL:   y = a << sh;
      ALU_SRL:   y = a >> sh;
      ALU_SRA:   y = XLEN'(a_s >>> sh);
      ALU_SLT:   y = {{(XLEN-1){1'b0}}, (a_s < b_s)};
      ALU_SLTU:  y = {{(XLEN-1){1'b0}}, (a < b)};
      ALU_PASSB: y = b;
      default:   y = '0;
    endcase
  end

  assign zero = (y == '0);

endmodule : rv_alu


// ---------------------------------------------------------------------------
// rv_mul -- three-cycle signed multiplier, returns the low word.
// ---------------------------------------------------------------------------
module rv_mul
  import aura_pkg::*;
(
  input  logic            clk,
  input  logic            rst_n,
  input  logic            start,
  input  logic [XLEN-1:0] a,
  input  logic [XLEN-1:0] b,
  output logic [XLEN-1:0] result,
  output logic            done
);

  logic signed [2*XLEN-1:0] prod_q;
  logic [1:0]               cnt_q;
  logic                     busy_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      prod_q <= '0;
      cnt_q  <= 2'd0;
      busy_q <= 1'b0;
    end else if (start && !busy_q) begin
      prod_q <= $signed(a) * $signed(b);
      cnt_q  <= 2'd2;
      busy_q <= 1'b1;
    end else if (busy_q) begin
      if (cnt_q == 2'd0) busy_q <= 1'b0;
      else               cnt_q <= cnt_q - 2'd1;
    end
  end

  assign result = prod_q[XLEN-1:0];
  assign done   = busy_q & (cnt_q == 2'd0);

endmodule : rv_mul


// ---------------------------------------------------------------------------
// rv_id_stage -- RV32I (plus MUL) instruction decoder.
// ---------------------------------------------------------------------------
module rv_id_stage
  import aura_pkg::*;
(
  input  logic [31:0] instr,
  output rv_ctrl_t    ctrl
);

  logic [6:0] opcode;
  logic [2:0] funct3;
  logic [6:0] funct7;

  assign opcode = instr[6:0];
  assign funct3 = instr[14:12];
  assign funct7 = instr[31:25];

  logic [XLEN-1:0] imm_i;
  logic [XLEN-1:0] imm_s;
  logic [XLEN-1:0] imm_b;
  logic [XLEN-1:0] imm_u;
  logic [XLEN-1:0] imm_j;

  assign imm_i = {{20{instr[31]}}, instr[31:20]};
  assign imm_s = {{20{instr[31]}}, instr[31:25], instr[11:7]};
  assign imm_b = {{19{instr[31]}}, instr[31], instr[7], instr[30:25],
                  instr[11:8], 1'b0};
  assign imm_u = {instr[31:12], 12'd0};
  assign imm_j = {{11{instr[31]}}, instr[31], instr[19:12], instr[20],
                  instr[30:21], 1'b0};

  always_comb begin
    ctrl           = '0;
    ctrl.valid     = 1'b1;
    ctrl.rd        = instr[11:7];
    ctrl.rs1       = instr[19:15];
    ctrl.rs2       = instr[24:20];
    ctrl.funct3    = funct3;
    ctrl.alu_op    = ALU_ADD;
    ctrl.imm       = imm_i;
    ctrl.illegal   = 1'b0;

    unique case (opcode)
      7'b0110111: begin                      // LUI
        ctrl.rf_we   = 1'b1;
        ctrl.imm     = imm_u;
        ctrl.use_imm = 1'b1;
        ctrl.alu_op  = ALU_PASSB;
      end

      7'b0010111: begin                      // AUIPC
        ctrl.rf_we   = 1'b1;
        ctrl.imm     = imm_u;
        ctrl.use_imm = 1'b1;
        ctrl.use_pc  = 1'b1;
        ctrl.alu_op  = ALU_ADD;
      end

      7'b1101111: begin                      // JAL
        ctrl.rf_we   = 1'b1;
        ctrl.imm     = imm_j;
        ctrl.is_jump = 1'b1;
        ctrl.use_pc  = 1'b1;
        ctrl.use_imm = 1'b1;
        ctrl.alu_op  = ALU_ADD;
      end

      7'b1100111: begin                      // JALR
        ctrl.rf_we   = 1'b1;
        ctrl.imm     = imm_i;
        ctrl.is_jump = 1'b1;
        ctrl.use_imm = 1'b1;
        ctrl.alu_op  = ALU_ADD;
      end

      7'b1100011: begin                      // BEQ / BNE / BLT / BGE / BLTU / BGEU
        ctrl.imm       = imm_b;
        ctrl.is_branch = 1'b1;
        unique case (funct3)
          3'b000, 3'b001: ctrl.alu_op = ALU_SUB;
          3'b100, 3'b101: ctrl.alu_op = ALU_SLT;
          3'b110, 3'b111: ctrl.alu_op = ALU_SLTU;
          default:        ctrl.illegal = 1'b1;
        endcase
      end

      7'b0000011: begin                      // LB / LH / LW / LBU / LHU
        ctrl.rf_we   = 1'b1;
        ctrl.is_load = 1'b1;
        ctrl.use_imm = 1'b1;
        ctrl.alu_op  = ALU_ADD;
      end

      7'b0100011: begin                      // SB / SH / SW
        ctrl.imm      = imm_s;
        ctrl.is_store = 1'b1;
        ctrl.use_imm  = 1'b1;
        ctrl.alu_op   = ALU_ADD;
      end

      7'b0010011: begin                      // register-immediate ALU
        ctrl.rf_we   = 1'b1;
        ctrl.use_imm = 1'b1;
        unique case (funct3)
          3'b000: ctrl.alu_op = ALU_ADD;
          3'b010: ctrl.alu_op = ALU_SLT;
          3'b011: ctrl.alu_op = ALU_SLTU;
          3'b100: ctrl.alu_op = ALU_XOR;
          3'b110: ctrl.alu_op = ALU_OR;
          3'b111: ctrl.alu_op = ALU_AND;
          3'b001: ctrl.alu_op = ALU_SLL;
          3'b101: ctrl.alu_op = funct7[5] ? ALU_SRA : ALU_SRL;
          default: ctrl.illegal = 1'b1;
        endcase
      end

      7'b0110011: begin                      // register-register ALU / MUL
        ctrl.rf_we = 1'b1;
        if (funct7 == 7'b0000001) begin
          ctrl.is_mul = 1'b1;
          if (funct3 != 3'b000) ctrl.illegal = 1'b1;
        end else begin
          unique case (funct3)
            3'b000: ctrl.alu_op = funct7[5] ? ALU_SUB : ALU_ADD;
            3'b001: ctrl.alu_op = ALU_SLL;
            3'b010: ctrl.alu_op = ALU_SLT;
            3'b011: ctrl.alu_op = ALU_SLTU;
            3'b100: ctrl.alu_op = ALU_XOR;
            3'b101: ctrl.alu_op = funct7[5] ? ALU_SRA : ALU_SRL;
            3'b110: ctrl.alu_op = ALU_OR;
            3'b111: ctrl.alu_op = ALU_AND;
            default: ctrl.illegal = 1'b1;
          endcase
        end
      end

      default: begin
        ctrl.valid   = 1'b0;
        ctrl.illegal = 1'b1;
      end
    endcase
  end

endmodule : rv_id_stage


// ---------------------------------------------------------------------------
// rv_if_stage -- program counter and next-PC selection.
// ---------------------------------------------------------------------------
module rv_if_stage
  import aura_pkg::*;
#(
  parameter logic [XLEN-1:0] RESET_PC = 32'h0000_0000
) (
  input  logic            clk,
  input  logic            rst_n,
  input  logic            pc_advance,
  input  logic            take_branch,
  input  logic [XLEN-1:0] branch_target,
  output logic [XLEN-1:0] pc,
  output logic [XLEN-1:0] pc_next
);

  logic [XLEN-1:0] pc_q;

  assign pc      = pc_q;
  assign pc_next = pc_q + XLEN'(4);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pc_q <= RESET_PC;
    end else if (pc_advance) begin
      pc_q <= take_branch ? {branch_target[XLEN-1:1], 1'b0} : pc_next;
    end
  end

endmodule : rv_if_stage


// ---------------------------------------------------------------------------
// rv_ex_stage -- operand selection wrapping the ALU and the multiplier.
// ---------------------------------------------------------------------------
module rv_ex_stage
  import aura_pkg::*;
(
  input  logic            clk,
  input  logic            rst_n,
  input  rv_ctrl_t        ctrl,
  input  logic [XLEN-1:0] pc,
  input  logic [XLEN-1:0] rs1_data,
  input  logic [XLEN-1:0] rs2_data,
  input  logic            mul_start,
  output logic [XLEN-1:0] alu_result,
  output logic [XLEN-1:0] mul_result,
  output logic            mul_done,
  output logic            branch_taken
);

  logic [XLEN-1:0] op_a;
  logic [XLEN-1:0] op_b;
  logic [XLEN-1:0] alu_y;
  logic            alu_zero;

  assign op_a = ctrl.use_pc  ? pc       : rs1_data;
  assign op_b = ctrl.use_imm ? ctrl.imm : rs2_data;

  rv_alu u_alu (
    .op   (ctrl.alu_op),
    .a    (op_a),
    .b    (op_b),
    .y    (alu_y),
    .zero (alu_zero)
  );

  rv_mul u_mul (
    .clk    (clk),
    .rst_n  (rst_n),
    .start  (mul_start),
    .a      (rs1_data),
    .b      (rs2_data),
    .result (mul_result),
    .done   (mul_done)
  );

  assign alu_result = alu_y;

  // Branches always compare rs1 against rs2, never the immediate.
  logic [XLEN-1:0] cmp_y;
  logic            cmp_zero;

  rv_alu u_cmp (
    .op   (ctrl.alu_op),
    .a    (rs1_data),
    .b    (rs2_data),
    .y    (cmp_y),
    .zero (cmp_zero)
  );

  always_comb begin
    branch_taken = 1'b0;
    if (ctrl.is_branch) begin
      unique case (ctrl.funct3)
        3'b000: branch_taken =  cmp_zero;        // BEQ
        3'b001: branch_taken = ~cmp_zero;        // BNE
        3'b100: branch_taken =  cmp_y[0];        // BLT
        3'b101: branch_taken = ~cmp_y[0];        // BGE
        3'b110: branch_taken =  cmp_y[0];        // BLTU
        3'b111: branch_taken = ~cmp_y[0];        // BGEU
        default: branch_taken = 1'b0;
      endcase
    end else if (ctrl.is_jump) begin
      branch_taken = 1'b1;
    end
  end

endmodule : rv_ex_stage


// ---------------------------------------------------------------------------
// rv_lsu -- load/store unit, AXI4-Lite master for data accesses.
// ---------------------------------------------------------------------------
module rv_lsu
  import aura_pkg::*;
  import aura_axi_pkg::*;
(
  axi_lite_if.mst         dbus,
  input  logic            req,
  input  logic            we,
  input  logic [XLEN-1:0] addr,
  input  logic [XLEN-1:0] wdata,
  input  logic [2:0]      funct3,
  output logic [XLEN-1:0] rdata,
  output logic            done,   // one-cycle completion pulse
  output logic            busy,
  output logic            err
);

  typedef enum logic [2:0] { L_IDLE, L_AW, L_B, L_AR, L_R } lst_e;

  lst_e            st_q;
  logic [XLEN-1:0] rdata_q;
  logic            err_q;
  logic [1:0]      off_q;
  logic [2:0]      f3_q;

  logic [STRB_W-1:0] strb;
  logic [XLEN-1:0]   wdata_sh;
  logic [1:0]        offset;

  assign offset = addr[1:0];

  always_comb begin
    unique case (funct3[1:0])
      2'b00:   strb = STRB_W'(4'b0001) << offset;                // byte
      2'b01:   strb = STRB_W'(4'b0011) << offset;                // half
      default: strb = STRB_W'(4'b1111);                          // word
    endcase
    wdata_sh = wdata << (8 * offset);
  end

  assign dbus.awaddr  = {addr[ADDR_W-1:2], 2'b00};
  assign dbus.awprot  = 3'b000;
  assign dbus.awvalid = (st_q == L_AW);
  assign dbus.wdata   = wdata_sh;
  assign dbus.wstrb   = strb;
  assign dbus.wvalid  = (st_q == L_AW);
  assign dbus.bready  = (st_q == L_B);
  assign dbus.araddr  = {addr[ADDR_W-1:2], 2'b00};
  assign dbus.arprot  = 3'b000;
  assign dbus.arvalid = (st_q == L_AR);
  assign dbus.rready  = (st_q == L_R);

  // Extract and extend the addressed sub-word.
  logic [XLEN-1:0] raw;
  logic [7:0]      byte_sel;
  logic [15:0]     half_sel;

  assign raw      = rdata_q;
  assign byte_sel = raw[8*off_q +: 8];
  assign half_sel = off_q[1] ? raw[31:16] : raw[15:0];

  always_comb begin
    unique case (f3_q)
      3'b000:  rdata = {{24{byte_sel[7]}},  byte_sel};   // LB
      3'b001:  rdata = {{16{half_sel[15]}}, half_sel};   // LH
      3'b100:  rdata = {24'd0, byte_sel};                // LBU
      3'b101:  rdata = {16'd0, half_sel};                // LHU
      default: rdata = raw;                              // LW
    endcase
  end

  logic done_q;

  assign done = done_q;
  assign busy = (st_q != L_IDLE);
  assign err  = err_q;

  always_ff @(posedge dbus.clk or negedge dbus.rst_n) begin
    if (!dbus.rst_n) begin
      st_q    <= L_IDLE;
      rdata_q <= '0;
      err_q   <= 1'b0;
      off_q   <= 2'b00;
      f3_q    <= 3'b010;
      done_q  <= 1'b0;
    end else begin
      done_q <= 1'b0;
      unique case (st_q)
        L_IDLE: begin
          if (req) begin
            off_q <= offset;
            f3_q  <= funct3;
            err_q <= 1'b0;
            st_q  <= we ? L_AW : L_AR;
          end
        end

        L_AW: if (dbus.awready && dbus.wready) st_q <= L_B;

        L_B: if (dbus.bvalid) begin
               err_q  <= (dbus.bresp != AXI_OKAY);
               done_q <= 1'b1;
               st_q   <= L_IDLE;
             end

        L_AR: if (dbus.arready) st_q <= L_R;

        L_R: if (dbus.rvalid) begin
               rdata_q <= dbus.rdata;
               err_q   <= (dbus.rresp != AXI_OKAY);
               done_q  <= 1'b1;
               st_q    <= L_IDLE;
             end

        default: st_q <= L_IDLE;
      endcase
    end
  end

endmodule : rv_lsu
