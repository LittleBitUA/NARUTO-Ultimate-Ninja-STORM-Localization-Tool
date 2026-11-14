"""STORM 3: independent structural checks for original EN + optional TTF.

No commercial assets bundled. To enable real regression:
  STORM3_FONT_LATIN_SAMPLE=/path/to/original.xfbin
  STORM3_FONT_RUSSIAN_SAMPLE=/path/to/russian.xfbin
  STORM3_FONT_TEST_TTF=/path/to/font.ttf
"""
import hashlib
import os
from pathlib import Path
import sys
import zipfile
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storm3_font import (Storm3FontBuildError,build_additive_font,generate_font_output,
                               load,_rel, OUTPUT_GLYPHS)
from storm1_font import decode_bc3_alpha


@pytest.mark.parametrize('input_path', ['/foo/../xfgame_cmn.fnt.xfbin', '../xfgame_cmn.fnt.xfbin',
                'C:/game/xfgame_cmn.fnt.xfbin', '/absolute/xfgame_cmn.fnt.xfbin',
                'ui/font/evil.xfbin'])
def test_no_path_traversal(input_path):
    with pytest.raises(Storm3FontBuildError):_rel(input_path)


def test_no_unsupported_font(tmp_path):
    p=tmp_path/'invalid.xfbin';p.write_bytes(b'notxfbin')
    with pytest.raises(Storm3FontBuildError):load(p)

A=os.environ.get('STORM3_FONT_LATIN_SAMPLE','')
B=os.environ.get('STORM3_FONT_RUSSIAN_SAMPLE','')
T=os.environ.get('STORM3_FONT_TEST_TTF','')
CAN_RUN=all(p and Path(p).is_file() for p in [A,T])


@pytest.mark.skipif(not CAN_RUN,reason='Game-owned EN XFBIN and user TTF not provided by environment')
def test_real_additive_font_structural():
    original=Path(A).read_bytes()
    russian=Path(B).read_bytes() if B and Path(B).is_file() else None
    data,report,preview=build_additive_font(A,T,russian_reference=B if russian else None)
    assert report['added_count']==66
    assert report['new_glyphs']==OUTPUT_GLYPHS
    assert report['atlas_after']==[512,512]
    assert report['original_bc3_blocks_preserved_exactly']
    assert report['roundtrip_parse_verified']
    assert report['engine_tested'] is False
    assert preview.startswith(b'\x89PNG\r\n\x1a\n')
    assert original==Path(A).read_bytes()
    if russian is not None:assert russian==Path(B).read_bytes()
    assert report['output_sha256']==hashlib.sha256(data).hexdigest()


@pytest.mark.skipif(not CAN_RUN,reason='Game-owned EN XFBIN and user TTF not provided by environment')
def test_output_paths_zip_and_no_overwrite(tmp_path):
    src=tmp_path/'from_game';src.mkdir()
    safe_input=src/'xfgame_cmn.fnt.xfbin';safe_input.write_bytes(Path(A).read_bytes())
    wr=tmp_path/'work';build=tmp_path/'build'
    result=generate_font_output(safe_input,T,wr,build, 'ui/font/xfgame_cmn.fnt.xfbin',
        russian_reference=B if B and Path(B).is_file() else None)
    assert Path(result['file']).is_file()
    with zipfile.ZipFile(result['zip']) as z:
        assert 'data_win32/ui/font/xfgame_cmn.fnt.xfbin' in z.namelist()
        assert z.read('data_win32/ui/font/xfgame_cmn.fnt.xfbin')==Path(result['file']).read_bytes()
    assert Path(result['preview']).exists()
    assert Path(result['report']).exists()
    assert safe_input.read_bytes()==Path(A).read_bytes()
    with pytest.raises(Storm3FontBuildError):
        generate_font_output(safe_input,T,wr,build, 'ui/font/xfgame_cmn.fnt.xfbin')
