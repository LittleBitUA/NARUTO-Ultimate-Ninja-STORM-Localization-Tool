# -*- coding: utf-8 -*-
"""STORM 2 font (NFH 0x11110002, 512x512 BC3): export, rebuild, additive Ukrainian glyphs, repair."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import hashlib
import io
import json
import os
import shutil
import struct
import tempfile
import zipfile

from xfbin import parse_xfbin
from storm1_font import (FontError, decode_bc3_alpha, png_of_alpha, _edited_alpha_png, _encode_bc3_alpha,
    render_preview, relative_font_path, clean_rel, UKRAINIAN_ALPHABET, _libraries, _calibrate_pixel_size, _font_cmap, _draw_glyph)
from i18n import T


# ============================================================================
# font
# ============================================================================

PROFILE = 'STORM2_NUCC104_NFH_11110002_512_BC3'
ORIGINAL_GLYPH_COUNT = 229
RECORD_BASE = 0x458
RECORD_SIZE = 32
LOOKUP_EMPTY = 0x80000001
ATLAS_FILE = 'xfgame_cmn_atlas.png'
PREVIEW_FILE = 'xfgame_cmn_preview.png'
CSV_FILE = 'font_glyphs.tsv'
MANIFEST_FILE = 'font_manifest.json'
DDS_FILE = 'xfgame_cmn_atlas.dds'
FONT_FORMAT = 'storm2-font-v1'
MAX_GLYPHS = 512
# The original BC3 atlas stores BLACK RGB in unused cells. Alpha-only edits
# make newly added glyphs visibly black even though their PNG preview is white.
BC3_OPAQUE_WHITE = struct.pack('<HHI', 0xFFFF, 0xFFFF, 0)
NATIVE_APOSTROPHE_ADVANCE = 0x0280  # 10 px in 1/64-pixel fixed point


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def be32(data:bytes, at:int) -> int:
    return struct.unpack_from('>I',data,at)[0]


def _lookup(cps:list[int])->bytes:
    lookup=[LOOKUP_EMPTY]*256
    for idx,cp in enumerate(cps):
        page=cp>>8
        if lookup[page]==LOOKUP_EMPTY:
            lookup[page]=(idx-(cp&255))&0xFFFFFFFF
    return struct.pack('>256I',*lookup)


def _page_metadata(cps:list[int]) -> dict[int,int]:
    pages={}
    for cp in cps:pages.setdefault(cp>>8,[]).append(cp)
    out={}
    for page,values in pages.items():
        if len(values)>256:raise FontError(T(f'Сторінка Unicode U+{page:02X}xx переповнена.',f'Unicode page U+{page:02X}xx has too many glyphs.'))
        for local,cp in enumerate(values):
            out[cp]=(local<<24)|((len(values)-local-1)<<16)
    return out


@dataclass(frozen=True)
class Glyph:
    index:int
    codepoint:int
    x:int
    y:int
    width:int
    height:int


@dataclass
class FontDoc:
    path:Path
    blob:bytes
    digest:str
    body_offset:int
    ntp_offset:int
    pixel_offset:int
    width:int
    height:int
    glyphs:list[Glyph]

    def alpha_bytes(self):
        return decode_bc3_alpha(self.blob[self.pixel_offset:self.pixel_offset+self.width*self.height],self.width,self.height)


def load_font(path:Path|str)->FontDoc:
    path=Path(path)
    if not path.is_file():raise FontError(T('Немає файлу шрифту STORM 2.','The STORM 2 font file was not found.'))
    if path.stat().st_size>16_000_000:raise FontError(T('Файл шрифту завеликий.','The font file is too large.'))
    c=parse_xfbin(path)
    if c.nucc_version!=104 or len(c.chunks)!=2 or [k.type_name for k in c.chunks]!=['nuccChunkFont','nuccChunkPage']:
        raise FontError(T('Шрифт STORM 2 повинен мати NUCC 104, nuccChunkFont і nuccChunkPage.','A STORM 2 font must have NUCC 104, nuccChunkFont and nuccChunkPage.'))
    ck=c.chunks[0];d=c.blob[ck.data_offset:ck.end]
    if len(d)<RECORD_BASE+ORIGINAL_GLYPH_COUNT*RECORD_SIZE+0x60 or d[8:12]!=b'NFH\0' or be32(d,0x0C)!=28:
        raise FontError(T('Невідома структура NFH STORM 2.','Unknown STORM 2 NFH structure.'))
    count=be32(d,0x10)
    if not ORIGINAL_GLYPH_COUNT<=count<=MAX_GLYPHS:raise FontError(T('Невідома кількість гліфів STORM 2.','Unknown STORM 2 glyph count.'))
    ntp=RECORD_BASE+count*RECORD_SIZE
    if (be32(d,0)!=ntp-8 or be32(d,4)!=len(d)-ntp or
        d[ntp:ntp+4]!=b'NTP3' or d[ntp+0x40:ntp+0x44]!=b'eXt\0' or
        d[ntp+0x50:ntp+0x54]!=b'GIDX'):
        raise FontError(T('Пошкоджена структура NFH/NTP3 або невідповідні довжини.','The NFH/NTP3 structure is damaged or the lengths do not match.'))
    packed=be32(d,ntp+0x24);width,height=packed>>16,packed&65535
    if (width,height)!=(512,512):raise FontError(T('Непідтримуваний атлас STORM 2: очікується 512×512.','Unsupported STORM 2 atlas: 512×512 is expected.'))
    size=width*height
    if be32(d,ntp+0x10)!=size+0x50 or be32(d,ntp+0x18)!=size or len(d)-(ntp+0x60)!=size:
        raise FontError(T('Невідома текстура шрифту (очікується один BC3/DXT5).','Unknown font texture (a single BC3/DXT5 is expected).'))
    glyphs=[]
    for idx in range(count):
        v=struct.unpack_from('>8I',d,RECORD_BASE+idx*RECORD_SIZE)
        cp=v[5]&0xFFFF; x=v[1]>>16; y=v[1]&0xFFFF; w=v[5]>>24;h=(v[5]>>16)&0xFF
        if (v[0]!=0x11110002 or v[7]!=0 or not 1<=cp<=0xFFFF or
            0xD800<=cp<=0xDFFF or w>64 or h>64 or x+w>width or y+h>height):
            raise FontError(T(f'Гліф STORM 2 №{idx}: невідома сигнатура або координати.',f'STORM 2 glyph #{idx}: unknown signature or coordinates.'))
        glyphs.append(Glyph(idx,cp,x,y,w,h))
    codes=[g.codepoint for g in glyphs]
    if codes!=sorted(set(codes)):raise FontError(T('Гліфи не відсортовані або Unicode повторюється.','The glyphs are not sorted or a Unicode value is repeated.'))
    if d[0x58:RECORD_BASE]!=_lookup(codes):raise FontError(T('Некоректний Unicode lookup (профіль STORM 2).','Invalid Unicode lookup (STORM 2 profile).'))
    meta=_page_metadata(codes)
    for g in glyphs:
        if be32(d,RECORD_BASE+g.index*RECORD_SIZE+24)!=meta[g.codepoint]:
            raise FontError(T(f'Пошкоджено індекс сторінки у гліфі {g.index}.',f'The page index in glyph {g.index} is damaged.'))
    return FontDoc(path,c.blob,c.sha256,ck.data_offset,ntp,ck.data_offset+ntp+0x60,width,height,glyphs)


def missing_ukrainian(doc:FontDoc)->str:
    present={x.codepoint for x in doc.glyphs}
    return ''.join(ch for ch in UKRAINIAN_ALPHABET if ord(ch) not in present)


def analyze_font(path:Path|str)->dict:
    doc=load_font(path)
    return {'profile':PROFILE,'source':doc.path.name,'source_sha256':doc.digest,
            'glyph_count':len(doc.glyphs),'atlas':[doc.width,doc.height],
            'font_name':_font_name(doc),'missing_ukrainian':missing_ukrainian(doc),
            'occupied_glyph_bottom':max(g.y+g.height for g in doc.glyphs),
            'note':'STORM 2: glyph v0x11110002, 229 glyphs on original; NOT interchangeable with STORM 1.'}


def _font_name(doc:FontDoc)->str:
    b=doc.blob[doc.body_offset+0x18:doc.body_offset+0x38]
    return b.partition(b'\0')[0].decode('ascii',errors='replace')


def _dds(doc:FontDoc)->bytes:
    raw=doc.blob[doc.pixel_offset:doc.pixel_offset+doc.width*doc.height]
    nums=[124,0x81007,doc.height,doc.width,len(raw),0,1]+[0]*11+[32,4,struct.unpack('<I',b'DXT5')[0],0,0,0,0,0]+[0x1000,0,0,0,0]
    return b'DDS '+struct.pack('<31I',*nums)+raw


def export_font(doc:FontDoc,workspace:Path|str,log=None)->dict:
    work=Path(workspace)
    if not str(work).strip():raise FontError(T('Укажіть робочу папку.','Specify the workspace folder.'))
    work.mkdir(parents=True,exist_ok=True)
    paths=[work/n for n in [CSV_FILE,MANIFEST_FILE,ATLAS_FILE,DDS_FILE,PREVIEW_FILE]]
    if any(x.exists() for x in paths):
        raise FontError(T('У папці вже є файли шрифту. Для експорту оберіть нову порожню папку.','The folder already contains font files. Choose a new empty folder for the export.'))
    alpha=doc.alpha_bytes();png=png_of_alpha(alpha,doc.width,doc.height)
    (work/ATLAS_FILE).write_bytes(png)
    (work/PREVIEW_FILE).write_bytes(render_preview(alpha,doc.width,doc.height))
    (work/DDS_FILE).write_bytes(_dds(doc))
    with (work/CSV_FILE).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f,delimiter='\t')
        w.writerow(('index','original_unicode','new_unicode','character','x','y','width','height','note'))
        for g in doc.glyphs:
            ch=chr(g.codepoint)
            if ch in '\r\n\t':ch=''
            w.writerow((g.index,f'U+{g.codepoint:04X}',f'U+{g.codepoint:04X}',ch,g.x,g.y,g.width,g.height,''))
    manifest={'format':FONT_FORMAT,'source_sha256':doc.digest,'source_file':doc.path.name,
              'source_glyph_count':len(doc.glyphs),'atlas_dimensions':[doc.width,doc.height],
              'original_atlas_sha256':sha(alpha),'original_png_sha256':sha(png),
              'profile':PROFILE,'notes':'Only new_unicode and RGBA alpha can be changed; original source is immutable.'}
    (work/MANIFEST_FILE).write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if log:log(T(f'Експортовано {len(doc.glyphs)} гліфів і PNG {doc.width}×{doc.height}.',f'Exported {len(doc.glyphs)} glyphs and a {doc.width}×{doc.height} PNG.'))
    return {'folder':str(work),'glyphs':len(doc.glyphs),'atlas':[doc.width,doc.height],
            'missing_ukrainian':missing_ukrainian(doc)}


def _work_codes(doc:FontDoc,work:Path)->tuple[list[int],dict]:
    man=json.loads((work/MANIFEST_FILE).read_text(encoding='utf-8'))
    if (man.get('format')!=FONT_FORMAT or man.get('source_sha256')!=doc.digest or
        man.get('source_glyph_count')!=len(doc.glyphs) or
        man.get('original_atlas_sha256')!=sha(doc.alpha_bytes())):
        raise FontError(T('Маніфест не відповідає оригінальному файлу STORM 2.','The manifest does not match the original STORM 2 file.'))
    with (work/CSV_FILE).open('r',encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f,delimiter='\t'))
    if len(rows)!=len(doc.glyphs):raise FontError(T('Кількість гліфів у TSV змінена.','The number of glyphs in the TSV was changed.'))
    codes=[]
    for g,row in zip(doc.glyphs,rows):
        for key,want in [('index',str(g.index)),('original_unicode',f'U+{g.codepoint:04X}'),
                         ('x',str(g.x)),('y',str(g.y)),('width',str(g.width)),('height',str(g.height))]:
            if row.get(key)!=want:raise FontError(T(f'Змінено захищене поле {key} у гліфі №{g.index}.',f'The protected field {key} was changed in glyph #{g.index}.'))
        value=(row.get('new_unicode') or '').strip().upper()
        if not value.startswith('U+'):
            raise FontError(T(f'Недійсний Unicode у гліфі №{g.index}.',f'Invalid Unicode in glyph #{g.index}.'))
        try:cp=int(value[2:],16)
        except ValueError as exc:raise FontError(T('Недійсний новий Unicode.','Invalid new Unicode.')) from exc
        if cp<=0 or cp>65535 or 0xD800<=cp<=0xDFFF:raise FontError(T('Unicode має бути BMP (без сурогатів).','Unicode must be in the BMP (no surrogates).'))
        codes.append(cp)
    if len(set(codes))!=len(codes):raise FontError(T('Після заміни з’явилося дублювання Unicode.','The replacement produced duplicate Unicode values.'))
    return codes,man


def _recode_document(doc:FontDoc, all_records:dict[int,bytes], raw_atlas:bytes)->bytes:
    """Preserve original XFBIN metadata, NTP header and non-font chunks byte-for-byte."""
    original=parse_xfbin(doc.path);ck=original.chunks[0]
    body=original.blob[ck.data_offset:ck.end]
    cps=sorted(all_records)
    if len(cps)>MAX_GLYPHS:raise FontError(T('Забагато Unicode-гліфів.','Too many Unicode glyphs.'))
    meta=_page_metadata(cps)
    records=[]
    for cp in cps:
        rec=bytearray(all_records[cp])
        struct.pack_into('>H',rec,22,cp)
        struct.pack_into('>I',rec,24,meta[cp])
        records.append(rec)
    head=bytearray(body[:RECORD_BASE])
    new_ntp=RECORD_BASE+len(cps)*RECORD_SIZE
    struct.pack_into('>I',head,0,new_ntp-8)
    struct.pack_into('>I',head,4,len(body)-doc.ntp_offset)
    struct.pack_into('>I',head,0x10,len(cps))
    head[0x58:RECORD_BASE]=_lookup(cps)
    # Same NTP3 size and texture dimensions, only its position shifts.
    ntp_blob=body[doc.ntp_offset:doc.ntp_offset+0x60]+raw_atlas
    newbody=bytes(head)+b''.join(records)+ntp_blob
    prefix=bytearray(doc.blob[:ck.data_offset])
    struct.pack_into('>I',prefix,ck.header_offset,len(newbody))
    return bytes(prefix)+newbody+doc.blob[ck.end:]


def _patch_alpha(doc:FontDoc,alpha:bytes, *,must_be_unused_y:int|None=None)->tuple[bytes,int]:
    old_alpha=doc.alpha_bytes();raw=bytearray(doc.blob[doc.pixel_offset:doc.pixel_offset+doc.width*doc.height])
    if len(alpha)!=len(old_alpha):raise FontError(T('Розмір декодованого атласу змінився.','The size of the decoded atlas has changed.'))
    changes=0;bxcount=doc.width//4
    for by in range(doc.height//4):
        for bx in range(bxcount):
            x=bx*4;y=by*4;old=[];new=[]
            for dy in range(4):
                pos=(y+dy)*doc.width+x
                old+=old_alpha[pos:pos+4];new+=alpha[pos:pos+4]
            if old==new:continue
            if must_be_unused_y is not None and y<must_be_unused_y:
                raise FontError(T('Спроба змінити вихідні BC3-блоки старих гліфів.','Attempt to change the original BC3 blocks of the old glyphs.'))
            if must_be_unused_y is not None and any(old):
                raise FontError(T('Додаткові гліфи перекрили непорожні пікселі оригіналу.','The added glyphs overlap non-empty pixels of the original.'))
            offset=(by*bxcount+bx)*16
            raw[offset:offset+8]=_encode_bc3_alpha(new)
            # STORM 2 stores BOTH alpha and RGB in BC3. Original empty blocks
            # have RGB565 nearly black (0x0001); when alpha is added without
            # changing those 8 RGB bytes, the resulting Cyrillic is DARK.
            # Encode opaque white for the new glyph area, preserving all other
            # BC3 blocks byte-identically.
            if any(new):
                raw[offset+8:offset+16]=BC3_OPAQUE_WHITE
            changes+=1
    return bytes(raw),changes


def _fix_apostrophe_advance(rec:bytes, donor:bytes)->bytes:
    """Use normal apostrophe's narrow advance for U+2019, preserving glyph art.

    In this game font the curly apostrophe is 7 px wide but advances 28 px;
    straight U+0027 advances 10 px. Do not change vertical offsets or alpha.
    """
    result=bytearray(rec)
    result[12:14]=donor[12:14]  # high 16 bits of the glyph's word[3]
    return bytes(result)


def _whiten_added_alpha_blocks(raw:bytes, alpha:bytes, width:int, height:int,
                               start_y:int)->tuple[bytes,int]:
    """Fix BC3 RGB for drawn glyphs in previously unused bottom atlas rows.

    Acts on RGB endpoints/selectors only; never changes original glyph pixels
    or their colors in rows preceding start_y.
    """
    if start_y%4:raise FontError(T('Рядок нових гліфів не вирівняно по BC3.','The row of new glyphs is not aligned to BC3 blocks.'))
    out=bytearray(raw);changed=0;bxc=width//4
    for by in range(start_y//4,height//4):
        for bx in range(bxc):
            p=(by*bxc+bx)*16
            x=bx*4;y=by*4
            if not any(alpha[(y+dy)*width+x+dx] for dy in range(4) for dx in range(4)):
                continue
            if out[p+8:p+16]!=BC3_OPAQUE_WHITE:
                out[p+8:p+16]=BC3_OPAQUE_WHITE
                changed+=1
    return bytes(out),changed


def repair_existing_font(font_file:Path|str)->tuple[bytes,dict,bytes]:
    """Repair v2.8/2.8.1 generated font IN PLACE IN MEMORY (never source).

    Fix near-black BC3 color in added Cyrillic cells, apostrophe U+2019
    advance, and over-tall Ґ/ґ using the already-generated glyph shapes.
    Doesn't require any TTF and doesn't touch the original 229 glyph art.
    """
    doc=load_font(font_file)
    glyph={g.codepoint:g for g in doc.glyphs}
    if len(doc.glyphs)<=ORIGINAL_GLYPH_COUNT or not all(ord(c) in glyph for c in 'Ґґ'):
        raise FontError(T('Для ремонту потрібен раніше зібраний шрифт STORM 2 із гліфами Ґ/ґ. Оригінал не змінюємо.','Repair needs a previously built STORM 2 font that has the glyphs U+0490/U+0491 (Ghe with upturn). The original is not changed.'))
    # Specific to our prior additive layout: original glyphs end at Y=274;
    # newly added Cyrillic starts at Y>=288.
    added=[g for g in doc.glyphs if g.codepoint>255 and g.y>=288]
    if len(added)<30 or any(g.y<288 for g in (glyph[ord('Ґ')],glyph[ord('ґ')])):
        raise FontError(T('Невідомий розклад кирилиці в атласі: автоматичний ремонт скасовано.','Unknown Cyrillic layout in the atlas: automatic repair cancelled.'))
    start=min(g.y-1 for g in added)
    start=max(288,(start//4)*4)
    # Avoid arbitrary image editing: only two problem glyphs are rescaled.
    try:
        from PIL import Image
    except ImportError as exc:
        raise FontError(T('Для ремонту потрібен Pillow: запустіть INSTALL_DEPENDENCIES.bat','Repair needs Pillow: run INSTALL_DEPENDENCIES.bat')) from exc
    alpha=doc.alpha_bytes()
    im=Image.frombytes('L',(doc.width,doc.height),alpha)
    body=doc.blob[doc.body_offset:doc.body_offset+doc.ntp_offset]
    records={g.codepoint:body[RECORD_BASE+g.index*RECORD_SIZE:RECORD_BASE+(g.index+1)*RECORD_SIZE]
             for g in doc.glyphs}
    height_fixes=[]
    for ch,latinlike in [('Ґ','Г'),('ґ','г')]:
        g=glyph[ord(ch)];native=glyph[ord(latinlike)]
        before=g.height;target=native.height
        if g.height>target:
            rect=(g.x,g.y,g.x+g.width,g.y+g.height)
            cell=im.crop(rect)
            scaled=cell.resize((g.width,target),Image.Resampling.LANCZOS)
            im.paste(0,rect)
            im.paste(scaled,(g.x,g.y))
            rec=bytearray(records[ord(ch)])
            # Genuine Latin-equivalent Г/г has the game's own bearings and
            # advance. Copy these metrics, retain graphic, coords and codepoint.
            donor=records[ord(latinlike)]
            rec[8:20]=donor[8:20]
            struct.pack_into('>I',rec,20,(g.width<<24)|(target<<16)|ord(ch))
            records[ord(ch)]=bytes(rec)
            height_fixes.append({'character':ch,'before':before,'after':target})
    apostrophe_fixed=False
    if 0x2019 in records and 0x27 in records:
        prev=records[0x2019]
        fixed=_fix_apostrophe_advance(prev,records[0x27])
        apostrophe_fixed=(fixed!=prev)
        records[0x2019]=fixed
    adjusted=im.tobytes()
    raw,changed_alpha=_patch_alpha(doc,adjusted) if adjusted!=alpha else (
        doc.blob[doc.pixel_offset:doc.pixel_offset+doc.width*doc.height],0)
    raw,changed_rgb=_whiten_added_alpha_blocks(raw,adjusted,doc.width,doc.height,start)
    data=_recode_document(doc,records,raw)
    _reparse_and_verify(data,doc,sorted(records))
    # Verify every original block above the Cyrillic area stays exact.
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as td:
        p=Path(td)/'xfgame_cmn.fnt.xfbin';p.write_bytes(data);checked=load_font(p)
        if (data[checked.pixel_offset:checked.pixel_offset+start*doc.width]!=
            doc.blob[doc.pixel_offset:doc.pixel_offset+start*doc.width]):
            raise FontError(T('Ремонт торкнувся оригінальних латинських пікселів!','The repair touched the original Latin pixels!'))
        for g in doc.glyphs:
            if g.codepoint in (ord('Ґ'),ord('ґ'),0x2019):continue
            h=next(x for x in checked.glyphs if x.codepoint==g.codepoint)
            old=records[g.codepoint]
            now=data[checked.body_offset+RECORD_BASE+h.index*RECORD_SIZE:checked.body_offset+RECORD_BASE+(h.index+1)*RECORD_SIZE]
            if old!=now:raise FontError(T('Змінено сторонній запис шрифту '+hex(g.codepoint),'An unrelated font record was changed: '+hex(g.codepoint)))
    from io import BytesIO
    # Only a user-facing preview; never used as replacement graphics source.
    bg=Image.new('RGBA',(doc.width,doc.height-start),(29,39,54,255))
    mask=Image.frombytes('L',(doc.width,doc.height-start),adjusted[start*doc.width:])
    fg=Image.new('RGBA',bg.size,(255,255,255,0));fg.putalpha(mask);bg.alpha_composite(fg)
    buf=BytesIO();bg.save(buf,format='PNG')
    report={'mode':'REPAIR_EXISTING_STORM2_FONT_V282','source_sha256':doc.digest,
            'output_sha256':sha(data),'glyphs':len(doc.glyphs),
            'fixed_bc3_color_blocks':changed_rgb,'modified_alpha_blocks':changed_alpha,
            'apostrophe_advance_narrowed':apostrophe_fixed,
            'apostrophe_old_advance_px':28,'apostrophe_new_advance_px':10,
            'resized_glyphs':height_fixes,'start_cyrillic_row':start,
            'source_untouched':True,'original_upper_atlas_untouched':True,
            'game_validation':'NOT_TESTED'}
    return data,report,buf.getvalue()


def repair_existing_font_output(font_file,output_root,relative_path,*,override=False,log=None):
    rel=clean_rel(relative_path)
    blob,report,preview=repair_existing_font(font_file)
    source=Path(font_file);root=Path(output_root)
    if (root/'data_win32'/rel).resolve()==source.resolve():
        # Users often select the current generated font from their Build folder.
        # Never overwrite that source; route the repaired result to a subfolder.
        root=root/'FONT_REPAIR_v282'
        report['output_note']='Вихідний XFBIN був у Build: нова збірка в окремій FONT_REPAIR_v282.'
    result=_safe_write(source,root,rel,blob,'STORM2_UA_FONT_REPAIRED_V282.zip',override,report)
    preview_path=root/'STORM2_FONT_V282_PREVIEW.png'
    preview_path.write_bytes(preview)
    result['preview']=str(preview_path)
    if log:log(T('STORM 2: виправлено BC3 RGB, апостроф і висоту Ґ/ґ; перевірка у грі потрібна.','STORM 2: fixed BC3 RGB, the apostrophe and the height of U+0490/U+0491 (Ghe with upturn); an in-game check is needed.'))
    return result


def _reparse_and_verify(data:bytes, doc:FontDoc, expected_codes:list[int])->FontDoc:
    with tempfile.TemporaryDirectory(prefix='storm2_font_check_') as folder:
        p=Path(folder)/'xfgame_cmn.fnt.xfbin'
        p.write_bytes(data)
        rebuilt=load_font(p)
        if [g.codepoint for g in rebuilt.glyphs]!=expected_codes:
            raise FontError(T('Зібраний шрифт не пройшов перевірку таблиці Unicode.','The built font failed the Unicode table check.'))
        if len(data)==len(doc.blob) and data==doc.blob:
            return rebuilt
        old=parse_xfbin(doc.path);new=parse_xfbin(p)
        if (len(old.chunks)!=len(new.chunks) or
            data[new.chunks[1].header_offset:]!=doc.blob[old.chunks[1].header_offset:]):
            raise FontError(T('Змінено службову частину XFBIN (nuccChunkPage).','The service part of the XFBIN (nuccChunkPage) was changed.'))
        return rebuilt


def build_font(doc:FontDoc,work:Path|str)->tuple[bytes,dict]:
    work=Path(work);newcodes,manifest=_work_codes(doc,work)
    raw_original=doc.blob[doc.pixel_offset:doc.pixel_offset+doc.width*doc.height]
    pngpath=work/ATLAS_FILE
    if not pngpath.is_file():raise FontError(T('Не знайдено атлас PNG.','The atlas PNG was not found.'))
    original=doc.alpha_bytes()
    edited=(original if sha(pngpath.read_bytes())==manifest['original_png_sha256'] else
            _edited_alpha_png(pngpath,doc.width,doc.height))
    if edited!=original:raw_atlas,bc3_changes=_patch_alpha(doc,edited)
    else:raw_atlas,bc3_changes=raw_original,0
    change_count=sum(a!=b for a,b in zip(newcodes,[g.codepoint for g in doc.glyphs]))
    if not change_count and not bc3_changes:
        result=doc.blob
    else:
        body=doc.blob[doc.body_offset:doc.body_offset+doc.ntp_offset]
        rows=[]
        for g,cp in zip(doc.glyphs,newcodes):
            rec=body[RECORD_BASE+g.index*RECORD_SIZE:RECORD_BASE+(g.index+1)*RECORD_SIZE]
            rows.append((cp,rec))
        result=_recode_document(doc,dict(rows),raw_atlas)
    _reparse_and_verify(result,doc,sorted(newcodes))
    return result,{'remapped_glyphs':change_count,'modified_bc3_blocks':bc3_changes,
                   'source_sha256':doc.digest,'output_sha256':sha(result),'byte_identical':result==doc.blob,
                   'warning':'ЕКСПЕРИМЕНТ: ще не перевірено в самій грі.'}


def _safe_write(source:Path, output_root:Path, rel:Path, payload:bytes,
                zip_name:str, override:bool,report:dict)->dict:
    target=output_root/'data_win32'/rel
    archive=output_root/zip_name
    if target.resolve()==source.resolve():raise FontError(T('Заборонено змінювати оригінал.','Changing the original is not allowed.'))
    if target.exists() and not override:raise FontError(T('Зібраний файл уже існує. Увімкніть резервне копіювання.','The built file already exists. Turn on backups.'))
    if archive.exists() and not override:raise FontError(T('ZIP уже існує. Увімкніть резервне копіювання.','The ZIP already exists. Turn on backups.'))
    for path in (target,archive):
        backup=path.with_name(path.name+'.bak')
        if path.exists() and not backup.exists():shutil.copy2(path,backup)  # keep the earliest copy
    target.parent.mkdir(parents=True,exist_ok=True);output_root.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='.storm2_font_',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(payload);f.flush();os.fsync(f.fileno())
        os.replace(temporary,target)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    fd,temporary=tempfile.mkstemp(prefix='.storm2_font_zip_',dir=output_root)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as z:
            z.write(target,'data_win32/'+rel.as_posix())
            z.writestr('STORM2_FONT_BUILD_REPORT.json',json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        os.replace(temporary,archive)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return {'file':str(target),'zip':str(archive),**report}


def build_font_output(original,workspace,output_root,relative_path,*,override=False,log=None)->dict:
    doc=load_font(original);rel=clean_rel(relative_path)
    data,report=build_font(doc,workspace)
    if report['byte_identical']:
        report['message']='Змін немає: шрифт не записано.'
        return report
    if log:log(T(f'STORM 2: {report["remapped_glyphs"]} Unicode, {report["modified_bc3_blocks"]} BC3-блоків.',f'STORM 2: {report["remapped_glyphs"]} Unicode, {report["modified_bc3_blocks"]} BC3 blocks.'))
    return _safe_write(doc.path,Path(output_root),rel,data,'STORM2_UA_FONT_EXPERIMENTAL.zip',override,report)


def build_additive_font(original:Path|str,ttf:Path|str, *,copy_native_lookalikes=True,include_preview=True)->tuple[bytes,dict,bytes|None]:
    """Append letters in UNUSED 512x512 atlas bottom; zero original pixel changes.

    Glyph style: Cyrillic shapes from user's TTF/OTF; visually identical
    Cyrillic/Latin shapes are optionally copied from the original game font.
    The font engine's acceptance of new glyphs still requires an IN-GAME test.
    """
    doc=load_font(original)
    if len(doc.glyphs)!=ORIGINAL_GLYPH_COUNT:
        raise FontError(T('Додавати літери можна лише до оригіналу STORM 2 (229 гліфів).','Letters can be added only to the original STORM 2 font (229 glyphs).'))
    ttf=Path(ttf)
    if not ttf.is_file() or ttf.suffix.lower() not in ('.ttf','.otf') or ttf.stat().st_size>30_000_000:
        raise FontError(T('Оберіть коректний TTF/OTF (до 30 МБ).','Choose a valid TTF/OTF (up to 30 MB).'))
    Image,ImageFont,ImageDraw,TTFont=_libraries()
    supported=_font_cmap(ttf,TTFont)
    existing={g.codepoint for g in doc.glyphs}
    add=[ch for ch in UKRAINIAN_ALPHABET if ord(ch) not in existing]
    missing=[ch for ch in add if ord(ch) not in supported]
    if missing:raise FontError(T('TTF не містить літер: '+''.join(missing),'The TTF does not contain these letters: '+''.join(missing)))
    if not add:raise FontError(T('У шрифті вже є всі українські літери.','The font already has all Ukrainian letters.'))
    start=((max(g.y+g.height for g in doc.glyphs)+15)//16)*16
    start=max(start,288)
    pitch=32;cols=doc.width//pitch
    rows=(len(add)+cols-1)//cols
    if start+rows*pitch>doc.height:
        raise FontError(T('Недостатньо вільного місця в атласі 512×512. Змінювати розміри не будемо.','Not enough free space in the 512×512 atlas. Its size will not be changed.'))
    alpha=doc.alpha_bytes()
    if any(alpha[start*doc.width:]):
        raise FontError(T('Нижня частина атласу не порожня! Додавання скасовано.','The bottom part of the atlas is not empty! Adding cancelled.'))
    orig_by_cp={g.codepoint:g for g in doc.glyphs}
    font_size=_calibrate_pixel_size(ttf,ImageFont,cap_height=22)
    font=ImageFont.truetype(str(ttf),font_size)
    image=Image.frombytes('L',(doc.width,doc.height),alpha)
    body=doc.blob[doc.body_offset:doc.body_offset+doc.ntp_offset]
    original_rows={g.codepoint:body[RECORD_BASE+g.index*RECORD_SIZE:RECORD_BASE+(g.index+1)*RECORD_SIZE]
                   for g in doc.glyphs}
    # Reuse native shapes for identical Latin/Cyrillic counterparts where possible.
    twins={'А':'A','В':'B','Е':'E','І':'I','К':'K','М':'M','Н':'H','О':'O','Р':'P','С':'C','Т':'T','Х':'X',
           'а':'a','е':'e','і':'i','о':'o','р':'p','с':'c','у':'y','х':'x'}
    reused=[];scaled=[];new=[]
    for i,ch in enumerate(add):
        x=(i%cols)*pitch;y=start+(i//cols)*pitch
        source_cp=ord(twins[ch]) if copy_native_lookalikes and ch in twins else None
        if source_cp is not None and source_cp in orig_by_cp:
            g=orig_by_cp[source_cp]
            cell=image.crop((g.x,g.y,g.x+g.width,g.y+g.height))
            # Use same original glyph dimensions and metric record for the native lookalike.
            width,height=g.width,g.height
            base=bytearray(original_rows[source_cp]);reused.append(ch)
        else:
            cell_28,was_scaled=_draw_glyph(ch,font,Image,ImageDraw)
            box=cell_28.getbbox()
            if not box:raise FontError(T('TTF намалював порожню літеру '+ch,'The TTF drew an empty letter: '+ch))
            cell=cell_28.crop(box)
            width,height=cell.size
            # Latin metric donor: roughly same glyph category and width.
            basecp=ord('A') if ch.isupper() else ord('a')
            donors=[g for g in doc.glyphs if g.codepoint in range(ord('A') if ch.isupper() else ord('a'),
                                     ord('Z')+1 if ch.isupper() else ord('z')+1) and g.width>0]
            donor=min(donors,key=lambda g:abs(g.width-width)+abs(g.height-height)) if donors else orig_by_cp[basecp]
            base=bytearray(original_rows[donor.codepoint])
            if ch in ('Ґ','ґ'):
                # Match native Г/г visual height and the game's own bearing / advance.
                # Г/г are themselves newly added earlier in the ordered
                # alphabet; the original STORM2 font contains NO Cyrillic.
                native_rec=next((r for cp,r in new if cp==ord('Г' if ch=='Ґ' else 'г')),None)
                if native_rec is None:
                    raise FontError(T('Для Ґ/ґ не знайдено щойно створеної Г/г.','The newly created U+0413/U+0433 (Ghe) was not found for U+0490/U+0491 (Ghe with upturn).'))
                native_w=native_rec[20]
                native_h=native_rec[21]  # packed glyph rect from word[5]
                cell=cell.resize((native_w,native_h),Image.Resampling.LANCZOS)
                width,height=cell.size
                base=bytearray(native_rec)
                if ch not in scaled:scaled.append(ch)
            elif was_scaled:scaled.append(ch)
        if width>30 or height>30:raise FontError(T('Завелика літера у комірці '+ch,'The letter is too big for its cell: '+ch))
        image.paste(cell,(x+1,y+1))
        # Source glyph data: word1 = x,y; word5 = draw rectangle width,height,Unicode.
        struct.pack_into('>I',base,4,((x+1)<<16)|(y+1))
        struct.pack_into('>I',base,20,(width<<24)|(height<<16)|ord(ch))
        new.append((ord(ch),bytes(base)))
    # The game's original curly apostrophe has a giant 28-pixel advance despite
    # a 7-pixel mark. Bring it in line with native straight apostrophe (10 px).
    apostrophe_fixed=False
    if 0x2019 in original_rows and 0x27 in original_rows:
        fixed=_fix_apostrophe_advance(original_rows[0x2019],original_rows[0x27])
        apostrophe_fixed=fixed!=original_rows[0x2019]
        original_rows[0x2019]=fixed
    all_records={**original_rows,**dict(new)}
    new_alpha=image.tobytes()
    raw_atlas,changes=_patch_alpha(doc,new_alpha,must_be_unused_y=start)
    # Assert every opaque Cyrillic BC3 cell has white RGB, not uninitialized black.
    raw_atlas,whitened=_whiten_added_alpha_blocks(raw_atlas,new_alpha,doc.width,doc.height,start)
    data=_recode_document(doc,all_records,raw_atlas)
    _reparse_and_verify(data,doc,sorted(all_records))
    # Strong invariant: all original 229 GLYPH RECORDS and every original BC3
    # block above the unused rows are binary exact after appending new glyphs.
    with tempfile.TemporaryDirectory() as td:
        tmp=Path(td)/'result.xfbin';tmp.write_bytes(data);checked=load_font(tmp)
        orig_prefix=doc.blob[doc.pixel_offset:doc.pixel_offset+start*doc.width]
        modified_prefix=data[checked.pixel_offset:checked.pixel_offset+start*doc.width]
        if orig_prefix!=modified_prefix:raise FontError(T('Пошкоджено оригінальну верхню частину атласу.','The original top part of the atlas was damaged.'))
        result_body=data[checked.body_offset:checked.body_offset+checked.ntp_offset]
        for cp,rec in original_rows.items():
            g=next(g for g in checked.glyphs if g.codepoint==cp)
            found=result_body[RECORD_BASE+g.index*RECORD_SIZE:RECORD_BASE+(g.index+1)*RECORD_SIZE]
            if found!=rec:raise FontError(T('Змінено оригінальний запис гліфа '+hex(cp),'An original glyph record was changed: '+hex(cp)))
    preview=None
    if include_preview:
        from io import BytesIO
        layer=Image.new('RGBA',(doc.width,rows*pitch),(255,255,255,0))
        layer.putalpha(image.crop((0,start,doc.width,start+rows*pitch)))
        bg=Image.new('RGBA',layer.size,(28,37,54,255));bg.alpha_composite(layer)
        stream=BytesIO();bg.convert('RGB').save(stream,format='PNG');preview=stream.getvalue()
    report={'profile':PROFILE,'mode':'ADDITIVE_UKRAINIAN_BC3_SAME_ATLAS_EXPERIMENTAL',
            'source_sha256':doc.digest,'source_font':doc.path.name,'ttf':ttf.name,
            'ttf_sha256':sha(ttf.read_bytes()),'original_glyphs':len(doc.glyphs),
            'new_glyphs':len(all_records),'added_count':len(add),'added_characters':''.join(add),
            'copied_from_native_game':''.join(reused),'scaled_ttf_glyphs':''.join(scaled),
            'atlas_before':[512,512],'atlas_after':[512,512],
            'original_glyph_records_unchanged_except_curly_apostrophe_spacing':True,
            'original_texture_top_unchanged':True,
            'additional_start_row':start,'changed_bc3_blocks':changes,
            'corrected_rgb_white_bc3_blocks':whitened,
            'apostrophe_advance_narrowed':apostrophe_fixed,
            'native_height_for_Ghe_u_ghe':True,
            'output_sha256':sha(data),
            'warning':'ЕКСПЕРИМЕНТ: XFBIN roundtrip пройдено, але в грі не тестовано; можливі відмінності ширини, кернінгу й обробки Unicode.'}
    return data,report,preview


def generate_additive_font(source,ttf,workspace,output_root,relative_path,*,override=False,log=None,copy_native_lookalikes=True)->dict:
    doc=load_font(source);rel=clean_rel(relative_path)
    blob,report,preview=build_additive_font(source,ttf,copy_native_lookalikes=copy_native_lookalikes)
    work=Path(workspace)/'STORM2_Font_Additive'/('font_'+report['output_sha256'][:12])
    # Do not create workspace metadata before checking existing output conflicts.
    output=Path(output_root);target=output/'data_win32'/rel;archive=output/'STORM2_UA_FONT_ADDITIVE_EXPERIMENTAL.zip'
    if (target.exists() or archive.exists()) and not override:
        raise FontError(T('Зібраний файл або ZIP вже існує. Увімкніть резервне копіювання.','The built file or the ZIP already exists. Turn on backups.'))
    work.mkdir(parents=True,exist_ok=True)
    (work/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if preview is not None:(work/'added_glyphs_preview.png').write_bytes(preview)
    result=_safe_write(doc.path,output,rel,blob,archive.name,override,report)
    result['preview']=str(work/'added_glyphs_preview.png')
    result['report']=str(work/'report.json')
    if log:log(T(f'STORM 2: {report["added_count"]} нових літер, оригінальні гліфи збережено; ІГРОВИЙ ТЕСТ ПОТРІБНИЙ.',f'STORM 2: {report["added_count"]} new letters, original glyphs preserved; AN IN-GAME TEST IS REQUIRED.'))
    return result
