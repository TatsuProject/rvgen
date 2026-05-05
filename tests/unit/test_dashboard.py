"""Tests for the interactive coverage dashboard."""

from __future__ import annotations

import pytest

from rvgen.coverage.cgf import Goals
from rvgen.coverage.dashboard import (
    _scorecard_svg,
    _timeline_svg,
    dashboard_html,
    write_dashboard,
)


# ---------- scorecard SVG ----------


def test_scorecard_svg_renders_per_row():
    sc = [
        {"subsystem": "RV32I+RV64I", "met": 30, "required": 36,
         "missing": 6, "extra": 100, "percent": 83.3},
        {"subsystem": "Vector", "met": 5, "required": 20,
         "missing": 15, "extra": 0, "percent": 25.0},
    ]
    svg = _scorecard_svg(sc)
    assert "<svg" in svg
    assert "RV32I+RV64I" in svg
    assert "Vector" in svg
    # Should mention met/req counts.
    assert "30/36" in svg or "30 / 36" in svg.replace(" ", " ")
    assert "5/20" in svg


def test_scorecard_svg_skips_zero_required_subsystems():
    sc = [
        {"subsystem": "Has goals", "met": 1, "required": 1,
         "missing": 0, "extra": 0, "percent": 100.0},
        {"subsystem": "No goals", "met": 0, "required": 0,
         "missing": 0, "extra": 5, "percent": 0.0},
    ]
    svg = _scorecard_svg(sc)
    assert "Has goals" in svg
    assert "No goals" not in svg


def test_scorecard_svg_returns_message_when_empty():
    out = _scorecard_svg([])
    # Returns an empty-state div / paragraph, not an SVG.
    assert "empty" in out
    assert "<svg" not in out


def test_scorecard_svg_color_codes_by_percent():
    sc = [
        {"subsystem": "Good", "met": 9, "required": 10, "missing": 1, "extra": 0, "percent": 90.0},
        {"subsystem": "Mid",  "met": 5, "required": 10, "missing": 5, "extra": 0, "percent": 50.0},
        {"subsystem": "Bad",  "met": 1, "required": 10, "missing": 9, "extra": 0, "percent": 10.0},
    ]
    svg = _scorecard_svg(sc)
    # New layout uses row-fg with status modifier classes.
    assert "row-fg good" in svg
    assert "row-fg warn" in svg
    assert "row-fg bad" in svg


# ---------- timeline SVG ----------


def test_timeline_svg_renders_polyline_and_points():
    tl = [
        {"seed": 100, "new_bins": 50},
        {"seed": 101, "new_bins": 30},
        {"seed": 102, "new_bins": 10},
    ]
    svg = _timeline_svg(tl)
    assert "<svg" in svg
    assert "<polyline" in svg
    # 3 points → 3 circles.
    assert svg.count("<circle") == 3
    # Tooltip text on hover.
    assert "seed 100: 50 new bins" in svg


def test_timeline_svg_handles_single_point():
    tl = [{"seed": 1, "new_bins": 5}]
    svg = _timeline_svg(tl)
    assert "<svg" in svg
    assert svg.count("<circle") == 1


def test_timeline_svg_returns_message_when_empty():
    out = _timeline_svg([])
    assert "empty" in out
    assert "<svg" not in out


# ---------- dashboard_html ----------


def test_dashboard_html_contains_all_sections():
    db = {"opcode_cg": {"ADD": 5, "SUB": 3}, "rs1_cg": {"A0": 1}}
    goals = Goals(data={
        "opcode_cg": {"ADD": 1, "MUL": 1},   # MUL missing
        "rs1_cg":    {"A0": 1, "A1": 1},     # A1 missing
    })
    html = dashboard_html(db, goals=goals)
    # Summary tiles.
    assert "Summary" in html
    assert "Covergroups" in html
    assert "Goals met" in html
    # Per-covergroup table section.
    assert "Covergroups" in html and "filter" in html.lower()
    # Top-missing-bins.
    assert "Top missing" in html
    assert "MUL" in html
    assert "A1" in html


def test_dashboard_html_no_goals_omits_goals_metrics():
    db = {"opcode_cg": {"ADD": 5}}
    html = dashboard_html(db)
    assert "Goals met" not in html
    # Summary still rendered.
    assert "Summary" in html


def test_dashboard_html_with_timeline():
    db = {"opcode_cg": {"ADD": 5}}
    timeline = [
        {"seed": 100, "new_bins": 30},
        {"seed": 101, "new_bins": 10},
    ]
    html = dashboard_html(db, timeline=timeline)
    assert "Convergence timeline" in html
    assert "<polyline" in html


def test_dashboard_html_with_scorecard():
    db = {"opcode_cg": {"ADD": 5}}
    scorecard = [
        {"subsystem": "RV32I+RV64I", "met": 1, "required": 1,
         "missing": 0, "extra": 0, "percent": 100.0},
    ]
    html = dashboard_html(db, scorecard=scorecard)
    # Card header is rendered with the new tabbed layout.
    assert "Per-subsystem closure" in html or "subsystem" in html.lower()
    assert "RV32I+RV64I" in html


def test_dashboard_html_filterable_cg_list():
    db = {f"cg_{i}": {f"BIN_{j}": 1 for j in range(3)} for i in range(5)}
    html = dashboard_html(db)
    # 5 covergroups → 5 details elements.
    assert html.count("<details") == 5
    # Every cg should carry a data-name attribute for filtering.
    for i in range(5):
        assert f'data-name="cg_{i}"' in html
    # Filter input is present (search box drives JS filtering).
    assert 'id="cg-search"' in html


def test_dashboard_html_per_cg_badge_changes_with_status():
    db = {
        "cg_met":     {"x": 1},
        "cg_partial": {"x": 1},
        "cg_missed":  {},
    }
    goals = Goals(data={
        "cg_met":     {"x": 1},
        "cg_partial": {"x": 1, "y": 1},
        "cg_missed":  {"x": 1},
    })
    html = dashboard_html(db, goals=goals)
    assert "MET 1/1" in html
    assert "PART 1/2" in html
    assert "MISS 0/1" in html


def test_write_dashboard_creates_file(tmp_path):
    db = {"opcode_cg": {"ADD": 1}}
    out = tmp_path / "subdir" / "cov.html"
    written = write_dashboard(db, out)
    assert written == out
    assert out.exists()
    text = out.read_text()
    assert "<!DOCTYPE html>" in text
