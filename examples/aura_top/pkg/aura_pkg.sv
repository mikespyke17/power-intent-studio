// ---------------------------------------------------------------------------
// aura_pkg -- global parameters and types for the AURA edge-sensor SoC
// ---------------------------------------------------------------------------
package aura_pkg;

  // ---- Core data widths ----------------------------------------------------
  parameter int unsigned XLEN   = 32;
  parameter int unsigned ADDR_W = 32;
  parameter int unsigned DATA_W = 32;
  parameter int unsigned STRB_W = DATA_W / 8;

  // ---- Interconnect topology ----------------------------------------------
  parameter int unsigned N_AXI_MST = 2;  // 0 = CPU, 1 = DSP
  parameter int unsigned N_AXI_SLV = 4;  // 0 = MEM, 1 = DSP cfg, 2 = APB, 3 = AON

  parameter int unsigned MST_CPU = 0;
  parameter int unsigned MST_DSP = 1;

  parameter int unsigned SLV_MEM = 0;
  parameter int unsigned SLV_DSP = 1;
  parameter int unsigned SLV_APB = 2;
  parameter int unsigned SLV_AON = 3;

  // ---- Address map ---------------------------------------------------------
  parameter logic [ADDR_W-1:0] SLV_BASE [N_AXI_SLV] = '{
      32'h0000_0000,   // SLV_MEM  : SRAM banks
      32'h2000_0000,   // SLV_DSP  : DSP configuration
      32'h4000_0000,   // SLV_APB  : peripheral bridge
      32'h6000_0000    // SLV_AON  : always-on / PMU registers
  };

  parameter logic [ADDR_W-1:0] SLV_MASK [N_AXI_SLV] = '{
      32'hE000_0000,
      32'hE000_0000,
      32'hE000_0000,
      32'hE000_0000
  };

  // ---- Memory sizing -------------------------------------------------------
  parameter int unsigned N_SRAM_BANK = 4;
  parameter int unsigned SRAM_DEPTH  = 1024;
  parameter int unsigned SRAM_AW     = $clog2(SRAM_DEPTH);
  parameter int unsigned BANK_SEL_W  = $clog2(N_SRAM_BANK);

  // ---- Instruction cache ---------------------------------------------------
  parameter int unsigned IC_LINES = 128;
  parameter int unsigned IC_IDX_W = $clog2(IC_LINES);
  parameter int unsigned IC_TAG_W = ADDR_W - IC_IDX_W - 2;

  // ---- Power domains -------------------------------------------------------
  parameter int unsigned N_DOMAIN = 6;

  // System-level power states -- these rows mirror the UPF power state table.
  typedef enum logic [2:0] {
      PS_TURBO      = 3'd0,
      PS_NOM        = 3'd1,
      PS_CPU_ONLY   = 3'd2,
      PS_DSP_BOOST  = 3'd3,
      PS_SLEEP_RET  = 3'd4,
      PS_DEEP_SLEEP = 3'd5,
      PS_SHUTDOWN   = 3'd6
  } power_state_e;

  // ---- CPU decode types ----------------------------------------------------
  typedef enum logic [3:0] {
      ALU_ADD   = 4'd0,
      ALU_SUB   = 4'd1,
      ALU_AND   = 4'd2,
      ALU_OR    = 4'd3,
      ALU_XOR   = 4'd4,
      ALU_SLL   = 4'd5,
      ALU_SRL   = 4'd6,
      ALU_SRA   = 4'd7,
      ALU_SLT   = 4'd8,
      ALU_SLTU  = 4'd9,
      ALU_PASSB = 4'd10
  } alu_op_e;

  typedef struct packed {
      logic            valid;
      logic            rf_we;
      logic [4:0]      rd;
      logic [4:0]      rs1;
      logic [4:0]      rs2;
      logic [XLEN-1:0] imm;
      alu_op_e         alu_op;
      logic            use_imm;    // operand B is the immediate
      logic            use_pc;     // operand A is the program counter
      logic            is_load;
      logic            is_store;
      logic            is_branch;
      logic            is_jump;
      logic            is_mul;
      logic [2:0]      funct3;
      logic            illegal;
  } rv_ctrl_t;

  // ---- Helpers -------------------------------------------------------------
  function automatic logic addr_hit(input logic [ADDR_W-1:0] addr,
                                    input int unsigned       slv);
    addr_hit = ((addr & SLV_MASK[slv]) == SLV_BASE[slv]);
  endfunction

  function automatic int unsigned idx_width(input int unsigned n);
    idx_width = (n <= 1) ? 1 : $clog2(n);
  endfunction

endpackage : aura_pkg
