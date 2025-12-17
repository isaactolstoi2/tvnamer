#!/usr/bin/env python

"""Main tvnamer utility functionality
"""

import os
from pathlib import Path
import sys
import logging
import warnings

try:
    import readline
except ImportError:
    pass

import json

import tvdb_api
from typing import List, Union, Optional

from tvnamer import cliarg_parser, __version__
from tvnamer.config_defaults import defaults
from tvnamer.config import Config
from tvnamer.files import FileFinder, FileParser, Renamer, _apply_replacements_input
from tvnamer.utils import (
    format_episode_numbers,
    make_valid_filename,
)
from tvnamer.data import (
    BaseInfo,
    EpisodeInfo,
    DatedEpisodeInfo,
    NoSeasonEpisodeInfo,
)

from tvnamer.tvnamer_exceptions import (
    ShowNotFound,
    SeasonNotFound,
    EpisodeNotFound,
    EpisodeNameNotFound,
    UserAbort,
    InvalidPath,
    NoValidFilesFoundError,
    SkipBehaviourAbort,
    InvalidFilename,
    DataRetrievalError,
)
from tvnamer import database


LOG = logging.getLogger(__name__)


# Key for use in tvnamer only - other keys can easily be registered at https://thetvdb.com/api-information
TVNAMER_API_KEY = "fb51f9b848ffac9750bada89ecba0225"


def get_move_destination(episode):
    # type: (BaseInfo) -> str
    """Constructs the location to move/copy the file
    """

    # TODO: Write functional test to ensure this valid'ifying works
    def wrap_validfname(fname):
        # type: (str) -> str
        """Wrap the make_valid_filename function as it's called twice
        and this is slightly long..
        """
        if Config["move_files_lowercase_destination"]:
            fname = fname.lower()
        return make_valid_filename(
            fname,
            windows_safe=Config["windows_safe_filenames"],
            custom_blacklist=Config["custom_filename_character_blacklist"],
            replace_with=Config["replace_invalid_characters_with"],
        )

    # Calls make_valid_filename on series name, as it must valid for a filename
    if isinstance(episode, DatedEpisodeInfo):
        dest_dir = Config["move_files_destination_date"] % {
            "seriesname": make_valid_filename(episode.seriesname),
            "year": episode.episodenumbers[0].year,
            "month": episode.episodenumbers[0].month,
            "day": episode.episodenumbers[0].day,
            "originalfilename": episode.originalfilename,
        }
    elif isinstance(episode, NoSeasonEpisodeInfo):
        dest_dir = Config["move_files_destination"] % {
            "seriesname": wrap_validfname(episode.seriesname),
            "episodenumbers": wrap_validfname(
                format_episode_numbers(episode.episodenumbers)
            ),
            "originalfilename": episode.originalfilename,
        }
    elif isinstance(episode, EpisodeInfo):
        dest_dir = Config["move_files_destination"] % {
            "seriesname": wrap_validfname(episode.seriesname),
            "seasonnumber": episode.seasonnumber,
            "episodenumbers": wrap_validfname(
                format_episode_numbers(episode.episodenumbers)
            ),
            "originalfilename": episode.originalfilename,
        }
    else:
        raise RuntimeError("Unhandled episode subtype of %s" % type(episode))

    return dest_dir


def do_file_operation(cnamer,mode, dest_dir=None, dest_filepath=None, get_path_preview=False):
    # type: (Renamer,str, Optional[str], Optional[str], bool) -> Optional[str]
    """Moves, rename, copy, or symlink file to dest_dir, or to dest_filepath
    """

    if (dest_dir, dest_filepath).count(None) != 1:
        raise ValueError("Specify only dest_dir or dest_filepath")

    try:
        return cnamer.new_path(
            new_path=dest_dir,
            new_fullpath=dest_filepath,
            mode=mode,
            add_link_back=Config["leave_symlink"],
            get_path_preview=get_path_preview,
            force=Config["overwrite_destination_on_move"],
        )
    except FileExistsError as e:
        if Config["skip_behaviour"] == "exit":
            LOG.info("Exiting due to error: %s" % e)
            raise SkipBehaviourAbort()
        LOG.debug("Skipping file due to error: %s" % e)
        return None
    except OSError as e:
        if Config["skip_behaviour"] == "exit":
            LOG.warning("Exiting due to error: %s" % e)
            raise SkipBehaviourAbort()
        LOG.warning("Skipping file due to error: %s" % e)
        return None


