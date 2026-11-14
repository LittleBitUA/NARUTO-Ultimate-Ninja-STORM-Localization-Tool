# -*- coding: utf-8 -*-
"""STORM 1 font (NUCC 104 / NFH / NTP3 BC3): atlas export, PNG+Unicode rebuild, additive Ukrainian glyphs from TTF."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import hashlib
import json
import os
import struct
import tempfile
import zlib
import zipfile
import re
import math
import shutil

from xfbin import parse_xfbin
from i18n import T


# ============================================================================
# font
# ============================================================================

VERSION = '0.5.0'
CSV_FILE = 'font_glyphs.tsv'
MANIFEST_FILE = 'font_manifest.json'
ATLAS_FILE = 'xfgame_cmn_atlas.png'
DDS_FILE = 'xfgame_cmn_atlas.dds'
PREVIEW_FILE = 'xfgame_cmn_preview.png'
MISSING = 0xf8000001
RECORD_BASE = 0x458
ROW_BYTES = 32
MAX_PIXELS = 8_000_000

class FontError(ValueError):
    pass


def be32(data: bytes, n: int) -> int:
    return struct.unpack_from('>I', data, n)[0]


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class Glyph:
    index: int
    codepoint: int
    x: int
    y: int
    width: int
    height: int
    metrics: tuple[int, int, int, int, int]


@dataclass
class FontDoc:
    path: Path
    blob: bytes
    digest: str
    body_offset: int
    ntp_offset: int
    pixel_offset: int
    width: int
    height: int
    glyphs: list[Glyph]

    def alpha_bytes(self) -> bytes:
        return decode_bc3_alpha(self.blob[self.pixel_offset:self.pixel_offset+self.width*self.height], self.width, self.height)


def _original_lookup(codepoints: list[int]) -> bytes:
    """Index for 256 high-byte pages: first glyph index minus low byte.
    Each page still needs a codepoint comparison; gaps are allowed.
    """
    index = [MISSING] * 256
    for n,cp in enumerate(codepoints):
        page = cp>>8
        if index[page] == MISSING:
            index[page] = (n-(cp&255)) & 0xffffffff
    return struct.pack('>256I',*index)


def load_font(path: Path | str) -> FontDoc:
    path = Path(path)
    if path.stat().st_size>32*1024*1024:
        raise FontError(T('Файл шрифту завеликий.', 'The font file is too large.'))
    c=parse_xfbin(path)
    if c.nucc_version!=104 or len(c.chunks)!=2 or [x.type_name for x in c.chunks]!=['nuccChunkFont','nuccChunkPage']:
        raise FontError(T('Потрібен саме xfgame_cmn.fnt.xfbin NUCC 104 з одним шрифтовим чанком.', 'This must be xfgame_cmn.fnt.xfbin, NUCC 104, with a single font chunk.'))
    ck=c.chunks[0];data=c.blob[ck.data_offset:ck.end]
    if data[8:12]!=b'NFH\0' or be32(data,0x0c)!=28 or be32(data,0x10)!=3683:
        raise FontError(T('Невідома структура заголовка NFH / кількості гліфів.', 'Unknown NFH header structure or glyph count.'))
    count=be32(data,0x10)
    ntp=RECORD_BASE+count*ROW_BYTES
    if (be32(data,0)!=ntp-8 or be32(data,4)!=len(data)-ntp or
        data[ntp:ntp+4]!=b'NTP3' or data[ntp+0x40:ntp+0x44]!=b'eXt\0' or
        data[ntp+0x50:ntp+0x54]!=b'GIDX'):
        raise FontError(T('Невідповідний заголовок NFH/NTP3 або зміщення атласу.', 'The NFH/NTP3 header or the atlas offset does not match.'))
    packed=be32(data,ntp+0x24)
    width,height=packed>>16,packed&65535
    if width<=0 or height<=0 or width*height>MAX_PIXELS or width%4 or height%4:
        raise FontError(T('Некоректні розміри текстури.', 'Invalid texture size.'))
    size=width*height
    if (be32(data,ntp+0x10)!=size+0x50 or
        be32(data,ntp+0x18)!=size or len(data)-(ntp+0x60)!=size):
        raise FontError(T('Невідома схема однієї текстури BC3 / DXT5.', 'Unknown layout of the single BC3 / DXT5 texture.'))
    glyphs=[]
    for i in range(count):
        vals=struct.unpack_from('>8I',data,RECORD_BASE+i*ROW_BYTES)
        cp=vals[5]&0xffff
        x,y=vals[1]>>16,vals[1]&65535
        w,h=vals[5]>>24,(vals[5]>>16)&255
        if cp==0 or 0xd800<=cp<=0xdfff or not (0<w<=128 and 0<h<=128) or x+w>width or y+h>height:
            raise FontError(T(f'Гліф №{i}: неправильний Unicode або межі атласу.', f'Glyph #{i}: wrong Unicode or atlas bounds.'))
        if vals[0]!=0x11110001 or vals[4]!=0x700:
            raise FontError(T(f'Гліф №{i}: непідтримувані службові поля.', f'Glyph #{i}: unsupported service fields.'))
        glyphs.append(Glyph(i,cp,x,y,w,h,(vals[0], vals[2], vals[3], vals[4], vals[6])))
    codepoints=[g.codepoint for g in glyphs]
    if codepoints!=sorted(set(codepoints)):
        raise FontError(T('Таблиця Unicode не відсортована або містить повтори.', 'The Unicode table is not sorted or contains duplicates.'))
    if data[0x58:RECORD_BASE]!=_original_lookup(codepoints):
        raise FontError(T('Непідтримувана таблиця індексів Unicode.', 'Unsupported Unicode index table.'))
    return FontDoc(path,c.blob,c.sha256,ck.data_offset,ntp,ck.data_offset+ntp+0x60,width,height,glyphs)


def decode_bc3_alpha(raw: bytes, width: int, height: int) -> bytes:
    if len(raw)!=width*height or width%4 or height%4:
        raise FontError(T('Невідповідний BC3-буфер.', 'The BC3 buffer does not match.'))
    alpha=bytearray(width*height)
    blocks_x=width//4
    for by in range(height//4):
        for bx in range(blocks_x):
            p=(by*blocks_x+bx)*16
            a,b=raw[p],raw[p+1]
            pal=([a,b]+[((7-j)*a+j*b)//7 for j in range(1,7)]) if a>b else ([a,b]+[((5-j)*a+j*b)//5 for j in range(1,5)]+[0,255])
            bits=int.from_bytes(raw[p+2:p+8],'little')
            z=(by*4)*width+bx*4
            for dy in range(4):
                for dx in range(4):
                    alpha[z+dy*width+dx]=pal[(bits>>(3*(dy*4+dx)))&7]
    return bytes(alpha)


def _png_chunk(ty:bytes,buf:bytes)->bytes:
    return struct.pack('>I',len(buf))+ty+buf+struct.pack('>I',zlib.crc32(ty+buf)&0xffffffff)


def png_of_alpha(alpha:bytes,width:int,height:int)->bytes:
    """White glyph mask with transparent background; RGBA PNG, no dependencies."""
    if len(alpha)!=width*height:raise FontError(T('Некоректні розміри зображення.', 'Invalid image size.'))
    rgba=bytearray(width*height*4)
    rgba[0::4]=b'\xff'*(width*height)
    rgba[1::4]=b'\xff'*(width*height)
    rgba[2::4]=b'\xff'*(width*height)
    rgba[3::4]=alpha
    compressor=zlib.compressobj(7)
    compressed=bytearray()
    for y in range(height):
        compressed.extend(compressor.compress(b'\x00'+rgba[y*width*4:(y+1)*width*4]))
    compressed.extend(compressor.flush())
    header=struct.pack('>IIBBBBB',width,height,8,6,0,0,0)
    return b'\x89PNG\r\n\x1a\n'+_png_chunk(b'IHDR',header)+_png_chunk(b'IDAT',bytes(compressed))+_png_chunk(b'IEND',b'')


def make_dds(doc:FontDoc)->bytes:
    raw=doc.blob[doc.pixel_offset:doc.pixel_offset+doc.width*doc.height]
    nums=[124,0x81007,doc.height,doc.width,len(raw),0,1]+[0]*11+[32,4,struct.unpack('<I',b'DXT5')[0],0,0,0,0,0]+[0x1000,0,0,0,0]
    return b'DDS '+struct.pack('<31I',*nums)+raw


def render_preview(alpha:bytes,w:int,h:int)->bytes:
    # 1024 x top 448 px. The dark background is only for preview, not atlas.
    ph=min(h,448)
    a=alpha[:w*ph]
    rgba=bytearray(w*ph*4)
    for i,opacity in enumerate(a):
        base=i*4
        rgba[base]=25+(230*opacity//255)
        rgba[base+1]=32+(223*opacity//255)
        rgba[base+2]=40+(215*opacity//255)
        rgba[base+3]=255
    compressor=zlib.compressobj(7);compressed=bytearray()
    for y in range(ph):compressed.extend(compressor.compress(b'\x00'+rgba[y*w*4:(y+1)*w*4]))
    compressed.extend(compressor.flush())
    header=struct.pack('>IIBBBBB',w,ph,8,6,0,0,0)
    return b'\x89PNG\r\n\x1a\n'+_png_chunk(b'IHDR',header)+_png_chunk(b'IDAT',bytes(compressed))+_png_chunk(b'IEND',b'')


def export_font(doc:FontDoc,work_dir:Path|str,log=None)->dict:
    work=Path(work_dir)
    if work.resolve()==doc.path.resolve():
        raise FontError(T('Робоча папка не може бути шляхом до самого шрифту.', 'The workspace folder cannot be the path of the font itself.'))
    work.mkdir(parents=True,exist_ok=True)
    targets=[work/f for f in [CSV_FILE,MANIFEST_FILE,ATLAS_FILE,DDS_FILE,PREVIEW_FILE]]
    if any(p.exists() for p in targets):
        raise FontError(T('У папці вже є дані шрифту. Для безпеки виберіть нову робочу папку.', 'The folder already contains font data. To be safe, choose a new workspace folder.'))
    atlas=doc.alpha_bytes()
    if log:log(T('Створення атласу PNG з BC3/DXT5, 3683 гліфи…', 'Creating the PNG atlas from BC3/DXT5, 3683 glyphs…'))
    atlas_png=png_of_alpha(atlas,doc.width,doc.height)
    (work/ATLAS_FILE).write_bytes(atlas_png)
    (work/DDS_FILE).write_bytes(make_dds(doc))
    (work/PREVIEW_FILE).write_bytes(render_preview(atlas,doc.width,doc.height))
    with (work/CSV_FILE).open('w',encoding='utf-8-sig',newline='') as out:
        writer=csv.writer(out,delimiter='\t')
        writer.writerow(('index','original_unicode','new_unicode','character','x','y','width','height','note'))
        for g in doc.glyphs:
            ch=chr(g.codepoint)
            if ch in '\n\r\t':ch=''
            writer.writerow((g.index,f'U+{g.codepoint:04X}',f'U+{g.codepoint:04X}',ch,g.x,g.y,g.width,g.height,''))
    manifest={
        'format':'storm1-font-v1', 'source_sha256':doc.digest,
        'source_name':doc.path.name,'atlas_png':ATLAS_FILE,'glyphs_tsv':CSV_FILE,
        'source_glyph_count':len(doc.glyphs),'original_atlas_sha256':sha(atlas),'original_atlas_png_sha256':sha(atlas_png),
        'width':doc.width,'height':doc.height,
        'notes':'Edit only new_unicode and note columns; leave original Unicode and coordinates intact. PNG alpha only.'}
    (work/MANIFEST_FILE).write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return {'glyphs':len(doc.glyphs),'size':(doc.width,doc.height),'missing_ukrainian':missing_ukrainian(doc),
            'folder':str(work),'files':[str(p) for p in targets]}


def missing_ukrainian(doc:FontDoc)->str:
    # All Ukrainian letters, upper+lower; not just four special pairs.
    chars='АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯабвгґдеєжзиіїйклмнопрстуфхцчшщьюя'
    present={g.codepoint for g in doc.glyphs}
    return ''.join(x for x in chars if ord(x) not in present)


def _new_codepoints(work:Path,doc:FontDoc)->list[int]:
    manifest=json.loads((work/MANIFEST_FILE).read_text(encoding='utf-8'))
    if manifest.get('format')!='storm1-font-v1' or manifest.get('source_sha256')!=doc.digest or manifest.get('source_glyph_count')!=len(doc.glyphs) or manifest.get('original_atlas_sha256')!=sha(doc.alpha_bytes()):
        raise FontError(T('Шрифт не збігається з маніфестом (SHA-256 або структура).', 'The font does not match the manifest (SHA-256 or structure).'))
    with (work/CSV_FILE).open('r',encoding='utf-8-sig',newline='') as fp:
        records=list(csv.DictReader(fp,delimiter='\t'))
    if len(records)!=len(doc.glyphs):raise FontError(T('Змінено кількість рядків у таблиці гліфів.', 'The number of rows in the glyph table was changed.'))
    result=[]
    for row,g in zip(records,doc.glyphs):
        for key,want in [('index',str(g.index)),('original_unicode',f'U+{g.codepoint:04X}'),('x',str(g.x)),('y',str(g.y)),('width',str(g.width)),('height',str(g.height))]:
            if row.get(key)!=want:raise FontError(T(f'Змінено захищене поле {key} у гліфі {g.index}.', f'Protected field {key} was changed in glyph {g.index}.'))
        code=(row.get('new_unicode') or '').upper().strip()
        if not code.startswith('U+') or len(code)<3:raise FontError(T(f'Невірний код Unicode гліфа {g.index}.', f'Invalid Unicode code for glyph {g.index}.'))
        try:cp=int(code[2:],16)
        except ValueError as exc:raise FontError(T(f'Невірний код Unicode гліфа {g.index}.', f'Invalid Unicode code for glyph {g.index}.')) from exc
        if not 1<=cp<=0xffff or 0xd800<=cp<=0xdfff:raise FontError(T(f'Підтримуються лише Unicode BMP без сурогатів: {code}.', f'Only Unicode BMP without surrogates is supported: {code}.'))
        result.append(cp)
    if len(set(result))!=len(result):raise FontError(T('Повторення Unicode після заміни. Один символ може мати тільки один гліф.', 'Duplicate Unicode after the replacement. One character can have only one glyph.'))
    return result


def _edited_alpha_png(path:Path,w:int,h:int)->bytes:
    try:
        from PIL import Image
    except ImportError as exc:
        raise FontError(T('Для імпорту відредагованого PNG потрібен Pillow: запустіть INSTALL_DEPENDENCIES.bat', 'Importing an edited PNG requires Pillow: run INSTALL_DEPENDENCIES.bat')) from exc
    with Image.open(path) as im:
        if im.size!=(w,h):raise FontError(T('Змінено розміри PNG-атласу. Потрібен оригінальний розмір.', 'The PNG atlas size was changed. The original size is required.'))
        im=im.convert('RGBA')
        rgba=im.tobytes()
    # Only alpha channel is editable. Colors at nontransparent locations must remain white.
    for p in range(0,len(rgba),4):
        if rgba[p+3] and rgba[p:p+3]!=b'\xff\xff\xff':
            raise FontError(T('PNG має кольорові непрозорі пікселі: дозволено лише білі гліфи й прозорість.', 'The PNG has colored non-transparent pixels: only white glyphs and transparency are allowed.'))
    return rgba[3::4]


def _encode_bc3_alpha(block: list[int]) -> bytes:
    maximum=max(block);minimum=min(block)
    # BC3 8-alpha palette, 3-bit indices, 6 interpolated values. For flat
    # blocks the constant endpoint is exact. Only alpha blocks are rewritten.
    a0,a1=maximum,minimum
    palette=[a0,a1]+[((7-j)*a0+j*a1)//7 for j in range(1,7)] if a0>a1 else [a0,a1]+[((5-j)*a0+j*a1)//5 for j in range(1,5)]+[0,255]
    indices=0
    for i,v in enumerate(block):
        k=min(range(8),key=lambda j:abs(palette[j]-v))
        indices |= k << (3*i)
    return bytes([a0,a1])+indices.to_bytes(6,'little')


def build_font(doc:FontDoc,work_dir:Path|str,*,use_edited_png:bool=True)->tuple[bytes,dict]:
    work=Path(work_dir)
    newcodes=_new_codepoints(work,doc)
    raw=bytearray(doc.blob)
    body_base=doc.body_offset
    records=[]
    for glyph,cp in zip(doc.glyphs,newcodes):
        rec=bytearray(raw[body_base+RECORD_BASE+glyph.index*32:body_base+RECORD_BASE+(glyph.index+1)*32])
        struct.pack_into('>H',rec,22,cp)
        records.append((cp,bytes(rec)))
    records.sort(key=lambda x:x[0])
    codes=[cp for cp,_ in records]
    lookup=_original_lookup(codes)
    raw[body_base+0x58:body_base+RECORD_BASE]=lookup
    raw[body_base+RECORD_BASE:body_base+doc.ntp_offset]=b''.join(rec for _,rec in records)
    edited_blocks=0
    if use_edited_png:
        original=doc.alpha_bytes()
        metadata=json.loads((work/MANIFEST_FILE).read_text(encoding='utf-8'))
        image_path=work/ATLAS_FILE
        if sha(image_path.read_bytes())==metadata.get('original_atlas_png_sha256'):
            edited=original
        else:
            edited=_edited_alpha_png(image_path,doc.width,doc.height)
        if edited!=original:
            xblocks=doc.width//4
            # Block-compression is aligned on 4x4 pixels. Compare with decoded
            # original and rewrite only affected alpha blocks; color bytes and
            # all unchanged BC3 blocks remain byte-identical.
            for by in range(doc.height//4):
                for bx in range(xblocks):
                    left=bx*4;top=by*4
                    ref=[];new=[]
                    for dy in range(4):
                        off=(top+dy)*doc.width+left
                        ref.extend(original[off:off+4]);new.extend(edited[off:off+4])
                    if ref!=new:
                        pos=doc.pixel_offset+(by*xblocks+bx)*16
                        raw[pos:pos+8]=_encode_bc3_alpha(new)
                        edited_blocks+=1
    remapped=sum(g.codepoint!=cp for g,cp in zip(doc.glyphs,newcodes))
    result=bytes(raw)
    if not remapped and not edited_blocks and result!=doc.blob:
        raise FontError(T('Нульове збирання шрифту не є побайтово ідентичним.', 'A font build with no changes is not byte-for-byte identical.'))
    # Reparse real rebuilt file to check all internal indexes and glyphs.
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/'verify.fnt.xfbin';p.write_bytes(result)
        verified=load_font(p)
    if [g.codepoint for g in verified.glyphs]!=codes:
        raise FontError(T('Внутрішня перевірка символів не пройшла.', 'The internal character check failed.'))
    return result, {'remapped_glyphs':remapped,'modified_bc3_blocks':edited_blocks,'output_sha256':sha(result),'byte_identical':result==doc.blob}


def relative_font_path(original:Path|str)->str:
    """Guess location ONLY when known extracted game root is in the path."""
    p=Path(original)
    bits=p.parts
    for i in range(len(bits)-1):
        if bits[i].lower()=='dataregion_extracted' and bits[i+1].lower()=='data':
            return '/'.join(bits[i+2:])
    return ''


def clean_rel(rel:str)->Path:
    rel=rel.replace('\\','/')
    p=Path(rel)
    if (not rel or p.is_absolute() or ':' in rel or any(x in ('.','..') for x in p.parts)
            or p.name!='xfgame_cmn.fnt.xfbin' or p.parts[0].lower()=='data_win32'):
        raise FontError(T('Укажіть точний відносний шлях до xfgame_cmn.fnt.xfbin у data_win32.', 'Enter the exact relative path to xfgame_cmn.fnt.xfbin inside data_win32.'))
    return p


def build_font_output(original:Path|str,work:Path|str,output_root:Path|str,relative_path:str,*,override=False,log=None)->dict:
    doc=load_font(original)
    rel=clean_rel(relative_path)
    output_root=Path(output_root)
    target=output_root/'data_win32'/rel
    if target.resolve()==Path(original).resolve():raise FontError(T('Не можна переписувати оригінальний шрифт.', 'The original font must not be overwritten.'))
    if target.exists() and not override:raise FontError(T('Такий зібраний шрифт уже існує. Заберіть файл або дозвольте резервне копіювання.', 'This built font already exists. Move the file away or allow a backup.'))
    rebuilt,report=build_font(doc,work)
    if rebuilt==doc.blob:
        report['message']='Змін немає, шрифт не записано.'
        return report
    archive=output_root/'STORM1_UA_FONT_experimental.zip'
    if archive.exists() and not override:
        raise FontError(T('Архів шрифту вже існує. Дозвольте заміну з резервною копією.', 'The font archive already exists. Allow it to be replaced, keeping a backup.'))
    if log:log(T(f'Зміни шрифту: {report["remapped_glyphs"]} Unicode, {report["modified_bc3_blocks"]} блоків.', f'Font changes: {report["remapped_glyphs"]} Unicode, {report["modified_bc3_blocks"]} blocks.'))
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        backup=target.with_name(target.name+'.bak')
        if backup.exists():raise FontError(T(f'Резервна копія вже існує: {backup}', f'The backup already exists: {backup}'))
        import shutil;shutil.copy2(target,backup)
    fd,temp=tempfile.mkstemp(prefix='.font_',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(rebuilt);f.flush();os.fsync(f.fileno())
        os.replace(temp,target)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    if archive.exists():
        backup=archive.with_name(archive.name+'.bak')
        if backup.exists():raise FontError(T(f'Резервна копія вже існує: {backup}', f'The backup already exists: {backup}'))
        import shutil;shutil.copy2(archive,backup)
    fd,tmp=tempfile.mkstemp(prefix='.font_zip_',suffix='.tmp',dir=output_root)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.write(target,'data_win32/'+rel.as_posix())
        os.replace(tmp,archive)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    report['file']=str(target);report['zip']=str(archive)
    report['warning']='ЕКСПЕРИМЕНТАЛЬНИЙ: не перевірено завантаження цього шрифту в грі.'
    return report


# ============================================================================
# ttf
# ============================================================================

UKRAINIAN_UPPER = 'АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ'
UKRAINIAN_LOWER = 'абвгґдеєжзиіїйклмнопрстуфхцчшщьюя'
UKRAINIAN_ALPHABET = UKRAINIAN_UPPER + UKRAINIAN_LOWER
# These signs are common in Ukrainian localization but aren't Cyrillic letters.
# Existing Unicode codepoints are not replaced (e.g. U+2019 is in the source).
OPTIONAL_SYMBOLS = 'ʼ«»„–—№₴'


def _libraries():
    try:
        from PIL import Image, ImageFont, ImageDraw
    except ImportError as exc:
        raise FontError(T('Потрібна бібліотека Pillow: запустіть INSTALL_DEPENDENCIES.bat', 'The Pillow library is required: run INSTALL_DEPENDENCIES.bat')) from exc
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:
        raise FontError(T('Потрібна бібліотека fonttools: запустіть INSTALL_DEPENDENCIES.bat', 'The fonttools library is required: run INSTALL_DEPENDENCIES.bat')) from exc
    return Image, ImageFont, ImageDraw, TTFont


def _font_cmap(ttf: Path, TTFont):
    try:
        font = TTFont(str(ttf), lazy=True)
        try:
            supported = set(font.getBestCmap() or {})
        finally:
            font.close()
    except Exception as exc:
        raise FontError(T('Не вдалося прочитати таблицю Unicode в TTF/OTF: ' + str(exc), 'Could not read the Unicode table in the TTF/OTF: ' + str(exc))) from exc
    return supported


def _calibrate_pixel_size(ttf: Path, ImageFont, cap_height=23):
    # Find one common pixel size for the whole font. Individual oversized glyphs
    # are only fitted as a fallback, rather than each glyph being independently sized.
    for sz in range(12,61):
        ft=ImageFont.truetype(str(ttf),sz)
        b=ft.getbbox('Н',anchor='ls')
        if (b[3]-b[1])>cap_height:
            return max(12,sz-1)
    return 60


def _draw_glyph(ch, font, Image, ImageDraw):
    cell=Image.new('L',(28,28),0)
    # Draw at a common baseline to keep Ukrainian lowercase, accents and
    # descenders visually consistent with the Latin atlas.
    draft=Image.new('L',(112,112),0)
    ImageDraw.Draw(draft).text((56,56),ch,font=font,fill=255,anchor='ls')
    bbox=draft.getbbox()
    if not bbox:
        raise FontError(T('TTF повернув порожній гліф: ' + repr(ch), 'The TTF returned an empty glyph: ' + repr(ch)))
    glyph=draft.crop(bbox)
    original_size=glyph.size
    if glyph.width>26 or glyph.height>26:
        glyph.thumbnail((26,26),Image.Resampling.LANCZOS)
    if not glyph.getbbox():
        raise FontError(T('Після масштабування гліф порожній: ' + repr(ch), 'The glyph is empty after scaling: ' + repr(ch)))
    # Glyph top relative to the draft's baseline, scaled if necessary.
    top_rel=bbox[1]-56
    if original_size[1] != glyph.height:
        top_rel=round(top_rel*glyph.height/original_size[1])
    natural = max(0,25+top_rel)
    top = min(natural,28-glyph.height)
    left = max(0,(28-glyph.width)//2)
    cell.paste(glyph,(left,top))
    # A deep descender does not fit under baseline row 25, so the ink is moved
    # up; the caller compensates through the record's vertical value.
    cell.info['lift'] = natural-top
    return cell, glyph.size != original_size


# ============================================================================
# optical
# ============================================================================

def shrink_small_r(mask, percent: int, Image):
    if not 70 <= percent <= 100:
        raise ValueError(T('Мала «р»: дозволено 70…100%.', 'Lowercase Cyrillic er (U+0440): 70…100% is allowed.'))
    if percent == 100:
        return mask
    box = mask.getbbox()
    if not box:
        return mask
    crop = mask.crop(box)
    new_size=(max(1,round(crop.width*percent/100)),max(1,round(crop.height*percent/100)))
    scaled=crop.resize(new_size, Image.Resampling.LANCZOS)
    # Preserve the baseline and the descender; do not confuse Cyrillic р with Р.
    old_base=25
    top=round(old_base-(old_base-box[1])*percent/100)
    top=max(0,min(28-scaled.height,top))
    left=(28-scaled.width)//2
    out=Image.new('L',(28,28),0)
    out.paste(scaled,(left,top))
    return out


# ============================================================================
# metrics_safe
# ============================================================================

SPECIAL = {'р':'p','і':'i','ї':'i','І':'I','Ї':'I','а':'a','А':'A',
           'о':'o','О':'O','с':'c','С':'C','е':'e','Е':'E',
           'м':'m','М':'M','н':'n','Н':'H','к':'k','К':'K',
           'т':'m','Т':'T','у':'y','У':'Y','х':'x','Х':'X'}

def _s16(num):
    return num-65536 if num>=32768 else num

def find_native_metrics(doc, alpha_img, newmask, ch):
    """Use an actual, engine-authored Latin record whose ink size fits.

    Returns reference glyph, not an inferred word2/word3 pair. Optional fixed
    letter analogues take precedence when their sizes are sufficiently close.
    """
    glyphs={chr(g.codepoint):g for g in doc.glyphs if 32<=g.codepoint<127}
    wanted=newmask.getbbox()
    if not wanted: raise FontError(T('Порожній гліф: '+repr(ch), 'Empty glyph: '+repr(ch)))
    want_w=wanted[2]-wanted[0]; want_h=wanted[3]-wanted[1]
    pool='ABCDEFGHIJKLMNOPQRSTUVWXYZ' if ch.isupper() else 'abcdefghijklmnopqrstuvwxyz'
    refs=[]
    for letter in pool:
        g=glyphs.get(letter)
        if not g:continue
        refbox=alpha_img.crop((g.x,g.y,g.x+g.width,g.y+g.height)).getbbox()
        if not refbox:continue
        rw=refbox[2]-refbox[0];rh=refbox[3]-refbox[1]
        # Priority is close ink width, then similar native cell alignment.
        score=abs(rw-want_w)*6+abs(refbox[0]-wanted[0])*2+abs(rh-want_h)
        refs.append((score,letter,g,refbox))
    if not refs:raise FontError(T('Немає оригінальних латинських метрик для зіставлення.', 'There are no original Latin metrics to match against.'))
    refs.sort(key=lambda row:(row[0],row[1]))
    chosen=refs[0]
    analog=SPECIAL.get(ch)
    if analog in glyphs:
        preferred=next((r for r in refs if r[1]==analog),None)
        if preferred and preferred[0]<=chosen[0]+8: chosen=preferred
    return chosen[2],chosen[1],{'reference_score':chosen[0],
                                 'target_bbox':list(wanted),'reference_bbox':list(chosen[3])}

def apply_native_metric_pair(rec, source_body, reference_glyph, tracking_px=0, copy_vertical=False):
    """Copy both proven native horizontal metric words, optionally test -1/-2 px.

    Never change the original font's glyphs or Unicode lookup indices. Only
    new records are edited. Experimental tracking modifies word3 HIGH16 only.
    """
    if tracking_px not in (0,-1,-2):
        raise FontError(T('Проміжок: дозволено лише 0, -1 або -2 пікселі.', 'Spacing: only 0, -1 or -2 pixels are allowed.'))
    src=source_body[RECORD_BASE+reference_glyph.index*32:RECORD_BASE+(reference_glyph.index+1)*32]
    dest=bytearray(rec)
    old_w2,old_w3=struct.unpack_from('>II',dest,8)
    new_w2,new_w3=struct.unpack_from('>II',src,8)
    # Only modify the observed horizontal fixed-point halves. The low16
    # vertical data remains as in the proven v0.9 baseline, except that the
    # exact original Latin p may opt into its own native vertical offset.
    vert_part=(new_w2&0xffff) if copy_vertical else (old_w2&0xffff)
    struct.pack_into('>II',dest,8,(new_w2&0xffff0000)|vert_part,
                     (new_w3&0xffff0000)|(old_w3&0xffff))
    if tracking_px:
        word3=struct.unpack_from('>I',dest,12)[0]
        advance=(word3>>16)+tracking_px*64
        if not 4*64<=advance<=32*64:
            raise FontError(T('Небезпечна геометрія гліфа після корекції проміжку.', 'Unsafe glyph geometry after the spacing correction.'))
        struct.pack_into('>I',dest,12,(advance<<16)|(word3&65535))
    w2,w3=struct.unpack_from('>II',dest,8)
    original_w2,original_w3=struct.unpack_from('>II',src,8)
    before=(_s16(original_w2>>16)+_s16(original_w3>>16))/64
    after=(_s16(w2>>16)+_s16(w3>>16))/64
    return dest,{'original_native_advance_px':before,'new_advance_px':after,
                 'reference_unicode':f'U+{reference_glyph.codepoint:04X}',
                 'tracking_px':tracking_px,'original_native_vertical_copied':bool(copy_vertical)}


# ============================================================================
# native_font
# ============================================================================

PROFILE = 'STORM1_NUCC104_NFH_11110001'
SLOT = 28
PITCH = 32
# Transparent texels kept around every NEW cell. The game samples the atlas
# with filtering, so a neighbour whose ink touches its cell edge (the copied
# full-width dash right after «я») bled into the previous glyph as a blob.
GUTTER = PITCH - SLOT
ATLAS_WIDTH = 1024
FIRST_GLYPH_OFFSET = 0x458
RECORD_BYTES = 32
ORIGINAL_GLYPH_COUNT = 3683


def _be32(blob: bytes, offset: int) -> int:
    return struct.unpack_from('>I', blob, offset)[0]


def _encode_added_rows(alpha_bytes: bytes, w: int, h: int) -> bytes:
    if w % 4 or h % 4 or len(alpha_bytes) != w*h:
        raise FontError(T('Некоректні розміри додаткового атласу.', 'Invalid size of the additional atlas.'))
    result=bytearray()
    for by in range(0,h,4):
        for bx in range(0,w,4):
            block=[]
            for yy in range(by,by+4):
                block.extend(alpha_bytes[yy*w+bx: yy*w+bx+4])
            result.extend(_encode_bc3_alpha(block))
            # Existing original DXT5 texture uses white RGB565 endpoint and zero selectors.
            result.extend(b'\xff\xff\x00\x00\x00\x00\x00\x00')
    return bytes(result)


PREVIEW_LINES = ('Яка гарна пісня — моя!', 'Її їжак, ґедзь і єнот — це «я».',
                 'Ім’я: Наруто Узумакі. Яблуня, язик, маяк, моя, твоя.',
                 UKRAINIAN_UPPER, UKRAINIAN_LOWER, 'Naruto Uzumaki — Believe it! 0123456789')


def check_cell_isolation(alpha: bytes, width: int, height: int, cells, *, size: int=SLOT,
                         gutter: int=GUTTER) -> list[str]:
    """Return a description for every cell whose surrounding gutter has ink.

    `cells` = (character, x, y) inside the given alpha plane. An empty result
    means a filtered texture fetch can never pick up a neighbouring glyph.
    """
    problems=[]
    for ch,x,y in cells:
        x0,y0=max(0,x-gutter),max(0,y-gutter)
        x1,y1=min(width,x+size+gutter),min(height,y+size+gutter)
        if x+size>width or y+size>height:
            problems.append(T(f'«{ch}» поза атласом', f'«{ch}» is outside the atlas'));continue
        ink=False;stray=False
        for yy in range(y0,y1):
            row=alpha[yy*width+x0:yy*width+x1]
            inside_y=y<=yy<y+size
            for i,v in enumerate(row):
                if not v:continue
                if inside_y and x<=x0+i<x+size:ink=True
                else:stray=True
        if stray:problems.append(T(f'«{ch}» має сторонні пікселі поруч', f'«{ch}» has stray pixels next to it'))
        if not ink:problems.append(T(f'«{ch}» порожній після BC3', f'«{ch}» is empty after BC3 packing'))
    return problems


BASELINE_LETTERS = 'acemnorsuvwxzABDEFHIKLMNPRTUVXZ'
DESCENDER_LETTERS = 'gjpqy'


def infer_vertical_model(doc, alpha_img, body: bytes) -> dict:
    """Learn from the game's own Latin glyphs how a record places its cell vertically.

    Bytes 10-11 of a record (word2 low16) are a signed 1/64 px value. In sibling
    NFH fonts it is the height of the cell top ABOVE the baseline; a letter with
    a descender (p) is stored higher in its 28x28 cell and carries a smaller
    value. Rather than trust that, both readings are tested against the font:
    with the right one, letters that stand on the baseline end on one screen
    row and descenders end below it.

    sign: +1 = cell top above baseline, -1 = offset down from the line top,
          0 = no usable vertical data (all cells are drawn at one height).
    decisive: only one reading fits, so new records may be adjusted with it.
    """
    values={};bottoms={}
    for g in doc.glyphs:
        ch=chr(g.codepoint)
        if ch not in BASELINE_LETTERS and ch not in DESCENDER_LETTERS:continue
        box=alpha_img.crop((g.x,g.y,g.x+g.width,g.y+g.height)).getbbox()
        if not box:continue
        values[ch]=_s16(_be32(body,RECORD_BASE+g.index*32+8)&0xffff)/64
        bottoms[ch]=box[3]
    sit=[c for c in BASELINE_LETTERS if c in values]
    desc=[c for c in DESCENDER_LETTERS if c in values]
    result={'sign':0,'decisive':False,'baseline_letters':len(sit),'descender_letters':len(desc)}
    if len(sit)<8 or not desc or len(set(values.values()))==1:
        result['note']='Вертикальні значення у шрифті однакові або їх замало: висота комірок не моделюється.'
        return result
    fits={}
    for sign in (1,-1):
        rows=sorted(bottoms[c]-sign*values[c] for c in sit)
        middle=rows[len(rows)//2]
        spread=rows[-2]-rows[1]          # tolerate one outlier at each end
        below=[bottoms[c]-sign*values[c]-middle for c in desc]
        fits[sign]=spread<=2 and min(below)>=2
        result['spread_px_'+('ascent' if sign==1 else 'offset')]=spread
    if fits[1] or fits[-1]:
        result['sign']=1 if fits[1] else -1
        result['decisive']=fits[1]!=fits[-1]
    else:
        result['note']='Жодне тлумачення вертикального значення не узгоджується з латинськими літерами.'
    return result


def render_ingame_preview(atlas, table: dict, Image, *, lines=PREVIEW_LINES, scale: int=2,
                          added_rows: tuple[int,int]|None=None, vertical_sign: int=0) -> bytes:
    """Draw sample text the way the game does: quads cut from the PACKED atlas.

    Every quad is cut one texel wider than its cell on each side and scaled
    with bilinear filtering, so any neighbour bleeding is visible exactly as a
    blob next to the letter. Each cell is placed by its own record: horizontal
    bearing, advance and the vertical value (see infer_vertical_model).
    `table`: codepoint -> (x, y, w, h, offx64, right64, vertical64).
    """
    from io import BytesIO
    from PIL import ImageChops
    pad=14;line_h=42
    width=max(900,atlas.width)
    strip=None
    if added_rows:
        strip=atlas.crop((0,added_rows[0],atlas.width,added_rows[0]+added_rows[1]))
    height=pad*2+len(lines)*line_h*scale+(strip.height+pad if strip else 0)
    canvas=Image.new('L',(width,height),0)
    used=[table[ord(ch)] for line in lines for ch in line if ord(ch) in table]
    drop=lambda entry:-vertical_sign*entry[6]/64       # rows below the highest possible cell top
    highest=min((drop(e) for e in used),default=0)
    y=pad
    for line in lines:
        pen=float(pad)
        for ch in line:
            entry=table.get(ord(ch))
            if entry is None:
                pen+=10*scale;continue
            x,gy,w,h,offx,right,_vertical=entry
            box=(max(0,x-1),max(0,gy-1),min(atlas.width,x+w+1),min(atlas.height,gy+h+1))
            quad=atlas.crop(box)
            quad=quad.resize((quad.width*scale,quad.height*scale),Image.Resampling.BILINEAR)
            px=round(pen+(offx/64-(x-box[0]))*scale)
            top=round(y+(drop(entry)-highest-(gy-box[1]))*scale)
            under=canvas.crop((px,top,px+quad.width,top+quad.height))
            canvas.paste(ImageChops.lighter(under,quad),(px,top))
            advance=(offx+right)/64
            if not 2<=advance<=40:advance=w
            pen+=advance*scale
            if pen>width-40*scale:break
        y+=line_h*scale
    if strip:
        canvas.paste(strip,(pad,y))
    out=Image.new('RGB',canvas.size,(22,27,38))
    out.paste(Image.new('RGB',canvas.size,(238,242,248)),(0,0),canvas)
    stream=BytesIO();out.save(stream,format='PNG')
    return stream.getvalue()


def build_additive_font(naruto_source: Path | str, ttf_path: Path | str,
                        *, include_symbols: bool=True, include_other_symbols: bool=False,
                        with_preview: bool=True, lowercase_r_percent: int=100, latin_p_for_r: bool=False,
                        latin_y_for_u: bool=False, metrics_mode: str='stable', tracking_px: int=0) -> tuple[bytes, dict, bytes | None]:
    """Create an experimental XFBIN with appended Ukrainian glyphs, no deletions.

    The original BC3 blocks and all existing glyph outlines/metrics are retained.
    When adding characters to an occupied Unicode page (for example U+2014),
    ONLY the word6 page-local order/count of glyphs on that page is reindexed.
    Only standard free-form TTF/OTF rendering from the user-selected font is used.
    """
    if metrics_mode not in ('stable','native_matched'):
        raise FontError(T('Непідтримуваний профіль метрик.', 'Unsupported metrics profile.'))
    if tracking_px not in (0,-1,-2) or (metrics_mode=='stable' and tracking_px!=0):
        raise FontError(T('Для зменшення інтервалу потрібен режим відповідних латинських метрик.', 'Reducing the spacing requires the matched Latin metrics mode.'))
    if latin_p_for_r and lowercase_r_percent!=100:
        raise FontError(T('Для точної копії оригінальної p встановіть розмір малої «р» 100%.', 'For an exact copy of the original p, set the size of the lowercase Cyrillic er (U+0440) to 100%.'))
    Image,ImageFont,ImageDraw,TTFont = _libraries()
    naruto_source=Path(naruto_source);ttf_path=Path(ttf_path)
    if not ttf_path.is_file() or ttf_path.suffix.lower() not in ('.ttf','.otf'):
        raise FontError(T('Виберіть TTF або OTF.', 'Choose a TTF or OTF file.'))
    if ttf_path.stat().st_size>30*1024*1024:
        raise FontError(T('Файл TTF/OTF завеликий.', 'The TTF/OTF file is too large.'))
    doc=load_font(naruto_source) # strict Naruto format NUCC 104, 3683 rows, BC3
    if doc.width!=ATLAS_WIDTH or len(doc.glyphs)!=ORIGINAL_GLYPH_COUNT:
        raise FontError(T('Профіль не збігається з перевіреним шрифтом STORM 1.', 'The profile does not match the verified STORM 1 font.'))
    codes_existing={g.codepoint for g in doc.glyphs}
    supported=_font_cmap(ttf_path,TTFont)
    missing=[ch for ch in UKRAINIAN_ALPHABET if ord(ch) not in codes_existing]
    unsupported=[ch for ch in missing if ord(ch) not in supported]
    if unsupported:
        raise FontError(T('Обраний TTF не містить літер: '+''.join(unsupported), 'The chosen TTF does not contain these letters: '+''.join(unsupported)))
    # A UTF-16 high-byte page may already contain original Naruto glyphs.
    # In that case insert the new codepoint into its sorted position and safely
    # recompute page-local order/count word6 for THAT PAGE ONLY. Other 28 bytes
    # of every original glyph record stay bit-exact. This is needed for em dash
    # U+2014, because source already has U+2015 in occupied U+20xx.
    # Em dash is enabled independently, by default. Other optional punctuation
    # is an explicit experimental opt-in: its Unicode pages (including the
    # Latin U+00xx page) would otherwise force reindexing many original glyphs.
    wanted=[ch for ch in OPTIONAL_SYMBOLS
            if (ch=='—' and include_symbols) or (ch!='—' and include_other_symbols)]
    optional=[ch for ch in wanted if ord(ch) not in codes_existing
              and (ord(ch) in supported or ch=='—')]
    unavailable_optional=[ch for ch in wanted if ord(ch) not in codes_existing
                          and ord(ch) not in supported and ch!='—']
    added=missing+optional
    if not added:
        raise FontError(T('Немає відсутніх літер для додавання.', 'There are no missing letters to add.'))
    if len(added)>128 or any(ord(ch)>>8 != 4 for ch in missing):
        raise FontError(T('Непідтримуваний набір гліфів.', 'Unsupported glyph set.'))
    columns=doc.width//PITCH
    extra_rows=math.ceil(len(added)/columns)
    if extra_rows>8:raise FontError(T('Завелика кількість нових рядків у текстурі.', 'Too many new rows in the texture.'))
    extra_height=extra_rows*PITCH+GUTTER
    new_height=doc.height+extra_height
    if new_height>65535:raise FontError(T('Завелика текстура.', 'The texture is too large.'))
    font_size=_calibrate_pixel_size(ttf_path,ImageFont)
    font=ImageFont.truetype(str(ttf_path),font_size)
    # The source atlas is the only source of original Latin p and its native
    # metric pairs; TTF never substitutes for game-native p in this mode.
    source_alpha_img=Image.frombytes('L',(doc.width,doc.height),doc.alpha_bytes())
    source_glyphs={g.codepoint:g for g in doc.glyphs}
    # Only append new rows, starting immediately after original texture height.
    new_alpha=Image.new('L',(doc.width,extra_height),0)
    inserted=[];scaled=[];lifts={}
    for j,ch in enumerate(added):
        x=(j%columns)*PITCH
        y=GUTTER+(j//columns)*PITCH
        alias_cp = (ord('p') if ch=='р' and latin_p_for_r
                    else ord('y') if ch=='у' and latin_y_for_u
                    else 0x2015 if ch=='—' else None)
        if alias_cp is not None:
            # Reuse the game's authentic ink, not the user's TTF glyph.
            # U+2015 and U+2014 are distinct codepoints; only the bitmap is
            # deliberately reused, with the target's OWN Unicode mapping.
            original_glyph=source_glyphs[alias_cp]
            cell=source_alpha_img.crop((original_glyph.x,original_glyph.y,
                                       original_glyph.x+SLOT,original_glyph.y+SLOT))
            resized=False
        else:
            cell, resized=_draw_glyph(ch,font,Image,ImageDraw)
            if ch=='р': cell=shrink_small_r(cell,lowercase_r_percent,Image)
        new_alpha.paste(cell,(x,y))
        if resized:scaled.append(ch)
        if alias_cp is None and cell.info.get('lift'):lifts[ord(ch)]=cell.info['lift']
        inserted.append((ord(ch),x,doc.height+y,ch))
    new_rows_bc3=_encode_added_rows(new_alpha.tobytes(),doc.width,extra_height)
    if len(new_rows_bc3)!=extra_height*doc.width:
        raise FontError(T('Помилка пакування DXT5.', 'DXT5 packing error.'))
    original = parse_xfbin(naruto_source)
    ck=original.chunks[0]
    body=original.blob[ck.data_offset:ck.end]
    old_count=_be32(body,0x10)
    old_ntp=FIRST_GLYPH_OFFSET+old_count*RECORD_BYTES
    old_ntp_blob=body[old_ntp:]
    old_ntp_head=bytearray(old_ntp_blob[:0x60])
    original_pixels=old_ntp_blob[0x60:]
    if len(original_pixels)!=doc.width*doc.height:
        raise FontError(T('Розмір вихідної текстури не збігається.', 'The source texture size does not match.'))
    new_pixels=original_pixels+new_rows_bc3
    new_total_pixels=doc.width*new_height
    if len(new_pixels)!=new_total_pixels:raise FontError(T('Новий розмір DXT5 не збігається.', 'The new DXT5 size does not match.'))
    struct.pack_into('>I',old_ntp_head,0x10,new_total_pixels+0x50)
    struct.pack_into('>I',old_ntp_head,0x18,new_total_pixels)
    struct.pack_into('>I',old_ntp_head,0x24,(doc.width<<16)|new_height)
    # A glyph record includes page-local index and remaining count in word6.
    # We keep every source record byte-identical and construct new records for
    # pages that were absent in the original glyph map, primarily U+04.
    existing_rows={g.codepoint:body[FIRST_GLYPH_OFFSET+g.index*32:FIRST_GLYPH_OFFSET+(g.index+1)*32]
                   for g in doc.glyphs}
    template_upper=bytearray(existing_rows[ord('A')])
    template_lower=bytearray(existing_rows[ord('a')])
    template_symbol=bytearray(existing_rows[ord('?')])
    new_rows={}
    new_per_page={}
    metrics_report={}
    vertical=infer_vertical_model(doc,source_alpha_img,body)
    lift_fixed=[]
    for cp,x,y,ch in inserted:
        p=cp>>8
        new_per_page.setdefault(p,[]).append(cp)
        base=template_upper if ch.isupper() else template_lower if ch.islower() else template_symbol
        rec=bytearray(base)
        struct.pack_into('>I',rec,4,(x<<16)|y)
        struct.pack_into('>I',rec,20,(SLOT<<24)|(SLOT<<16)|cp)
        reference_alias = (ord('p') if ch=='р' and latin_p_for_r
                           else ord('y') if ch=='у' and latin_y_for_u
                           else 0x2015 if ch=='—' else None)
        if metrics_mode=='native_matched':
            mask=new_alpha.crop((x,y-doc.height,x+SLOT,y-doc.height+SLOT))
            if reference_alias is not None:
                ref=source_glyphs[reference_alias]
                refchar=chr(reference_alias)
                match_details={'reference_score':0,'target_bbox':list(mask.getbbox()),
                               'reference_bbox':list(mask.getbbox())}
            else:
                ref,refchar,match_details=find_native_metrics(doc,source_alpha_img,mask,ch)
            rec,metrics=apply_native_metric_pair(rec,body,ref,tracking_px,
                                                  copy_vertical=(reference_alias is not None))
            metrics_report[f'U+{cp:04X}']={**match_details,**metrics,
                                           'character':ch,'latin_reference':refchar}
        elif reference_alias is not None:
            # A bitmap copied from the game must keep that glyph's own placement:
            # with the template's values the game drew «р» as high as a capital.
            rec,_=apply_native_metric_pair(rec,body,source_glyphs[reference_alias],0,copy_vertical=True)
        if cp in lifts and vertical['decisive']:
            word2=_be32(rec,8)
            moved=_s16(word2&0xffff)-vertical['sign']*lifts[cp]*64
            if -32768<=moved<=32767:
                struct.pack_into('>I',rec,8,(word2&0xffff0000)|(moved&0xffff))
                lift_fixed.append(ch)
        new_rows[cp]=rec
    # Every glyph in a Unicode page stores a page-local index and remaining
    # count in word6. Adding — to existing page U+20xx requires reindexing the
    # 14 pre-existing glyphs on that page. DO NOT alter their art, metrics,
    # coordinates, Unicode, or any other byte of their original 32-byte record.
    all_rows={**existing_rows, **{cp:bytes(rec) for cp,rec in new_rows.items()}}
    all_codes=sorted(all_rows)
    reindexed_original={}
    for page,new_codes in new_per_page.items():
        page_codes=[cp for cp in all_codes if cp>>8==page]
        if len(page_codes)>256:
            raise FontError(T(f'Переповнено сторінку Unicode U+{page:02X}xx.', f'Unicode page U+{page:02X}xx has overflowed.'))
        for local,cp in enumerate(page_codes):
            desired=(local<<24)|((len(page_codes)-local-1)<<16)|0x4000
            row=bytearray(all_rows[cp])
            previous=_be32(row,24)
            struct.pack_into('>I',row,24,desired)
            all_rows[cp]=bytes(row)
            if cp in existing_rows and previous!=desired:
                reindexed_original[cp]=(previous,desired)
    new_per_page={p:sorted(v) for p,v in new_per_page.items()}
    if len(all_codes)!=len(doc.glyphs)+len(added):
        raise FontError(T('Колізія Unicode.', 'Unicode collision.'))
    new_count=len(all_codes)
    new_ntp_offset=FIRST_GLYPH_OFFSET+new_count*RECORD_BYTES
    head=bytearray(body[:FIRST_GLYPH_OFFSET])
    struct.pack_into('>I',head,0,new_ntp_offset-8)
    struct.pack_into('>I',head,4,0x60+new_total_pixels)
    struct.pack_into('>I',head,0x10,new_count)
    head[0x58:FIRST_GLYPH_OFFSET]=_original_lookup(all_codes)
    newbody=bytes(head)+b''.join(all_rows[cp] for cp in all_codes)+bytes(old_ntp_head)+new_pixels
    if len(newbody)!=len(body)+len(added)*RECORD_BYTES+len(new_rows_bc3):
        raise FontError(T('Обсяг нового тіла чанка не збігається.', 'The size of the new chunk body does not match.'))
    prefix=bytearray(original.blob[:ck.data_offset])
    struct.pack_into('>I',prefix,ck.header_offset,len(newbody))
    rebuilt=bytes(prefix)+newbody+original.blob[ck.end:]
    # Verify the container and all source resources persisted exactly.
    with tempfile.TemporaryDirectory(prefix='stormfont_add_') as td:
        out=Path(td)/'extended.xfbin'
        out.write_bytes(rebuilt)
        res=parse_xfbin(out)
        if len(res.chunks)!=len(original.chunks) or res.nucc_version!=104:
            raise FontError(T('Після вставляння пошкоджено контейнер XFBIN.', 'The XFBIN container is damaged after the insertion.'))
        rb=res.blob[res.chunks[0].data_offset:res.chunks[0].end]
        if _be32(rb,0x10)!=new_count or _be32(rb,0)!=new_ntp_offset-8 or rb[new_ntp_offset:new_ntp_offset+4]!=b'NTP3':
            raise FontError(T('Некоректний NFH/NTP3 після збирання.', 'Invalid NFH/NTP3 after the build.'))
        codes=[struct.unpack_from('>H',rb,FIRST_GLYPH_OFFSET+i*32+22)[0] for i in range(new_count)]
        if codes!=all_codes or len(codes)!=len(set(codes)):
            raise FontError(T('Таблиця нових Unicode не читається.', 'The new Unicode table cannot be read.'))
        if rb[0x58:FIRST_GLYPH_OFFSET]!=_original_lookup(all_codes):
            raise FontError(T('Неправильна таблиця індексів Naruto Unicode.', 'Wrong Naruto Unicode index table.'))
        for page in new_per_page:
            page_codes=[cp for cp in all_codes if cp>>8==page]
            for local,cp in enumerate(page_codes):
                ix=all_codes.index(cp)
                vals=struct.unpack_from('>8I',rb,FIRST_GLYPH_OFFSET+ix*32)
                expect=(local<<24)|((len(page_codes)-local-1)<<16)|0x4000
                if vals[0]!=0x11110001 or vals[6]!=expect or vals[5]&65535!=cp:
                    raise FontError(T('Пошкоджено індекс гліфа сторінки: '+hex(cp), 'The page glyph index is damaged: '+hex(cp)))
        for g in doc.glyphs:
            i=all_codes.index(g.codepoint)
            got=rb[FIRST_GLYPH_OFFSET+i*32:FIRST_GLYPH_OFFSET+(i+1)*32]
            before=existing_rows[g.codepoint]
            if g.codepoint in reindexed_original:
                if got[:24]!=before[:24] or got[28:]!=before[28:]:
                    raise FontError(T('Змінено оригінальну графіку або метрики: '+hex(g.codepoint), 'The original graphics or metrics were changed: '+hex(g.codepoint)))
            elif got!=before:
                raise FontError(T('Пошкоджено оригінальний запис гліфа: '+hex(g.codepoint), 'The original glyph record is damaged: '+hex(g.codepoint)))
        pixel_start=new_ntp_offset+0x60
        if rb[pixel_start:pixel_start+len(original_pixels)]!=original_pixels:
            raise FontError(T('Змінено вихідні DXT5-блоки!', 'The source DXT5 blocks were changed!'))
        if rb[pixel_start+len(original_pixels):]!=new_rows_bc3:
            raise FontError(T('Пошкоджено додаткові DXT5-рядки.', 'The additional DXT5 rows are damaged.'))
        if res.blob[res.chunks[1].header_offset:]!=original.blob[original.chunks[1].header_offset:]:
            raise FontError(T('Пошкоджено службовий чанк сторінки.', 'The service page chunk is damaged.'))
    # Read the atlas BACK from the packed BC3 (what the game will sample) and
    # prove that nothing sits next to any new glyph cell.
    packed_alpha=decode_bc3_alpha(new_rows_bc3,doc.width,extra_height)
    isolation=check_cell_isolation(packed_alpha,doc.width,extra_height,
                                   [(ch,x,y-doc.height) for cp,x,y,ch in inserted])
    if isolation:
        raise FontError(T('Гліфи виходять за межі комірки атласу: '+', '.join(isolation[:12]), 'Glyphs extend beyond their atlas cell: '+', '.join(isolation[:12])))
    if with_preview:
        full_alpha=Image.frombytes('L',(doc.width,new_height),doc.alpha_bytes()+packed_alpha)
        table={}
        for cp in all_codes:
            v=struct.unpack_from('>8I',all_rows[cp],0)
            table[cp]=(v[1]>>16,v[1]&65535,v[5]>>24,(v[5]>>16)&255,_s16(v[2]>>16),_s16(v[3]>>16),
                       _s16(v[2]&0xffff))
        preview=render_ingame_preview(full_alpha,table,Image,added_rows=(doc.height,extra_height),
                                      vertical_sign=vertical['sign'])
    else:
        preview=None
    result={
      'mode':'ADD (EXPERIMENTAL)',
      'source_naruto_sha256':doc.digest, 'ttf_sha256':sha(ttf_path.read_bytes()),
      'ttf_name':ttf_path.name, 'pixel_size':font_size,
      'original_glyph_count':len(doc.glyphs), 'new_glyph_count':new_count,
      'added_glyph_count':len(added),'added_characters':''.join(added),
      'optional_chars_added':''.join(optional),
      'optional_chars_unavailable_in_ttf':''.join(unavailable_optional),
      'existing_glyphs_page_metadata_updated':len(reindexed_original),
      'existing_page_metadata_updates':{f'U+{cp:04X}':{'before':f'{a:08X}','after':f'{b:08X}'} for cp,(a,b) in reindexed_original.items()},
      'glyphs_scaled_to_fit':''.join(scaled),
      'metric_policy':('COPIED_NATIVE_LATIN_PAIRS' if metrics_mode=='native_matched'
                        else 'RESTORED_v0.9_UNMODIFIED'),
      'tracking_px':tracking_px,
      'vertical_model':vertical,
      'vertical_compensated_for_lifted_glyphs':''.join(lift_fixed),
      'metrics_analysis':metrics_report,
      'cyrillic_r_source':('ORIGINAL_NARUTO_LATIN_p' if latin_p_for_r else 'TTF_U0440'),
      'cyrillic_u_source':('ORIGINAL_NARUTO_LATIN_y' if latin_y_for_u else 'TTF_U0443'),
      'em_dash_source':('ORIGINAL_NARUTO_U2015' if '—' in added else 'NOT_ADDED'),
      'lowercase_r_bitmap_percent':lowercase_r_percent,
      'cyrillic_r_bitmap_from_latin_p':latin_p_for_r,
      'atlas_original':[doc.width,doc.height],'atlas_new':[doc.width,new_height],
      'original_glyph_records_preserved':len(doc.glyphs),
      'original_texture_data_preserved':True,
      'output_sha256':sha(rebuilt),
      'warning':'НЕ ПЕРЕВІРЕНО У ГРІ! Збільшений NFH/NTP3 може не підтримуватися рушієм STORM 1.',
      'paths_note':'Українська абетка — з TTF/OTF, крім налаштованих копій оригінальних гліфів Наруто (р→p, у→y); тире — копія U+2015. '}
    return rebuilt,result,preview


def generate_additive_font(source, ttf,workspace,output_root,relative_path,*,
                           include_symbols=True,include_other_symbols=False,
                           override=False,log=None,
                           lowercase_r_percent=100, latin_p_for_r=False,
                           latin_y_for_u=False, metrics_mode='stable', tracking_px=0) -> dict:
    """Build + write to a separate data_win32 path; never replace game original."""
    rel=clean_rel(relative_path)
    src=Path(source).resolve()
    output_root=Path(output_root)
    target=output_root/'data_win32'/rel
    if target.resolve()==src:
        raise FontError(T('Заборонено записувати поверх оригінального шрифту.', 'Writing over the original font is not allowed.'))
    if target.exists() and not override:
        raise FontError(T('Шрифт уже існує в data_win32. Збережіть його або явно дозвольте .bak.', 'The font already exists in data_win32. Save it elsewhere or explicitly allow a .bak.'))
    dest_zip=output_root/'STORM1_UA_FONT_ADDITIVE_EXPERIMENTAL.zip'
    if dest_zip.exists() and not override:
        raise FontError(T('Експериментальний ZIP уже існує. Заберіть або дозвольте резервну копію.', 'The experimental ZIP already exists. Move it away or allow a backup.'))
    if log:log(T('Збирання адитивного NFH із TTF (усі японські гліфи будуть збережені)…', 'Building the additive NFH from the TTF (all Japanese glyphs will be kept)…'))
    blob,report,preview=build_additive_font(src,ttf,include_symbols=include_symbols,
                       include_other_symbols=include_other_symbols,
                       lowercase_r_percent=lowercase_r_percent,
                       latin_p_for_r=latin_p_for_r,latin_y_for_u=latin_y_for_u,
                       metrics_mode=metrics_mode,tracking_px=tracking_px)
    workroot=Path(workspace)/'Font_Additive'
    workroot.mkdir(parents=True,exist_ok=True)
    folder=workroot/('font_'+report['output_sha256'][:12])
    folder.mkdir(parents=False,exist_ok=True)
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if preview is not None:
        (folder/'added_glyphs_preview.png').write_bytes(preview)
    target.parent.mkdir(parents=True,exist_ok=True)
    for existing in [target,dest_zip]:
        if existing.exists():
            backup=existing.with_name(existing.name+'.bak')
            if not backup.exists():shutil.copy2(existing,backup)  # keep the earliest copy
    fd,tmp=tempfile.mkstemp(dir=target.parent,prefix='.stormfont_',suffix='.tmp')
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(blob);f.flush();os.fsync(f.fileno())
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    fd,tmp=tempfile.mkstemp(dir=output_root,prefix='.stormfont_zip_',suffix='.tmp');os.close(fd)
    try:
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
            z.write(target,arcname='data_win32/'+rel.as_posix())
            z.write(folder/'report.json',arcname='font_additive_report.json')
        os.replace(tmp,dest_zip)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    if log:log(T('Готово: '+str(report['new_glyph_count'])+' символів загалом, японські гліфи збережено. НЕ ТЕСТОВАНО В ГРІ.', 'Done: '+str(report['new_glyph_count'])+' characters in total, Japanese glyphs kept. NOT TESTED IN THE GAME.'))
    return {'new_font':str(target),'zip':str(dest_zip),'preview':str(folder/'added_glyphs_preview.png'),
            'report':str(folder/'report.json'),**report}
