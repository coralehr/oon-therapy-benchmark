"""Enable ``python -m oon_bench`` -> delegates to the CLI's main()."""

from __future__ import annotations

from oon_bench.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
