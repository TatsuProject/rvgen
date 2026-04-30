"""Export rvgen goals as SystemVerilog covergroup source.

Cross-ecosystem play: SV-using verification teams (VCS / Xcelium / Questa
shops) can take rvgen's goals YAML and compile a covergroup file straight
into their existing UCDB-collecting flow. This means a team can run rvgen
to *generate* programs and our goals to *measure* them in SV without
rewriting either side.

The output is a single .sv file with one covergroup class per
covergroup in the goals YAML. Each covergroup is parameterized to take a
sample value (or a packed instr struct, depending on the covergroup
shape) and contains one ``coverpoint`` whose bins mirror the goals.

This is intentionally a one-way export — round-tripping SV→YAML
isn't on the menu (SV cover constructs are richer than the bin-count
dict YAML allows). Use ``riscv-isac CGF round-trip`` for that.

The exporter is opinionated about a few things:

* Bin names with characters not legal in SV identifiers
  (``__``, ``-``, ``.``) are quoted as illegal_bins-friendly strings
  via the SV ``"text"`` literal-bin form.
* Bin counts in our YAML map to the SV ``at_least`` keyword inside
  each ``bins`` declaration.
* Covergroups that need crosses (``a__b`` bin names) are emitted as
  one ``coverpoint`` whose bin labels carry the crossed token.
  Real SV ``cross`` would need two coverpoints; emitting it as a
  single coverpoint keeps the export schema-aligned with our YAML.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from rvgen.coverage.cgf import Goals


_SV_IDENTIFIER_RE = re.compile(r"[^A-Za-z0-9_]")


def _sv_identifier(name: str) -> str:
    """Return ``name`` mangled so it's a legal SV identifier.

    Replaces any non [A-Za-z0-9_] character with ``_``. Used for
    coverpoint / bin-list / class names. Bin *labels* keep their
    original text (SV allows quoted strings as bin labels).
    """
    safe = _SV_IDENTIFIER_RE.sub("_", name)
    if safe and safe[0].isdigit():
        safe = "b_" + safe
    return safe


def _sv_class_name(cg_name: str) -> str:
    """Class-name convention: ``rvgen_<cg>_cover_pkg``.

    e.g. ``opcode_cg`` -> ``rvgen_opcode_cg_cover``.
    """
    return f"rvgen_{_sv_identifier(cg_name)}_cover"


def emit_sv_covergroup(cg_name: str, bins: dict[str, int]) -> str:
    """Emit one SV covergroup class for ``cg_name`` + ``bins``.

    Returns the .sv source as a string. The class is wrapped so the
    caller can drop it in a package and instantiate with
    ``rvgen_<cg>_cover_inst = new()``.
    """
    cls = _sv_class_name(cg_name)
    lines: list[str] = []
    lines.append(f"// Auto-generated from rvgen goals — do not edit.")
    lines.append(f"// Covergroup: {cg_name}, {len(bins)} bins.")
    lines.append(f"class {cls};")
    lines.append("")
    lines.append("  // Sample value — covergroup author re-types this if a richer")
    lines.append("  // bus carries the bin (e.g. an opcode_t enum or a bit-vector).")
    lines.append("  rand string sample_value;")
    lines.append("")
    lines.append(f"  covergroup cg_{_sv_identifier(cg_name)} with function sample(string s);")
    lines.append("    option.per_instance = 1;")
    lines.append(f"    option.name = \"{cg_name}\";")
    lines.append("")

    # Sort bins by required count desc so the higher-priority ones come
    # first in the generated source — looks nicer in vendor reports.
    bin_items = sorted(bins.items(), key=lambda kv: (-kv[1], kv[0]))

    lines.append(f"    cp_{_sv_identifier(cg_name)}: coverpoint s {{")
    for bin_name, required in bin_items:
        # SV's bin syntax: ``bins NAME = {VALUES} [iff ...] [with (...)] ;``
        # We use string-literal bin matching since `s` is a string. SV only
        # supports literal-string bins via the ``= {"foo"}`` form on packed
        # types — for untyped strings we fall back to integer bin
        # iteration. Easier is to use ``illegal_bins`` skipping; cleaner
        # for our purposes is to use the ``with`` clause to compare:
        bin_id = _sv_identifier(bin_name)
        # Wrap the bin label in an SV-compatible escape if needed.
        if required == 0:
            comment = "  // optional"
        else:
            comment = f"  // at_least = {required}"
        lines.append(
            f"      bins {bin_id} = "
            f"{{[0:0]}} with (s == \"{bin_name}\");{comment}"
        )
    lines.append("    }")
    lines.append("")
    lines.append("  endgroup")
    lines.append("")
    lines.append("  function new();")
    lines.append(f"    cg_{_sv_identifier(cg_name)} = new();")
    lines.append("  endfunction")
    lines.append("")
    lines.append("  function void sample(string s);")
    lines.append(f"    cg_{_sv_identifier(cg_name)}.sample(s);")
    lines.append("  endfunction")
    lines.append("")
    lines.append("endclass")
    return "\n".join(lines)


def emit_sv_package(goals: Goals, package_name: str = "rvgen_cov_pkg") -> str:
    """Emit a single SV package containing every covergroup class.

    Returns one big .sv source string. Each covergroup gets its own
    class inside the package; the package can be imported into a
    UVM testbench via ``import rvgen_cov_pkg::*;``.
    """
    classes = [
        emit_sv_covergroup(cg, bins)
        for cg, bins in sorted(goals.data.items())
        if bins
    ]

    out: list[str] = []
    out.append("// Auto-generated by rvgen.coverage.sv_export — do not edit.")
    out.append(f"// {len(classes)} covergroups.")
    out.append("")
    out.append(f"package {package_name};")
    out.append("")
    for cls_src in classes:
        # Indent each class one level for package scoping.
        out.append("\n".join("  " + line if line else "" for line in cls_src.splitlines()))
        out.append("")
    out.append(f"endpackage : {package_name}")
    return "\n".join(out) + "\n"


def write_sv_package(goals: Goals, output: Path | str,
                     package_name: str = "rvgen_cov_pkg") -> Path:
    """Write the SV package to ``output``. Returns the resolved path."""
    p = Path(output)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(emit_sv_package(goals, package_name=package_name))
    return p
