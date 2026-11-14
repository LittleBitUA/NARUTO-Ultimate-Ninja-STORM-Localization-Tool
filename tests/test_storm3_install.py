from pathlib import Path
import hashlib
import json
import os
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import storm3_text as d


def make_game(tmp_path,standard=True,hd=True):
    game=tmp_path/'My STORM 3'
    game.mkdir()
    if standard:
        p=game/'data'/'disc'/'dataRegion.cpk'
        p.parent.mkdir(parents=True)
        p.write_bytes(b'example only')
    if hd:
        p=game/'dlc'/'NSUNS3.exe'
        p.parent.mkdir(parents=True)
        p.write_bytes(b'example only')
    return game


def test_dual_stage_font_install_restore(tmp_path):
    game=make_game(tmp_path)
    build=tmp_path/'Build'
    suffix=d.FONT_SUFFIX
    a=game/d.PREFIXES[0]/suffix
    b=game/d.PREFIXES[1]/suffix
    a.parent.mkdir(parents=True);a.write_bytes(b'old standard')
    assert not b.exists()
    package=d.prepare_bundle(build,suffix,b'new font')
    assert len(package['stage_paths'])==2
    assert (build/'STORM3_UA_LOOSE_OVERRIDES.zip').is_file()
    installed=d.install_staged(build,game,[suffix])
    assert installed['installed']==2
    assert a.read_bytes()==b'new font' and b.read_bytes()==b'new font'
    restored=d.restore_staged_last(build,game)
    assert restored['restored']==2
    assert a.read_bytes()==b'old standard' and not b.exists()


@pytest.mark.parametrize('standard,hd,expected',[(True,False,1),(False,True,1),(True,True,2)])
def test_detected_editions_only(tmp_path,standard,hd,expected):
    game=make_game(tmp_path,standard=standard,hd=hd)
    build=tmp_path/'Build'
    d.prepare_bundle(build,d.FONT_SUFFIX,b'new')
    report=d.install_staged(build,game,[d.FONT_SUFFIX])
    assert report['installed']==expected
    assert (game/'data_win32'/'ui'/'font'/'xfgame_cmn.fnt.xfbin').exists()==standard
    assert (game/'dlc'/'data_win32'/'ui'/'font'/'xfgame_cmn.fnt.xfbin').exists()==hd


def test_game_root_guard(tmp_path):
    build=tmp_path/'Build'
    d.prepare_bundle(build,d.FONT_SUFFIX,b'new')
    with pytest.raises(d.Storm3InstallError):
        d.install_staged(build,tmp_path,[d.FONT_SUFFIX])
    assert not (tmp_path/'data_win32').exists()


def test_abort_when_target_symlink(tmp_path):
    game=make_game(tmp_path)
    build=tmp_path/'Build'
    d.prepare_bundle(build,d.FONT_SUFFIX,b'new')
    external=tmp_path/'outside';external.mkdir()
    try:
        (game/'data_win32').symlink_to(external,target_is_directory=True)
    except OSError:
        pytest.skip('This Windows environment does not permit symlink creation')
    with pytest.raises(d.Storm3InstallError):
        d.install_staged(build,game,[d.FONT_SUFFIX])
    assert not list(external.iterdir())


def test_rollback_on_partial_failure(tmp_path,monkeypatch):
    game=make_game(tmp_path)
    build=tmp_path/'Build'
    suffix=d.FONT_SUFFIX
    targets=[game/p/suffix for p in d.PREFIXES]
    for target in targets:
        target.parent.mkdir(parents=True)
        target.write_bytes(b'old')
    d.prepare_bundle(build,suffix,b'new')
    original=d._atomic
    def fail_hd(path,data):
        if path==targets[1] and data==b'new':
            raise OSError('Simulated failed write')
        original(path,data)
    monkeypatch.setattr(d,'_atomic',fail_hd)
    with pytest.raises(d.Storm3InstallError):
        d.install_staged(build,game,[suffix])
    assert [t.read_bytes() for t in targets]==[b'old',b'old']


def test_changed_after_install_refuses_rollback(tmp_path):
    game=make_game(tmp_path)
    build=tmp_path/'Build'
    d.prepare_bundle(build,d.FONT_SUFFIX,b'new')
    d.install_staged(build,game,[d.FONT_SUFFIX])
    p=game/'data_win32'/d.FONT_SUFFIX
    p.write_bytes(b'external edit')
    with pytest.raises(d.Storm3InstallError):
        d.restore_staged_last(build,game)
    assert p.read_bytes()==b'external edit'