def confirm(question, options, default="y"):
    # type: (str, List[str], str) -> str
    """Takes a question (string), list of options and a default value (used
    when user simply hits enter).
    Asks until valid option is entered.
    """
    # Highlight default option with [ ]
    options_chunks = []
    for x in options:
        if x == default:
            x = "[%s]" % x
        if x != "":
            options_chunks.append(x)
    options_str = "/".join(options_chunks)

    while True:
        print(question)
        print("(%s) " % (options_str), end="")
        try:
            ans = input().strip()
        except KeyboardInterrupt as errormsg:
            print("\n", errormsg)
            raise UserAbort(errormsg)

        if ans in options:
            return ans
        elif ans == "":
            return default

def lookup_previous_choice(should_lookup: bool,fullfilename :str):
    """lookup in database if fullfilname has a entry, to avoid re-asking or the same file
    if should_lookup is false this method is a noop
    """
    if not should_lookup:
        return None
    result = database.lookup(fullfilename)
    LOG.debug(f"kvstore lookup {fullfilename}: {result}")
    return result

def store_new_choice(fullfilename,seriesid, episode:EpisodeInfo):
    LOG.debug(f"kvstore store {fullfilename}: {seriesid}")
    season = str(episode.seasonnumber) if type(episode) != NoSeasonEpisodeInfo else "00"
    database.upsert(fullfilename,seriesid, season," ".join([str(i) for i in episode.episodenumbers]),None)

def ask_for_seriesname(episode):
    print(f"Current file: {episode.fullpath}")
    print("Please enter series name:")
    return input().strip()

def process_file(tvdb_instance, episode):
    # type: (tvdb_api.Tvdb, BaseInfo) -> None
    """Gets episode name, prompts user for input
    """
    episode = get_episode_name_maybe_prompt(tvdb_instance, episode)
    if episode is None:
        return
    generate_filename_and_rename(episode)

def get_episode_name_maybe_prompt(tvdb_instance, episode):
    retries = 1
    force_name = None
    while (retries >0):

        LOG.info("#" * 20)
        LOG.info("# Processing file: %s" % episode.fullfilename)

        if len(Config["input_filename_replacements"]) > 0:
            replaced = _apply_replacements_input(episode.fullfilename)
            LOG.debug("# With custom replacements: %s" % (replaced))

        # Use force_name option. Done after input_filename_replacements so
        # it can be used to skip the replacements easily
        if Config["force_name"] is not None:
            episode.seriesname = Config["force_name"]

        LOG.info("# Detected series: %s (%s)" % (episode.seriesname, episode.number_string()))
        series_id = Config["series_id"] or lookup_previous_choice(Config['remember_choice'],episode.fullfilename)
        try:
            episode.populate_from_tvdb(
                tvdb_instance,
                force_name=Config["force_name"] or force_name,
                series_id=series_id,
            )
            # no error at first try.
            retries=0
        except (ShowNotFound) as errormsg:
                if Config["skip_behaviour"] == "exit":
                    LOG.warning("Exiting due to error: %s" % errormsg)
                    raise SkipBehaviourAbort()
                if Config["skip_behaviour"] == "ask":
                    LOG.info(errormsg)
                    force_name = ask_for_seriesname(episode)
                    if len(force_name)>1:
                        retries+=1
                else:
                    LOG.warning("Skipping file due to error: %s" % errormsg)
                    return

        except (DataRetrievalError) as errormsg:
            if Config["always_rename"] and Config["skip_file_on_error"] is True:
                if Config["skip_behaviour"] == "exit":
                    LOG.warning("Exiting due to error: %s" % errormsg)
                    raise SkipBehaviourAbort()
                if Config["skip_behaviour"] == "ask":
                    LOG.info(errormsg)
                    force_name = ask_for_seriesname(episode)
                    if len(force_name)>1:
                        retries+=1
                else:
                    LOG.warning("Skipping file due to error: %s" % errormsg)
                    return
            else:
                LOG.warning("%s" % (errormsg))
        except (SeasonNotFound, EpisodeNotFound, EpisodeNameNotFound) as errormsg:
            # Show was found, so use corrected series name
            if Config["always_rename"] and Config["skip_file_on_error"]:
                if Config["skip_behaviour"] == "exit":
                    LOG.warning("Exiting due to error: %s" % errormsg)
                    raise SkipBehaviourAbort()
                LOG.warning("Skipping file due to error: %s" % errormsg)
                return

            LOG.warning("%s" % (errormsg))
        retries-=1
    if 'seriesid' in episode.__dict__:
        store_new_choice(episode.fullfilename,episode.seriesid or None, episode)
    return episode

