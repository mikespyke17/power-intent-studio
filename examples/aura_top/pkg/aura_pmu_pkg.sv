// ---------------------------------------------------------------------------
// aura_pmu_pkg -- power domain identifiers and sequencing constants.
//                 The tables here are the RTL mirror of the UPF power states.
// ---------------------------------------------------------------------------
package aura_pmu_pkg;

  import aura_pkg::N_DOMAIN;
  import aura_pkg::power_state_e;

  typedef enum logic [2:0] {
      DOM_TOP = 3'd0,
      DOM_AON = 3'd1,
      DOM_CPU = 3'd2,
      DOM_DSP = 3'd3,
      DOM_MEM = 3'd4,
      DOM_PER = 3'd5
  } domain_e;

  // Domains that own retention registers / retention-capable memory.
  parameter logic [N_DOMAIN-1:0] DOM_RETENTIVE = 6'b010100;  // MEM, CPU

  // Domains fed by a header switch (the rest are hard-wired to a rail).
  parameter logic [N_DOMAIN-1:0] DOM_SWITCHED  = 6'b101100;  // PER, DSP, CPU

  // Sequencer dwell times, in always-on clock cycles.
  parameter int unsigned T_SAVE    = 8;
  parameter int unsigned T_ISO     = 4;
  parameter int unsigned T_SWITCH  = 16;
  parameter int unsigned T_RESTORE = 8;
  parameter int unsigned T_RESET   = 8;
  parameter int unsigned CNT_W     = 6;

  // Per-state domain on/off mask, indexed by aura_pkg::power_state_e.
  // Bit order: {PER, MEM, DSP, CPU, AON, TOP}
  parameter logic [N_DOMAIN-1:0] STATE_MASK [7] = '{
      6'b111111,  // PS_TURBO      : everything up, DSP on the boost rail
      6'b111111,  // PS_NOM        : everything up, nominal voltage
      6'b110111,  // PS_CPU_ONLY   : DSP parked
      6'b111111,  // PS_DSP_BOOST  : CPU throttled, DSP boosted
      6'b010011,  // PS_SLEEP_RET  : CPU/DSP/PER off, MEM retained
      6'b010011,  // PS_DEEP_SLEEP : as above, AON at its low rail
      6'b000011   // PS_SHUTDOWN   : only TOP + AON alive
  };

  function automatic logic domain_on(input power_state_e st, input domain_e dm);
    return STATE_MASK[int'(st)][int'(dm)];
  endfunction

  function automatic logic is_retentive(input domain_e dm);
    return DOM_RETENTIVE[int'(dm)];
  endfunction

  function automatic logic is_switched(input domain_e dm);
    return DOM_SWITCHED[int'(dm)];
  endfunction

endpackage : aura_pmu_pkg
