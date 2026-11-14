"""STORM 1 additive font: the «я» blob regression (neighbour bleeding)."""
import io
import struct

from PIL import Image

import storm1_font as f


def _added_cells(blob, report):
    from xfbin import parse_xfbin
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / 'x.xfbin'; p.write_bytes(blob)
        c = parse_xfbin(p)
    body = c.blob[c.chunks[0].data_offset:c.chunks[0].end]
    count = struct.unpack_from('>I', body, 0x10)[0]
    ntp = f.FIRST_GLYPH_OFFSET + count * 32
    w, h = report['atlas_new']
    alpha = f.decode_bc3_alpha(body[ntp + 0x60:], w, h)
    cells = {}
    for i in range(count):
        v = struct.unpack_from('>8I', body, f.FIRST_GLYPH_OFFSET + i * 32)
        cells[v[5] & 0xFFFF] = (v[1] >> 16, v[1] & 0xFFFF)
    return alpha, w, h, cells


def test_synthetic_font_loads(storm1_font_file):
    doc = f.load_font(storm1_font_file)
    assert len(doc.glyphs) == 3683 and doc.width == 1024
    assert len(f.missing_ukrainian(doc)) == 66


def test_new_cells_are_isolated_in_packed_atlas(storm1_font_file, ttf):
    blob, report, preview = f.build_additive_font(storm1_font_file, ttf, latin_p_for_r=True,
                                                 latin_y_for_u=True, metrics_mode='native_matched')
    alpha, w, h, cells = _added_cells(blob, report)
    old_h = report['atlas_original'][1]
    ya = cells[ord('я')]; dash = cells[0x2014]
    assert ya[1] >= old_h and dash[1] >= old_h
    # «я» and the edge-to-edge dash copied from U+2015 are neighbours.
    assert dash[0] - ya[0] == f.PITCH and dash[1] == ya[1]
    # The dash really does touch its own cell edges...
    row = (dash[1] + 13) * w
    assert alpha[row + dash[0]] and alpha[row + dash[0] + f.SLOT - 1]
    # ...yet every texel between the two cells is transparent.
    for yy in range(ya[1] - 1, ya[1] + f.SLOT + 1):
        assert not any(alpha[yy * w + ya[0] + f.SLOT: yy * w + dash[0]])
    added = [(ch, *cells[ord(ch)]) for ch in report['added_characters']]
    assert f.check_cell_isolation(alpha, w, h, added) == []
    assert Image.open(io.BytesIO(preview)).size[0] >= 900


def test_isolation_check_detects_touching_neighbour():
    w = h = 64
    alpha = bytearray(w * h)
    for yy in range(4, 32):
        alpha[yy * w + 10] = 255      # ink inside cell A (x 4..31)
    alpha[20 * w + 32] = 255          # neighbour ink right at the border
    assert f.check_cell_isolation(bytes(alpha), w, h, [('я', 4, 4)]) != []


# ---- vertical placement: the lowercase «р» that looked like a capital ----
import pytest
from conftest import storm1_font_bytes


def _records(blob):
    at = blob.index(b'NFH\0') - 8
    body = blob[at:]
    count = struct.unpack_from('>I', body, 0x10)[0]
    table = {}
    for i in range(count):
        v = struct.unpack_from('>8I', body, f.FIRST_GLYPH_OFFSET + i * 32)
        table[v[5] & 0xFFFF] = (v[1] >> 16, v[1] & 0xFFFF, v[5] >> 24, (v[5] >> 16) & 255,
                                f._s16(v[2] >> 16), f._s16(v[3] >> 16), f._s16(v[2] & 0xFFFF))
    return table


@pytest.mark.parametrize('convention,sign', [('ascent', 1), ('offset', -1), ('flat', 0)])
def test_vertical_convention_is_learned_from_latin(tmp_path, convention, sign):
    path = tmp_path / 'font.xfbin'
    path.write_bytes(storm1_font_bytes(vertical=convention))
    doc = f.load_font(path)
    body = doc.blob[doc.body_offset:]
    image = Image.frombytes('L', (doc.width, doc.height), doc.alpha_bytes())
    model = f.infer_vertical_model(doc, image, body)
    assert model['sign'] == sign and model['decisive'] == (sign != 0)


@pytest.mark.parametrize('mode', ['stable', 'native_matched'])
def test_copied_p_keeps_its_own_placement(storm1_font_file, ttf, mode):
    blob, report, _ = f.build_additive_font(storm1_font_file, ttf, latin_p_for_r=True, latin_y_for_u=True,
                                            metrics_mode=mode, with_preview=False)
    table = _records(blob)
    assert table[ord('р')][4:] == table[ord('p')][4:]
    assert table[ord('у')][4:] == table[ord('y')][4:]
    assert table[0x2014][4:] == table[0x2015][4:]
    assert table[ord('р')][6] != table[ord('а')][6]      # not the template 'a' height any more


def test_preview_puts_descender_below_the_baseline(storm1_font_file, ttf):
    blob, report, _ = f.build_additive_font(storm1_font_file, ttf, latin_p_for_r=True, metrics_mode='stable',
                                            with_preview=False)
    alpha, w, h, _cells = _added_cells(blob, report)
    atlas = Image.frombytes('L', (w, h), alpha)
    png = f.render_ingame_preview(atlas, _records(blob), Image, lines=('nр',), scale=1, vertical_sign=1)
    shot = Image.open(io.BytesIO(png)).convert('L')

    def bottom(column):
        lit = [y for y in range(shot.height) if shot.getpixel((column, y)) > 128]
        return max(lit)
    # pad 14, bearing -7, ink columns 8..19 of the cell, advance 14
    assert bottom(34) - bottom(20) == 5
    flat = Image.open(io.BytesIO(f.render_ingame_preview(atlas, _records(blob), Image, lines=('nр',),
                                                          scale=1, vertical_sign=0))).convert('L')
    assert max(y for y in range(flat.height) if flat.getpixel((34, y)) > 128) == \
           max(y for y in range(flat.height) if flat.getpixel((20, y)) > 128)


def test_lifted_ttf_descender_is_compensated(storm1_font_file, ttf):
    blob, report, _ = f.build_additive_font(storm1_font_file, ttf, latin_p_for_r=False, latin_y_for_u=False,
                                            with_preview=False)
    table = _records(blob)
    fixed = report['vertical_compensated_for_lifted_glyphs']
    assert 'р' in fixed and 'а' not in fixed
    assert table[ord('р')][6] < table[ord('а')][6] == table[ord('a')][6]
