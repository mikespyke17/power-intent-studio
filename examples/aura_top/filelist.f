// ---------------------------------------------------------------------------
// filelist.f -- compile order for aura_top
//
//   vcs   -sverilog -f filelist.f -upf upf/aura_top.upf
//   xrun  -sv       -f filelist.f -lps_upf upf/aura_top.upf
//   vlog  -sv       -f filelist.f
//
// Packages first, then interfaces, then leaf RTL, then the top level.
// ---------------------------------------------------------------------------

+incdir+./

// ---- packages -------------------------------------------------------------
./pkg/aura_pkg.sv
./pkg/aura_axi_pkg.sv
./pkg/aura_dsp_pkg.sv
./pkg/aura_pmu_pkg.sv

// ---- interfaces -----------------------------------------------------------
./intf/axi_lite_if.sv
./intf/apb_if.sv
./intf/sram_if.sv
./intf/pmu_if.sv

// ---- memory macros --------------------------------------------------------
./rtl/macro/sram_1rw_macro.sv
./rtl/macro/sram_2rw_macro.sv
./rtl/macro/sram_wrap.sv

// ---- fabric ---------------------------------------------------------------
./rtl/fabric/axi_lite_regs.sv
./rtl/fabric/axi_lite_xbar.sv
./rtl/fabric/fabric_subsys.sv

// ---- always-on ------------------------------------------------------------
./rtl/aon/pmu_fsm.sv
./rtl/aon/aon_periph.sv
./rtl/aon/aon_subsys.sv

// ---- cpu ------------------------------------------------------------------
./rtl/cpu/rv_datapath.sv
./rtl/cpu/rv_core.sv
./rtl/cpu/icache.sv
./rtl/cpu/cpu_subsys.sv

// ---- dsp ------------------------------------------------------------------
./rtl/dsp/mac_unit.sv
./rtl/dsp/fir_filter.sv
./rtl/dsp/dsp_subsys.sv

// ---- memory ---------------------------------------------------------------
./rtl/mem/mem_subsys.sv

// ---- peripherals ----------------------------------------------------------
./rtl/periph/uart.sv
./rtl/periph/spi_timer_gpio.sv
./rtl/periph/periph_subsys.sv

// ---- top ------------------------------------------------------------------
./rtl/aura_top.sv
