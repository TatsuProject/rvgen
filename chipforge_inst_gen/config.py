"""Instruction generator configuration, ported from ``riscv_instr_gen_config.sv``.

This module exposes :class:`Config`, a dataclass that carries every knob
riscv-dv's SV config honors. Defaults track the SV source; ``gen_opts``
plus-arg strings coming from testlist YAML are parsed into field values
via :meth:`Config.apply_plusargs`.

The goal of Phase 1 is to keep the knob surface compatible with existing
testlists — a ``+instr_cnt=5000 +no_fence=1`` line in a testlist must set
``cfg.instr_cnt = 5000`` and ``cfg.no_fence = True`` the same way SV does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any, Iterable

from chipforge_inst_gen.isa.enums import (
    DataPattern,
    FRoundingMode,
    MtvecMode,
    PrivilegedMode,
    PrivilegedReg,
    RiscvReg,
    VregInitMethod,
)
from chipforge_inst_gen.targets import TargetCfg
from chipforge_inst_gen.vector_config import VectorConfig, Vtype


# ---------------------------------------------------------------------------
# Parse helpers (private)
# ---------------------------------------------------------------------------


_PLUSARG_RE = re.compile(r"\+(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?:=(?P<value>\S+))?")


def _parse_bool(val: str | bool | int) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return bool(val)
    v = val.strip().lower()
    return v in ("1", "true", "t", "yes", "y")


def _parse_int(val: str | int) -> int:
    if isinstance(val, int):
        return val
    s = val.strip()
    base = 10
    if s.startswith(("0x", "0X")):
        base = 16
        s = s[2:]
    elif s.startswith(("0b", "0B")):
        base = 2
        s = s[2:]
    return int(s, base)


def _parse_boot_mode(val: str) -> PrivilegedMode:
    v = val.strip().lower()
    if v == "m":
        return PrivilegedMode.MACHINE_MODE
    if v == "s":
        return PrivilegedMode.SUPERVISOR_MODE
    if v == "u":
        return PrivilegedMode.USER_MODE
    raise ValueError(f"Invalid boot_mode {val!r}; expected one of m/s/u")


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """Instruction generator configuration (SV: ``riscv_instr_gen_config``).

    Fields are grouped by topic; each group tracks a SV constraint block or
    logical subsystem. Scalar ``int`` / ``bool`` knobs map 1:1 to SV plusargs.

    Only fields relevant to Phase 1 are modelled. Phase 2 can add more with
    no breaking changes.
    """

    # ---- Program structure ----
    num_of_tests: int = 1
    num_of_sub_program: int = 5
    instr_cnt: int = 200
    main_program_instr_cnt: int = 0  # derived from instr_cnt in post-validate
    no_branch_jump: bool = False
    no_load_store: bool = False
    no_fence: bool = False
    no_data_page: bool = False
    no_directed_instr: bool = False
    no_csr_instr: bool = False
    no_ebreak: bool = True
    no_ecall: bool = True
    no_dret: bool = True
    no_wfi: bool = True
    enable_unaligned_load_store: bool = False
    bare_program_mode: bool = False

    # ---- Privilege modes / mstatus ----
    init_privileged_mode: PrivilegedMode = PrivilegedMode.MACHINE_MODE
    virtual_addr_translation_on: bool = False
    enable_page_table_exception: bool = False
    mstatus_mprv: bool = False
    mstatus_mxr: bool = False
    mstatus_sum: bool = False
    mstatus_tvm: bool = False
    set_mstatus_tw: bool = False
    set_mstatus_mprv: bool = False
    enable_sfence: bool = False
    allow_sfence_exception: bool = False
    mstatus_fs: int = 0
    mstatus_vs: int = 0

    # ---- Interrupts / traps ----
    enable_interrupt: bool = False
    enable_nested_interrupt: bool = False
    enable_timer_irq: bool = False
    enable_illegal_csr_instruction: bool = False
    enable_access_invalid_csr_level: bool = False
    no_delegation: bool = True
    force_m_delegation: bool = False
    force_s_delegation: bool = False
    mtvec_mode: MtvecMode = MtvecMode.VECTORED
    tvec_alignment: int = 2

    # ---- Extensions ----
    enable_floating_point: bool = False
    enable_vector_extension: bool = False
    vector_instr_only: bool = False
    vreg_init_method: VregInitMethod = VregInitMethod.RANDOM_VALUES_VMV
    enable_b_extension: bool = False
    enable_zba_extension: bool = False
    enable_zbb_extension: bool = False
    enable_zbc_extension: bool = False
    enable_zbs_extension: bool = False
    disable_compressed_instr: bool = False

    # ---- Reserved registers ----
    gpr: tuple[RiscvReg, RiscvReg, RiscvReg, RiscvReg] = (
        RiscvReg.T0, RiscvReg.T1, RiscvReg.T2, RiscvReg.T3,
    )
    scratch_reg: RiscvReg = RiscvReg.T5
    pmp_reg: tuple[RiscvReg, RiscvReg] = (RiscvReg.T4, RiscvReg.S11)
    sp: RiscvReg = RiscvReg.SP
    tp: RiscvReg = RiscvReg.TP
    ra: RiscvReg = RiscvReg.RA
    fix_sp: bool = False

    # ---- CSR handling ----
    gen_all_csrs_by_default: bool = False
    gen_csr_ro_write: bool = False
    randomize_csr: bool = False
    check_misa_init_val: bool = False
    check_xstatus: bool = True

    # ---- Memory / stack ----
    stack_len: int = 5000
    kernel_stack_len: int = 4000
    kernel_program_instr_cnt: int = 400
    min_stack_len_per_program: int = 0  # set from XLEN in post-validate
    max_stack_len_per_program: int = 0  # set from XLEN in post-validate
    data_page_pattern: DataPattern = DataPattern.RAND_DATA
    use_push_data_section: bool = False

    # ---- Signature / HTIF ----
    signature_addr: int = 0xDEADBEEF
    require_signature_addr: bool = False

    # ---- FP ----
    fcsr_rm: FRoundingMode = FRoundingMode.RNE

    # ---- Debug ----
    gen_debug_section: bool = False
    enable_ebreak_in_debug_rom: bool = False
    set_dcsr_ebreak: bool = False
    num_debug_sub_program: int = 0
    enable_debug_single_step: bool = False
    single_step_iterations: int = 0

    # ---- Misc ----
    asm_test_suffix: str = ""
    illegal_instr_ratio: int = 0
    hint_instr_ratio: int = 0
    num_of_harts: int = 1
    enable_misaligned_instr: bool = False
    enable_dummy_csr_write: bool = False
    max_branch_step: int = 20
    max_directed_instr_stream_seq: int = 20

    # ---- Directed instruction streams (collected from gen_opts) ----
    directed_instr: dict[int, tuple[str, int]] = field(default_factory=dict)

    # ---- Reserved register set (computed in post-validate) ----
    reserved_regs: tuple[RiscvReg, ...] = ()

    # ---- Target cfg reference (for downstream code that needs e.g. XLEN) ----
    target: TargetCfg | None = None

    # ---- Vector extension config (populated by make_config when the target
    # enables RVV; None otherwise) ----
    vector_cfg: VectorConfig | None = None

    # -- Runtime-only (not for SV parity) --
    seed: int | None = None

    # ---- Post-init validation ----

    def __post_init__(self) -> None:
        self._finalize_stack_sizing()
        self._finalize_reserved_regs()
        self._finalize_main_program_cnt()

    def _finalize_stack_sizing(self) -> None:
        """SV post_randomize: ``min_stack_len_per_program = 2*(XLEN/8)``."""
        xlen = self.target.xlen if self.target else 32
        if self.min_stack_len_per_program == 0:
            self.min_stack_len_per_program = 2 * (xlen // 8)
        if self.max_stack_len_per_program == 0:
            self.max_stack_len_per_program = 16 * (xlen // 8)

    def _finalize_reserved_regs(self) -> None:
        """SV post_randomize: ``reserved_regs = {tp, sp, scratch_reg}``."""
        self.reserved_regs = (self.tp, self.sp, self.scratch_reg)

    def _finalize_main_program_cnt(self) -> None:
        """``main_program_instr_cnt + sum(sub) == instr_cnt`` — for Phase 1 we
        just use the full count for the main program and let per-sub counts
        be determined later by the sequence generator.

        Always re-derives from ``instr_cnt`` so late plusarg updates propagate.
        """
        self.main_program_instr_cnt = self.instr_cnt

    # ---- Public API ----

    def apply_plusarg(self, key: str, value: str | None) -> None:
        """Apply a single ``+key=value`` plusarg (SV-compatible).

        Unknown keys are silently ignored so that yet-unimplemented knobs
        don't crash the CLI on real testlists. TODO(Phase 2): log a warning
        with ``--verbose``.
        """
        # directed_instr_N=<name>,<count>
        m = re.fullmatch(r"directed_instr_(\d+)", key)
        if m:
            idx = int(m.group(1))
            if not value:
                return
            parts = value.split(",")
            stream_name = parts[0]
            count = int(parts[1]) if len(parts) > 1 else 0
            self.directed_instr[idx] = (stream_name, count)
            return

        if key == "boot_mode":
            if value is not None:
                self.init_privileged_mode = _parse_boot_mode(value)
            return

        # Generic attribute lookup by name.
        if hasattr(self, key):
            current = getattr(self, key)
            if isinstance(current, bool):
                setattr(self, key, _parse_bool(value) if value is not None else True)
            elif isinstance(current, int):
                if value is None:
                    setattr(self, key, 1)
                else:
                    setattr(self, key, _parse_int(value))
            elif isinstance(current, str):
                setattr(self, key, value or "")
            # Enums and nested dataclasses: parsing left to Phase 2.

    def apply_plusargs(self, gen_opts: str) -> None:
        """Parse a space-separated plusarg string (e.g., ``"+instr_cnt=5000 +no_fence=1"``)."""
        if not gen_opts:
            return
        for match in _PLUSARG_RE.finditer(gen_opts):
            self.apply_plusarg(match.group("key"), match.group("value"))
        # Re-run the stack/reserved recalculations if they depended on XLEN
        # or reserved regs: changing `instr_cnt` should bump main_program_cnt.
        self._finalize_main_program_cnt()
        self._finalize_reserved_regs()

    def as_dict(self) -> dict[str, Any]:
        """Snapshot the config for debugging / YAML dump."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


