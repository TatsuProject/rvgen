"""Shared helper for picking a random RVV op inside a directed stream.

Three streams (vsetvli_stress, vector_hazard, vstart_corner) all need to
emit "a random vector arithmetic op at the current vtype". The picking
+ randomization boilerplate was identical across them; this module is
the single source of truth.
"""

from __future__ import annotations

from typing import Iterable

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrGroup,
    RiscvInstrName,
)
from rvgen.isa.filtering import (
    AvailableInstrs,
    get_rand_instr,
    randomize_gpr_operands,
)


_DEFAULT_DATA_CATS = (
    RiscvInstrCategory.ARITHMETIC,
    RiscvInstrCategory.LOGICAL,
    RiscvInstrCategory.SHIFT,
    RiscvInstrCategory.COMPARE,
)

_VSET_NAMES = (RiscvInstrName.VSETVLI, RiscvInstrName.VSETVL)


def pick_random_vector_op(
    rng,
    avail: AvailableInstrs,
    cfg,
    vector_cfg,
    *,
    allowed_categories: Iterable = _DEFAULT_DATA_CATS,
    extra_excludes: Iterable[RiscvInstrName] = (),
    max_retries: int = 8,
) -> Instr | None:
    """Pick a random RVV op + run the standard randomization pass.

    ``get_rand_instr`` combines ``include_category`` and ``include_group``
    via UNION (matches SV semantics), so we filter by group only and
    post-filter by category — retrying on miss.

    Returns the randomized Instr or None on failure / no candidates.
    """
    wanted_cats = tuple(allowed_categories)
    exclude = list(_VSET_NAMES) + list(extra_excludes)
    for _ in range(max_retries):
        try:
            cand = get_rand_instr(
                rng, avail,
                include_group=[RiscvInstrGroup.RVV],
                exclude_instr=exclude,
            )
        except Exception:  # noqa: BLE001
            return None
        if cand.category not in wanted_cats:
            continue
        randomize_gpr_operands(cand, rng, cfg)
        fp_rand = getattr(cand, "randomize_fpr_operands", None)
        if fp_rand is not None:
            fp_rand(rng)
        vec_rand = getattr(cand, "randomize_vector_operands", None)
        if vec_rand is not None:
            vec_rand(rng, vector_cfg)
        if cand.has_imm:
            cand.randomize_imm(rng, xlen=cfg.target.xlen)
        cand.post_randomize()
        cand.atomic = True
        return cand
    return None
