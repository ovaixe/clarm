"""Enable ``python -m clarm`` as an alias for the ``clarm`` console script."""

from clarm.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
