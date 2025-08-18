#!/usr/bin/env python

"""Constructs command line argument parser for tvnamer
"""

import optparse
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .config_defaults import TypedDefaults


class Group(object):
    """Simple helper context manager to add a group to an OptionParser
    """

    def __init__(self, parser, name):
        # type: (optparse.OptionParser, str) -> None
        self.parser = parser
        self.name = name
        self.group = optparse.OptionGroup(self.parser, name)

    def __enter__(self):
        # type: () -> optparse.OptionGroup
        return self.group

    def __exit__(self, *k, **kw):
        # type: (Any, Any) -> None
        self.parser.add_option_group(self.group)


def get_cli_parser(defaults):
    # type: (TypedDefaults) -> optparse.OptionParser
    parser = optparse.OptionParser(
        usage="%prog [options] <files>", add_help_option=False
    )

    parser.set_defaults(**defaults)

    # fmt: off

    # Console output
    with Group(parser, "Console output") as g:
        g.add_option("-v", "--verbose", action="store_true", dest="verbose", help="show debugging info")
        g.add_option("-q", "--not-verbose", action="store_false", dest="verbose", help="no verbose output (useful to override 'verbose':true in config file)")
        g.add_option("--dry-run", action="store_true", dest="dry_run", help="Only tell what script is going to do")

    # Batch options
    with Group(parser, "Batch options") as g:
        g.add_option("-a", "--always", action="store_true", dest="always_rename", help="Always renames files (but prompt for correct series)")
        g.add_option("--not-always", action="store_true", dest="always_rename", help="Overrides --always")

        g.add_option("-f", "--selectfirst", action="store_true", dest="select_first", help="Select first series search result automatically")
        g.add_option("--not-selectfirst", action="store_false", dest="select_first", help="Overrides --selectfirst")

        g.add_option("-b", "--batch", action="store_true", dest="batch", help="Rename without human intervention, same as --always and --selectfirst combined")
        g.add_option("--not-batch", action="store_false", dest="batch", help="Overrides --batch")
        g.add_option("--ask-again", action="store_false", dest="remember_choice", help="Ask again for files previously seen")

    # Config options
    with Group(parser, "Config options") as g:
        g.add_option("-c", "--config", action="store", dest="loadconfig", help="Load config from this file")
        g.add_option("-s", "--save", action="store", dest="saveconfig", help="Save configuration to this file and exit")
        g.add_option("-p", "--preview-config", action="store_true", dest="showconfig", help="Show current config values and exit")

    # Override values
    with Group(parser, "Override values") as g:
        g.add_option("-n", "--name", action="store", dest="force_name", help="override the parsed series name with this (applies to all files)")
        g.add_option("--series-id", action="store", dest="series_id", help="explicitly set the show id for TVdb to use (applies to all files)")
        g.add_option("--order", action="store", dest="order", help="set the TvDB episode order ('aired' [default] or 'dvd')")
        g.add_option("-l", "--lang", action="store", dest="language", help="set the language used to retrieve data")

    # Misc
    with Group(parser, "Misc") as g:
        g.add_option("-r", "--recursive", action="store_true", dest="recursive", help="Descend more than one level directories supplied as arguments")
        g.add_option("--not-recursive", action="store_false", dest="recursive", help="Only descend one level into directories")

        g.add_option("-m", "--mode", action="store", dest="mode", help="Mode can be copy, move, symlink")

        g.add_option("-d", "--movedestination", action="store", dest="move_files_destination", help="Destination to move files to. Variables: %(seriesname)s %(seasonnumber)d %(episodenumbers)s")

        g.add_option("-h", "--help", action="help", help="show this help message and exit")
        g.add_option("--version", action="store_true", dest="show_version", help="show verison number and exit")

    return parser