# ---------------------------------------------------------------------------
# Construction helper
# ---------------------------------------------------------------------------


def make_config(target: TargetCfg, gen_opts: str = "", **overrides: Any) -> Config:
    """Build a :class:`Config` for ``target`` and apply ``gen_opts`` plusargs.

    Post-apply, ``overrides`` (if any) are set directly — these are meant for
    programmatic use (tests, library callers), whereas ``gen_opts`` is the
    path the CLI takes.
    """
    cfg = Config(target=target, num_of_harts=target.num_harts)
    # Target-specific defaults that the SV would set via randomize:
    # - disable_compressed_instr if target doesn't include C.
    from chipforge_inst_gen.isa.enums import RiscvInstrGroup as G
    compressed_groups = {
        G.RV32C, G.RV64C, G.RV32FC, G.RV32DC, G.RV128C,
    }
    if not any(g in target.supported_isa for g in compressed_groups):
        cfg.disable_compressed_instr = True

    # If the target enables RVV (or advertises a Zve* subset), flip on the
    # cfg-level flag and stamp a VectorConfig. SV bringup_c defaults:
    # vstart=0, vl=VLEN/vsew, vediv=1, LMUL=1, SEW=min(32, ELEN).
    from chipforge_inst_gen.isa.filtering import (
        target_has_any_vector,
        target_supports_fp_vector,
    )
    if target.vector_extension_enable or target_has_any_vector(target):
        cfg.enable_vector_extension = True
        # For embedded Zve32x-style profiles the target may not set
        # vector_extension_enable=True; honor the ISA advertisement either way.
        cfg.vector_cfg = VectorConfig(
            vtype=Vtype(vlmul=1, vsew=min(32, target.elen), vediv=1),
            vlen=target.vlen,
            elen=target.elen,
            selen=target.selen,
            max_lmul=target.max_lmul,
            num_vec_gpr=target.num_vec_gpr,
            # vec_fp defaults False; a target with an FP-vector profile can
            # opt in via a testlist plusarg (+vec_fp=1).
            vec_fp=False,
        )

    cfg.apply_plusargs(gen_opts)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    cfg.__post_init__()
    return cfg
