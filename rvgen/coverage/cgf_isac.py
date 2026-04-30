"""riscv-isac CGF (Coverage Goals Format) round-trip.

The upstream `riscv-isac <https://riscv-isac.readthedocs.io/>`_ tool
uses a YAML format that's structurally similar to ours but with
different naming conventions and more value-combination expressivity.
Round-trip lets users:

* **Import**: take a riscv-isac CGF (e.g. an arch-test goal set) and
  drop it into rvgen — bins land in our covergroups so we can
  measure against them with the existing collector pipeline.
* **Export**: take our goals YAML and emit an isac-format CGF that
  the broader RISC-V verification ecosystem can consume.

Mapping (rvgen ↔ riscv-isac, shown by isac field name):

==================  ========================  =========================
riscv-isac field    rvgen covergroup          Notes
==================  ========================  =========================
mnemonics            opcode_cg                 Lowercase mnemonics → uppercase enum
rs1                  rs1_cg                    x0..x31 → ZERO..T6 (ABI names)
rs2                  rs2_cg                    Same
rd                   rd_cg                     Same
op_comb              rs1_eq_rs2_cg, etc.       Best-effort string match
val_comb             rs1_val_class_cg + ...    Python expressions → coarse bins
csr_comb             csr_cg                    CSR addresses → CSR names
cross_comb           ``a__b`` style bins       Arrow notation flattened
==================  ========================  =========================

Things we deliberately don't try to round-trip:

* riscv-isac's full Python expression evaluator (we don't run trace-time
  expressions; covergroups sample at gen time). Imported val_comb
  expressions land as opaque optional bins (count=0).
* riscv-isac's per-test "config" filters. Our goals YAML doesn't carry
  ISA-string filters; instead users layer per-target goals files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from rvgen.coverage.cgf import Goals


# ABI name <-> x-number mapping for register-bin translation.
_ABI_TO_XNUM: dict[str, str] = {
    "zero": "ZERO", "ra": "RA", "sp": "SP", "gp": "GP", "tp": "TP",
    "t0": "T0", "t1": "T1", "t2": "T2",
    "s0": "S0", "fp": "S0", "s1": "S1",
    "a0": "A0", "a1": "A1", "a2": "A2", "a3": "A3",
    "a4": "A4", "a5": "A5", "a6": "A6", "a7": "A7",
    "s2": "S2", "s3": "S3", "s4": "S4", "s5": "S5",
    "s6": "S6", "s7": "S7", "s8": "S8", "s9": "S9",
    "s10": "S10", "s11": "S11",
    "t3": "T3", "t4": "T4", "t5": "T5", "t6": "T6",
}
_XNUM_NAMES = (
    "ZERO", "RA", "SP", "GP", "TP", "T0", "T1", "T2",
    "S0", "S1", "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7",
    "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11",
    "T3", "T4", "T5", "T6",
)


def _reg_to_rvgen(reg_label: str) -> str | None:
    """Translate a riscv-isac register label to our enum-name form.

    Accepts ``x5`` / ``a0`` / ``ra`` / ``zero`` / ``ZERO`` etc. and
    returns the rvgen enum name (uppercase ABI). Returns ``None`` if
    the label can't be parsed.
    """
    s = reg_label.strip().lower()
    if s.startswith("x") and s[1:].isdigit():
        idx = int(s[1:])
        if 0 <= idx < 32:
            return _XNUM_NAMES[idx]
        return None
    if s in _ABI_TO_XNUM:
        return _ABI_TO_XNUM[s]
    if reg_label in _XNUM_NAMES:
        return reg_label
    return None


def _opcode_to_rvgen(mnem: str) -> str:
    """Translate a riscv-isac mnemonic to our enum name (uppercase)."""
    # riscv-isac uses lowercase with dots (e.g. fadd.s); ours uses
    # uppercase with underscores (FADD_S).
    return mnem.upper().replace(".", "_")


def _opcode_from_rvgen(name: str) -> str:
    """Inverse of _opcode_to_rvgen — rvgen → riscv-isac (lowercase, dots)."""
    return name.lower().replace("_", ".")


def import_cgf(path: Path | str) -> Goals:
    """Load a riscv-isac CGF YAML and translate it to rvgen Goals.

    Per riscv-isac convention each top-level key is a *coverage group
    name* (typically an instruction name like ``add`` or ``fadd.s``).
    Underneath, the recognised field names are: ``mnemonics``,
    ``rs1``, ``rs2``, ``rd``, ``op_comb``, ``val_comb``, ``csr_comb``,
    ``cross_comb``, ``config``. We project each into the matching
    rvgen covergroup.

    Returns a :class:`Goals` whose ``data`` dict-of-dict is populated
    with translated bins. Caller can use the result with the existing
    coverage pipeline (lint-goals, scorecard, etc.).
    """
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"riscv-isac CGF must be a top-level mapping; got {type(raw)}")

    out: dict[str, dict[str, int]] = {}

    def _add(cg: str, bn: str, count: int) -> None:
        out.setdefault(cg, {})[bn] = max(out.get(cg, {}).get(bn, 0), count)

    for entry_name, body in raw.items():
        if not isinstance(body, dict):
            continue

        # mnemonics: {add: 0, sub: 0} → opcode_cg
        for mnem, count in (body.get("mnemonics") or {}).items():
            _add("opcode_cg", _opcode_to_rvgen(mnem), max(int(count), 1))

        # rs1 / rs2 / rd → rs1_cg / rs2_cg / rd_cg
        for field, cg in (("rs1", "rs1_cg"), ("rs2", "rs2_cg"), ("rd", "rd_cg")):
            for reg_label, count in (body.get(field) or {}).items():
                rv = _reg_to_rvgen(str(reg_label))
                if rv is not None:
                    _add(cg, rv, max(int(count), 1))

        # op_comb → rs1_eq_rs2_cg / rs1_eq_rd_cg (best-effort string match).
        # riscv-isac CGFs use op_comb as a dict where the *value* is the
        # Python-style expression and the *key* is a human label (e.g.
        # ``unique_rs1_rs2: 'rs1 != rs2'``). Match against both so
        # whichever convention the source used we still detect the intent.
        op_comb = body.get("op_comb") or {}
        for label, expr in (op_comb.items() if isinstance(op_comb, dict)
                            else [(e, e) for e in op_comb]):
            # Strip whitespace + underscores so "unique_rs1_rs2" and
            # "rs1 != rs2" both flatten to a comparable form.
            haystack = (str(label) + " " + str(expr)).lower()
            haystack = haystack.replace(" ", "").replace("_", "")
            if "rs1==rs2" in haystack or "rs1eqrs2" in haystack:
                _add("rs1_eq_rs2_cg", "equal", 1)
            if "rs1!=rs2" in haystack or "uniquers1rs2" in haystack \
                    or "rs1diffrs2" in haystack:
                _add("rs1_eq_rs2_cg", "distinct", 1)
            if "rs1==rd" in haystack or "rd==rs1" in haystack \
                    or "rs1eqrd" in haystack:
                _add("rs1_eq_rd_cg", "equal", 1)
            if "rs1!=rd" in haystack or "uniquers1rd" in haystack:
                _add("rs1_eq_rd_cg", "distinct", 1)

        # val_comb → rs1_val_class / rs2_val_class (coarse mapping;
        # arbitrary Python expressions land as optional opaque bins).
        for expr, count in (body.get("val_comb") or {}).items():
            # Heuristic: expressions containing "==0" → zero corner;
            # "==-1" / "==(-1)" → all-ones; otherwise opaque.
            s = str(expr).lower().replace(" ", "")
            target_count = max(int(count), 1)
            if "rs1==0" in s:
                _add("rs1_val_class_cg", "zero", target_count)
            elif "rs2==0" in s:
                _add("rs2_val_class_cg", "zero", target_count)
            else:
                # Land as optional bin so users see the imported
                # expression in the report without it blocking goals.
                _add("rs1_val_class_cg", str(expr).strip()[:64], 0)

        # csr_comb → csr_cg
        for csr_label, count in (body.get("csr_comb") or {}).items():
            _add("csr_cg", str(csr_label).upper(), max(int(count), 1))

        # cross_comb → arrow-notation flattened (a -> b becomes "A__B")
        for expr in (body.get("cross_comb") or {}):
            s = str(expr).replace(" ", "")
            if "->" in s:
                parts = s.split("->")
                if len(parts) == 2:
                    a, b = parts
                    _add("category_transition_cg",
                         f"{a.upper()}__{b.upper()}", 1)

    return Goals(data=out)


def export_cgf(goals: Goals, path: Path | str) -> Path:
    """Write a riscv-isac CGF YAML from rvgen :class:`Goals`.

    Inverse mapping of :func:`import_cgf`. The output isn't pixel-
    perfect to riscv-isac's hand-curated CGFs (we don't have the
    rich expression strings), but it's structurally compatible and
    can be consumed by isac's coverage pipeline.

    Per-mnemonic entries collect register / op_comb fields that
    apply specifically to that mnemonic; covergroup-wide bins
    (``rs1_eq_rs2_cg``) lift to a synthetic ``__global__`` entry so
    they don't get lost.
    """
    cgf: dict[str, Any] = {}

    # Per-mnemonic entries — one per opcode_cg bin.
    for op_name, count in (goals.data.get("opcode_cg") or {}).items():
        if op_name.endswith("__dyn"):
            continue   # runtime suffix, skip
        entry: dict[str, Any] = {"mnemonics": {_opcode_from_rvgen(op_name): count}}
        cgf[_opcode_from_rvgen(op_name)] = entry

    # Global entry holds register-only / op_comb / csr bins that aren't
    # mnemonic-specific in our schema. Lift them onto a `__global__`
    # CGF entry so isac picks them up.
    global_entry: dict[str, Any] = {}

    for cg, isac_field in (("rs1_cg", "rs1"),
                            ("rs2_cg", "rs2"),
                            ("rd_cg", "rd")):
        bins = goals.data.get(cg) or {}
        if bins:
            global_entry[isac_field] = {bn.lower(): cnt for bn, cnt in bins.items()}

    op_comb: dict[str, int] = {}
    if "equal" in (goals.data.get("rs1_eq_rs2_cg") or {}):
        op_comb["rs1 == rs2"] = goals.data["rs1_eq_rs2_cg"]["equal"]
    if "distinct" in (goals.data.get("rs1_eq_rs2_cg") or {}):
        op_comb["rs1 != rs2"] = goals.data["rs1_eq_rs2_cg"]["distinct"]
    if "equal" in (goals.data.get("rs1_eq_rd_cg") or {}):
        op_comb["rs1 == rd"] = goals.data["rs1_eq_rd_cg"]["equal"]
    if op_comb:
        global_entry["op_comb"] = op_comb

    csr_bins = goals.data.get("csr_cg") or {}
    if csr_bins:
        global_entry["csr_comb"] = {bn.upper(): cnt for bn, cnt in csr_bins.items()}

    if global_entry:
        cgf["__global__"] = global_entry

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cgf, sort_keys=True))
    return p
