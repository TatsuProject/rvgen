#!/bin/bash
# chipforge-mcu cross-compare: gen → spike → core → diff.
#
# Runs one test through the full validation chain:
#   1. rvgen           → .S
#   2. riscv-gcc                    → .elf
#   3. objcopy -O verilog + split   → imem.mem / dmem.mem
#   4. spike --log-commits          → spike_trace.csv
#   5. MCU Verilator sim (Vtb_top)  → core_trace.csv
#   6. instr_trace_compare.py       → diff.log
#
# Prints a one-line [PASSED]/[FAILED] per test invocation.
#
# Env vars (all have sensible defaults — override if your layout differs):
#   WORK_DIR    Scratch dir for generated artifacts (default: /var/tmp/mcu/work)
#   MCU_VERIF   chipforge-mcu verif/ directory (must contain obj_dir/Vtb_top + scripts/)
#   RISCV_GCC   Path to riscv-*-gcc (auto-detected if not set)
#   RISCV_OBJCOPY  Path to riscv-*-objcopy (default: sibling of GCC)
#   SPIKE       Path to spike binary (auto-detected if not set)
#   PYTHON      Python interpreter (default: python3)
#   RISCV_DV    riscv-dv checkout (for testlist imports)
#
# Usage:  mcu_validate.sh <test_name> [seed]
#
# Example:
#   MCU_VERIF=~/chipforge-mcu/verif \
#     ./scripts/mcu_validate.sh riscv_arithmetic_basic_test 100
#
set -e

: "${WORK_DIR:=/var/tmp/mcu/work}"
: "${MCU_VERIF:?MCU_VERIF must point to chipforge-mcu/verif}"
: "${PYTHON:=python3}"
: "${RISCV_DV:?RISCV_DV must point to a riscv-dv checkout (for testlist imports)}"

# tool discovery with minimal surprise
if [ -z "${RISCV_GCC:-}" ]; then
    RISCV_GCC=$(command -v riscv64-unknown-elf-gcc || command -v riscv32-unknown-elf-gcc || true)
fi
: "${RISCV_GCC:?RISCV_GCC must point to riscv-gcc}"
: "${RISCV_OBJCOPY:=${RISCV_GCC/-gcc/-objcopy}}"
: "${SPIKE:=$(command -v spike)}"
: "${SPIKE:?SPIKE must point to spike}"

# The MCU's ratified ISA — RV32IMC + Zbkb/Zbkc/Zbkx/Zkne/Zknd/Zknh.
MARCH="rv32imc_zbkb_zbkc_zbkx_zknd_zkne_zknh_zicsr_zifencei"
MABI="ilp32"

export TMPDIR=/var/tmp   # GCC needs a writable /tmp if the system's is full

test_name=$1
seed=${2:-100}
[ -z "$test_name" ] && { echo "usage: $0 <test_name> [seed]" >&2; exit 2; }

LINK="$MCU_VERIF/scripts/link.ld"
SIM="$MCU_VERIF/obj_dir/Vtb_top"

rm -rf "$WORK_DIR"; mkdir -p "$WORK_DIR"

# 1) generate .S
"$PYTHON" -m rvgen --target rv32imc_zkn \
    --testlist "$RISCV_DV/target/rv32imc/testlist.yaml" \
    --test "$test_name" --steps gen \
    --output "$WORK_DIR" --start_seed "$seed" -i 1 >/dev/null 2>&1

# 2) assemble with the MCU's link script
cp "$LINK" "$WORK_DIR/"
touch "$WORK_DIR/user_define.h" "$WORK_DIR/user_init.s"
"$RISCV_GCC" \
    -march="$MARCH" -mabi="$MABI" \
    -static -mcmodel=medany -fvisibility=hidden -nostdlib -nostartfiles \
    -I"$WORK_DIR" -T"$WORK_DIR/link.ld" "$WORK_DIR/asm_test/${test_name}_0.S" \
    -o "$WORK_DIR/test.elf" 2>"$WORK_DIR/gcc.err" \
    || { echo "GCC FAIL: $(head -1 "$WORK_DIR/gcc.err")"; exit 1; }

# 3) ELF → verilog hex
"$RISCV_OBJCOPY" -O verilog "$WORK_DIR/test.elf" "$WORK_DIR/test.hex"

# 4) run spike; strip default bootrom; convert to CSV
"$SPIKE" --log-commits --isa="$MARCH" --priv=m --misaligned -l \
    -m0x80000000:0x200000 "$WORK_DIR/test.elf" > "$WORK_DIR/spike.log" 2>&1

"$PYTHON" - <<PYEOF
import re
with open("$WORK_DIR/spike.log") as f:
    lines = f.readlines()
# Skip spike's default bootrom (address 0x0..0x10). The upstream parser
# looks for a trampoline ending at 0x1010 (spike-pk); we fake that marker
# so the parser's FSM switches into "user code" state.
start = 0
for i, ln in enumerate(lines):
    if re.search(r"core\s+\d+:\s+0x8", ln):
        start = i
        break
with open("$WORK_DIR/spike_stripped.log", "w") as f:
    f.write("core   0: 0x00001010 (nop)\n")
    f.writelines(lines[start:])
PYEOF

"$PYTHON" "$MCU_VERIF/scripts/spike_log_to_trace_csv.py" \
    --log "$WORK_DIR/spike_stripped.log" --csv "$WORK_DIR/spike_trace.csv" >/dev/null 2>&1

# 5) split verilog hex into the MCU's memory regions and run Vtb_top
"$PYTHON" - <<PYEOF
def parse(path, base, size):
    m = {}; a = 0
    for ln in open(path):
        ln = ln.strip()
        if not ln: continue
        if ln.startswith("@"):
            a = int(ln[1:], 16); continue
        for b in ln.split():
            m[a] = int(b, 16); a += 1
    return bytearray(m.get(x, 0) for x in range(base, base + size))

def write_hex(data, out):
    with open(out, "w") as f:
        for i in range(0, len(data), 4):
            chunk = data[i:i+4]
            while len(chunk) < 4:
                chunk += b"\x00"
            f.write(f"{int.from_bytes(chunk, 'little'):08X}\n")

write_hex(parse("$WORK_DIR/test.hex", 0x80000000, 32 * 1024), "$WORK_DIR/imem.mem")
write_hex(parse("$WORK_DIR/test.hex", 0x80008000, 16 * 1024), "$WORK_DIR/dmem.mem")
PYEOF

cp "$WORK_DIR/imem.mem" "$WORK_DIR/dmem.mem" "$MCU_VERIF/"
(cd "$MCU_VERIF" && timeout 30 "$SIM" >/dev/null 2>&1) \
    || { echo "SIM TIMEOUT"; exit 1; }

"$PYTHON" "$MCU_VERIF/scripts/core_log_to_trace_csv.py" \
    --log "$MCU_VERIF/trace_core_00000001.log" \
    --csv "$WORK_DIR/core_trace.csv" >/dev/null 2>&1

# 6) diff spike vs core
"$PYTHON" "$MCU_VERIF/scripts/instr_trace_compare.py" \
    --csv_file_1 "$WORK_DIR/spike_trace.csv" --csv_file_2 "$WORK_DIR/core_trace.csv" \
    --csv_name_1 spike --csv_name_2 core --log "$WORK_DIR/diff.log" >/dev/null 2>&1

result=$(grep "\[PASSED\]\|\[FAILED\]" "$WORK_DIR/diff.log" | head -1)
echo "$test_name seed=$seed: $result"
