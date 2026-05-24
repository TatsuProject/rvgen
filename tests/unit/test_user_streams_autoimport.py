"""Auto-import of user-area directed streams.

When a user drops ``user/streams/my_burst.py`` into their user area and
runs ``rvgen --gen_opts +directed_instr_1=my_burst_stream,5``, the
CLI must auto-import that module so its ``@register_stream`` side
effect fires. Without this test the path is fragile — a user could
write a perfectly valid stream and silently get zero insertions in the
generated `.S`.
"""

from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from rvgen.cli import _auto_import_user_streams
from rvgen.streams import STREAM_REGISTRY


_STREAM_TEMPLATE = textwrap.dedent("""
    \"\"\"Test stream registered for autoimport coverage.\"\"\"
    from dataclasses import dataclass
    from rvgen.streams import register_stream
    from rvgen.streams.base import DirectedInstrStream
    from rvgen.isa.factory import get_instr
    from rvgen.isa.enums import RiscvInstrName, RiscvReg


    @register_stream("{stream_name}")
    @dataclass
    class _Stream(DirectedInstrStream):
        def build(self) -> None:
            i = get_instr(RiscvInstrName.ADD)
            i.rd = i.rs1 = i.rs2 = RiscvReg.A0
            self.instr_list.append(i)
""").strip()


def test_autoimport_registers_stream_from_user_dir(tmp_path):
    streams_dir = tmp_path / "streams"
    streams_dir.mkdir()
    name = "rvgen_autoimport_smoke_stream"
    (streams_dir / "smoke.py").write_text(
        _STREAM_TEMPLATE.format(stream_name=name)
    )
    # Sanity — the stream isn't registered yet.
    assert name not in STREAM_REGISTRY

    _auto_import_user_streams(tmp_path)

    assert name in STREAM_REGISTRY


def test_autoimport_skips_underscore_files(tmp_path):
    streams_dir = tmp_path / "streams"
    streams_dir.mkdir()
    skipped = "rvgen_autoimport_skipped_stream"
    (streams_dir / "_private.py").write_text(
        _STREAM_TEMPLATE.format(stream_name=skipped)
    )
    _auto_import_user_streams(tmp_path)
    assert skipped not in STREAM_REGISTRY


def test_autoimport_tolerates_broken_module(tmp_path, caplog):
    import logging
    streams_dir = tmp_path / "streams"
    streams_dir.mkdir()
    (streams_dir / "broken.py").write_text(
        "import nonexistent_package_xyzzy\n"
    )
    # Mustn't raise — the CLI should keep running.
    with caplog.at_level(logging.WARNING, logger="rvgen.cli"):
        _auto_import_user_streams(tmp_path)
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "Failed to auto-import" in msgs


def test_autoimport_noop_for_missing_dir(tmp_path):
    # streams/ subdir doesn't exist — must not raise.
    _auto_import_user_streams(tmp_path)


def test_autoimport_noop_for_none_user_dir():
    _auto_import_user_streams(None)


# ---------------------------------------------------------------------------
# Target validator
# ---------------------------------------------------------------------------


