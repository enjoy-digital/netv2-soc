#!/usr/bin/env python3

import sys
import os
import math

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.misc import timeline

from litex.boards.platforms import netv2

from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform

from litex.soc.interconnect.csr import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.soc_sdram import *
from litex.soc.integration.builder import *
from litex.soc.integration.export import get_csr_header
from litex.soc.cores.clock import *
from litex.soc.cores import dna, xadc, timer, uart
from litex.soc.cores.freqmeter import FreqMeter
from litex.soc.cores.timer import Timer

from litedram.modules import MT41J128M16
from litedram.phy import a7ddrphy
from litedram.core import ControllerSettings

from liteeth.common import convert_ip
from liteeth.phy.rmii import LiteEthPHYRMII
from liteeth.core.mac import LiteEthMAC
from liteeth.core import LiteEthUDPIPCore
from liteeth.frontend.etherbone import LiteEthEtherbone

from litepcie.phy.s7pciephy import S7PCIEPHY
from litepcie.core import LitePCIeEndpoint, LitePCIeMSI
from litepcie.frontend.dma import LitePCIeDMA
from litepcie.frontend.wishbone import LitePCIeWishboneBridge

from litevideo.input import HDMIIn
from litevideo.output import VideoOut

from gateware.icap import ICAP
from gateware.flash import Flash

def csr_map_update(csr_map, csr_peripherals):
    csr_map.update(dict((n, v)
        for v, n in enumerate(csr_peripherals, start=max(csr_map.values()) + 1)))

class CRG(Module, AutoCSR):
    def __init__(self, platform, sys_clk_freq):
        self.reset = CSR()

        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_sys4x = ClockDomain(reset_less=True)
        self.clock_domains.cd_sys4x_dqs = ClockDomain(reset_less=True)
        self.clock_domains.cd_clk200 = ClockDomain()
        self.clock_domains.cd_clk100 = ClockDomain()
        self.clock_domains.cd_eth = ClockDomain()

        clk50 = platform.request("clk50")
        platform.add_period_constraint(clk50, 1e9/50e6)

        soft_reset = Signal()
        self.sync += timeline(self.reset.re, [(1024, [soft_reset.eq(1)])])

        self.submodules.pll = pll = S7PLL()
        pll.register_clkin(clk50, 50e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq)
        pll.create_clkout(self.cd_sys4x, 4*sys_clk_freq)
        pll.create_clkout(self.cd_sys4x_dqs, 4*sys_clk_freq, phase=90)
        pll.create_clkout(self.cd_clk200, 200e6)
        pll.create_clkout(self.cd_clk100, 100e6)
        pll.create_clkout(self.cd_eth, 50e6)

        self.submodules.idelayctrl = S7IDELAYCTRL(self.cd_clk200)


