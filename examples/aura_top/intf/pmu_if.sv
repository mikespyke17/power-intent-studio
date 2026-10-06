// ---------------------------------------------------------------------------
// pmu_if -- one channel of power control between the always-on PMU and a
//           power domain.  Instantiated as an array, one element per domain.
//
//           Every signal in this bundle is generated in PD_AON and consumed
//           in a switchable domain, so each one is a level-shift and/or
//           isolation candidate in the UPF.
// ---------------------------------------------------------------------------
interface pmu_if (
  input logic pclk,    // always-on clock
  input logic prst_n   // always-on reset
);

  logic pwr_en;       // header switch enable
  logic pwr_ack;      // switch acknowledge back from the domain
  logic iso_en;       // isolation clamp enable
  logic ret_save;     // retention save strobe
  logic ret_restore;  // retention restore strobe
  logic clk_en;       // functional clock enable for the domain
  logic rst_n;        // functional reset for the domain

  modport pmu (
    input  pclk, prst_n,
    output pwr_en, iso_en, ret_save, ret_restore, clk_en, rst_n,
    input  pwr_ack
  );

  modport dom (
    input  pclk, prst_n,
    input  pwr_en, iso_en, ret_save, ret_restore, clk_en, rst_n,
    output pwr_ack
  );

  modport mon (
    input pclk, prst_n,
    input pwr_en, pwr_ack, iso_en, ret_save, ret_restore, clk_en, rst_n
  );

endinterface : pmu_if
