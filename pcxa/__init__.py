"""pcxa - Unified CLI for the PCXA construction intelligence platform.

Public surface:
    main()         — CLI entrypoint (used by `pcxa = "pcxa:main"` in pyproject)
    __version__    — current package version
"""

__version__ = "0.7.2"

# main() is wired in pcxa._main once the package is fully assembled. Imports
# are deferred to keep package import cheap and avoid pulling argparse on
# `import pcxa` from library callers.

def main():
    from pcxa._main import main as _main
    return _main()


__all__ = ["main", "__version__"]
