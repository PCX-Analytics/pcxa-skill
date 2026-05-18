"""Tier 2: parser ↔ dispatch consistency.

Every (group, subcommand) declared in the argparse tree must have a handler in
SUB_HANDLERS, and every handler must be reachable through the parser. Catches
drift like "added the subparser, forgot to wire the dispatcher" or vice versa,
which would otherwise only show up as a runtime crash on first use.
"""

import argparse

import pytest

from pcxa._main import AUTH_FREE, HANDLERS, SUB_HANDLERS
from pcxa._parser import build_parser


def _collect_subcommands(parser):
    """Yield ``(group, subcommand)`` for every two-level command in the parser.

    For a parser like ``pcxa files purge``, yields ``("files", "purge")``.
    Top-level commands without nested subparsers are yielded as ``(name, None)``.
    """
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for group_name, group_parser in action.choices.items():
            found_nested = False
            for sub_action in group_parser._actions:
                if isinstance(sub_action, argparse._SubParsersAction):
                    found_nested = True
                    for sub_name in sub_action.choices:
                        yield (group_name, sub_name)
            if not found_nested:
                yield (group_name, None)


def test_every_parser_subcommand_has_dispatcher():
    parser = build_parser()
    missing = []
    for group, sub in _collect_subcommands(parser):
        if sub is None:
            # Top-level commands map through HANDLERS, SUB_HANDLERS, or the
            # AUTH_FREE branch in main() (login, setup, projects, etc.).
            if (group not in HANDLERS
                    and group not in SUB_HANDLERS
                    and group not in AUTH_FREE):
                missing.append(f"{group}")
            continue
        if group not in SUB_HANDLERS:
            missing.append(f"{group} (group missing)")
            continue
        if sub not in SUB_HANDLERS[group]:
            missing.append(f"{group} {sub}")
    assert not missing, (
        "Parser declares subcommands with no dispatcher in SUB_HANDLERS: "
        + ", ".join(missing)
    )


def test_every_dispatcher_is_reachable_from_parser():
    parser = build_parser()
    declared = {
        (group, sub)
        for group, sub in _collect_subcommands(parser)
        if sub is not None
    }
    orphans = []
    for group, handlers in SUB_HANDLERS.items():
        for sub in handlers:
            if (group, sub) not in declared:
                orphans.append(f"{group} {sub}")
    assert not orphans, (
        "SUB_HANDLERS entries with no matching parser subcommand: "
        + ", ".join(orphans)
    )


def test_purge_subcommand_is_wired():
    """Regression guard for #562: `files purge` must remain reachable."""
    parser = build_parser()
    pairs = set(_collect_subcommands(parser))
    assert ("files", "purge") in pairs
    assert "purge" in SUB_HANDLERS["files"]


@pytest.mark.parametrize("argv", [
    ["files", "purge", "--help"],
    ["files", "purge", "1", "2", "3", "--dry-run"],
    ["files", "purge", "--ids-file", "-", "--chunk", "100", "--yes"],
])
def test_purge_argv_parses(argv):
    """The purge parser accepts its documented invocations without crashing."""
    parser = build_parser()
    if "--help" in argv:
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(argv)
        assert exc_info.value.code == 0
    else:
        args = parser.parse_args(argv)
        assert args.command == "files"
        assert args.files_command == "purge"
