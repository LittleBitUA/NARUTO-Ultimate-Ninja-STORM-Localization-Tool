"""Steam detection, TXT backups and the all-at-once tag check."""
import time
from pathlib import Path

import steam
import txtfmt

HEAD = '### {n} — message\\S{n}.txt · рядок 1 · EN: {en}'


def record(n, en, *body):
    return [HEAD.format(n=n, en=en), *body, '', '###']


def write(path, lines):
    path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8-sig'))


# ---- Steam ---------------------------------------------------------------------

def make_library(root, games, extracted=True):
    common = root / 'steamapps' / 'common'
    common.mkdir(parents=True)
    for game in games:
        folder = common / steam.GAME_FOLDERS[game]
        folder.mkdir()
        if extracted:
            (folder / steam.DATA).mkdir(parents=True)
    return root


def test_games_are_found_in_every_library(tmp_path):
    main = make_library(tmp_path / 'Steam', ['s1'])
    second = make_library(tmp_path / 'D' / 'SteamLibrary', ['s2', 's3'], extracted=False)
    (main / 'steamapps' / 'libraryfolders.vdf').write_text(
        '"libraryfolders"\n{\n "0" { "path" "%s" }\n "1" { "path" "%s" }\n}\n'
        % (str(main).replace('\\', '\\\\'), str(second).replace('\\', '\\\\')), encoding='utf-8')
    games = steam.find_games([main])
    assert set(games) == {'s1', 's2', 's3'} and games['s2'].parent.parent.parent == second
    paths, state = steam.proposed_paths(games)
    assert state == {'s1': 'ready', 's2': 'not_extracted', 's3': 'not_extracted'}
    assert paths['eng'].endswith(str(Path('event') / 'eng')) and paths['game'] == str(games['s1'])
    assert 'storm2_eng' not in paths and paths['storm3_game'] == str(games['s3'])


def test_storm3_sources_are_detected(tmp_path):
    library = make_library(tmp_path / 'Steam', ['s3'])
    root = library / 'steamapps' / 'common' / steam.GAME_FOLDERS['s3']
    for base in (root, root / 'dlc'):
        file = base / steam.DATA / 'message' / 'WIN32' / 'eng' / steam.MESSAGE
        file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(b'x')
    paths, state = steam.proposed_paths(steam.find_games([library]))
    assert state['s3'] == 'ready' and 'storm3_eng' in paths and 'storm3_hd1' in paths and 'storm3_hd2' not in paths


def test_nothing_found_is_not_an_error(tmp_path):
    assert steam.find_games([tmp_path / 'nowhere']) == {}
    assert steam.proposed_paths({}) == ({}, {'s1': 'missing', 's2': 'missing', 's3': 'missing'})


def test_default_outputs_on_a_missing_drive_move_to_documents(tmp_path, monkeypatch):
    defaults = {'workspace': r'Q:\Localization\Naruto\Storm1\UA_Workspace', 'output': r'Q:\Localization\Naruto\Storm1\Build'}
    current = {'workspace': defaults['workspace'], 'output': r'D:\my\build'}       # the second one was customised
    monkeypatch.setattr(steam.Path, 'exists', lambda self: False)
    moved = steam.movable_outputs(current, defaults, ['workspace', 'output'], tmp_path / 'Docs')
    assert moved == {'workspace': str(tmp_path / 'Docs' / 'Naruto' / 'Storm1' / 'UA_Workspace')}


# ---- backups -------------------------------------------------------------------

def test_backup_keeps_the_newest_and_skips_unchanged(tmp_path, monkeypatch):
    txt = tmp_path / 'STORM1.ALL.UA.txt'
    manifest = tmp_path / 'STORM1.ALL.manifest.json'
    assert txtfmt.backup_project(txt) == {'created': None, 'kept': 0}
    manifest.write_text('{}', encoding='utf-8')
    stamps = iter(f'2026-01-01_00-00-{n:02d}' for n in range(60))
    monkeypatch.setattr(time, 'strftime', lambda fmt, *a: next(stamps))
    made = []
    for version in range(5):
        txt.write_text(f'version {version}', encoding='utf-8')
        result = txtfmt.backup_project(txt, keep=3)
        made.append(Path(result['created']))
        again = txtfmt.backup_project(txt, keep=3)              # unchanged TXT: no new copy
        assert again['created'] is None and again['same_as'] == result['created']
    kept = sorted(p.name for p in (tmp_path / 'backups').iterdir())
    assert kept == [p.name for p in made[-3:]]
    assert (made[-1] / txt.name).read_text(encoding='utf-8') == 'version 4'
    assert (made[-1] / manifest.name).is_file()


# ---- tag check -----------------------------------------------------------------

def test_tag_check_lists_every_problem(tmp_path):
    txt = tmp_path / 'ALL.txt'
    write(txt, ['#! comment',
                *record(1, '<red>Hello</red>', '<red>Привіт</red>'),                 # fine
                *record(2, '<red>Hello</red>', 'Привіт'),                            # tags lost
                *record(3, 'Press <btn_a>', 'Натисни <btn_b>'),                      # tag changed
                *record(4, '<a><b>', '<b><a>'),                                      # order changed
                *record(5, '<red>Empty</red>'),                                      # not translated: skipped
                *record(6, '<Destination>', '<Пункт призначення>'),                  # accepted localized heading
                *record(7, 'No tags', 'Без тегів')])
    lines = []
    result = txtfmt.check_tags(txt, {'<Destination>': '<Пункт призначення>'}, log=lines.append)
    assert list(result.values()) == [6, 3]
    assert [line.split(':')[0].split()[-1] for line in lines] == ['2', '3', '4']
    assert '<red> </red>' in lines[0] and '<btn_b>' in lines[1]
    limited = []
    txtfmt.check_tags(txt, log=limited.append, limit=1)
    assert len(limited) == 2 and '3' in limited[-1]                                   # one line + "and N more"
    skipped = txtfmt.check_tags(txt, skip=('S2.txt ·', 'S3.txt ·'))
    assert list(skipped.values()) == [4, 2]          # records 4 and 6: no accepted headings were passed


def test_tag_check_through_the_job_process(tmp_path):
    import json
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    txt = tmp_path / 'ALL.txt'
    write(txt, record(1, '<red>Hello</red>', 'Привіт'))
    spec = json.dumps({'lang': 'en', 'target': 'jobs.check_tags', 'args': [str(txt), 's2'], 'kwargs': {'log': '<<log>>'}})
    done = subprocess.run([sys.executable, str(root / 'jobs.py')], input=spec.encode(), capture_output=True, cwd=str(root))
    lines = [json.loads(line) for line in done.stdout.decode('utf-8').splitlines()]
    assert lines[0][0] == 'log' and 'record 1' in lines[0][1]
    assert lines[-1] == ['success', {'translated records checked': 1, 'records with different tags': 1}]
