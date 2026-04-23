"""Coverage-directed seed perturbation — heuristics + mapping.

Turns "these bins are missing" into "try these gen_opts perturbations".
The auto-regress driver uses this when ``--cov_directed`` is set: each
seed can tweak its gen_opts based on the current missing-bin set so we
don't just blindly spin the seed until all goals are hit.

Mapping is intentionally simple and local-only — no RL, no search. When
in doubt, prefer enabling *more* things (dropping ``+no_X=1`` flags) over
adding directed streams, because the former keeps the existing random
stream while the latter inserts atomic blocks that can sometimes mask
coverage in other groups.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rvgen.coverage.cgf import Goals, missing_bins
from rvgen.coverage.collectors import CoverageDB


@dataclass(frozen=True, slots=True)
class Perturbation:
    """A single candidate gen_opts tweak.

    ``drop`` is a regex matched against existing plusargs; when present
    those plusargs are removed.
    ``add`` is a string appended to gen_opts when the perturbation is
    activated.
    ``reason`` is a human-readable description for the auto_regress log.
    """
    drop: re.Pattern | None
    add: str
    reason: str


# Order matters — we apply the first N perturbations whose target bins
# are currently missing, in list order.
_PERTURBATIONS: tuple[tuple[str, Perturbation], ...] = (
    # ECALL / EBREAK / WFI / DRET are off by default; disable the gating
    # plusargs to let them appear in the random stream.
    ("opcode_cg.ECALL",
     Perturbation(re.compile(r"\+no_ecall=\S+"), "+no_ecall=0",
                  "drop +no_ecall to enable ECALL")),
    ("opcode_cg.EBREAK",
     Perturbation(re.compile(r"\+no_ebreak=\S+"), "+no_ebreak=0",
                  "drop +no_ebreak to enable EBREAK")),
    ("opcode_cg.WFI",
     Perturbation(re.compile(r"\+no_wfi=\S+"), "+no_wfi=0",
                  "drop +no_wfi to enable WFI")),
    ("opcode_cg.FENCE",
     Perturbation(re.compile(r"\+no_fence=\S+"), "+no_fence=0",
                  "drop +no_fence to enable FENCE/FENCE_I")),
    ("category_cg.SYNCH",
     Perturbation(re.compile(r"\+no_fence=\S+"), "+no_fence=0",
                  "SYNCH bin empty → drop +no_fence")),
    ("category_cg.CSR",
     Perturbation(re.compile(r"\+no_csr_instr=\S+"), "+no_csr_instr=0",
                  "CSR ops missing → drop +no_csr_instr")),
    # Branches — the test might be setting +no_branch_jump=1; drop it.
    ("category_cg.BRANCH",
     Perturbation(re.compile(r"\+no_branch_jump=\S+"), "+no_branch_jump=0",
                  "BRANCH ops missing → drop +no_branch_jump")),
    # Loads/stores — inject a directed load/store stream if byte/halfword
    # ops aren't hit.
    ("opcode_cg.LB",
     Perturbation(None, "+directed_instr_9=riscv_load_store_rand_instr_stream,6",
                  "LB missing → inject a load/store directed stream")),
    ("opcode_cg.LH",
     Perturbation(None, "+directed_instr_10=riscv_load_store_rand_instr_stream,6",
                  "LH missing → inject a load/store directed stream")),
    ("opcode_cg.SB",
     Perturbation(None, "+directed_instr_11=riscv_load_store_rand_instr_stream,6",
                  "SB missing → inject a load/store directed stream")),
    # Hazard bins — inject the hazard stream if RAW/WAR/WAW counts stall.
    ("hazard_cg.raw",
     Perturbation(None, "+directed_instr_12=riscv_load_store_hazard_instr_stream,3",
                  "raw hazard bin low → inject hazard stream")),
    ("hazard_cg.waw",
     Perturbation(None, "+directed_instr_13=riscv_load_store_hazard_instr_stream,3",
                  "waw hazard bin low → inject hazard stream")),
    # JAL/JALR — inject the jal chain.
    ("opcode_cg.JAL",
     Perturbation(None, "+directed_instr_14=riscv_jal_instr,10",
                  "JAL missing → inject JAL chain")),
    ("opcode_cg.JALR",
     Perturbation(None, "+directed_instr_16=riscv_jalr_instr,3",
                  "JALR missing → inject JALR blocks")),
    # Multi-page load/store if mem_align or load_store_width bins are poor.
    ("mem_align_cg.word_aligned",
     Perturbation(None, "+directed_instr_15=riscv_multi_page_load_store_instr_stream,3",
                  "word_aligned loads missing → inject multi-page stream")),
)


def directed_gen_opts(
    base_gen_opts: str,
    db: CoverageDB,
    goals: Goals,
    *,
    max_perturbations: int = 6,
) -> tuple[str, list[str]]:
    """Return (new_gen_opts, reasons) — perturbed form of ``base_gen_opts``.

    For every perturbation in :data:`_PERTURBATIONS` whose target bin is
    currently missing from ``db`` relative to ``goals``, apply it (up to
    ``max_perturbations``). The returned ``reasons`` list gives one line
    per applied perturbation, suitable for logging.
    """
    miss = missing_bins(db, goals)
    out = base_gen_opts
    reasons: list[str] = []
    for key, pert in _PERTURBATIONS:
        if len(reasons) >= max_perturbations:
            break
        cg, bn = key.split(".", 1)
        if bn not in miss.get(cg, {}):
            continue
        changed = False
        if pert.drop is not None and pert.drop.search(out):
            out = pert.drop.sub("", out).strip()
            changed = True
        if pert.add and pert.add not in out:
            out = (out + " " + pert.add).strip()
            changed = True
        if changed:
            reasons.append(pert.reason)
    return out, reasons
