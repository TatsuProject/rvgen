# User testlists

Drop riscv-dv-format testlist YAMLs here. No auto-discovery — point
rvgen at them explicitly:

```bash
python -m rvgen \
    --target <t> --test <name> \
    --testlist user/testlists/my_regression.yaml \
    --steps gen,gcc_compile,iss_sim --iss spike \
    --output out/
```

## Testlist format

```yaml
- test: my_basic_test
  description: >
    What this test exercises.
  iterations: 2
  gen_test: riscv_rand_instr_test     # rvgen entry point — usually rand_instr_test
  gen_opts: >
    +instr_cnt=2000
    +num_of_sub_program=0
    +no_fence=1
    +boot_mode=m
  rtl_test: core_base_test            # RTL sim test name (unused by rvgen itself)
```

See `docs/examples/privileged-testlist.yaml` for a complete worked
example covering ebreak / ecall / timer-IRQ / U-mode boot.