class NeTV2SoC(SoCSDRAM):
    csr_peripherals = [
        "crg",
        "dna",
        "xadc",
        "flash",
        "icap",

        "ddrphy",

        "ethphy",
        "ethmac",

        "pcie_phy",
        "pcie_dma0",
        "pcie_dma1",
        "pcie_msi",

        "hdmi_out0",
        "hdmi_in0",
        "hdmi_in0_freq",
        "hdmi_in0_edid_mem",
    ]
    csr_map_update(SoCSDRAM.csr_map, csr_peripherals)

    interrupt_map = {
        "ethmac": 3,
    }
    interrupt_map.update(SoCSDRAM.interrupt_map)

    mem_map = {
        "ethmac": 0x30000000,
    }
    mem_map.update(SoCSDRAM.mem_map)

    #mem_map["csr"] = 0x00000000
    #mem_map["rom"] = 0x20000000

    def __init__(self, platform,
        with_sdram=True,
        with_ethernet=False,
        with_etherbone=True,
        with_pcie=False,
        with_hdmi_in0=True, with_hdmi_out0=True):
        assert not (with_pcie and with_interboard_communication)
        sys_clk_freq = int(100e6)
        sd_freq = int(100e6)
        SoCSDRAM.__init__(self, platform, sys_clk_freq,
            #cpu_type="vexriscv", l2_size=32,
            cpu_type=None, l2_size=32,
            #csr_data_width=8, csr_address_width=14,
            csr_data_width=32, csr_address_width=14,
            integrated_rom_size=0x8000,
            integrated_sram_size=0x4000,
            integrated_main_ram_size=0x8000 if not with_sdram else 0,
            ident="NeTV2 LiteX Test SoC", ident_version=True,
            reserve_nmi_interrupt=False)

        # crg
        self.submodules.crg = CRG(platform, sys_clk_freq)

        # dnax
        self.submodules.dna = dna.DNA()

        # xadc
        self.submodules.xadc = xadc.XADC()

        # icap
        self.submodules.icap = ICAP(platform)

        # flash
        self.submodules.flash = Flash(platform.request("flash"), div=math.ceil(sys_clk_freq/25e6))

        # sdram
        if with_sdram:
            self.submodules.ddrphy = a7ddrphy.A7DDRPHY(platform.request("ddram"),
                sys_clk_freq=sys_clk_freq, iodelay_clk_freq=200e6)
            sdram_module = MT41J128M16(sys_clk_freq, "1:4")
            self.register_sdram(self.ddrphy,
                                sdram_module.geom_settings,
                                sdram_module.timing_settings,
                                controller_settings=ControllerSettings(with_bandwidth=True,
                                                                       cmd_buffer_depth=8,
                                                                       with_refresh=True))
        # ethernet
        if with_ethernet:
            self.submodules.ethphy = LiteEthPHYRMII(self.platform.request("eth_clocks"),
                                                    self.platform.request("eth"))
            self.submodules.ethmac = LiteEthMAC(phy=self.ethphy, dw=32, interface="wishbone")
            self.add_wb_slave(mem_decoder(self.mem_map["ethmac"]), self.ethmac.bus)
            self.add_memory_region("ethmac", self.mem_map["ethmac"] | self.shadow_base, 0x2000)

            self.crg.cd_eth.clk.attr.add("keep")
            self.platform.add_false_path_constraints(
                self.crg.cd_sys.clk,
                self.crg.cd_eth.clk)

        # etherbone
        if with_etherbone:
            self.submodules.ethphy = LiteEthPHYRMII(self.platform.request("eth_clocks"), self.platform.request("eth"))
            self.submodules.ethcore = LiteEthUDPIPCore(self.ethphy, 0x10e2d5000000, convert_ip("192.168.1.50"), sys_clk_freq)
            self.submodules.etherbone = LiteEthEtherbone(self.ethcore.udp, 1234, mode="master")
            self.add_wb_master(self.etherbone.wishbone.bus)
            #self.submodules.etherbone = LiteEthEtherbone(self.ethcore.udp, 1234, mode="master")
            #self.add_wb_master(self.etherbone.wishbone.bus)

            self.crg.cd_eth.clk.attr.add("keep")
            self.platform.add_false_path_constraints(
                self.crg.cd_sys.clk,
                self.crg.cd_eth.clk)

        # pcie
        if with_pcie:
            # pcie phy
            self.submodules.pcie_phy = S7PCIEPHY(platform, platform.request("pcie_x2"))
            platform.add_false_path_constraints(
                self.crg.cd_sys.clk,
                self.pcie_phy.cd_pcie.clk)

            # pcie endpoint
            self.submodules.pcie_endpoint = LitePCIeEndpoint(self.pcie_phy, with_reordering=True)

            # pcie wishbone bridge
            self.submodules.pcie_bridge = LitePCIeWishboneBridge(self.pcie_endpoint, lambda a: 1, shadow_base=0x80000000)
            self.add_wb_master(self.pcie_bridge.wishbone)

            # pcie dma
            self.submodules.pcie_dma0 = LitePCIeDMA(self.pcie_phy, self.pcie_endpoint, with_loopback=True)

            # pcie msi
            self.submodules.pcie_msi = LitePCIeMSI()
            self.comb += self.pcie_msi.source.connect(self.pcie_phy.msi)
            self.interrupts = {
                "PCIE_DMA0_WRITER":    self.pcie_dma0.writer.irq,
                "PCIE_DMA0_READER":    self.pcie_dma0.reader.irq
            }
            for i, (k, v) in enumerate(sorted(self.interrupts.items())):
                self.comb += self.pcie_msi.irqs[i].eq(v)
                self.add_constant(k + "_INTERRUPT", i)

        # hdmi in 0
        if with_hdmi_in0:
            hdmi_in0_pads = platform.request("hdmi_in", 0)
            self.submodules.hdmi_in0_freq = FreqMeter(period=sys_clk_freq)
            self.submodules.hdmi_in0 = HDMIIn(
                hdmi_in0_pads,
                self.sdram.crossbar.get_port(mode="write"),
                fifo_depth=512,
                device="xc7",
                split_mmcm=True)
            self.comb += self.hdmi_in0_freq.clk.eq(self.hdmi_in0.clocking.cd_pix.clk),
            for clk in [self.hdmi_in0.clocking.cd_pix.clk,
                        self.hdmi_in0.clocking.cd_pix1p25x.clk,
                        self.hdmi_in0.clocking.cd_pix5x.clk]:
                self.platform.add_false_path_constraints(self.crg.cd_sys.clk, clk)
            self.platform.add_period_constraint(platform.lookup_request("hdmi_in", 0).clk_p, 1e9/148.5e6)

        # hdmi out 0
        if with_hdmi_out0:
            hdmi_out0_dram_port = self.sdram.crossbar.get_port(mode="read", dw=16, cd="hdmi_out0_pix", reverse=True)
            self.submodules.hdmi_out0 = VideoOut(
                platform.device,
                platform.request("hdmi_out", 0),
                hdmi_out0_dram_port,
                "ycbcr422",
                fifo_depth=4096)
            for clk in [self.hdmi_out0.driver.clocking.cd_pix.clk,
                        self.hdmi_out0.driver.clocking.cd_pix5x.clk]:
                self.platform.add_false_path_constraints(self.crg.cd_sys.clk, clk)

        # hdmi in 1
        if with_hdmi_in1:
            hdmi_in1_pads = platform.request("hdmi_in", 1)
            self.submodules.hdmi_in1_freq = FreqMeter(period=sys_clk_freq)
            self.submodules.hdmi_in1 = HDMIIn(
                hdmi_in1_pads,
                self.sdram.crossbar.get_port(mode="write"),
                fifo_depth=512,
                device="xc7",
                split_mmcm=True)
            self.comb += self.hdmi_in1_freq.clk.eq(self.hdmi_in1.clocking.cd_pix.clk),
            for clk in [self.hdmi_in1.clocking.cd_pix.clk,
                        self.hdmi_in1.clocking.cd_pix1p25x.clk,
                        self.hdmi_in1.clocking.cd_pix5x.clk]:
                self.platform.add_false_path_constraints(self.crg.cd_sys.clk, clk)
            self.platform.add_period_constraint(platform.lookup_request("hdmi_in", 1).clk_p, 1e9/148.5e6)

        # hdmi out 1
        if with_hdmi_out1:
            hdmi_out1_dram_port = self.sdram.crossbar.get_port(mode="read", dw=16, cd="hdmi_out1_pix", reverse=True)
            self.submodules.hdmi_out1 = VideoOut(
                platform.device,
                platform.request("hdmi_out", 1),
                hdmi_out1_dram_port,
                "ycbcr422",
                fifo_depth=4096)
            for clk in [self.hdmi_out1.driver.clocking.cd_pix.clk,
                        self.hdmi_out1.driver.clocking.cd_pix5x.clk]:
                self.platform.add_false_path_constraints(self.crg.cd_sys.clk, clk)

        # led blinking (sys)
        sys_counter = Signal(32)
        self.sync.sys += sys_counter.eq(sys_counter + 1)
        self.comb += platform.request("user_led", 0).eq(sys_counter[26])


        # led blinking (pcie)
        if with_pcie:
            pcie_counter = Signal(32)
            self.sync.pcie += pcie_counter.eq(pcie_counter + 1)
            self.comb += platform.request("user_led", 1).eq(pcie_counter[26])

    def generate_software_header(self):
        csr_header = get_csr_header(self.csr_regions,
                                    self.constants,
                                    with_access_functions=False)
        tools.write_to_file(os.path.join("software", "pcie", "kernel", "csr.h"), csr_header)


class NeTV2MVPSoC(NeTV2SoC):
    def __init__(self, platform):
        NeTV2SoC.__init__(self, platform, with_ethernet=False)


def main():
    platform = netv2.Platform()
    if "mvp" in sys.argv[1:]:
        soc = NeTV2MVPSoC(platform)
    else:
        soc = NeTV2SoC(platform)
    if "no-compile" in sys.argv[1:]:
        compile_gateware = False
    else:
        compile_gateware = True
    builder = Builder(soc, output_dir="build", csr_csv="test/csr.csv", compile_gateware=compile_gateware)
    vns = builder.build(build_name="netv2")
    soc.generate_software_header()

if __name__ == "__main__":
    main()
