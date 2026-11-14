# -*- coding: utf-8 -*-
"""Run: py -3 tests/test_font_dash_y_v21.py "<original_xfgame_cmn.fnt.xfbin>" "<user_ReggaeC-UA.ttf>"

Read-only regression test: validates U+2014, U+0443, U+0440 and all 3683
source glyphs. Never installs or modifies the source font or user's TTF.
"""
from pathlib import Path
import struct
import os
import pytest
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storm1_font import build_additive_font, FIRST_GLYPH_OFFSET
from xfbin import parse_xfbin
from storm1_font import load_font


@pytest.fixture
def source():
    path=Path(os.environ.get('STORM1_FONT_TEST_FILE',''))
    if not path.is_file():
        pytest.skip('Set STORM1_FONT_TEST_FILE to a valid original font XFBIN')
    return path

@pytest.fixture
def ttf():
    path=Path(os.environ.get('STORM1_FONT_TTF',''))
    if not path.is_file():
        pytest.skip('Set STORM1_FONT_TTF to a TTF supporting Ukrainian')
    return path


def test(source: Path,ttf: Path):
    source_doc=load_font(source)
    source_container=parse_xfbin(source)
    original_body=source_container.blob[source_container.chunks[0].data_offset:source_container.chunks[0].end]
    original_codes={g.codepoint:g for g in source_doc.glyphs}
    assert 0x2014 not in original_codes and 0x2015 in original_codes
    blob,report,_=build_additive_font(source,ttf,
        include_symbols=True,include_other_symbols=False,
        latin_p_for_r=True,latin_y_for_u=True,
        metrics_mode='native_matched',tracking_px=0)
    assert report['added_characters'].count('—')==1
    assert report['optional_chars_added']=='—',report
    assert report['existing_glyphs_page_metadata_updated']==14,report
    assert report['added_glyph_count']==67,report
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        patched=Path(tmp)/'output.xfbin'
        patched.write_bytes(blob)
        container=parse_xfbin(patched)
        body=container.blob[container.chunks[0].data_offset:container.chunks[0].end]
        count=struct.unpack_from('>I',body,0x10)[0]
        recs={}
        for i in range(count):
            row=body[FIRST_GLYPH_OFFSET+i*32:FIRST_GLYPH_OFFSET+(i+1)*32]
            cp=struct.unpack_from('>I',row,20)[0]&0xffff
            recs[cp]=row
        assert len(recs)==3750
        for cp in (0x2014,0x0443,0x0440):
            assert cp in recs,hex(cp)
        original_bytes=original_body[0x458:0x458+3683*32]
        for g in source_doc.glyphs:
            old=original_bytes[g.index*32:(g.index+1)*32]
            got=recs[g.codepoint]
            if g.codepoint>>8==0x20:
                assert got[:24]==old[:24] and got[28:]==old[28:],hex(g.codepoint)
            else:
                assert got==old,hex(g.codepoint)
        ntp=FIRST_GLYPH_OFFSET+count*32
        pixels=body[ntp+0x60:]
        originalpixels=original_body[source_doc.ntp_offset+0x60:]
        assert pixels.startswith(originalpixels)
    print('PASS: 67 new glyphs (66 Ukrainian letters + U+2014); 14 page-index updates;')
    print('      original 3683 outlines and BC3 blocks unchanged; U+0443 -> native y, U+0440 -> native p')

if __name__=='__main__':
    if len(sys.argv)!=3:
        sys.exit('Використання: py -3 tests/test_font_dash_y_v21.py "<original_font.xfbin>" "<ReggaeC-UA.ttf>"')
    test(Path(sys.argv[1]),Path(sys.argv[2]))
