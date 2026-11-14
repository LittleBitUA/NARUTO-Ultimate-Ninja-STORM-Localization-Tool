# -*- coding: utf-8 -*-
"""STORM 2 uploaded-font roundtrip and defensive repack regression.
Run: python -m unittest discover -s tests -p 'test_storm2_font.py' -v
May use STORM2_FONT_TEST_SOURCE and STORM2_FONT_TEST_TTF env vars.
"""
import csv
import os
import tempfile
import unittest
from pathlib import Path

from storm2_font import (load_font,analyze_font,export_font,build_font,build_additive_font,
                         FontError,CSV_FILE,ATLAS_FILE,missing_ukrainian)

DEFAULT_SOURCE='/mnt/data/xfgame_cmn.fnt(2).xfbin'
DEFAULT_TTF='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

@unittest.skipUnless(Path(os.environ.get('STORM2_FONT_TEST_SOURCE',DEFAULT_SOURCE)).is_file(),
                     'STORM2_FONT_TEST_SOURCE is required for local fixture test')
class TestStorm2Font(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=Path(os.environ.get('STORM2_FONT_TEST_SOURCE',DEFAULT_SOURCE))
        cls.doc=load_font(cls.source)

    def test_parse_original(self):
        self.assertEqual(len(self.doc.glyphs),229)
        self.assertEqual((self.doc.width,self.doc.height),(512,512))
        self.assertEqual(len(missing_ukrainian(self.doc)),66)
        self.assertEqual(analyze_font(self.source)['font_name'],'FOT-Reggae Std')

    def test_export_rebuild_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            export_font(self.doc,tmp)
            out,report=build_font(self.doc,tmp)
            self.assertEqual(out,self.source.read_bytes())
            self.assertTrue(report['byte_identical'])

    def test_unicode_relabel_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            export_font(self.doc,tmp)
            tsv=Path(tmp)/CSV_FILE
            with tsv.open('r',encoding='utf-8-sig',newline='') as fp:
                rows=list(csv.DictReader(fp,delimiter='\t'))
                headings=list(rows[0].keys())
            self.assertEqual(rows[-1]['original_unicode'],'U+FF65')
            rows[-1]['new_unicode']='U+0491'  # test only: remap U+FF65
            with tsv.open('w',encoding='utf-8-sig',newline='') as fp:
                w=csv.DictWriter(fp,fieldnames=headings,delimiter='\t');w.writeheader();w.writerows(rows)
            data,report=build_font(self.doc,tmp)
            self.assertNotEqual(data,self.doc.blob)
            self.assertEqual(report['remapped_glyphs'],1)
            with tempfile.TemporaryDirectory() as td:
                p=Path(td)/'unicode_test.xfbin';p.write_bytes(data)
                cp={g.codepoint for g in load_font(p).glyphs}
                self.assertIn(0x0491,cp)
                self.assertNotIn(0xFF65,cp)

    def test_png_modified_blocks_only(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            export_font(self.doc,tmp)
            png=Path(tmp)/ATLAS_FILE
            with Image.open(png) as im:
                im=im.convert('RGBA')
            im.putpixel((20,350),(255,255,255,255))
            im.save(png)
            data,report=build_font(self.doc,tmp)
            self.assertGreater(report['modified_bc3_blocks'],0)
            self.assertEqual(report['remapped_glyphs'],0)
            with tempfile.TemporaryDirectory() as td:
                p=Path(td)/'png_test.xfbin';p.write_bytes(data)
                doc=load_font(p)
                self.assertEqual([x.codepoint for x in doc.glyphs],[x.codepoint for x in self.doc.glyphs])
                self.assertNotEqual(doc.alpha_bytes(),self.doc.alpha_bytes())

    def test_manifest_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            export_font(self.doc,tmp)
            with (Path(tmp)/'font_manifest.json').open('r+',encoding='utf-8') as f:
                data=f.read().replace(self.doc.digest,'0'*64)
                f.seek(0);f.write(data);f.truncate()
            with self.assertRaises(FontError):build_font(self.doc,tmp)

    def test_storm1_loader_rejects_storm2(self):
        from storm1_font import load_font as storm1_load
        with self.assertRaises(FontError):storm1_load(self.source)

    @unittest.skipUnless(Path(os.environ.get('STORM2_FONT_TEST_TTF',DEFAULT_TTF)).is_file(),
                         'Need local TTF for additive test')
    def test_additive_unchanged_source_glyphs(self):
        ttf=Path(os.environ.get('STORM2_FONT_TEST_TTF',DEFAULT_TTF))
        blob,report,preview=build_additive_font(self.source,ttf)
        self.assertEqual(report['added_count'],66)
        self.assertEqual(report['new_glyphs'],295)
        self.assertTrue(preview)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.xfbin';path.write_bytes(blob)
            d=load_font(path)
            self.assertEqual(d.width*d.height,512*512)
            self.assertEqual(missing_ukrainian(d),'')
            self.assertEqual(d.alpha_bytes()[:288*512],self.doc.alpha_bytes()[:288*512])
            self.assertEqual(len(d.glyphs),295)

if __name__=='__main__':unittest.main()
