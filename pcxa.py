#!/usr/bin/env python3
"""pcxa - top-level script shim.

Delegates to the `pcxa` package. Direct execution via `python pcxa.py …`
remains supported alongside `python -m pcxa` and the installed `pcxa`
console-script entry point.
"""

from pcxa import main


if __name__ == "__main__":
    main()
