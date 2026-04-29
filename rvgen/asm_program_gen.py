"""Top-level assembly composer — Phase 1 step-5 skeleton.

Port of ``src/riscv_asm_program_gen.sv::gen_program`` (riscv_asm_program_gen.sv:68).
The Phase-1 MVP handles the M-mode / DIRECT-trap / no-paging path:

    .include "user_define.h"
    .globl _start
    .section .text
    [.option norvc]
    _start:
       la <scratch>, h0_start
       jalr x0, <scratch>, 0
    h0_start:
       <setup_misa>
       <pre_enter_privileged_mode>      # boot CSRs + mret
    init:
       <GPR init>
       la <sp>, h<N>user_stack_end
       [signature INITIALIZED]
    main:
       <sequence body>
    test_done:
       li gp, 1
       ecall
    <trap handler>
    write_tohost:  sw gp, tohost, t5
    _exit:          j write_tohost
    .section .data
    .align 6; .global tohost; tohost: .dword 0;
    .align 6; .global fromhost; fromhost: .dword 0;
    <user stack>
    <kernel stack>

Step 8 will add S/U modes, paging, PMP, debug.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from rvgen.config import Config
from rvgen.isa.enums import (
    LABEL_STR_LEN,
    PrivilegedMode,
    RiscvReg,
)
from rvgen.isa.filtering import AvailableInstrs, create_instr_list
from rvgen.isa.utils import format_string, hart_prefix
from rvgen.privileged.boot import (
    gen_pre_enter_privileged_mode,
    gen_setup_misa,
)
from rvgen.privileged.trap import gen_trap_handler
from rvgen.sections.data_page import (
    DEFAULT_AMO_REGION,
    DEFAULT_MEM_REGIONS,
    gen_data_page,
    gen_stack_section,
    gen_tohost_fromhost,
)
from rvgen.sections.signature import (
    INITIALIZED,
    IN_MACHINE_MODE,
    TEST_PASS,
    emit_core_status,
    emit_test_result,
)
from rvgen.sequence import InstrSequence


_INDENT = " " * LABEL_STR_LEN


def _line(s: str) -> str:
    return f"{_INDENT}{s}"


def _labeled(label: str, body: str = "") -> str:
    return format_string(f"{label}:", LABEL_STR_LEN) + body


# GPR init-value distribution (SV: riscv_asm_program_gen.sv:672).
# SV weights: 0, 0x80000000, [0x1..0xF], [0x10..0xEFFFFFFF], [0xF0000000..0xFFFFFFFF] each :/ 1.
_GPR_INIT_BUCKETS = (
    (lambda rng: 0, 1),
    (lambda rng: 0x80000000, 1),
    (lambda rng: rng.randint(0x1, 0xF), 1),
    (lambda rng: rng.randint(0x10, 0xEFFFFFFF), 1),
    (lambda rng: rng.randint(0xF0000000, 0xFFFFFFFF), 1),
)


def _pick_gpr_init(rng: random.Random) -> int:
    pool, weights = zip(*_GPR_INIT_BUCKETS)
    (fn,) = rng.choices(pool, weights=weights, k=1)
    return fn(rng)


# Random SPF / DPF value buckets — port of SV ``get_rand_spf_value`` and
# ``get_rand_dpf_value`` (riscv_asm_program_gen.sv:648, :675). Uniform pick
# across {±inf, ±largest, ±0, sNaN/qNaN, normal, subnormal} so FP init
# stresses every classification of float operand.
def _rand_spf_value(rng: random.Random) -> int:
    bucket = rng.randrange(6)
    match bucket:
        case 0:  # ±infinity
            return rng.choice((0x7F800000, 0xFF800000))
        case 1:  # ±largest finite
            return rng.choice((0x7F7FFFFF, 0xFF7FFFFF))
        case 2:  # ±zero
            return rng.choice((0x00000000, 0x80000000))
        case 3:  # sNaN / qNaN
            return rng.choice((0x7F800001, 0x7FC00000))
        case 4:  # normal (exponent != 0)
            sign = rng.randrange(2) << 31
            exp = rng.randint(1, 0xFE) << 23
            frac = rng.randrange(1 << 23)
            return sign | exp | frac
        case _:  # subnormal (exponent == 0, frac != 0)
            sign = rng.randrange(2) << 31
            frac = rng.randint(1, (1 << 23) - 1)
            return sign | frac


def _rand_dpf_value(rng: random.Random) -> int:
    bucket = rng.randrange(6)
    match bucket:
        case 0:
            return rng.choice((0x7FF0000000000000, 0xFFF0000000000000))
        case 1:
            return rng.choice((0x7FEFFFFFFFFFFFFF, 0xFFEFFFFFFFFFFFFF))
        case 2:
            return rng.choice((0x0000000000000000, 0x8000000000000000))
        case 3:
            return rng.choice((0x7FF0000000000001, 0x7FF8000000000000))
        case 4:
            sign = rng.randrange(2) << 63
            exp = rng.randint(1, 0x7FE) << 52
            frac = rng.randrange(1 << 52)
            return sign | exp | frac
        case _:
            sign = rng.randrange(2) << 63
            frac = rng.randint(1, (1 << 52) - 1)
            return sign | frac


@dataclass
class AsmProgramGen:
    """Top-level assembler-file composer."""

    cfg: Config
    avail: AvailableInstrs
    rng: random.Random

    # Output bucket: the final list of ``.S`` lines.
    instr_stream: list[str] = field(default_factory=list)

    # Per-hart sequences (Phase 1 MVP: single hart only).
    main_sequence: InstrSequence | None = None

    # Symbol names bound in :meth:`gen_program`.
    hart: int = 0

    def gen_program(self) -> list[str]:
        """Assemble the full ``.S`` line list (M-mode, DIRECT, no paging)."""
        self.instr_stream = []
        self._gen_program_header()
        for hart in range(self.cfg.num_of_harts):
            self.hart = hart
            self._gen_hart_section(hart)
        self._gen_test_done()
        self._gen_trap_handler_section()
        self._gen_program_end()
        self._gen_data_section()
        return self.instr_stream

    # ------------------------------------------------------------------
    # Phase-1 MVP phases
    # ------------------------------------------------------------------

    def _gen_program_header(self) -> None:
        """Port of SV ``gen_program_header`` (riscv_asm_program_gen.sv:522)."""
        self.instr_stream.append('.include "user_define.h"')
        self.instr_stream.append(".globl _start")
        self.instr_stream.append(".section .text")
        if self.cfg.disable_compressed_instr:
            self.instr_stream.append(".option norvc;")
        self.instr_stream.append('.include "user_init.s"')

        # _start always dispatches to ``h<hart>_start`` labels (SV always
        # emits these literally regardless of num_harts).
        self.instr_stream.append(_labeled("_start"))
        if self.cfg.num_of_harts > 1:
            self.instr_stream.append(
                _line(f"csrr {self.cfg.gpr[0].abi}, 0xf14")
            )
            for h in range(self.cfg.num_of_harts):
                self.instr_stream.append(
                    _line(f"li {self.cfg.gpr[1].abi}, {h}")
                )
                self.instr_stream.append(
                    _line(f"beq {self.cfg.gpr[0].abi}, {self.cfg.gpr[1].abi}, {h}f")
                )
            for h in range(self.cfg.num_of_harts):
                self.instr_stream.append(_labeled(f"{h}"))
                self.instr_stream.append(
                    _line(f"la {self.cfg.scratch_reg.abi}, h{h}_start")
                )
                self.instr_stream.append(
                    _line(f"jalr x0, {self.cfg.scratch_reg.abi}, 0")
                )
        else:
            self.instr_stream.append(_line("j h0_start"))

    def _gen_hart_section(self, hart: int) -> None:
        """Emit h<hart>_start + init + main.

        SV convention (riscv_asm_program_gen.sv:75, :338): the ``h<N>_start``
        label is always literal (even for num_harts=1), so ``_start:`` can
        uniquely dispatch. All other hart-local labels (``init``,
        ``mtvec_handler``, ``user_stack_*``, ``kernel_stack_*``) use the
        collapsible ``hart_prefix()`` which is ``""`` when num_harts == 1.
        """
        gpr0 = self.cfg.gpr[0]
        prefix = hart_prefix(hart, self.cfg.num_of_harts)
        # h<hart>_start is always literal, independent of hart_prefix.
        hart_start_label = f"h{hart}_start"

        self.instr_stream.append(_labeled(hart_start_label))
        # SV guards both setup_misa + pre_enter_privileged_mode behind
        # ``if (!cfg.bare_program_mode)`` (riscv_asm_program_gen.sv:76). Skipping
        # them lets the output target rv32ui-only cores that lack CSRs entirely.
        if not self.cfg.bare_program_mode:
            self.instr_stream.extend(gen_setup_misa(self.cfg, gpr0))
            self.instr_stream.extend(gen_pre_enter_privileged_mode(
                self.cfg,
                hart=hart,
                init_label=f"{prefix}init",
                trap_handler_label=f"{prefix}mtvec_handler",
            ))

        self.instr_stream.append(_labeled(f"{prefix}init"))
        self._gen_init_section(hart)

        main_label = f"{prefix}main"
        self.main_sequence = InstrSequence(
            cfg=self.cfg,
            avail=self.avail,
            label_name=main_label,
            instr_cnt=self.cfg.main_program_instr_cnt,
        )
        # Build directed-stream instances from cfg.directed_instr = {idx: (name, cnt)}.
        # Label uniqueness: use a stream-name-keyed global counter so that
        # two directive entries naming the same stream (e.g. testlist has
        # +directed_instr_2=riscv_hazard_instr_stream,4 and a plusarg adds
        # +directed_instr_1=riscv_hazard_instr_stream,20) produce
        # non-colliding labels. The old per-directive counter ran 0..N-1
        # once per directive — two directives with the same stream name
        # emitted the same labels and GCC rejected the asm with
        # "symbol 'main_riscv_hazard_instr_stream_3' is already defined".
        #
        # When +no_data_page=1, the .data section is empty and the region_N
        # symbols are never defined. Any stream that does `la rs1, region_N`
        # would then fail to link. Skip those streams up front.
        from rvgen.streams import get_stream
        from rvgen.stream import InstrStream
        # Streams that reference region_N labels in their generated asm.
        _DATA_REGION_STREAMS = frozenset({
            "riscv_load_store_rand_instr_stream",
            "riscv_load_store_stress_instr_stream",
            "riscv_load_store_hazard_instr_stream",
            "riscv_hazard_instr_stream",
            "riscv_multi_page_load_store_instr_stream",
            "riscv_mem_region_stress_test",
            "riscv_load_store_rand_addr_instr_stream",
            "riscv_load_store_shared_mem_stream",
            "riscv_vector_load_store_instr_stream",
            "riscv_vector_amo_instr_stream",
        })
        self.main_sequence.directed_instr = []
        stream_counter: dict[str, int] = {}
        for idx, (name, count) in sorted(self.cfg.directed_instr.items()):
            if self.cfg.no_data_page and name in _DATA_REGION_STREAMS:
                # Testlist asked for LS stream but data pages are disabled —
                # emitting it would produce undefined region_N references.
                continue
            try:
                stream_cls = get_stream(name)
            except KeyError:
                # Unknown streams are skipped silently for Phase 1 forward-compat.
                continue
            for _ in range(max(count, 1)):
                i = stream_counter.get(name, 0)
                stream_counter[name] = i + 1
                stream = stream_cls(
                    cfg=self.cfg,
                    avail=self.avail,
                    rng=self.rng,
                    stream_name=name,
                    label=f"{main_label}_{name}_{i}",
                    hart=hart,
                )
                stream.generate()
                # Wrap in a plain InstrStream so InstrSequence can insert it.
                wrapper = InstrStream(instr_list=stream.instr_list)
                self.main_sequence.directed_instr.append(wrapper)

        self.main_sequence.gen_instr(self.rng, no_branch=self.cfg.no_branch_jump)
        self.main_sequence.post_process_instr(self.rng)
        self.main_sequence.generate_instr_stream()
        self.instr_stream.extend(self.main_sequence.instr_string_list)

    def _gen_init_section(self, hart: int) -> None:
        """FP init + GPR init + stack pointer + signature INITIALIZED handshake."""
        prefix = hart_prefix(hart, self.cfg.num_of_harts)
        # Floating-point register init runs FIRST so the random GPR scratch
        # used by `fmv.w.x` / `fmv.d.x` doesn't clobber a freshly-loaded GPR.
        # Without this, an uninitialized f0..f31 read produces Spike-vs-DUT
        # mismatches: Spike resets FPRs to canonical qNaN (0x7FC00000), most
        # DUTs reset to 0. SV: gen_init_section → init_floating_point_gpr.
        if self.cfg.enable_floating_point:
            self._gen_fp_init()

        # Initialize x1..x31 with biased random values (skip SP and TP — they
        # will be set below / via the trap handler).
        skip = {self.cfg.sp.value, self.cfg.tp.value, 0}
        for i in range(32):
            if i in skip:
                continue
            val = _pick_gpr_init(self.rng)
            self.instr_stream.append(_line(f"li x{i}, 0x{val:x}"))

        # Vector engine init — emitted before the stack pointer load because
        # init_vec_gpr runs in a "temporary SEW/LMUL" regime (SV line 1629)
        # before the final vsetvli sets the real vtype.
        if self.cfg.enable_vector_extension and self.cfg.vector_cfg is not None:
            self._gen_vector_init(hart)

        # Stack pointer.
        self.instr_stream.append(
            _line(f"la {self.cfg.sp.abi}, {prefix}user_stack_end")
        )

        # Signature: CORE_STATUS INITIALIZED then IN_MACHINE_MODE.
        if self.cfg.require_signature_addr:
            self.instr_stream.extend(
                emit_core_status(
                    signature_addr=self.cfg.signature_addr,
                    core_status=INITIALIZED,
                    gpr0=self.cfg.gpr[0],
                    gpr1=self.cfg.gpr[1],
                )
            )
            if self.cfg.init_privileged_mode == PrivilegedMode.MACHINE_MODE:
                self.instr_stream.extend(
                    emit_core_status(
                        signature_addr=self.cfg.signature_addr,
                        core_status=IN_MACHINE_MODE,
                        gpr0=self.cfg.gpr[0],
                        gpr1=self.cfg.gpr[1],
                    )
                )

        # Optional: arm a CLINT timer interrupt so the test body traps
        # into the ISR at least once. Gated on both enable_interrupt and
        # enable_timer_irq — the boot sequence already turned on the MIE
        # and MSTATUS bits that make the IRQ visible to the core.
        if self.cfg.enable_interrupt and self.cfg.enable_timer_irq:
            from rvgen.privileged.interrupts import gen_arm_timer_irq
            self.instr_stream.extend(gen_arm_timer_irq(self.cfg, hart=hart))

    def _gen_fp_init(self) -> None:
        """Emit per-FP-register initialization.

        SV port (riscv_asm_program_gen.sv:601 init_floating_point_gpr):
        for each f0..f31 emit `li xGPR0, <rand_spf>; fmv.w.x fN, xGPR0`,
        then `fsrmi <fcsr_rm>` to set rounding mode. When the target also
        has the D extension, randomly use a double-precision sequence
        (li/slli/li/or/fmv.d.x) for that register instead.
        """
        from rvgen.isa.enums import RiscvInstrGroup as _G

        gpr0 = self.cfg.gpr[0].abi
        gpr1 = self.cfg.gpr[1].abi
        supported = set(self.cfg.target.supported_isa)
        has_d = bool({_G.RV32D, _G.RV64D, _G.RV32DC} & supported)
        num_fp = self.cfg.target.num_float_gpr

        # `fmv.d.x` is RV64D only — on RV32D the GPR is 32-bit so there is no
        # encoding for moving a full 64-bit double via xreg. Restrict the
        # double-precision init path to XLEN=64. RV32D uses single-precision
        # init only; the upper 32 bits get NaN-boxed via fmv.w.x.
        d_init_legal = has_d and self.cfg.target.xlen >= 64
        for i in range(num_fp):
            if d_init_legal and self.rng.random() < 0.5:
                imm = _rand_dpf_value(self.rng)
                hi = (imm >> 32) & 0xFFFFFFFF
                lo = imm & 0xFFFFFFFF
                self.instr_stream.append(_line(f"li {gpr0}, 0x{hi:x}"))
                self.instr_stream.append(_line(f"slli {gpr0}, {gpr0}, 16"))
                self.instr_stream.append(_line(f"slli {gpr0}, {gpr0}, 16"))
                self.instr_stream.append(_line(f"li {gpr1}, 0x{lo:x}"))
                self.instr_stream.append(_line(f"or {gpr1}, {gpr1}, {gpr0}"))
                self.instr_stream.append(_line(f"fmv.d.x f{i}, {gpr1}"))
            else:
                imm = _rand_spf_value(self.rng)
                self.instr_stream.append(_line(f"li {gpr0}, 0x{imm:x}"))
                self.instr_stream.append(_line(f"fmv.w.x f{i}, {gpr0}"))
        self.instr_stream.append(_line(f"fsrmi {int(self.cfg.fcsr_rm)}"))

    def _gen_vector_init(self, hart: int) -> None:
        """Emit the vector engine init section.

        Port of SV ``init_vec_gpr`` + ``randomize_vec_gpr_and_csr``
        (riscv_asm_program_gen.sv:544 and :1624).

        Flow:
          csrwi vxsat, <val>
          csrwi vxrm, <val>
          <temporary vsetvli with LMUL=1, SEW=min(ELEN,XLEN)>
          vec_reg_init:
            <per-register init — SAME_VALUES_ALL_ELEMS form:
              vmv.v.x v<N>, x<N>>
          <final vsetvli with cfg.vector_cfg.vtype>
        """
        vcfg = self.cfg.vector_cfg
        assert vcfg is not None
        gpr0 = self.cfg.gpr[0].abi
        gpr1 = self.cfg.gpr[1].abi

        # VXSAT / VXRM setup (SV emits unconditionally).
        self.instr_stream.append(_line(f"csrwi vxsat, {int(vcfg.vxsat)}"))
        self.instr_stream.append(_line(f"csrwi vxrm, {int(vcfg.vxrm)}"))

        # Temporary vsetvli with LMUL=1 for vreg init. SEW = min(ELEN, XLEN).
        # GCC 15 implements RVV v1.0 vsetvli: <sew>,<lmul>,<ta|tu>,<ma|mu>.
        # The legacy v0.8 `d<N>` (EDIV) tail no longer assembles. We emit
        # tail-agnostic / mask-agnostic by default — standard for random code.
        tmp_sew = min(vcfg.elen, self.cfg.target.xlen)
        self.instr_stream.append(_line(f"li {gpr1}, {vcfg.vl}"))
        self.instr_stream.append(
            _line(f"vsetvli {gpr0}, {gpr1}, e{tmp_sew}, m1, ta, ma")
        )
        self.instr_stream.append(_labeled(f"{hart_prefix(hart, self.cfg.num_of_harts)}vec_reg_init"))

        # SAME_VALUES_ALL_ELEMS init (simplest form, assembles cleanly).
        # Avoids the reserved scratch regs.
        reserved = {self.cfg.sp.value, self.cfg.tp.value, self.cfg.gpr[0].value,
                    self.cfg.gpr[1].value, self.cfg.scratch_reg.value}
        for v in range(vcfg.num_vec_gpr):
            # Use x<N> if safe, else x0. vmv.v.x accepts x0.
            src = v if v not in reserved else 0
            self.instr_stream.append(_line(f"vmv.v.x v{v}, x{src}"))

        # Final vsetvli with the intended vtype.
        self.instr_stream.append(_line(f"li {gpr1}, {vcfg.vl}"))
        self.instr_stream.append(
            _line(
                f"vsetvli {gpr0}, {gpr1}, e{vcfg.vtype.vsew}, "
                f"{vcfg.lmul_str()}, ta, ma"
            )
        )

    def _gen_test_done(self) -> None:
        """SV: gen_test_done (riscv_asm_program_gen.sv:700)."""
        self.instr_stream.append(_labeled("test_done"))
        self.instr_stream.append(_line("li gp, 1"))
        if self.cfg.bare_program_mode:
            self.instr_stream.append(_line("j write_tohost"))
        else:
            self.instr_stream.append(_line("ecall"))

    def _gen_trap_handler_section(self) -> None:
        """Emit the trap handler(s). Phase 1: M-mode DIRECT only.

        SV ``gen_trap_handler_section`` (riscv_asm_program_gen.sv:1046) emits an
        ``.align`` directive before each handler so MTVEC.BASE lands on an
        architecturally legal boundary (bottom two bits of MTVEC are the MODE
        field and are masked out of the jump target). Without this, compressed
        code can land the label on a 2-byte boundary and spike jumps into the
        middle of the preceding instruction.
        """
        if self.cfg.bare_program_mode:
            return
        # .align 12 (= 4 KiB) always — SV only uses it when paging is on,
        # but on bare targets too it creates a gap between the random
        # test body and the handler. Without the gap, random stores that
        # generate small offsets from a large base (typical) can land on
        # handler bytes, corrupting .text and causing infinite trap loops.
        # Cost: up to 4 KiB of .text padding per hart.
        align = 12
        for hart in range(self.cfg.num_of_harts):
            self.instr_stream.append(f".align {align}")
            self.instr_stream.extend(gen_trap_handler(self.cfg, hart=hart))

    def _gen_program_end(self) -> None:
        """Write-to-host terminator. SV: gen_program_end (riscv_asm_program_gen.sv:540)."""
        self.instr_stream.append(_labeled("write_tohost", "sw gp, tohost, t5"))
        self.instr_stream.append(_labeled("_exit", "j write_tohost"))
        self.instr_stream.append(_labeled("instr_end", "nop"))

    def _gen_data_section(self) -> None:
        """SV: gen_data_page_begin + gen_data_page + stack_section."""
        self.instr_stream.append("")
        # tohost/fromhost live in their own .tohost section (isolated page)
        # so random stores into .data regions cannot corrupt them.
        self.instr_stream.extend(gen_tohost_fromhost())
        self.instr_stream.append(".section .data")

        # Data pages (skip when cfg.no_data_page=True). Use the
        # cfg-effective region sizing so the data section honors
        # ``target.data_section_size_bytes`` (when set) and stays
        # within the DUT's physical DMEM.
        if not self.cfg.no_data_page:
            regions = self.cfg.mem_regions()
            for hart in range(self.cfg.num_of_harts):
                lines = gen_data_page(
                    regions,
                    self.cfg.data_page_pattern,
                    hart=hart,
                    num_harts=self.cfg.num_of_harts,
                    rng=self.rng,
                    use_push_data_section=self.cfg.use_push_data_section,
                )
                self.instr_stream.extend(lines)

        # AMO region (always emitted when an AMO directed stream is configured;
        # the symbol ``amo_0`` is referenced by LR/SC/AMO streams). Always-on
        # for simplicity — GCC just ignores unreferenced sections.
        self.instr_stream.extend(
            gen_data_page(
                DEFAULT_AMO_REGION,
                self.cfg.data_page_pattern,
                amo=True,
                rng=self.rng,
                use_push_data_section=self.cfg.use_push_data_section,
            )
        )

        # User stack.
        for hart in range(self.cfg.num_of_harts):
            self.instr_stream.extend(
                gen_stack_section(
                    stack_len=self.cfg.stack_len,
                    hart=hart,
                    num_harts=self.cfg.num_of_harts,
                    xlen=self.cfg.target.xlen,
                )
            )

        # Kernel stack (only needed when we actually use a trap handler).
        if not self.cfg.bare_program_mode:
            for hart in range(self.cfg.num_of_harts):
                self.instr_stream.extend(
                    gen_stack_section(
                        stack_len=self.cfg.kernel_stack_len,
                        hart=hart,
                        num_harts=self.cfg.num_of_harts,
                        xlen=self.cfg.target.xlen,
                        kernel=True,
                    )
                )
