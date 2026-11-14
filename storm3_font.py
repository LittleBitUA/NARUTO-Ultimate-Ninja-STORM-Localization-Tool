# -*- coding: utf-8 -*-
"""STORM 3 font: read-only audit/comparison and additive Ukrainian glyphs from TTF (512x512 atlas)."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
from dataclasses import dataclass
from io import BytesIO
import os
import re
import tempfile
import zipfile

from xfbin import parse_xfbin, FormatError
from storm1_font import decode_bc3_alpha, _encode_bc3_alpha, UKRAINIAN_ALPHABET, _libraries, _font_cmap, _calibrate_pixel_size
from storm2_font import _lookup, _page_metadata, BC3_OPAQUE_WHITE
from audit import is_within
from i18n import T


# ============================================================================
# font_audit
# ============================================================================

VERSION = '2.9.1-font-research'
PROFILE = 'STORM3_NUCC104_NFH_11110002_512x256_BC3'
GLYPH_BASE = 0x458
GLYPH_BYTES = 32
NTP_HEADER_SIZE = 0x60
OUT_JSON = 'STORM3_FONT_COMPARISON.json'
OUT_TSV = 'STORM3_FONT_GLYPHS.tsv'
OUT_MD = 'STORM3_FONT_COMPARISON_UA.md'
UKRAINIAN_REQUIRED = ''.join(dict.fromkeys(UKRAINIAN_ALPHABET))

class Storm3FontError(ValueError):
    pass


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('>I', data, offset)[0]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _charset(values: list[int]) -> str:
    return ''.join(chr(c) for c in values if c>=32 and not 0xD800<=c<=0xDFFF)


def inspect(path: str | Path) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise Storm3FontError(T(f'Файл не знайдено: {p}', f'File not found: {p}'))
    try:
        xf = parse_xfbin(p)
    except (OSError, ValueError, FormatError) as exc:
        raise Storm3FontError(T(f'{p.name}: XFBIN не вдалося прочитати: {exc}', f'{p.name}: could not read the XFBIN: {exc}')) from exc
    if xf.nucc_version != 104 or len(xf.chunks)!=2 or [c.type_name for c in xf.chunks] != ['nuccChunkFont','nuccChunkPage']:
        raise Storm3FontError(T(f'{p.name}: очікуються NUCC 104 та два чанки Font / Page.', f'{p.name}: expected NUCC 104 and the two chunks Font / Page.'))
    chunk=xf.chunks[0]
    blob=xf.blob[chunk.data_offset:chunk.end]
    if len(blob)<GLYPH_BASE+NTP_HEADER_SIZE or blob[8:12]!=b'NFH\0' or _u32(blob,12)!=28:
        raise Storm3FontError(T(f'{p.name}: непідтримуваний заголовок NFH.', f'{p.name}: unsupported NFH header.'))
    count=_u32(blob,16)
    if not 1<=count<=512:
        raise Storm3FontError(T(f'{p.name}: недопустима кількість гліфів: {count}.', f'{p.name}: invalid glyph count: {count}.'))
    ntp=GLYPH_BASE+count*GLYPH_BYTES
    if ntp+NTP_HEADER_SIZE>len(blob) or blob[ntp:ntp+4]!=b'NTP3' or blob[ntp+0x40:ntp+0x44]!=b'eXt\0' or blob[ntp+0x50:ntp+0x54]!=b'GIDX':
        raise Storm3FontError(T(f'{p.name}: структуру NTP3 не підтверджено.', f'{p.name}: the NTP3 structure could not be confirmed.'))
    if _u32(blob,0)!=ntp-8 or _u32(blob,4)!=len(blob)-ntp:
        raise Storm3FontError(T(f'{p.name}: розміри розділів NFH/NTP3 не відповідають заголовку.', f'{p.name}: the NFH/NTP3 section sizes do not match the header.'))
    packed=_u32(blob,ntp+0x24)
    width,height=packed>>16,packed&65535
    if (width,height)!=(512,256) or _u32(blob,ntp+0x18)!=width*height or len(blob)-ntp-NTP_HEADER_SIZE!=width*height:
        raise Storm3FontError(T(f'{p.name}: підтримується тільки підтверджений зразок 512×256 BC3.', f'{p.name}: only the confirmed 512×256 BC3 format is supported.'))
    if _u32(blob,ntp+0x10)!=width*height+0x50:
        raise Storm3FontError(T(f'{p.name}: невідома довжина NTP3.', f'{p.name}: unknown NTP3 length.'))
    glyphs=[]
    codes=[]
    for i in range(count):
        v=struct.unpack_from('>8I',blob,GLYPH_BASE+i*GLYPH_BYTES)
        cp=v[5]&0xffff; x=v[1]>>16; y=v[1]&0xffff
        w=v[5]>>24;h=(v[5]>>16)&255
        if v[0]!=0x11110002 or v[7]!=0 or not 1<=cp<=0xffff or 0xd800<=cp<=0xdfff or w>64 or h>64 or x+w>width or y+h>height:
            raise Storm3FontError(T(f'{p.name}: недійсний запис гліфа #{i}, U+{cp:04X}.', f'{p.name}: invalid glyph record #{i}, U+{cp:04X}.'))
        glyphs.append({'index':i,'unicode':f'U+{cp:04X}', 'character':chr(cp) if cp>=32 else '',
                       'x':x,'y':y,'width':w,'height':h,
                       'advance_64ths':(v[3]>>16), 'advance_px':(v[3]>>16)/64,
                       'page_index_raw':v[6]})
        codes.append(cp)
    if codes!=sorted(set(codes)):
        raise Storm3FontError(T(f'{p.name}: Unicode не впорядковано або повторюється.', f'{p.name}: the Unicode values are not sorted or are repeated.'))
    if blob[0x58:GLYPH_BASE]!=_lookup(codes):
        raise Storm3FontError(T(f'{p.name}: таблиця Unicode lookup не збігається з набором гліфів.', f'{p.name}: the Unicode lookup table does not match the glyph set.'))
    metadata=_page_metadata(codes)
    for i,cp in enumerate(codes):
        if glyphs[i]['page_index_raw']!=metadata[cp]:
            raise Storm3FontError(T(f'{p.name}: помилковий сторінковий індекс гліфа {i}.', f'{p.name}: wrong page index for glyph {i}.'))
    raw=blob[ntp+NTP_HEADER_SIZE:]
    alpha=decode_bc3_alpha(raw,width,height)
    actual_bottom=max((i//width)+1 for i,a in enumerate(alpha) if a) if any(alpha) else 0
    bbox_bottom=max((g['y']+g['height'] for g in glyphs),default=0)
    # Inspect unused last full-BC3 rows, WITHOUT exporting font imagery.
    scan_y=(bbox_bottom+3)//4*4
    color_pairs={}
    color_selectors={}
    for by in range(scan_y//4,height//4):
        for bx in range(width//4):
            k=(by*(width//4)+bx)*16
            ep=raw[k+8:k+12].hex()
            selectors=raw[k+12:k+16].hex()
            color_pairs[ep]=color_pairs.get(ep,0)+1
            color_selectors[selectors]=color_selectors.get(selectors,0)+1
    font_name=blob[0x18:0x38].split(b'\0')[0].decode('ascii','replace')
    variant=blob[0x38:0x58].split(b'\0')[0].decode('ascii','replace')
    present=set(codes)
    cy=[cp for cp in codes if 0x400<=cp<=0x52f]
    result={'profile':PROFILE,'file_name':p.name,'path':str(p),'sha256':xf.sha256,
            'size_bytes':len(xf.blob),'font_name':font_name,'variant':variant,
            'nucc_version':xf.nucc_version,'chunks':[c.type_name for c in xf.chunks],
            'glyph_count':count,'cyrillic_glyphs':len(cy),
            'cyrillic':_charset(cy),'missing_ukrainian':''.join(ch for ch in UKRAINIAN_REQUIRED if ord(ch) not in present),
            'present_ukrainian':''.join(ch for ch in UKRAINIAN_REQUIRED if ord(ch) in present),
            'atlas_dimensions':[width,height], 'ntp_offset_in_font_chunk':ntp,
            'font_chunk_offset':chunk.data_offset,'font_chunk_size':len(blob),
            'occupied_bbox_bottom_exclusive':bbox_bottom,'actual_nontransparent_bottom_exclusive':actual_bottom,
            'unused_rows_by_bbox':height-bbox_bottom,'unused_rows_after_bc3_alignment':height-scan_y,
            'blank_alpha_below_bbox':not any(alpha[bbox_bottom*width:]),
            'unused_region_color_endpoints':color_pairs,
            'unused_region_color_selectors':color_selectors,
            'unicode_lookup_verified':True,'page_metadata_verified':True,
            'glyphs':glyphs}
    return result


def compare(a: dict,b: dict)->dict:
    sa={int(row['unicode'][2:],16) for row in a['glyphs']}
    sb={int(row['unicode'][2:],16) for row in b['glyphs']}
    return {'font_a':a,'font_b':b,'same_binary':a['sha256']==b['sha256'],
            'common_codepoints':len(sa&sb),
            'only_a_codepoints':[f'U+{x:04X}' for x in sorted(sa-sb)],
            'only_b_codepoints':[f'U+{x:04X}' for x in sorted(sb-sa)],
            'same_font_family':a['font_name']==b['font_name']}


def _safe_output_directory(results:dict,output:str|Path)->Path:
    root=Path(output).expanduser().resolve()
    for src in [results['font_a']['path'],results['font_b']['path']]:
        source=Path(src).resolve()
        if root==source or root==source.parent:
            raise Storm3FontError(T('Папка звітів не повинна збігатися з папкою оригінальних файлів або бути всередині неї.', 'The report folder must not be the same as the folder of the original files or be inside it.'))
    name=datetime.now().strftime('STORM3_FONT_AUDIT_%Y%m%d_%H%M%S_%f')
    dest=root/name
    dest.mkdir(parents=True,exist_ok=False)
    return dest


def write_reports(results:dict,output:str|Path)->dict:
    folder=_safe_output_directory(results,output)
    a,b=results['font_a'],results['font_b']
    (folder/OUT_JSON).write_text(json.dumps({'version':VERSION,'created_utc':datetime.now(timezone.utc).isoformat(),**results},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (folder/OUT_TSV).open('w',encoding='utf-8-sig',newline='') as fp:
        w=csv.writer(fp,delimiter='\t',lineterminator='\r\n')
        w.writerow(['Файл','Назва шрифту','№','Unicode','Символ','X','Y','Ширина','Висота','Advance (px)','Сторінковий індекс'])
        for font in [a,b]:
            for g in font['glyphs']:
                w.writerow([font['file_name'],font['font_name'],g['index'],g['unicode'],g['character'],g['x'],g['y'],g['width'],g['height'],g['advance_px'],f"0x{g['page_index_raw']:08X}"])
    doc=[
        '# STORM 3 — незалежний порівняльний аналіз NFH/NTP3',
        '',f'Профіль: `{PROFILE}`. Режим **лише читання**, оригінали не змінювалися.',
        '', '| Властивість | Зразок A | Зразок B |','|---|---|---|',
        f"| Файл | `{a['file_name']}` | `{b['file_name']}` |",
        f"| SHA256 | `{a['sha256']}` | `{b['sha256']}` |",
        f"| Назва шрифту | {a['font_name']} | {b['font_name']} |",
        f"| Кількість гліфів | {a['glyph_count']} | {b['glyph_count']} |",
        f"| Кириличних Unicode | {a['cyrillic_glyphs']} | {b['cyrillic_glyphs']} |",
        f"| Атлас | {a['atlas_dimensions'][0]}×{a['atlas_dimensions'][1]} | {b['atlas_dimensions'][0]}×{b['atlas_dimensions'][1]} |",
        f"| Кінець зайнятої ділянки | y={a['occupied_bbox_bottom_exclusive']} | y={b['occupied_bbox_bottom_exclusive']} |",
        f"| Прозорий залишок атласу | {a['unused_rows_by_bbox']} рядків | {b['unused_rows_by_bbox']} рядків |",
        f"| Відсутні українські літери | {a['missing_ukrainian']} | {b['missing_ukrainian']} |",
        '', '## Порівняння таблиць Unicode',
        f"Спільних кодових позицій: {results['common_codepoints']}. Лише в A: {len(results['only_a_codepoints'])}; лише в B: {len(results['only_b_codepoints'])}.",
        f"- Лише A: {', '.join(results['only_a_codepoints'])}",
        f"- Лише B: {', '.join(results['only_b_codepoints'])}",
        '', '## Технічні висновки',
        '- Обидва зразки проходять перевірку NUCC 104 / NFH / NTP3 / BC3 512×256, записів гліфів 32 байти.',
        '- Таблиці сортування, Unicode lookup та сторінкові індекси перевірені за реальними записами.',
        '- Вільне місце в атласі **не є доказом**, що рушій прийме додані гліфи. Для запису потрібні оновлення NFH, lookup, індексів і довжин NUCC та повторна ігрова перевірка.',
        '- Не можна механічно переносити атлас STORM 2 (512×512) до STORM 3 (512×256).',
        '- Оригінали наданих двох файлів не включено до звіту або пакета програми.',
        '', '## Вміст файлів звіту',
        '- JSON — перевірені структурні дані та метрики, без гліфових зображень.',
        '- TSV — символи, координати, розміри та горизонтальні метрики.',
        '- MD — цей короткий порівняльний звіт.',
    ]
    (folder/OUT_MD).write_text('\n'.join(doc)+'\n',encoding='utf-8')
    return {'folder':str(folder),'json':str(folder/OUT_JSON),'tsv':str(folder/OUT_TSV),'md':str(folder/OUT_MD),'glyphs':[a['glyph_count'],b['glyph_count']]}


def run(font_a: str|Path,font_b: str|Path,output:str|Path, *,log=None)->dict:
    if Path(font_a).expanduser().resolve()==Path(font_b).expanduser().resolve():
        raise Storm3FontError(T('Для порівняння виберіть два різні файли.', 'Choose two different files to compare.'))
    a=inspect(font_a)
    b=inspect(font_b)
    if log:
        log(T(f"STORM 3: {a['font_name']} — {a['glyph_count']} гліфів; {b['font_name']} — {b['glyph_count']} гліфів.", f"STORM 3: {a['font_name']} — {a['glyph_count']} glyphs; {b['font_name']} — {b['glyph_count']} glyphs."))
    return write_reports(compare(a,b),output)


# ============================================================================
# font_build
# ============================================================================

BUILD_VERSION = '2.9.4'
SOURCE_GLYPHS = 211
OUTPUT_GLYPHS = SOURCE_GLYPHS + 66
BASE = 0x458
REC_BYTES = 32
NTP_HEADER_BYTES = 0x60
ORIGINAL_RES = (512,256)
OUTPUT_RES = (512,512)
DEFAULT_REL = 'ui/font/xfgame_cmn.fnt.xfbin'  # tentative! user must verify in game data

class Storm3FontBuildError(ValueError):
    pass

@dataclass
class Font:
    path: Path
    xfbin: bytes
    ck_start: int
    ck_end: int
    ck_header: int
    body: bytes
    count: int
    ntp: int
    raw: bytes
    width: int
    height: int
    records: dict[int,bytes]
    info: dict


def _sha256(b:bytes)->str:
    return hashlib.sha256(b).hexdigest()


def load(path: str|Path, *, require_original=True)->Font:
    path=Path(path).expanduser().resolve()
    try:
        info=inspect(path) if require_original else None
        xf=parse_xfbin(path)
    except (Storm3FontError,ValueError,OSError) as exc:
        raise Storm3FontBuildError(T(f'Непідтримуваний шрифт STORM 3: {exc}', f'Unsupported STORM 3 font: {exc}')) from exc
    if xf.nucc_version!=104 or [c.type_name for c in xf.chunks]!=['nuccChunkFont','nuccChunkPage']:
        raise Storm3FontBuildError(T('Непідтримуваний контейнер шрифту. Потрібен NUCC 104 Font/Page.', 'Unsupported font container. NUCC 104 Font/Page is required.'))
    ck=xf.chunks[0];body=xf.blob[ck.data_offset:ck.end]
    if len(body)<BASE+NTP_HEADER_BYTES or body[8:12]!=b'NFH\x00':
        raise Storm3FontBuildError(T('Пошкоджений заголовок NFH.', 'Damaged NFH header.'))
    count=struct.unpack_from('>I',body,16)[0]
    ntp=BASE+count*REC_BYTES
    if count<1 or count>512 or ntp+NTP_HEADER_BYTES>len(body) or body[ntp:ntp+4]!=b'NTP3':
        raise Storm3FontBuildError(T('Непідтримувана кількість гліфів або NTP3.', 'Unsupported glyph count or NTP3.'))
    packed=struct.unpack_from('>I',body,ntp+0x24)[0]
    w,h=packed>>16,packed&0xffff
    if (w,h) not in (ORIGINAL_RES,OUTPUT_RES) or len(body)-ntp-NTP_HEADER_BYTES!=w*h:
        raise Storm3FontBuildError(T('Непідтримувана текстура або її довжина.', 'Unsupported texture or texture length.'))
    if struct.unpack_from('>I',body,0)[0]!=ntp-8 or struct.unpack_from('>I',body,4)[0]!=len(body)-ntp:
        raise Storm3FontBuildError(T('NFH / NTP3: несумісні розміри.', 'NFH / NTP3: the sizes do not match each other.'))
    if struct.unpack_from('>I',body,ntp+0x10)[0]!=w*h+0x50 or struct.unpack_from('>I',body,ntp+0x18)[0]!=w*h:
        raise Storm3FontBuildError(T('NTP3 має неочікувану довжину.', 'NTP3 has an unexpected length.'))
    records={}
    for i in range(count):
        rec=body[BASE+i*REC_BYTES:BASE+(i+1)*REC_BYTES]
        cp=struct.unpack_from('>H',rec,22)[0]
        if cp in records or struct.unpack_from('>I',rec,0)[0]!=0x11110002:
            raise Storm3FontBuildError(T('Некоректні Unicode або сигнатура гліфів.', 'Invalid Unicode values or glyph signature.'))
        records[cp]=rec
    cps=sorted(records)
    if cps!=list(records) or body[0x58:BASE]!=_lookup(cps):
        raise Storm3FontBuildError(T('Некоректна таблиця Unicode.', 'Invalid Unicode table.'))
    meta=_page_metadata(cps)
    for cp,rec in records.items():
        if struct.unpack_from('>I',rec,24)[0]!=meta[cp]:
            raise Storm3FontBuildError(T('Неочікувана таблиця індексів сторінок Unicode.', 'Unexpected Unicode page index table.'))
    if require_original:
        if count!=SOURCE_GLYPHS or (w,h)!=ORIGINAL_RES or info['font_name']!='FOT-Reggae Std' or info['cyrillic_glyphs']!=0:
            raise Storm3FontBuildError(T('Потрібен саме вихідний англійський FOT-Reggae Std (211 гліфів, 512×256).', 'The original English FOT-Reggae Std font is required (211 glyphs, 512×256).'))
    return Font(path,xf.blob,ck.data_offset,ck.end,ck.header_offset,body,count,ntp,
                body[ntp+NTP_HEADER_BYTES:],w,h,records,info or {})


def _try_pillow():
    try:
        return _libraries()
    except Exception as exc:
        raise Storm3FontBuildError(T(f'Потрібні Pillow і fonttools. Запусти INSTALL_DEPENDENCIES.bat. {exc}', f'Pillow and fonttools are required. Run INSTALL_DEPENDENCIES.bat. {exc}')) from exc


def _rel(value:str)->Path:
    # Never use a Windows absolute path or a parent traversal as an archive entry.
    value=(value or '').strip().replace('\\','/')
    parts=value.split('/')
    if (not parts or any(p in ('','.', '..') for p in parts)
        or ':' in value or value.startswith('/') or '\x00' in value
        or parts[-1].lower()!='xfgame_cmn.fnt.xfbin'):
        raise Storm3FontBuildError(T('Відносний шлях має закінчуватися xfgame_cmn.fnt.xfbin без .. або абсолютних каталогів.', 'The relative path must end with xfgame_cmn.fnt.xfbin and contain no .. or absolute folders.'))
    return Path(*parts)


def _image_glyph(ch, font, Image, ImageDraw):
    bbox=font.getbbox(ch,anchor='ls')
    x0,y0,x1,y1=bbox
    if x1<=x0 or y1<=y0:
        raise Storm3FontBuildError(T('Шрифт TTF повернув порожній гліф: '+ch, 'The TTF font returned an empty glyph: '+ch))
    image=Image.new('L',(x1-x0,y1-y0),0)
    ImageDraw.Draw(image).text((-x0,-y0),ch,anchor='ls',font=font,fill=255)
    crop=image.getbbox()
    if crop is None:
        raise Storm3FontBuildError(T('TTF згенерував прозорий гліф: '+ch, 'The TTF produced a transparent glyph: '+ch))
    if crop!=(0,0,*image.size):
        image=image.crop(crop)
        y0+=crop[1]
    scaled=False
    if image.width>29 or image.height>29:
        old=image.size
        image.thumbnail((29,29), Image.Resampling.LANCZOS)
        y0=round(y0*image.height/old[1])
        scaled=True
    return image,y0,scaled


def _native_twin(ch:str)->str|None:
    twins={'А':'A','В':'B','Е':'E','І':'I','К':'K','М':'M','Н':'H','О':'O','Р':'P','С':'C','Т':'T','Х':'X',
           'а':'a','е':'e','і':'i','о':'o','р':'p','с':'c','у':'y','х':'x'}
    return twins.get(ch)


def _read_glyph_pos(rec:bytes)->tuple[int,int,int,int]:
    xy=struct.unpack_from('>I',rec,4)[0]
    wh=struct.unpack_from('>I',rec,20)[0]
    return xy>>16,xy&65535,wh>>24,(wh>>16)&255


def _write_record(base:bytes,ch:str,x:int,y:int,w:int,h:int,advance_px:int,ascent_px:int)->bytes:
    rec=bytearray(base)
    struct.pack_into('>I',rec,4,(x<<16)|y)
    struct.pack_into('>I',rec,20,(w<<24)|(h<<16)|ord(ch))
    # Word[2] = signed x bearing + baseline-to-top ascent; Word[3] = horizontal advance + other
    # original font-specific metric. Preserve donor's complex secondary data.
    bx=struct.unpack_from('>I',rec,8)[0] & 0xffff0000
    struct.pack_into('>I',rec,8,bx | min(65535,ascent_px*64))
    wd3=struct.unpack_from('>I',rec,12)[0]
    struct.pack_into('>I',rec,12,(min(65535,advance_px*64)<<16)|(wd3&0xffff))
    return bytes(rec)


def _extra_bc3(alpha:bytes,width:int,height:int,start_y:int)->tuple[bytes,int]:
    """Encode ONLY the lower new BC3 rows. Unchanged original blocks are copied exactly."""
    assert start_y%4==0 and width%4==0 and height%4==0
    extra=bytearray((height-start_y)*width)
    written=0
    blocks_x=width//4
    for by in range(start_y//4,height//4):
        for bx in range(blocks_x):
            x=bx*4;y=by*4
            vals=[alpha[(y+dy)*width+x+dx] for dy in range(4) for dx in range(4)]
            idx=((by-start_y//4)*blocks_x+bx)*16
            if any(vals):
                extra[idx:idx+8]=_encode_bc3_alpha(vals)
                extra[idx+8:idx+16]=BC3_OPAQUE_WHITE
                written+=1
            else:
                extra[idx:idx+16]=bytes(16)
    return bytes(extra),written


def _verify(payload:bytes,src:Font,added:str,expected_alpha:bytes):
    """Re-open independently, compare every retained EN record and original BC3 byte."""
    with tempfile.TemporaryDirectory(prefix='storm3_font_verify_') as d:
        path=Path(d)/'xfgame_cmn.fnt.xfbin';path.write_bytes(payload)
        out=load(path,require_original=False)
        if (out.width,out.height)!=OUTPUT_RES or out.count!=OUTPUT_GLYPHS:
            raise Storm3FontBuildError(T('Після збирання атлас або кількість записів змінилася.', 'After the build, the atlas or the record count changed.'))
        if set(out.records)!=set(src.records)|set(map(ord,added)):
            raise Storm3FontBuildError(T('Втрата або дублювання гліфів після збирання.', 'Glyphs were lost or duplicated after the build.'))
        for cp,rec in src.records.items():
            now=out.records[cp]
            if rec[:24]!=now[:24] or rec[28:]!=now[28:]:
                raise Storm3FontBuildError(T(f'Оригінальний запис U+{cp:04X} пошкоджено.', f'The original record U+{cp:04X} was damaged.'))
        if out.raw[:len(src.raw)]!=src.raw:
            raise Storm3FontBuildError(T('Змінено оригінальні BC3-блоки латинських гліфів.', 'The original BC3 blocks of the Latin glyphs were changed.'))
        old=parse_xfbin(src.path)
        new=parse_xfbin(path)
        if len(old.chunks)!=len(new.chunks) or payload[new.chunks[1].header_offset:]!=src.xfbin[old.chunks[1].header_offset:]:
            raise Storm3FontBuildError(T('Змінено nuccChunkPage.', 'nuccChunkPage was changed.'))
        alpha=decode_bc3_alpha(out.raw,out.width,out.height)
        if alpha[:src.height*src.width]!=decode_bc3_alpha(src.raw,src.width,src.height):
            raise Storm3FontBuildError(T('Пікселі англійського шрифту змінилися.', 'The pixels of the English font changed.'))
        for ch in added:
            x,y,w,h=_read_glyph_pos(out.records[ord(ch)])
            if not any(alpha[(y+dy)*out.width+x+dx] for dy in range(h) for dx in range(w)):
                raise Storm3FontBuildError(T(f'Новий гліф {ch} зник у BC3 після кодування.', f'The new glyph {ch} disappeared from BC3 after encoding.'))
        if out.xfbin!=payload:
            raise Storm3FontBuildError(T('Бінарний повторний розбір не відтворив вихідний файл.', 'Re-parsing the binary did not reproduce the output file.'))


def build_additive_font(original:str|Path,ttf:str|Path,*,copy_native_lookalikes:bool=True,
                        russian_reference:str|Path|None=None)->tuple[bytes,dict,bytes]:
    """Prepare EXPERIMENTAL expanded-atlas font; original and Russian data only read."""
    src=load(original)
    path=Path(ttf).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in ('.ttf','.otf') or path.stat().st_size>30_000_000:
        raise Storm3FontBuildError(T('Вкажи свій TTF/OTF (до 30 МБ).', 'Choose your TTF/OTF file (up to 30 MB).'))
    Image, ImageFont, ImageDraw, TTFont = _try_pillow()
    supported=_font_cmap(path,TTFont)
    add=''.join(dict.fromkeys(ch for ch in UKRAINIAN_ALPHABET if ord(ch) not in src.records))
    if len(add)!=66:
        raise Storm3FontBuildError(T('Очікували 66 відсутніх українських літер у вихідному EN шрифті.', 'The original EN font was expected to be missing 66 Ukrainian letters.'))
    if any(ord(ch) not in supported for ch in add):
        raise Storm3FontBuildError(T('Вибраний TTF не містить літер: '+''.join(ch for ch in add if ord(ch) not in supported), 'The chosen TTF does not contain these letters: '+''.join(ch for ch in add if ord(ch) not in supported)))
    reference_info=None
    if russian_reference:
        reference_info=inspect(russian_reference)
        if reference_info['font_name']!='DFGHSGothic-W7' or reference_info['cyrillic_glyphs']!=66:
            raise Storm3FontBuildError(T('Обраний довідковий шрифт не схожий на перевірений офіційний RUS.', 'The chosen reference font does not look like the verified official RUS font.'))
    w,h=OUTPUT_RES
    rows=5; pitch=32; cols=w//pitch;start_y=272
    if start_y+rows*pitch>h:raise Storm3FontBuildError(T('Не вдалося розмістити кирилицю в 512×512.', 'Could not fit the Cyrillic letters into 512×512.'))
    native_alpha=decode_bc3_alpha(src.raw,src.width,src.height)
    new_alpha_img=Image.new('L',(w,h),0)
    new_alpha_img.paste(Image.frombytes('L',(src.width,src.height),native_alpha),(0,0))
    font_size=_calibrate_pixel_size(path,ImageFont,cap_height=22)
    ttf_font=ImageFont.truetype(str(path),font_size)
    donor_cp=[cp for cp in src.records if (ord('A')<=cp<=ord('Z') or ord('a')<=cp<=ord('z'))]
    records=dict(src.records)
    copied=[];scaled=[];metrics=[]
    for i,ch in enumerate(add):
        cellx=(i%cols)*pitch+1;celly=start_y+(i//cols)*pitch+1
        twin=_native_twin(ch) if copy_native_lookalikes else None
        if twin and ord(twin) in src.records:
            donor=src.records[ord(twin)]
            dx,dy,cw,chh=_read_glyph_pos(donor)
            glyph=Image.frombytes('L',(src.width,src.height),native_alpha).crop((dx,dy,dx+cw,dy+chh))
            ascent_px=struct.unpack_from('>H',donor,10)[0]//64
            advance_px=struct.unpack_from('>H',donor,12)[0]//64
            copied.append(ch)
            new=bytearray(donor)
            struct.pack_into('>I',new,4,(cellx<<16)|celly)
            struct.pack_into('>I',new,20,(cw<<24)|(chh<<16)|ord(ch))
            rec=bytes(new)
        else:
            glyph,ttf_top,was_scaled=_image_glyph(ch,ttf_font,Image,ImageDraw)
            if was_scaled: scaled.append(ch)
            cw,chh=glyph.size
            best=min(donor_cp,key=lambda cp:abs(_read_glyph_pos(src.records[cp])[2]-cw)+abs(_read_glyph_pos(src.records[cp])[3]-chh)+ (0 if chr(cp).isupper()==ch.isupper() else 25))
            donor=src.records[best]
            ascent_px=max(1,min(31,-ttf_top))
            advance_px=max(cw+1,min(40,round(ttf_font.getlength(ch))))
            rec=_write_record(donor,ch,cellx,celly,cw,chh,advance_px,ascent_px)
        if cw>30 or chh>30 or cellx+cw>w or celly+chh>h:
            raise Storm3FontBuildError(T('Гліф не вміщується в комірку: '+ch, 'The glyph does not fit into its cell: '+ch))
        new_alpha_img.paste(glyph,(cellx,celly))
        records[ord(ch)]=rec
        metrics.append({'glyph':ch,'codepoint':f'U+{ord(ch):04X}', 'x':cellx,'y':celly,'width':cw,
                        'height':chh,'advance_px':struct.unpack_from('>H',rec,12)[0]/64,
                        'native_copy':ch in copied,'scaled_from_ttf':ch in scaled})
    cp_list=sorted(records)
    meta=_page_metadata(cp_list)
    ordered=[]
    for cp in cp_list:
        record=bytearray(records[cp]);struct.pack_into('>I',record,24,meta[cp]);ordered.append(bytes(record))
    atlas_alpha=new_alpha_img.tobytes()
    extra,bc3_blocks=_extra_bc3(atlas_alpha,w,h,src.height)
    raw=src.raw+extra
    if len(raw)!=w*h: raise Storm3FontBuildError(T('Новий розмір BC3 некоректний.', 'The new BC3 size is wrong.'))
    head=bytearray(src.body[:BASE])
    new_ntp=BASE+len(ordered)*REC_BYTES
    struct.pack_into('>I',head,0,new_ntp-8)
    struct.pack_into('>I',head,4,NTP_HEADER_BYTES+len(raw))
    struct.pack_into('>I',head,16,len(ordered))
    head[0x58:BASE]=_lookup(cp_list)
    ntp_head=bytearray(src.body[src.ntp:src.ntp+NTP_HEADER_BYTES])
    struct.pack_into('>I',ntp_head,0x10,len(raw)+0x50)
    struct.pack_into('>I',ntp_head,0x18,len(raw))
    struct.pack_into('>I',ntp_head,0x24,(w<<16)|h)
    new_body=bytes(head)+b''.join(ordered)+bytes(ntp_head)+raw
    prefix=bytearray(src.xfbin[:src.ck_start])
    struct.pack_into('>I',prefix,src.ck_header,len(new_body))
    result=bytes(prefix)+new_body+src.xfbin[src.ck_end:]
    _verify(result,src,add,atlas_alpha)
    display=Image.new('RGB',(w,192),(29,37,54))
    display_alpha=Image.frombytes('L',(w,h),decode_bc3_alpha(raw,w,h))
    sheet=Image.new('RGB',(w,192),'white');display.paste(sheet,(0,0),display_alpha.crop((0,start_y,w,start_y+192)))
    stream=BytesIO();display.save(stream,format='PNG')
    report={
        'version':BUILD_VERSION,'mode':'STORM3_EN_ADD_66_TTF_EXPAND_ATLAS_512x512_EXPERIMENTAL',
        'source':src.path.name,'source_sha256':_sha256(src.xfbin),'output_sha256':_sha256(result),
        'ttf':path.name,'ttf_sha256':_sha256(path.read_bytes()),
        'russian_reference_sha256':reference_info['sha256'] if reference_info else None,
        'russian_reference_is_required':False,
        'original_family':'FOT-Reggae Std','original_glyphs':src.count,
        'new_glyphs':len(ordered),'added_letters':add,'added_count':len(add),
        'atlas_before':list(ORIGINAL_RES),'atlas_after':list(OUTPUT_RES),
        'original_glyph_shapes_and_metrics_preserved':True,
        'original_bc3_blocks_preserved_exactly':True,
        'added_bc3_blocks_with_white_rgb':bc3_blocks,'native_latin_twin_copies':''.join(copied),
        'ttf_scaled_letters':''.join(scaled),'font_pixel_size':font_size,
        'glyph_metrics':metrics,
        'roundtrip_parse_verified':True,'engine_tested':False,
        'notes':[
            'Російська версія слугує довідкою щодо формату; її гліфи не копіюються у цей файл.',
            'Збільшення 512×256 → 512×512 НЕ підтверджено в рушії STORM 3; перевірити в грі.',
            'Жодного оригінального латинського гліфа не видалено. Метрики нових TTF-гліфів експериментальні.',
            'Вихідний XFBIN і TTF залишилися незмінними.'
        ]}
    return result,report,stream.getvalue()


def generate_font_output(source:str|Path,ttf:str|Path,workspace:str|Path,
                         output_root:str|Path,relative_path:str=DEFAULT_REL, *,
                         russian_reference:str|Path|None=None,copy_native_lookalikes:bool=True,
                         log=None)->dict:
    """Generate versioned isolated report/preview, XFBIN and ZIP; no overwrite."""
    relative=_rel(relative_path)
    src=Path(source).expanduser().resolve()
    wr=Path(workspace).expanduser().resolve();out=Path(output_root).expanduser().resolve()
    if wr==out or wr==src.parent or out==src.parent or wr==src or out==src:
        raise Storm3FontBuildError(T('Робоча папка, Build і тека оригіналу повинні бути окремими.', 'The workspace folder, the Build folder and the folder of the original must be separate.'))
    if wr.is_relative_to(src.parent) or out.is_relative_to(src.parent):
        raise Storm3FontBuildError(T('Не записуємо результати всередину теки вихідного шрифту.', 'Results are not written inside the folder of the original font.'))
    payload,report,preview=build_additive_font(src,ttf,
        russian_reference=russian_reference,copy_native_lookalikes=copy_native_lookalikes)
    stamp=report['output_sha256'][:12]
    work=wr/f'STORM3_FONT_TTF_{stamp}'
    build=out/f'STORM3_FONT_TEST_{stamp}'
    target=build/'data_win32'/relative
    archive=build/'STORM3_UA_FONT_EXPERIMENTAL.zip'
    for p in (work,build,target,archive):
        if p.exists():raise Storm3FontBuildError(T('Такий результат уже існує. Використай нову папку для збірки.', 'This result already exists. Use a new folder for the build.'))
    if src==target.resolve():raise Storm3FontBuildError(T('Не можна перезаписувати оригінал.', 'The original must not be overwritten.'))
    report['relative_path_in_build']='data_win32/'+relative.as_posix()
    report['relative_path_confirmed_in_game']=False
    report_bytes=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    work.mkdir(parents=True,exist_ok=False)
    try:
        (work/'STORM3_FONT_TTF_REPORT.json').write_bytes(report_bytes)
        (work/'STORM3_UA_GLYPHS_PREVIEW.png').write_bytes(preview)
        target.parent.mkdir(parents=True,exist_ok=False)
        target.write_bytes(payload)
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            z.write(target,'data_win32/'+relative.as_posix())
            z.writestr('STORM3_FONT_TTF_REPORT.json',report_bytes)
        if log:
            log(T(f'STORM 3: {len(report["added_letters"])} літер із TTF; 211 вихідних гліфів збережено; ІГРОВИЙ ТЕСТ ОБОВ’ЯЗКОВИЙ.', f'STORM 3: {len(report["added_letters"])} letters from the TTF; 211 original glyphs kept; AN IN-GAME TEST IS REQUIRED.'))
        return {'file':str(target),'zip':str(archive),'preview':str(work/'STORM3_UA_GLYPHS_PREVIEW.png'),
                'report':str(work/'STORM3_FONT_TTF_REPORT.json'), **report}
    except Exception:
        # leave pre-existing folders unmodified; only clean fresh owned folders
        import shutil
        if work.exists():shutil.rmtree(work)
        if build.exists():shutil.rmtree(build)
        raise
