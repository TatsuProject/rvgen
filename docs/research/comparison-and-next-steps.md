# Comparison with top RISC-V instruction generators and coverage tools — and what to build next

> Research brief prepared before the v0.1.0 open-source release. Compares
> rvgen against the five open-source / industry references
> a verification team is likely to evaluate us against, identifies our
> genuine strengths and gaps, and ranks the next wave of work by impact.

## 1 — The landscape

The RISC-V verification-tool ecosystem splits into **generators**
(produce `.S` files), **coverage tools** (consume traces / emit
covergroups), and **full verification platforms** (both, plus
reference model + testbench integration).

| Tool | Category | Primary language | Status | Licence |
|---|---|---|---|---|
| [riscv-dv](https://github.com/chipsalliance/riscv-dv) | Generator | SystemVerilog + UVM + Python glue | Mature, v1.x | Apache-2.0 |
| [force-riscv](https://github.com/openhwgroup/force-riscv) | Generator | C++ + Python templates | Active | Apache-2.0 |
| [ImperasDV](https://www.synopsys.com/verification/imperasdv.html) | Full platform | Proprietary | Commercial | Closed |
| [riscvISACOV](https://github.com/riscv-verification/riscvISACOV) | Coverage | SystemVerilog | RV32I initial | Apache-2.0 |
| [riscv-isac](https://github.com/riscv-verification/riscv-isac) | Coverage (post-sim) | Python | Mature | BSD-3 |
| **rvgen** | **Generator + Coverage** | **Python** | **v0.1.0** | **Apache-2.0** |

> Sources: [riscv-dv GitHub](https://github.com/chipsalliance/riscv-dv),
> [force-riscv GitHub](https://github.com/openhwgroup/force-riscv),
> [ImperasDV product page](https://www.synopsys.com/verification/imperasdv.html),
> [riscvISACOV](https://github.com/riscv-verification/riscvISACOV),
> [riscv-isac docs](https://riscv-isac.readthedocs.io/).

## 2 — Ease of install / use

| | rvgen | riscv-dv | force-riscv | ImperasDV |
|---|---|---|---|---|
| Install | `pip install -e .` | Commercial/OSS SV simulator + UVM | C++ build + SCons + Python | Licence + Synopsys tool install |
| Time to first `.S` | **~30 sec** (pip + spike) | 10-30 min (SV flow setup) | 30-60 min (build) | Days (licencing) |
| Runtime deps | PyYAML + spike | SV simulator | Imperas riscvOVPsim for coverage | Full Synopsys stack |
| Works on a laptop | **✓** | marginal (needs SV sim) | ✓ | ✗ |
| Works offline / no licence | **✓** | depends on SV sim | ✓ | ✗ |

**Our position:** strongest on ease. The SV-based tools carry an
unavoidable "install an EDA tool first" cost even before you can
generate a `.S`.

## 3 — Test authoring

| | rvgen | riscv-dv | force-riscv | ImperasDV |
|---|---|---|---|---|
| Primary authoring surface | **YAML gen_opts** | YAML gen_opts (+ optional SV subclass) | Python test-template API | SV / C testbench |
| New test without code | **✓ (YAML entry)** | ✓ (YAML entry) | ✗ (Python required) | ✗ |
| New behavior | Python stream class (~20 LOC) | SV `riscv_instr_stream` subclass (~50+ LOC SV) | Python API (~40 LOC) | SV/C (~100+ LOC) |
| Custom instructions / user extension | `define_instr` call | `user_extension/` | built-in custom-op hooks | framework |
| Backward-compat with riscv-dv testlists | **✓ (same YAML schema)** | ✓ (native) | ✗ (different format) | ✗ |

**Our position:** tied with riscv-dv on trivial changes (YAML), simpler
than riscv-dv on non-trivial changes (Python vs SV), less expressive
than force-riscv for highly-constrained test sequences. Good middle
ground.

## 4 — Extension support

The single most important dimension for verification-tool selection.
Marked **bold** where we're uniquely strong; **`✗`** where we have a
genuine gap.

| Extension | us | riscv-dv | force-riscv | Imperas |
|---|---|---|---|---|
| RV32/RV64 I/M/A/F/D/C | ✓ | ✓ | ✓ | ✓ |
| RV32FC / RV32DC (compressed FP) | ✓ | ✓ | ✓ | ✓ |
| Zba / Zbb / Zbc / Zbs (ratified B) | ✓ | ✓ | ✓ | ✓ |
| Draft RV32B | ✓ | partial | unclear | unclear |
| Zbkb / Zbkc / Zbkx (crypto B) | ✓ | unclear | unclear | ✓ |
| Zknd / Zkne / Zknh (AES + SHA) | ✓ | unclear | unclear | ✓ |
| **Zksh / Zksed (SM3 / SM4)** | **✓** | **✗** | **✗** | unclear |
| RVV 1.0 (vector) | ✓ | ✓ | ✓ | ✓ (5000+ tests) |
| **Zve32x / Zve32f / Zve64x / Zve64f / Zve64d** | **✓ (5 targets)** | **✗** | **✗** | unclear |
| **Coral NPU (rv32imf_zve32x_zbb)** | **✓** | **✗** | **✗** | unclear |
| Privileged M-mode | ✓ | ✓ | ✓ | ✓ |
| Privileged S / U mode (boot only) | partial | ✓ | ✓ | ✓ |
| **Full paging (Sv32/39/48)** | `✗` | ✓ | ✓ | ✓ |
| **PMP + NAPOT** | `✗` | open issue | ✓ | ✓ |
| **Debug mode (DCSR/DPC/DSCRATCH)** | `✗` | ✓ | unclear | ✓ |
| Multi-hart | partial (2-hart target, no ld/st races) | ✓ | ✓ | ✓ |
| Zicbom/Zicbop/Zicboz (cache mgmt) | `✗` | `✗` | unclear | ✓ |
| Zicond (integer conditional) | `✗` | `✗` | unclear | ✓ |
| Zimop (may-be-ops) | `✗` | `✗` | unclear | ✓ |
| Zihintpause | `✗` | `✗` | unclear | unclear |
| Zfh (half-precision FP) | `✗` | `✗` | unclear | ✓ |
| Zvfh (vector half-precision FP) | `✗` | `✗` | unclear | ✓ |
| H-extension (hypervisor) | `✗` | `✗` | unclear | ✓ |
| Smaia / Ssaia (advanced interrupt) | `✗` | `✗` | unclear | ✓ |
| Svnapot / Svpbmt (advanced paging) | `✗` | `✗` | unclear | ✓ |

### Summary of ISA gaps

**Unique strengths vs open-source competition:**
- Only Zksh/Zksed (SM3/SM4) support.
- Only Zve* embedded-vector profiles.
- Only Coral NPU target out-of-the-box.

**Parity with riscv-dv:**
- Every ratified Zb* + Zk* extension.
- Full RVV 1.0.

**Clear gaps vs riscv-dv (blocking real verification work):**
- Full paging (Sv32/39/48).
- Debug mode.
- Multi-hart with shared-memory load/store races.

**Gaps vs ImperasDV (commercial ceiling):**
- H-extension, Smaia/Ssaia, Svnapot/Svpbmt, Zicbom, Zfh, Zvfh, Zicond, Zimop.

## 5 — Coverage methodology

Different tools take very different approaches — this is where we
currently stand out most visibly.

### riscv-dv's approach

- A single ~8000-line SystemVerilog file (`riscv_instr_cover_group.sv`)
  with macro-expanded per-opcode covergroups.
- Collection requires an SV simulator (Synopsys VCS, Cadence Xcelium,
  Mentor Questa). Open-source simulators (Verilator) only partially
  support SV covergroups.
- Output is a vendor-specific coverage database (UCDB / VCS-cov).
- Post-processing to HTML via vendor tools.
- **Goals as a formal contract**: not standardised; typically ad-hoc
  Makefile targets.

### force-riscv's approach

- Does not emit coverage itself.
- Runs generated programs on Imperas `riscvOVPsim` which produces a
  coverage report.
- Coverage format: Imperas-proprietary (tied to the licensed simulator).

### riscvISACOV's approach

- Auto-generates SV covergroup files from Imperas' machine-readable
  ISA definitions.
- Initial release covers RV32I.
- Still requires an SV simulator to collect.
- Community extending coverage via OpenHW projects.

### riscv-isac's approach

- **Post-simulation** — consumes an ISS log + a CGF (Coverage Goals
  Format) YAML file and emits a coverage report.
- Requires whoever ran the simulation to produce the trace format
  riscv-isac expects.
- Great tool; orthogonal to test generation (you bring your own
  generator).

### ImperasDV's approach

- Lock-step continuous compare — RTL + reference model run per-cycle
  in lock-step; coverage emerges from the compare.
- 5000+ pre-written vector tests + architectural test suites for FP /
  Bitmanip / Crypto / Vector.
- Proprietary coverage model; integrated with their simulator.

### rvgen's approach

- **Built-in, simulator-agnostic coverage** — 32 covergroups sampled
  in pure Python:
  - 18 **static** groups (from the generator's own instr list — no
    simulator needed).
  - 10 **runtime** groups (parsed from `spike -l --log-commits`,
    open-source only).
  - 4 **crosses** (reg-pair, format × category, etc.).
- **CGF-style YAML goals** — schema-compatible with riscv-isac's CGF
  format, plus layered overlays (baseline + per-target + per-test).
- **Coverage-directed auto-regression** — closes missing bins by
  perturbing `gen_opts` per seed. Nothing else in the ecosystem does
  this.
- **Analysis tools** — 9 subcommands: `merge / diff / attribute /
  per-test / export (CSV + HTML) / report / baseline-check /
  suggest-seeds / lint-goals`.
- **CI integration** — GitHub Actions `GITHUB_OUTPUT` +
  `GITHUB_STEP_SUMMARY` + composite 0-100 quality grade + standardised
  exit codes.
- **Convergence tracking** — per-bin first-hit seed + per-seed new-bin
  counts + ASCII sparkline + time-series JSON for dashboards.

### Coverage comparison at a glance

| Aspect | us | riscv-dv | force-riscv | riscv-isac | Imperas |
|---|---|---|---|---|---|
| Simulator licence needed | **No** | Yes (SV sim) | Yes (Imperas) | No | Yes (Imperas) |
| Static (generator-side) coverage | **✓** | ✗ | ✗ | ✗ | partial |
| Runtime coverage | ✓ (spike trace) | ✓ (SV sim) | ✓ (Imperas) | ✓ (any ISS) | ✓ |
| Built-in goals format | **✓ (CGF-style, linted)** | ✗ | ✗ | ✓ (CGF, native) | ✓ (proprietary) |
| Coverage-directed regression | **✓** | ✗ | ✗ | ✗ | partial |
| Per-test attribution | **✓** | partial | ✗ | ✗ | ✓ |
| HTML dashboard (zero-dep) | **✓ (self-contained)** | needs vendor tool | ✗ | ✓ | ✓ (vendor) |
| CI-native integration | **✓** | ✗ | ✗ | partial | ✓ (commercial) |
| CSV export | ✓ | ✗ | ✗ | ✓ | ✓ |
| Diff between runs | **✓** | ✗ | ✗ | ✗ | ✓ |
| Convergence analysis | **✓** | ✗ | ✗ | ✗ | ✗ |

**Our unique wins in coverage:**

1. **Coverage-directed auto-regression** — no one else does this.
   Closes baseline rv32imc goals in 1 seed (vs 8+ blind).
2. **`suggest-seeds`** — given historical convergence data, rank seeds
   by how many currently-missing bins they closed last time. No other
   tool does this.
3. **`lint-goals`** — typo protection. Static-validates every
   (covergroup, bin) pair.
4. **`baseline-check`** — a literal one-command CI gate against
   coverage regression.

## 6 — Next-wave work items, ranked by impact

### Tier 1 — generator extension gaps (weeks of work, unblocks real use cases)

1. **Full privileged mode + paging** (Sv32/39/48, PMP, debug ROM). Without
   this we can't claim parity with riscv-dv on `mmu_stress_test`,
   `privileged_mode_rand_test`, `pmp_test`, `ebreak_debug_mode_test`.
   **Estimated effort:** 1–2 weeks of focused work. Largest single item.

2. **Vector load/store + AMO directed streams** (rv64gcv's testlist
   references `riscv_vector_load_store_instr_stream` and
   `riscv_vector_amo_instr_stream` — they currently resolve to nothing).
   **Effort:** 2–3 days.

3. **Vector FP / widening / narrowing** — classes already exist but
   are gated off by default. Flip via `+vec_fp=1` /
   `+vec_narrowing_widening=1` + wire the VMV alignment constraints.
   **Effort:** 1–2 days.

### Tier 2 — extensions that close checkbox gaps with Imperas/cutting-edge cores

4. **Zicbom / Zicboz / Zicbop** (cache-management hints — ratified
   2022). Every modern core is starting to implement these. Trivial
   mnemonics; effort: 1 day.

5. **Zicond** (integer conditional — ratified 2023). Low-entropy.
   Effort: a few hours.

6. **Zimop** (may-be-operations — ratified 2024). Effort: a few
   hours.

7. **Zfh** (half-precision FP scalar). Matters for ML-accelerator
   cores (coralnpu could grow Zfh). Effort: 2-3 days (new FP format
   variants).

8. **Zvfh** (vector half-precision FP) — follow-on from Zfh for
   rv64gcv. Effort: 2–3 days.

9. **Svnapot / Svpbmt** — advanced paging. Dependent on #1. Effort:
   ~2 days given #1 landed.

### Tier 3 — deep feature additions

10. **H-extension (hypervisor mode + two-stage translation)** —
    unlocks server/datacentre-class cores. Large: 1-2 weeks.

11. **Smaia / Ssaia** — advanced interrupt architecture. Dependent
    on #10. Effort: 1 week.

12. **Multi-hart with shared-memory races** — `num_harts > 1` works
    but the load/store streams don't race. Needs a hart-aware region
    allocator + synchronisation primitives (fence + LR/SC rendezvous).
    Effort: 3–5 days.

### Tier 4 — coverage-subsystem polish (we're already strong)

13. **riscvISACOV-compatible export** — emit our goals as SV
    covergroup source so teams with SV sims can reuse our goal sets.
    Effort: 2–3 days.

14. **riscv-isac-compatible JSON format** — speak the upstream CGF
    tool's output format so our coverage data integrates into the
    broader ecosystem. Effort: 1–2 days (mostly naming alignment).

15. **Interactive dashboard** — replace the static HTML export with
    a plotly-based one (collapsible covergroups, time-series plots of
    cov_timeline.json, bin-vs-seed heatmaps). Single-file HTML still.
    Effort: 2-3 days.

16. **Coverage comparison across tool runs** — `tools compare --a
    baseline.json --b new.json --delta-threshold 5%` for CI gating.
    Effort: 1 day.

17. **Goals auto-generator** — from a CGF-format fixed ISA spec (the
    same tables Imperas uses), generate a starter goals file for any
    target. Effort: 2–3 days.

### Tier 5 — polish / community

18. **PyPI publication** — tag v0.1.0, build wheel, publish. Effort:
    1 day including the first bug reports.

19. **Docker image** — `docker run chipforge/inst-gen` with toolchain
    pre-installed. Effort: 1–2 days.

20. **ReadTheDocs site** — render `docs/` via Sphinx / mkdocs +
    GitHub Pages. Effort: 1 day.

## 7 — Recommendation: order of execution

If the goal is **"maximise value for verification engineers picking
this up"**, do Tier 1 first. Verification teams invariably need
privileged-mode / paging / PMP — it's the single biggest remaining
story against riscv-dv.

If the goal is **"minimise friction for OSS first-time users"**, do
Tier 5 first (PyPI + Docker + hosted docs). This is what makes
someone try the project in 30 seconds.

If the goal is **"differentiate on coverage forever"**, Tier 4 deepens
our already-leading position. Items #13-#14 specifically make us the
neutral-format bridge between riscv-dv-style SV coverage and
riscv-isac's Python CGF world — nobody else sits there.

**Suggested v0.2.0 scope (4 weeks):**

- Tier 1 items #1, #2, #3.
- Tier 2 items #4, #5, #6 (cheap wins that bump the checkbox score).
- Tier 5 item #18 (PyPI publication — lets adopters `pip install
  rvgen`).

**Stretch (v0.3.0, 4 weeks after that):**

- Tier 2 items #7, #8, #9 (Zfh / Zvfh / Svnapot).
- Tier 4 item #13 (riscvISACOV export — cross-ecosystem play).
- Tier 5 item #19 (Docker image).

**v1.0 horizon (6 months):**

- Tier 3 item #10 (H-extension) — required for server-class verification.
- Full riscv-dv testlist parity (every SV-emitted test runs clean).
- A single published case study (blog + paper?) showing a core brought
  up using rvgen exclusively.

## 8 — Honest self-assessment

**Where we win today:**

- Ease of install / use.
- Coverage workflow (CGF goals + directed auto-regress + CI integration +
  analysis tools).
- Zksh/Zksed, Zve*, Coral NPU — unique targets.
- Python-native — one codebase, no SV toolchain.

**Where we're competitive:**

- Ratified extension coverage (Zb* + Zk* + RVV 1.0 all covered).
- Testlist / gen_opts compatibility with riscv-dv.

**Where we're objectively behind:**

- Privileged mode + paging + PMP + debug (Tier 1 above).
- Experimental / cutting-edge extensions (Zfh / H / Smaia).
- Published case-study / adoption story (we have chipforge-mcu but
  that's internal).

**The big-picture take:** for rv32/rv64 without virtual memory or
hypervisor, we're already a credible riscv-dv replacement *and* we
have a clearly stronger coverage story. For privileged-mode-heavy
verification, we're not there yet — Tier 1 closes that. The coverage
tooling is the moat that's hardest for competitors to copy.

---

## Sources

- [riscv-dv GitHub](https://github.com/chipsalliance/riscv-dv)
- [force-riscv GitHub](https://github.com/openhwgroup/force-riscv) / [force-riscv target README](https://github.com/openhwgroup/force-riscv/blob/master/target/README.md)
- [ImperasDV product page](https://www.synopsys.com/verification/imperasdv.html)
- [Imperas — developing standards-based verification environments](https://riscv.org/blog/developing-standards-based-verification-environments-for-extensible-risc-v-processor-cores-kevin-mcdermott-imperas-software/)
- [RISC-V Verification: The 5 Levels](https://semiengineering.com/risc-v-verification-the-5-levels-of-simulation-based-processor-hardware-dv/)
- [riscvISACOV](https://github.com/riscv-verification/riscvISACOV)
- [riscv-isac CGF Specification](https://riscv-isac.readthedocs.io/en/latest/cgf.html)
- [PMP support — riscv-dv open issue #459](https://github.com/google/riscv-dv/issues/459)
- [Ratified RISC-V extensions](https://wiki.riscv.org/display/HOME/Ratified+Extensions)
