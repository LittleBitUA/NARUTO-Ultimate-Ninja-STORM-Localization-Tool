# -*- coding: utf-8 -*-
"""Finds the installed STORM games in the Steam libraries and proposes all paths.

Nothing here changes any file: it only looks for folders.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
import string

# Steam install folder names of the three games.
GAME_FOLDERS = {
    's1': 'QWANT',
    's2': 'Qwant2',
    's3': 'NARUTO SHIPPUDEN Ultimate Ninja STORM 3 Full Burst',
}
DATA = Path('data') / 'disc' / 'dataRegion_extracted' / 'data'
MESSAGE = 'messageInfo.bin.xfbin'


def steam_roots() -> list[Path]:
    """Steam's own folder: from the registry on Windows, plus the usual places."""
    roots = []
    if os.name == 'nt':
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam'),
                              (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam')):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        for name in ('SteamPath', 'InstallPath'):
                            try:
                                roots.append(Path(winreg.QueryValueEx(handle, name)[0]))
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles')):
            if base:
                roots.append(Path(base) / 'Steam')
    else:
        roots += [Path.home() / '.steam' / 'steam', Path.home() / '.local' / 'share' / 'Steam',
                  Path.home() / 'Library' / 'Application Support' / 'Steam']
    return roots


def libraries(roots=None) -> list[Path]:
    """Every Steam library folder (the ones that contain steamapps/common)."""
    found: list[Path] = []

    def add(path):
        path = Path(path)
        try:
            if (path / 'steamapps' / 'common').is_dir() and path not in found:
                found.append(path)
        except OSError:
            pass

    for root in (steam_roots() if roots is None else roots):
        add(root)
        for listing in (Path(root) / 'steamapps' / 'libraryfolders.vdf', Path(root) / 'config' / 'libraryfolders.vdf'):
            try:
                text = listing.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            for value in re.findall(r'"path"\s+"([^"]+)"', text):
                add(value.replace('\\\\', '\\'))
    if roots is None and os.name == 'nt':                  # libraries Steam's list does not mention
        for letter in string.ascii_uppercase:
            for name in ('SteamLibrary', 'Steam', 'Games\\SteamLibrary'):
                add(Path(f'{letter}:\\') / name)
    return found


def find_games(roots=None) -> dict[str, Path]:
    """game id -> install folder, for the games that are installed."""
    result = {}
    for library in libraries(roots):
        common = library / 'steamapps' / 'common'
        try:
            present = {entry.name.casefold(): entry for entry in common.iterdir() if entry.is_dir()}
        except OSError:
            continue
        for game, folder in GAME_FOLDERS.items():
            if game not in result and folder.casefold() in present:
                result[game] = present[folder.casefold()]
    return result


def proposed_paths(games: dict[str, Path]) -> tuple[dict[str, str], dict[str, str]]:
    """(settings to change, state per game).

    state: 'ready' — resources are extracted; 'not_extracted' — the game is
    installed but dataRegion_extracted is missing; 'missing' — not installed.
    """
    paths: dict[str, str] = {}
    state = {game: 'missing' for game in GAME_FOLDERS}
    for game, root in games.items():
        data = root / DATA
        extracted = data.is_dir()
        state[game] = 'ready' if extracted else 'not_extracted'
        if game == 's1':
            paths['game'] = str(root)
            if extracted:
                paths.update(eng=str(data / 'event' / 'eng'), telop=str(data / 'interface' / 'telop' / 'eng'),
                             texture_source=str(data), texture_scan_root=str(data / 'interface'),
                             adv_title_scan_root=str(data / 'adv_title'), patch_scope_storm1=str(data))
        elif game == 's2':
            if extracted:
                paths.update(storm2_eng=str(data / 'event' / 'WIN64' / 'eng'), storm2_ui_root=str(data / 'ui'),
                             storm2_pt_root=str(data / 'pt'), patch_scope_storm2=str(data))
        else:
            paths['storm3_game'] = str(root)
            sources = {'storm3_eng': root / DATA / 'message' / 'WIN32' / 'eng' / MESSAGE,
                       'storm3_hd1': root / 'dlc' / DATA / 'message' / 'WIN32' / 'eng' / MESSAGE,
                       'storm3_hd2': root / 'dlc' / DATA / 'message' / 'WIN64' / 'eng' / MESSAGE}
            for key, file in sources.items():
                if file.is_file():
                    paths[key] = str(file)
            state[game] = 'ready' if 'storm3_eng' in paths else 'not_extracted'
    return paths, state


def movable_outputs(current: dict[str, str], defaults: dict[str, str], keys, base: Path) -> dict[str, str]:
    """Output folders still set to a factory default on a drive that does not exist.

    They are moved under `base` (the user's Documents), keeping their sub-folders.
    """
    changed = {}
    for key in keys:
        value = current.get(key, '')
        if value != defaults.get(key) or not re.match(r'^[A-Za-z]:\\', value):
            continue
        if Path(value[:3]).exists():
            continue
        parts = [part for part in value[3:].split('\\') if part]
        if parts and parts[0].casefold() == 'localization':
            parts = parts[1:]
        changed[key] = str(base.joinpath(*parts))
    return changed