def generate_filename_and_rename(episode):
    cnamer = Renamer(episode.fullpath)

    should_rename = Config['always_rename']

    new_name = episode.generate_filename()
    if new_name == episode.fullfilename:
        LOG.info("#" * 20)
        LOG.info("Existing filename is correct: %s" % episode.fullfilename)
        LOG.info("#" * 20)

        should_rename = True

    else:
        LOG.info("#" * 20)
        LOG.info("Old filename: %s" % episode.fullfilename)

        if len(Config["output_filename_replacements"]) > 0:
            # Show filename without replacements
            LOG.info(
                "Before custom output replacements: %s"
                % (episode.generate_filename(preview_orig_filename=True))
            )
        # check collisions
        collision = database.find_by_newname(new_name)
        if collision is not None and collision[0].fullfilename != episode.fullfilename:
            # if we did not see this source yet, but the destination is in the database we have another version of the same episode, append something
            # if we saw this file already, as such nothing to do but the destination could have been deleted, which is checked in do_file_operation so let it continue
            new_name = episode.generate_filename(add_variant=True)
        database.upsert(episode.fullfilename,newfilename=new_name)
        LOG.debug("New filename: %s" % new_name)

        if Config["dry_run"]:
            LOG.debug("%s will be %s'ed to %s" % (episode.fullfilename, Config["mode"],new_name))
            return
        if Config['always_rename'] == False:
            should_rename = ask_for_rename()

    if should_rename:
        do_file_operation(cnamer,Config["mode"], dest_dir=None,dest_filepath=new_name)


def ask_for_rename():
    should_rename = False
    ans = confirm("Rename?", options=["y", "n", "a", "q"], default="y")

    if ans == "a":
        LOG.info("Always renaming")
        Config["always_rename"] = True
        should_rename = True
    elif ans == "q":
        LOG.info("Quitting")
        raise UserAbort("User exited with q")
    elif ans == "y":
        LOG.info("Renaming")
        should_rename = True
    elif ans == "n":
        LOG.info("Skipping")
    else:
        LOG.info("Invalid input, skipping")
    return should_rename


def find_files(paths):
    # type: (List[str]) -> List[str]
    """Takes an array of paths, returns all files found
    """
    valid_files = []

    for cfile in paths:
        cur = FileFinder(
            cfile,
            with_extension=Config["valid_extensions"],
            filename_blacklist=Config["filename_blacklist"],
            recursive=Config["recursive"],
        )

        try:
            valid_files.extend(cur.find_files())
        except InvalidPath:
            LOG.warning("Invalid path: %s" % cfile)

    if len(valid_files) == 0:
        raise NoValidFilesFoundError()

    # Remove duplicate files (all paths from FileFinder are absolute)
    valid_files = list(set(valid_files))

    return valid_files


def tvnamer(paths, show_progress):
    # type: (List[str], bool) -> None
    """Main tvnamer function, takes an array of paths, does stuff.
    """

    LOG.info("#" * 20)
    LOG.info("# Starting tvnamer")

    episodes_found = []

    for cfile in find_files(paths):
        parser = FileParser(cfile)
        try:
            episode = parser.parse()
        except InvalidFilename as e:
            LOG.warning("Invalid filename: %s" % e)
        else:
            if (
                episode.seriesname is None
                and Config["force_name"] is None
                and Config["series_id"] is None
            ):
                LOG.warning(
                    "Parsed filename did not contain series name (and --name or --series-id not specified), skipping: %s"
                    % cfile
                )

            else:
                episodes_found.append(episode)

    if len(episodes_found) == 0:
        raise NoValidFilesFoundError()

    LOG.info(
        "# Found %d episode" % len(episodes_found) + ("s" * (len(episodes_found) > 1))
    )

    # Sort episodes by series name, season and episode number
    episodes_found.sort(key=lambda x: x.sortable_info())

    # episode sort order
    if Config["order"] == "dvd":
        dvdorder = True
    else:
        dvdorder = False

    if Config["tvdb_api_key"] is not None:
        LOG.debug("Using custom API key from config")
        api_key = Config["tvdb_api_key"]
    else:
        LOG.debug("Using tvnamer default API key")
        api_key = TVNAMER_API_KEY

    if os.getenv("TVNAMER_TEST_MODE", "0") == "1":
        from .test_cache import get_test_cache_session
        cache = get_test_cache_session()
    else:
        if Config['kvstore']:
            cache = str(Path(Config['kvstore']).parent)
        else:
            cache = True

    tvdb_instance = tvdb_api.Tvdb(
        interactive=not Config["select_first"],
        language=Config["language"],
        dvdorder=dvdorder,
        cache=cache,
        apikey=api_key,
    )

    progress=0
    progress_printed=-0.1 #initialized to negative value. emits progress once at start
    progress_total= len(episodes_found)
    for i, episode in enumerate(episodes_found):
        process_file(tvdb_instance, episode)
        if show_progress:
            progress = i/progress_total
            if (progress - progress_printed) > 0.05:
                progress_printed=progress
                print(f"progress: {progress*100:.0f}%")
        LOG.info("")

    LOG.info("#" * 20)
    LOG.info("# Done")


