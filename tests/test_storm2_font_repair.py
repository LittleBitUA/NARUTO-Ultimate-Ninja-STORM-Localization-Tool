# -*- coding: utf-8 -*-
"""Regression for black Cyrillic, apostrophe spacing and tall Ґ/ґ (v2.8.2)."""
from __future__ import annotations
import os
import sys
import struct
import tempfile
import unittest
from pathlib import Path

from storm2_font import (load_font,build_additive_font,repair_existing_font,
                         export_font,build_font,BC3_OPAQUE_WHITE,FontError)
from xfbin import parse_xfbin

ORIG=Path(os.environ.get('STORM2_FONT_TEST_SOURCE','/mnt/data/xfgame_cmn.fnt(3).xfbin'))
OLD=Path(os.environ.get('STORM2_FONT_OLD_BAD','/mnt/data/STORM2_ADDITIVE_TEST_ONLY.xfbin'))
TTF=Path(os.environ.get('STORM2_FONT_TEST_TTF','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))

def check_opaque_white(doc, start=288):
    raw=doc.blob[doc.pixel_offset:doc.pixel_offset+doc.width*doc.height]
    al=doc.alpha_bytes();w=doc.width;n=0
    for y in range(start,doc.height,4):
        for x in range(0,w,4):
            if any(al[(y+dy)*w+x+dx] > 60 for dy in range(4) for dx in range(4)):
                off=(y//4*(w//4)+x//4)*16
                assert raw[off+8:off+16]==BC3_OPAQUE_WHITE,(x,y,raw[off+8:off+16].hex())
                n+=1
    return n

def metrics(doc,cp):
    g=next(g for g in doc.glyphs if g.codepoint==cp)
    rec=struct.unpack_from('>8I',doc.blob,doc.body_offset+0x458+g.index*32)
    return g,rec

@unittest.skipUnless(ORIG.is_file() and TTF.is_file(),'Need original + installed TTF for fixture')
class TestColorFix(unittest.TestCase):
    def test_generation_white_rgb_and_native_metrics(self):
        old=ORIG.read_bytes()
        blob,r,_=build_additive_font(ORIG,TTF)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'generated.xfbin';p.write_bytes(blob);d=load_font(p)
            self.assertEqual(len(d.glyphs),295)
            self.assertGreater(check_opaque_white(d),1000)
            self.assertEqual((metrics(d,0x490)[0].height,metrics(d,0x491)[0].height),(22,16))
            self.assertEqual(metrics(d,0x2019)[1][3]>>16,metrics(d,0x27)[1][3]>>16)
            self.assertEqual(d.alpha_bytes()[:288*512],load_font(ORIG).alpha_bytes()[:288*512])
            self.assertEqual(ORIG.read_bytes(),old)

    def test_original_noop_unchanged(self):
        original=load_font(ORIG)
        with tempfile.TemporaryDirectory() as td:
            export_font(original,td)
            b,r=build_font(original,td)
            self.assertEqual(b,original.blob)
            self.assertTrue(r['byte_identical'])

    @unittest.skipUnless(OLD.is_file(),'Prior broken additive fixture absent')
    def test_repair_old_black_additive_without_ttf(self):
        original=OLD.read_bytes()
        fixed,r,_=repair_existing_font(OLD)
        self.assertGreater(r['fixed_bc3_color_blocks'],1000)
        self.assertEqual(r['modified_alpha_blocks'],29)
        self.assertTrue(r['apostrophe_advance_narrowed'])
        self.assertEqual(OLD.read_bytes(),original)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'fixed.xfbin';p.write_bytes(fixed);d=load_font(p)
            self.assertEqual(len(d.glyphs),295)
            self.assertGreater(check_opaque_white(d),1000)
            self.assertEqual((metrics(d,0x490)[0].height,metrics(d,0x491)[0].height),(22,16))
            self.assertEqual(metrics(d,0x2019)[1][3]>>16,10*64)
            a=load_font(OLD)
            self.assertEqual(d.alpha_bytes()[:288*512],a.alpha_bytes()[:288*512])
            self.assertEqual(fixed[d.pixel_offset+288*512:],fixed[d.pixel_offset+288*512:])

    def test_original_repair_rejected(self):
        with self.assertRaises(FontError):repair_existing_font(ORIG)

if __name__=='__main__':unittest.main()
