"""Shared fixtures: synthetic game files, so the suite runs without game data."""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TTF_CANDIDATES = (r'C:\Windows\Fonts\arial.ttf', '/System/Library/Fonts/Supplemental/Arial.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')


def make_xfbin(chunks):
    """chunks: [(type_name, body_bytes)] -> minimal valid NUCC 104 container."""
    types = [t for t, _ in chunks]
    tables = [b''.join(x.encode() + b'\0' for x in types), b'font.fnt\0', b'a\0']
    maps = b''.join(struct.pack('>III', i, 0, 0) for i in range(len(chunks)))
    index = b''.join(struct.pack('>I', i) for i in range(len(chunks)))
    head = b'NUCC' + struct.pack('>I', 104) + bytes(20)
    head += struct.pack('>10I', len(types), len(tables[0]), 1, len(tables[1]), 1, len(tables[2]),
                        len(chunks), len(maps), len(chunks), 0)
    head += b''.join(tables)
    head += bytes((-len(head)) % 4) + maps + index
    out = bytearray(head)
    for i, (_, body) in enumerate(chunks):
        out += struct.pack('>IIHH', len(body), i, 0x79, 0) + body
    return bytes(out)


DESCENDERS = 'gjpqy'
# vertical value (px) of a baseline letter / a descender letter, per convention
VERTICAL = {'ascent': (24, 19), 'offset': (2, 7), 'flat': (0, 0)}


def storm1_font_bytes(edge_dash=True, vertical='ascent'):
    """3683-glyph STORM 1 style NFH/NTP3 font; U+2015 ink touches both cell edges.

    Like the real font, letters with a descender are stored 5 rows higher in
    their cell and carry a different vertical value in word2 low16.
    """
    from storm1_font import _original_lookup, _encode_bc3_alpha
    codes = list(range(0x20, 0x7F)) + [0x2015, 0x2018, 0x2019]
    codes += list(range(0x4E00, 0x4E00 + 3683 - len(codes)))
    width, cols = 1024, 32
    height = ((len(codes) + cols - 1) // cols) * 32
    alpha = bytearray(width * height)
    records = []
    pages = {}
    for cp in codes:
        pages.setdefault(cp >> 8, []).append(cp)
    for i, cp in enumerate(codes):
        x, y = (i % cols) * 32, (i // cols) * 32
        if cp == 0x2015 and edge_dash:
            for xx in range(28):
                for yy in (13, 14):
                    alpha[(y + yy) * width + x + xx] = 255
        elif cp != 0x20:
            for yy in range(1 if chr(cp) in DESCENDERS else 6, 24):
                for xx in range(8, 20):
                    alpha[(y + yy) * width + x + xx] = 255
        page = pages[cp >> 8]
        local = page.index(cp)
        up = VERTICAL[vertical][1 if chr(cp) in DESCENDERS else 0] * 64
        records.append(struct.pack('>8I', 0x11110001, (x << 16) | y, (((-7 * 64) & 0xFFFF) << 16) | up,
                                   (21 * 64) << 16, 0x700, (28 << 24) | (28 << 16) | cp,
                                   (local << 24) | ((len(page) - local - 1) << 16) | 0x4000, 0))
    pixels = bytearray()
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            block = [alpha[(by + dy) * width + bx + dx] for dy in range(4) for dx in range(4)]
            pixels += _encode_bc3_alpha(block) + b'\xff\xff\x00\x00\x00\x00\x00\x00'
    size = width * height
    ntp = bytearray(0x60)
    ntp[0:4] = b'NTP3'; ntp[0x40:0x44] = b'eXt\0'; ntp[0x50:0x54] = b'GIDX'
    struct.pack_into('>I', ntp, 0x10, size + 0x50)
    struct.pack_into('>I', ntp, 0x18, size)
    struct.pack_into('>I', ntp, 0x24, (width << 16) | height)
    head = bytearray(0x458)
    ntp_at = 0x458 + len(codes) * 32
    struct.pack_into('>I', head, 0, ntp_at - 8)
    struct.pack_into('>I', head, 4, 0x60 + size)
    head[8:12] = b'NFH\0'
    struct.pack_into('>I', head, 0x0C, 28)
    struct.pack_into('>I', head, 0x10, len(codes))
    head[0x58:0x458] = _original_lookup(codes)
    body = bytes(head) + b''.join(records) + bytes(ntp) + bytes(pixels)
    return make_xfbin([('nuccChunkFont', body), ('nuccChunkPage', struct.pack('>II', 2, 0))])


@pytest.fixture(scope='session')
def ttf():
    for name in TTF_CANDIDATES:
        if Path(name).is_file():
            return Path(name)
    pytest.skip('No system TTF with Cyrillic found')


@pytest.fixture(scope='session')
def storm1_font_file(tmp_path_factory):
    path = tmp_path_factory.mktemp('font1') / 'xfgame_cmn.fnt.xfbin'
    path.write_bytes(storm1_font_bytes())
    return path