def main():
    # type: () -> None
    """Parses command line arguments, displays errors from tvnamer in terminal
    """
    opter = cliarg_parser.get_cli_parser(defaults)

    opts, args = opter.parse_args()

    if opts.show_version:
        print("tvnamer version: %s" % (__version__,))
        print("tvdb_api version: %s" % (tvdb_api.__version__,))
        print("python version: %s" % (sys.version,))
        sys.exit(0)

    if opts.verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
        )
    elif opts.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    else:
        logging.basicConfig()

    # If a config is specified, load it, update the defaults using the loaded
    # values, then reparse the options with the updated defaults.
    default_configuration = os.path.expanduser("~/.config/tvnamer/tvnamer.json")
    old_default_configuration = os.path.expanduser("~/.tvnamer.json")

    if opts.loadconfig is not None:
        # Command line overrides loading ~/.config/tvnamer/tvnamer.json
        config_to_load = opts.loadconfig
    elif os.path.isfile(default_configuration):
        # No --config arg, so load default config if it exists
        config_to_load = default_configuration
    elif os.path.isfile(old_default_configuration):
        # No --config arg and neow defualt config so load old version if it exist
        config_to_load = old_default_configuration
    else:
        # No arg, nothing at default config location, don't load anything
        config_to_load = None

    if config_to_load is not None:
        LOG.info("Loading config: %s" % (config_to_load))
        if os.path.isfile(old_default_configuration):
            LOG.warning("WARNING: you have a config at deprecated ~/.tvnamer.json location.")
            LOG.warning("Config must be moved to new location: ~/.config/tvnamer/tvnamer.json")

        try:
            loaded_config = json.load(open(os.path.expanduser(config_to_load)))
        except ValueError as e:
            LOG.error("Error loading config: %s" % e)
            opter.exit(1)
        else:
            # Config loaded, update optparser's defaults and reparse
            defaults.update(loaded_config)
            opter = cliarg_parser.get_cli_parser(defaults)
            opts, args = opter.parse_args()

    # Save config argument
    if opts.saveconfig is not None:
        LOG.info("Saving config: %s" % (opts.saveconfig))
        config_to_save = dict(opts.__dict__)
        del config_to_save["saveconfig"]
        del config_to_save["loadconfig"]
        del config_to_save["showconfig"]
        json.dump(
            config_to_save,
            open(os.path.expanduser(opts.saveconfig), "w+"),
            sort_keys=True,
            indent=4,
        )

        opter.exit(0)

    # Show config argument
    if opts.showconfig:
        print(json.dumps(opts.__dict__, sort_keys=True, indent=2))
        return

    # Process values
    if opts.batch:
        opts.select_first = True
        opts.always_rename = True

    # Update global config object
    Config.update(opts.__dict__)

    if Config["titlecase_filename"] and Config["lowercase_filename"]:
        LOG.warning(
            "Setting 'lowercase_filename' clobbers 'titlecase_filename' option"
        )

    if len(args) == 0:
        opter.error("No filenames or directories supplied")

    try:
        database.init_database(Config)
        tvnamer(paths=sorted(args), show_progress=opts.progress or False)
    except NoValidFilesFoundError:
        opter.error("No valid files were supplied")
    except UserAbort as errormsg:
        opter.error(str(errormsg))
    except SkipBehaviourAbort as errormsg:
        opter.error(str(errormsg))


if __name__ == "__main__":
    main()
