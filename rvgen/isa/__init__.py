"""ISA layer: enums, registers, CSRs, helpers, instruction base class + factory.

Importing this package auto-registers every ISA extension module so that
``get_instr(name)`` / filtering / streams can find every opcode without the
caller having to import per-extension modules explicitly.
"""

from rvgen.isa import enums, csrs, utils  # noqa: F401

# Auto-register all per-extension instruction classes at import time.
# Order is arbitrary — each module is idempotent.
from rvgen.isa import rv32i   # noqa: F401,E402
from rvgen.isa import rv32m   # noqa: F401,E402
from rvgen.isa import rv64i   # noqa: F401,E402
from rvgen.isa import rv64m   # noqa: F401,E402
from rvgen.isa import rv32a   # noqa: F401,E402
from rvgen.isa import rv64a   # noqa: F401,E402
from rvgen.isa import rv32c   # noqa: F401,E402
from rvgen.isa import rv64c   # noqa: F401,E402
from rvgen.isa import rv32f   # noqa: F401,E402
from rvgen.isa import rv64f   # noqa: F401,E402
from rvgen.isa import rv32d   # noqa: F401,E402
from rvgen.isa import rv64d   # noqa: F401,E402
from rvgen.isa import rv32fc  # noqa: F401,E402
from rvgen.isa import rv32dc  # noqa: F401,E402
from rvgen.isa import bitmanip  # noqa: F401,E402
from rvgen.isa import crypto    # noqa: F401,E402
from rvgen.isa import rv32v          # noqa: F401,E402
from rvgen.isa import vector_crypto  # noqa: F401,E402

__all__ = ["enums", "csrs", "utils"]
