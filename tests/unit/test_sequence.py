"""Tests for rvgen.sequence."""

from __future__ import annotations

import random

from rvgen.config import make_config
from rvgen.isa import rv32i  # noqa: F401
from rvgen.isa.enums import LABEL_STR_LEN, RiscvInstrCategory
from rvgen.isa.filtering import create_instr_list
from rvgen.sequence import InstrSequence
from rvgen.targets import get_target


def _make_sequence(*, instr_cnt: int = 50, no_branch: bool = False, seed: int = 1) -> InstrSequence:
    cfg = make_config(get_target("rv32imc"), gen_opts="+no_fence=1 +no_csr_instr=1 +no_branch_jump=0")
    avail = create_instr_list(cfg)
    seq = InstrSequence(cfg=cfg, avail=avail, label_name="main", instr_cnt=instr_cnt)
    rng = random.Random(seed)
    seq.gen_instr(rng, no_branch=no_branch)
    seq.post_process_instr(rng)
    seq.generate_instr_stream()
    return seq


def test_sequence_generates_nonempty_string_list():
    seq = _make_sequence()
    assert len(seq.instr_string_list) > 0


def test_first_line_has_main_label_in_18_char_column():
    seq = _make_sequence()
    first = seq.instr_string_list[0]
    # "main:" is 5 chars; padded to LABEL_STR_LEN=18 then instr.
    assert first.startswith("main:" + " " * (LABEL_STR_LEN - len("main:")))
    # The rest is the first instruction's assembly.
    assert first[LABEL_STR_LEN:].strip() != ""


def test_no_label_lines_have_18_space_prefix():
    seq = _make_sequence()
    for line in seq.instr_string_list[1:]:
        prefix = line[:LABEL_STR_LEN]
        # Either a label (ending with ":") or 18 spaces.
        assert prefix == " " * LABEL_STR_LEN or prefix.endswith(" ") and ":" in prefix


def test_branch_targets_assigned_forward_only():
    seq = _make_sequence(instr_cnt=200, no_branch=False, seed=3)
    # Every BRANCH must have branch_assigned=True after post_process_instr.
    for instr in seq.instr_stream.instr_list:
        if instr.category == RiscvInstrCategory.BRANCH:
            assert instr.branch_assigned
            # imm_str must reference a forward label (ends with "f").
            assert instr.imm_str.endswith("f"), f"{instr.convert2asm()!r}"


def test_labels_preserved_only_when_used():
    seq = _make_sequence(instr_cnt=200, no_branch=False, seed=5)
    # Every label that remains must be referenced by some BRANCH's imm_str.
    used_targets: set[str] = set()
    for instr in seq.instr_stream.instr_list:
        if instr.category == RiscvInstrCategory.BRANCH and instr.imm_str.endswith("f"):
            used_targets.add(instr.imm_str[:-1])
    for instr in seq.instr_stream.instr_list:
        if instr.has_label and instr.is_local_numeric_label:
            # Either this is the very first instr (label_name = "main"), or it
            # must be a referenced branch target.
            if instr is not seq.instr_stream.instr_list[0]:
                assert instr.label in used_targets, (
                    f"Unreferenced label {instr.label!r} survived"
                )


def test_sequence_format_matches_golden_shape():
    seq = _make_sequence(instr_cnt=30, no_branch=True, seed=9)
    for line in seq.instr_string_list:
        # Every line starts with either the main: label, a numeric label
        # (e.g., "3:"), or 18 spaces.
        prefix = line[:LABEL_STR_LEN]
        assert (
            prefix.startswith("main:")
            or prefix == " " * LABEL_STR_LEN
            or (":" in prefix and prefix.rstrip(" ").endswith(":"))
        ), f"Unexpected prefix: {prefix!r}"


def test_directed_instr_injected_before_label_assignment():
    """Directed streams should be inserted before labels are allocated."""
    from rvgen.isa.factory import get_instr
    from rvgen.isa.enums import RiscvInstrName
    from rvgen.stream import InstrStream

    cfg = make_config(get_target("rv32imc"), gen_opts="+no_fence=1 +no_csr_instr=1")
    avail = create_instr_list(cfg)
    seq = InstrSequence(cfg=cfg, avail=avail, label_name="main", instr_cnt=20)

    # Build a small directed stream of two atomic instrs.
    directed = InstrStream(instr_list=[
        get_instr(RiscvInstrName.ADD),
        get_instr(RiscvInstrName.SUB),
    ])
    for instr in directed.instr_list:
        instr.atomic = True
    seq.directed_instr = [directed]

    rng = random.Random(0)
    seq.gen_instr(rng)
    seq.post_process_instr(rng)
    seq.generate_instr_stream()

    # ADD + SUB must appear as contiguous atomic pair somewhere.
    names = [i.instr_name.name for i in seq.instr_stream.instr_list]
    found = False
    for i in range(len(names) - 1):
        if names[i] == "ADD" and names[i + 1] == "SUB":
            found = True
            break
    assert found