def test_validate_target_good_yaml(tmp_path, capsys):
    from rvgen.cli import _validate_target_yaml
    yaml_path = tmp_path / "good.yaml"
    yaml_path.write_text(textwrap.dedent("""
        name: my_core
        xlen: 32
        supported_isa: [RV32I, RV32M, RV32C]
        data_section_size_bytes: 8KiB
        text_section_size_bytes: 16KiB
    """).strip())
    rc = _validate_target_yaml(yaml_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out
    assert "8,192 bytes" in out
    assert "16,384 bytes" in out


def test_validate_target_unknown_key(tmp_path, capsys):
    from rvgen.cli import _validate_target_yaml
    yaml_path = tmp_path / "typo.yaml"
    yaml_path.write_text(textwrap.dedent("""
        name: t
        xlen: 32
        supported_isa: [RV32I]
        not_a_real_key: 42
    """).strip())
    rc = _validate_target_yaml(yaml_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "not_a_real_key" in out


def test_validate_target_bad_enum(tmp_path, capsys):
    from rvgen.cli import _validate_target_yaml
    yaml_path = tmp_path / "bad_enum.yaml"
    yaml_path.write_text(textwrap.dedent("""
        name: t
        xlen: 32
        supported_isa: [RV32X_BOGUS]
    """).strip())
    rc = _validate_target_yaml(yaml_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "RV32X_BOGUS" in out
    assert "RiscvInstrGroup" in out


def test_validate_target_bad_size_string(tmp_path, capsys):
    from rvgen.cli import _validate_target_yaml
    yaml_path = tmp_path / "bad_size.yaml"
    yaml_path.write_text(textwrap.dedent("""
        name: t
        xlen: 32
        supported_isa: [RV32I]
        text_section_size_bytes: "totally_invalid"
    """).strip())
    rc = _validate_target_yaml(yaml_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "text_section_size_bytes" in out


def test_validate_target_missing_required(tmp_path, capsys):
    from rvgen.cli import _validate_target_yaml
    yaml_path = tmp_path / "missing.yaml"
    yaml_path.write_text("xlen: 32\n")
    rc = _validate_target_yaml(yaml_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "missing required field" in out


def test_validate_target_nonexistent_file(tmp_path, capsys):
    from rvgen.cli import _validate_target_yaml
    rc = _validate_target_yaml(tmp_path / "nope.yaml")
    err = capsys.readouterr().err
    assert rc == 1
    assert "does not exist" in err


# ---------------------------------------------------------------------------
# --help_streams flag
# ---------------------------------------------------------------------------


def test_help_streams_lists_builtins(capsys):
    from rvgen.cli import main
    rc = main(["--help_streams"])
    out = capsys.readouterr().out
    assert rc == 0
    # Every built-in stream from the canonical registry should appear.
    assert "riscv_loop_instr" in out
    assert "riscv_rand" in out or "riscv_jal_instr" in out
    # Format header — must mention the plusarg name it's used in.
    assert "directed_instr_N" in out


def test_help_streams_shows_user_streams(tmp_path, capsys, monkeypatch):
    """A registered user stream under rvgen_user_streams.* appears in a
    separate ``User-area streams:`` section."""
    from rvgen.cli import _auto_import_user_streams, main
    streams_dir = tmp_path / "streams"
    streams_dir.mkdir()
    name = "rvgen_help_streams_test_stream"
    (streams_dir / "user_demo.py").write_text(
        _STREAM_TEMPLATE.format(stream_name=name)
    )
    _auto_import_user_streams(tmp_path)

    rc = main(["--help_streams"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "User-area streams" in out
    assert name in out


# ---------------------------------------------------------------------------
# --help_tests flag — completes the discoverability trio with --help_targets
# / --help_streams. Closes the #1 onboarding question: "what tests can I
# actually run on this target?"
# ---------------------------------------------------------------------------


def test_help_tests_lists_tests_for_target(capsys):
    from rvgen.cli import main
    rc = main(["--target", "rv32imc", "--help_tests"])
    out = capsys.readouterr().out
    assert rc == 0
    # Header lines.
    assert "Testlist:" in out
    assert "Target:" in out and "rv32imc" in out
    # Column header + at least one canonical riscv-dv test entry.
    assert "TEST" in out and "ITER" in out and "DESCRIPTION" in out
    assert "riscv_rand_instr_test" in out
    # The footer counts the tests and shows a runnable command.
    assert "tests available" in out
    assert "rvgen --target rv32imc --test" in out


def test_help_tests_dedups_imported_entries(capsys):
    """Per-target testlists `import` base_testlist.yaml; the same test name
    can appear twice. The list output must de-duplicate."""
    from rvgen.cli import main
    rc = main(["--target", "rv32imc", "--help_tests"])
    out = capsys.readouterr().out
    assert rc == 0
    # riscv_rand_instr_test exists in both per-target and base lists;
    # output must show it exactly once.
    assert out.count("  riscv_rand_instr_test ") == 1
