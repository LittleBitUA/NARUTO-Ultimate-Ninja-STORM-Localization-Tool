"""STORM 3 font comparison regression; no proprietary test fixture is bundled."""
from __future__ import annotations
import os
from pathlib import Path
import sys
import json
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storm3_font import Storm3FontError, inspect, compare, run, PROFILE


def test_reject_non_nucc(tmp_path):
    p=tmp_path/'invalid.xfbin';p.write_bytes(b'NOT A FONT')
    with pytest.raises(Storm3FontError):inspect(p)


def test_missing_file(tmp_path):
    with pytest.raises(Storm3FontError):inspect(tmp_path/'missing.xfbin')


def test_comparison_basics_without_game_assets():
    g=lambda cp:{'unicode':f'U+{cp:04X}'}
    a={'sha256':'a','font_name':'Latin','glyphs':[g(65),g(66)]}
    b={'sha256':'b','font_name':'Cyr','glyphs':[g(66),g(0x410)]}
    c=compare(a,b)
    assert c['common_codepoints']==1
    assert c['only_a_codepoints']==['U+0041']
    assert c['only_b_codepoints']==['U+0410']
    assert not c['same_font_family']


A=os.environ.get('STORM3_FONT_LATIN_SAMPLE','')
B=os.environ.get('STORM3_FONT_RUSSIAN_SAMPLE','')
@pytest.mark.skipif(not (A and B and Path(A).is_file() and Path(B).is_file()),reason='Optional game-owned font samples not configured')
def test_real_samples_are_only_read(tmp_path):
    before={p:Path(p).read_bytes() for p in [A,B]}
    a,b=inspect(A),inspect(B)
    assert a['profile']==b['profile']==PROFILE
    assert a['glyph_count']==211 and b['glyph_count']==178
    assert a['cyrillic_glyphs']==0 and b['cyrillic_glyphs']==66
    assert b['missing_ukrainian']=='ҐЄІЇґєії'
    assert b['blank_alpha_below_bbox'] is True
    assert b['unused_rows_by_bbox']==62
    report=run(A,B,tmp_path/'output')
    assert Path(report['json']).exists()
    assert Path(report['tsv']).exists()
    assert Path(report['md']).exists()
    stored=json.loads(Path(report['json']).read_text('utf-8'))
    assert stored['common_codepoints']==112
    assert all(Path(p).read_bytes()==original for p,original in before.items())
