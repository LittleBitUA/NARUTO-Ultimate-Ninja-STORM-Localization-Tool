# -*- coding: utf-8 -*-
"""NTP3 textures: PNG export / rebuild for single XFBIN and safe incremental batch mode (STORM 1 and 2)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from hashlib import sha256
import io
import json
import os
import struct
import tempfile
import zipfile
import csv
from datetime import datetime, timezone
import shutil

from xfbin import parse_xfbin
from i18n import T


# ============================================================================
# texture
# ============================================================================

class TextureError(ValueError):
    """kind: language-independent class for the batch report ('no_texture', 'protected')."""
    def __init__(self, message='', kind=''):
        super().__init__(message)
        self.kind = kind

@dataclass(frozen=True)
class TextureEntry:
    chunk_index: int
    chunk_name: str
    chunk_path: str
    ntp_offset: int
    pixel_offset: int
    length: int                # Entire NTP3 image region, including mip levels
    width: int
    height: int
    nut_format: int = 2
    texture_index: int = 0
    texture_count: int = 1
    mip_levels: int = 1
    base_length: int = 0


def _base_size(w, h, fmt):
    if fmt == 0:
        return w*h//2 if w % 4 == 0 and h % 4 == 0 else None
    if fmt == 2:
        return w*h if w % 4 == 0 and h % 4 == 0 else None
    if fmt == 7:
        return w*h*2       # Verified visual A4-R4-G4-B4 packed BE
    if fmt in (14, 17):
        return w*h*4       # NTP3 ARGB8888; rotate alpha from first to last
    return None


def load_entries(source, *, skip_unsupported=False):
    path = Path(source)
    doc = parse_xfbin(path)
    entries = []
    for ch in doc.chunks:
        if ch.type_name != 'nuccChunkTexture':
            continue
        blob = doc.blob
        n = ch.data_offset + 12
        if n + 0x60 > ch.end or blob[n:n+4] != b'NTP3':
            problem = f'Чанк {ch.number}: не підтримуваний заголовок NTP3.'
            if skip_unsupported: doc.warnings.append(problem); continue
            raise TextureError(problem)
        ver, count = struct.unpack_from('>HH', blob, n+4)
        if ver != 0x100 or not 1 <= count <= 4096:
            problem = f'Чанк {ch.number}: NTP3 version={ver:04x}, textures={count}.'
            if skip_unsupported: doc.warnings.append(problem); continue
            raise TextureError(problem)
        offset = n + 0x10
        pending = []
        for tex_i in range(count):
            if offset + 0x50 > ch.end:
                doc.warnings.append(f'Чанк {ch.number}: текстура {tex_i}: заголовок за межами чанка.')
                pending = []
                break
            total = struct.unpack_from('>I', blob, offset)[0]
            imgsize = struct.unpack_from('>I', blob, offset+8)[0]
            header_size = struct.unpack_from('>H', blob, offset+12)[0]
            levels, fmt = struct.unpack_from('>HH', blob, offset+16)
            w, h = struct.unpack_from('>HH', blob, offset+20)
            pixel = offset + header_size
            base = _base_size(w, h, fmt)
            if (not 1 <= levels <= 16 or header_size < 0x50 or header_size % 0x10
                    or not 1 <= w <= 8192 or not 1 <= h <= 8192 or base is None
                    or total != header_size + imgsize or imgsize < base
                    or pixel + imgsize > ch.end):
                doc.warnings.append(f'Чанк {ch.number}, texture {tex_i}: unsupported fmt={fmt} mip={levels} hdr={header_size} {w}x{h}, size={imgsize}.')
                pending = []
                break
            # Never claim we can rewrite the unfamiliar lower mip levels.
            # Export their full-resolution images; pack refuses changed PNGs.
            if levels == 1 and imgsize != base:
                doc.warnings.append(f'Чанк {ch.number}, texture {tex_i}: нестандартний розмір {imgsize}, очікувався {base}.')
                pending = []
                break
            pending.append(TextureEntry(ch.number, ch.name, ch.path, n, pixel,
                                         imgsize, w, h, fmt, tex_i, count, levels, base))
            offset += total
        # All-or-nothing for a multi-texture NTP3: refuse partially parsed groups.
        if pending and len(pending) == count:
            if offset > ch.end:
                doc.warnings.append(f'Чанк {ch.number}: список текстур виходить за межі чанка.')
            else:
                entries.extend(pending)
        elif not skip_unsupported:
            raise TextureError(doc.warnings[-1] if doc.warnings else T(f'Чанк {ch.number}: NTP3 не підтримується.', f'Chunk {ch.number}: NTP3 is not supported.'))
    if not entries:
        raise TextureError(T('У файлі немає підтримуваних чанків nuccChunkTexture.' + (' '+doc.warnings[0] if doc.warnings else ''), 'The file has no supported nuccChunkTexture chunks.' + (' '+doc.warnings[0] if doc.warnings else '')), kind='no_texture')
    return doc, entries


def _png_name(e):
    return (f'chunk_{e.chunk_index:03d}_tex_{e.texture_index:02d}.png'
            if e.texture_count > 1 else f'chunk_{e.chunk_index:03d}.png')


def raw_rgba(raw, w, h, fmt):
    from PIL import Image
    if fmt in (0, 2):
        return pil_rgba(dds_bytes(raw, w, h, fmt))
    if fmt in (14, 17):
        # Source is ARGB; the byte first stores alpha. A,R,G,B -> R,G,B,A.
        rgba = bytearray(w*h*4)
        for j in range(0, w*h*4, 4):
            rgba[j:j+4] = bytes((raw[j+1], raw[j+2], raw[j+3], raw[j]))
        return Image.frombytes('RGBA', (w, h), bytes(rgba))
    if fmt == 7:
        # A4R4G4B4 big-endian, verified against actual transparency and shape.
        rgba=bytearray(w*h*4)
        for j in range(w*h):
            val=(raw[j*2]<<8)|raw[j*2+1]
            rgba[j*4:j*4+4]=bytes((((val>>8)&15)*17, ((val>>4)&15)*17,
                                        (val&15)*17, ((val>>12)&15)*17))
        return Image.frombytes('RGBA', (w,h), bytes(rgba))
    raise TextureError(T('Непідтримуваний формат PNG: '+str(fmt), 'Unsupported PNG format: '+str(fmt)))


def encode_uncompressed_changed(raw, original, edited, fmt):
    """Same-size in-place edit; preserve unchanged pixels bit for bit."""
    w,h=edited.size
    p_old=original.load();p_new=edited.load()
    out=bytearray(raw)
    changed=0
    for y in range(h):
        for x in range(w):
            if p_old[x,y] == p_new[x,y]: continue
            r,g,b,a=p_new[x,y]; changed+=1
            p=y*w+x
            if fmt == 7:
                def q(v):return (v*15+127)//255
                val=(q(a)<<12)|(q(r)<<8)|(q(g)<<4)|q(b)
                out[p*2:p*2+2]=val.to_bytes(2,'big')
            elif fmt in (14, 17):
                out[p*4:p*4+4]=bytes((a,r,g,b))
            else:
                raise TextureError(T('Невідомий формат збирання: '+str(fmt), 'Unknown build format: '+str(fmt)))
    return bytes(out),changed


def dds_bytes(raw,w,h,fmt=2):
    if fmt not in (0,2):raise TextureError(T('Невідомий формат для DDS: '+str(fmt), 'Unknown format for DDS: '+str(fmt)))
    fourcc = b'DXT1' if fmt == 0 else b'DXT5'
    nums=[124,0x81007,h,w,len(raw),0,1]+[0]*11+[32,4,struct.unpack('<I',fourcc)[0],0,0,0,0,0]+[0x1000,0,0,0,0]
    return b'DDS '+struct.pack('<31I',*nums)+raw


def pil_rgba(dds):
    try: from PIL import Image
    except ImportError as exc: raise TextureError(T('Для PNG потрібен Pillow. Установи: запустіть INSTALL_DEPENDENCIES.bat', 'Pillow is required for PNG. To install it, run INSTALL_DEPENDENCIES.bat')) from exc
    with Image.open(io.BytesIO(dds)) as im:
        return im.convert('RGBA')


def export_textures(source,work_dir,*,export_dds=False,preserve_existing=True):
    """Export supported NTP3 chunks without overwriting already edited images.

    In incremental mode, fill missing PNGs, validate/extend a prior manifest
    when new BC1/BC3 chunks become supported, and leave existing PNG bytes
    untouched. A source/hash mismatch or malformed manifest is a hard stop:
    translated images must never be associated with an unrelated XFBIN.
    """
    path=Path(source)
    work=Path(work_dir)
    if work.is_symlink() or (work.exists() and not work.is_dir()):
        raise TextureError(T('Робоча папка не може бути файлом або символічним посиланням: '+str(work), 'The workspace folder cannot be a file or a symbolic link: '+str(work)))
    doc,entries=load_entries(path,skip_unsupported=True)
    manifest_path=work/'textures_manifest.json'
    if manifest_path.is_symlink():
        raise TextureError(T('Маніфест є символічним посиланням, його не змінено: '+str(manifest_path), 'The manifest is a symbolic link and was not changed: '+str(manifest_path)), kind='protected')
    old=None
    if manifest_path.exists():
        try:
            old=json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:
            raise TextureError(T('Наявний маніфест пошкоджений; PNG не змінено: '+str(manifest_path), 'The existing manifest is damaged; PNG files were not changed: '+str(manifest_path)), kind='protected') from exc
        if not isinstance(old,dict) or old.get('format') not in ('STORM1_TEXTURES_NTP3_BC3_V1','STORM1_TEXTURES_NTP3_V2'):
            raise TextureError(T('Невідомий формат старого маніфеста; нічого не змінено: '+str(manifest_path), 'Unknown format of the old manifest; nothing was changed: '+str(manifest_path)), kind='protected')
        if old.get('source_sha256') != doc.sha256:
            raise TextureError(T('Оригінальний XFBIN відрізняється від маніфеста. Захищено наявні PNG; повторний експорт зупинено.', 'The original XFBIN differs from the manifest. Existing PNG files are protected; the repeated export was stopped.'), kind='protected')
        if not isinstance(old.get('entries'),list):
            raise TextureError(T('У маніфесті бракує списку текстур; наявні PNG не змінено.', 'The manifest has no texture list; existing PNG files were not changed.'), kind='protected')
        actual={(e.chunk_index,e.texture_index):e for e in entries}
        seen=set()
        for item in old['entries']:
            if not isinstance(item,dict) or not isinstance(item.get('chunk'),int):
                raise TextureError(T('Старий маніфест містить некоректні чанк-ідентифікатори.', 'The old manifest contains invalid chunk identifiers.'), kind='protected')
            i=(item['chunk'],item.get('texture_index',0))
            e=actual.get(i)
            if i in seen or e is None or (e.width,e.height,e.nut_format,e.mip_levels)!=(item.get('width'),item.get('height'),item.get('nut_format',2),item.get('mip_levels',1)):
                raise TextureError(T('Структура XFBIN не збігається зі старим маніфестом; PNG не змінено.', 'The XFBIN structure does not match the old manifest; PNG files were not changed.'), kind='protected')
            if item.get('png') != _png_name(e):
                raise TextureError(T('Небезпечне ім’я PNG у маніфесті; наявні файли не змінено.', 'Unsafe PNG name in the manifest; existing files were not changed.'), kind='protected')
            seen.add(i)
    # Build metadata in source chunk order. The old manifest may cover only a
    # subset because earlier versions lacked BC1 or skipped unknown chunks.
    parts=[]
    new_png=kept_png=0
    work.mkdir(parents=True,exist_ok=True)
    for e in entries:
        name=_png_name(e).removesuffix('.png')
        raw=doc.blob[e.pixel_offset:e.pixel_offset+e.base_length]
        png_name=f'{name}.png'
        dest=work/png_name
        if dest.exists() or dest.is_symlink():
            if not dest.is_file() or dest.is_symlink():
                raise TextureError(T('Наявний PNG не є звичайним файлом: '+str(dest), 'The existing PNG is not a regular file: '+str(dest)), kind='protected')
            if not preserve_existing:
                raise TextureError(T('Захист активний: експортер не замінює вже наявні PNG.', 'Protection is on: the exporter does not replace PNG files that already exist.'), kind='protected')
            kept_png+=1
        else:
            image=raw_rgba(raw,e.width,e.height,e.nut_format)
            # Exclusive-create means even a competing process cannot overwrite
            # an image that appeared between the existence check and writing.
            data=io.BytesIO()
            image.save(data,format='PNG')
            try:
                with dest.open('xb') as stream:
                    try:
                        stream.write(data.getvalue())
                    except BaseException:
                        stream.close()
                        dest.unlink(missing_ok=True)
                        raise
                new_png+=1
            except FileExistsError:
                kept_png+=1
        if export_dds and e.nut_format in (0,2):
            dds=work/f'{name}.dds'
            if not dds.exists() and not dds.is_symlink():
                with dds.open('xb') as stream:
                    stream.write(dds_bytes(raw,e.width,e.height,e.nut_format))
        parts.append({'chunk':e.chunk_index,'texture_index':e.texture_index,'name':e.chunk_name,'path':e.chunk_path,
                      'width':e.width,'height':e.height,
                      'nut_format':e.nut_format, 'mip_levels':e.mip_levels, 'encoding':{0:'BC1/DXT1',2:'BC3/DXT5',7:'ARGB4444',14:'ARGB8888',17:'ARGB8888'}[e.nut_format],
                      'png':png_name,
                      'pixel_sha256':sha256(doc.blob[e.pixel_offset:e.pixel_offset+e.length]).hexdigest()})
    manifest={'format':'STORM1_TEXTURES_NTP3_V2',
              'source_name':path.name,'source_sha256':doc.sha256,
              'source_version':doc.nucc_version,'entries':parts,'skipped_chunks':doc.warnings,
              'note':'PNG-only. Змінені PNG із mip>1 блокуються до підтвердження побудови всіх рівнів. Оригінали залишаються незмінними.'}
    # Avoid unnecessarily rewriting old manifests; when new chunks become
    # available, reconstruct the full ordered list for build_textures().
    update_manifest = old is None or old.get('entries')!=parts or old.get('format')!=manifest['format']
    if update_manifest:
        temp=manifest_path.with_name('textures_manifest.json.tmp')
        if temp.is_symlink():
            raise TextureError(T('Тимчасовий маніфест є символічним посиланням: '+str(temp), 'The temporary manifest is a symbolic link: '+str(temp)), kind='protected')
        temp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        os.replace(temp,manifest_path)
    return {'textures':len(entries),'new_png':new_png,'preserved_png':kept_png,
            'manifest_updated':update_manifest,'new_manifest_entries':len(parts)-(len(old['entries']) if old else 0),
            'skipped_chunks':len(doc.warnings),'work_dir':str(work),'size':[(e.width,e.height) for e in entries],
            'manifest':str(manifest_path)}

def pack565(rgb):
    r,g,b=rgb
    return ((r*31+127)//255<<11)|((g*63+127)//255<<5)|((b*31+127)//255)


def unpack565(x):
    r=(x>>11)&31;g=(x>>5)&63;b=x&31
    return (r*255//31,g*255//63,b*255//31)


def compress_bc3_block(pixels):
    alphas=[p[3] for p in pixels]
    a0=max(alphas);a1=min(alphas)
    if a0==a1: ap=[a0]*8
    else:ap=[a0,a1]+[((7-i)*a0+i*a1+3)//7 for i in range(1,7)]
    ai=0
    for i,a in enumerate(alphas):
        k=min(range(8),key=lambda j:(a-ap[j])**2)
        ai|=k<<(3*i)
    alpha=bytes((a0,a1))+ai.to_bytes(6,'little')
    colors=[p[:3] for p in pixels if p[3]>4]
    if not colors: colors=[(0,0,0)]
    lo=tuple(min(p[j] for p in colors) for j in range(3))
    hi=tuple(max(p[j] for p in colors) for j in range(3))
    c0=pack565(hi);c1=pack565(lo)
    if c0<=c1:
        c0,c1=max(c0,c1),min(c0,c1)
        if c0==c1:
            if c0<65535:c0+=1
            else:c1-=1
    c0rgb=unpack565(c0);c1rgb=unpack565(c1)
    palette=[c0rgb,c1rgb,
             tuple((2*c0rgb[i]+c1rgb[i])//3 for i in range(3)),
             tuple((c0rgb[i]+2*c1rgb[i])//3 for i in range(3))]
    ci=0
    for i,p in enumerate(pixels):
        k=min(range(4),key=lambda j:sum((p[t]-palette[j][t])**2 for t in range(3))) if p[3]>4 else 0
        ci|=k<<(2*i)
    return alpha+struct.pack('<HHI',c0,c1,ci)


def compress_bc1_block(pixels):
    """Encode one 4x4 DXT1/BC1 block, respecting single-bit alpha.

    Conservative baseline encoder, not a visually optimal compressor.
    Never invoked for unchanged blocks.
    """
    transparent = any(p[3] < 128 for p in pixels)
    colors = [p[:3] for p in pixels if p[3] >= 128]
    if not colors:
        # Valid DXT1 transparent block (index 3 for every pixel).
        return struct.pack('<HHI', 0, 0, 0xFFFFFFFF)
    lo=tuple(min(p[j] for p in colors) for j in range(3))
    hi=tuple(max(p[j] for p in colors) for j in range(3))
    c_hi=pack565(hi);c_lo=pack565(lo)
    if transparent:
        c0,c1=min(c_hi,c_lo),max(c_hi,c_lo)
        r0,r1=unpack565(c0),unpack565(c1)
        palette=[r0,r1,tuple((r0[i]+r1[i])//2 for i in range(3))]
        choices=range(3)
    else:
        c0,c1=max(c_hi,c_lo),min(c_hi,c_lo)
        if c0==c1:
            if c0<65535:c0+=1
            else:c1-=1
        r0,r1=unpack565(c0),unpack565(c1)
        palette=[r0,r1,
                 tuple((2*r0[i]+r1[i])//3 for i in range(3)),
                 tuple((r0[i]+2*r1[i])//3 for i in range(3))]
        choices=range(4)
    index_bits=0
    for i,p in enumerate(pixels):
        if transparent and p[3]<128:
            index=3
        else:
            index=min(choices,key=lambda j:sum((p[k]-palette[j][k])**2 for k in range(3)))
        index_bits|=index<<(2*i)
    return struct.pack('<HHI', c0,c1,index_bits)


def encode_changed_blocks(original_raw,original_image,new_image,fmt=2):
    w,h=new_image.size
    old=original_image.load();new=new_image.load()
    result=bytearray(original_raw)
    changed=0
    for by in range(h//4):
        for bx in range(w//4):
            px=[];different=False
            for yy in range(4):
                for xx in range(4):
                    x=4*bx+xx;y=4*by+yy
                    p=new[x,y];px.append(p)
                    if p!=old[x,y]:different=True
            if different:
                block_bytes=8 if fmt==0 else 16
                off=(by*(w//4)+bx)*block_bytes
                result[off:off+block_bytes]=(compress_bc1_block(px) if fmt==0 else compress_bc3_block(px))
                changed+=1
    return bytes(result),changed


def _clean_rel(relative):
    from pathlib import PureWindowsPath
    p=PureWindowsPath(relative)
    if not relative or p.is_absolute() or p.drive or any(x in ('..','.') for x in p.parts):
        raise TextureError(T('Відносний шлях має бути без літери диска, .. та початкового слеша.', 'The relative path must not contain a drive letter, .. or a leading slash.'))
    if p.suffix.lower()!='.xfbin':raise TextureError(T('Відносний шлях має закінчуватися .xfbin', 'The relative path must end with .xfbin'))
    return Path(*p.parts)


def build_textures(source,work_dir,build_dir,relative_path,*,overwrite=False):
    source=Path(source);work=Path(work_dir);build=Path(build_dir)
    rel=_clean_rel(relative_path)
    manifest_path=work/'textures_manifest.json'
    if not manifest_path.is_file():raise TextureError(T('Немає textures_manifest.json — спочатку експортуй текстури.', 'textures_manifest.json is missing — export the textures first.'))
    info=json.loads(manifest_path.read_text(encoding='utf-8'))
    doc,entries=load_entries(source,skip_unsupported=True)
    if info.get('format') not in ('STORM1_TEXTURES_NTP3_BC3_V1','STORM1_TEXTURES_NTP3_V2') or info.get('source_sha256')!=doc.sha256:
        raise TextureError(T('Оригінал не збігається з маніфестом. Повторно експортуй із потрібного XFBIN.', 'The original does not match the manifest. Export again from the correct XFBIN.'))
    if len(info['entries'])!=len(entries):raise TextureError(T('Кількість текстур не збігається.', 'The number of textures does not match.'))
    output=bytearray(doc.blob);changed_total=0;changed_files=0
    for e,meta in zip(entries,info['entries']):
        if (e.chunk_index!=meta['chunk'] or e.texture_index!=meta.get('texture_index',0) or e.width!=meta['width'] or e.height!=meta['height'] or e.mip_levels!=meta.get('mip_levels',1)
                or e.nut_format!=meta.get('nut_format',2)):
            raise TextureError(T('Порушено порядок або геометрію чанків.', 'The order or geometry of the chunks has been changed.'))
        raw=doc.blob[e.pixel_offset:e.pixel_offset+e.base_length]
        original=raw_rgba(raw,e.width,e.height,e.nut_format)
        png=work/meta['png']
        if not png.exists():raise TextureError(T('Відсутній PNG: '+str(png), 'Missing PNG: '+str(png)))
        try: from PIL import Image
        except ImportError as exc:raise TextureError(T('Потрібен Pillow', 'Pillow is required')) from exc
        with Image.open(png) as im: edit=im.convert('RGBA')
        if edit.size!=(e.width,e.height):raise TextureError(T('Не змінюй розміри PNG: '+str(png), 'Do not change the PNG size: '+str(png)))
        if e.mip_levels > 1:
            if edit.tobytes()!=original.tobytes():
                raise TextureError(T(f'Змінений PNG з {e.mip_levels} mip-рівнями: потрібна безпечна генерація всіх рівнів. Збирання заблоковано.', f'Edited PNG with {e.mip_levels} mip levels: all levels would have to be generated safely. Build blocked.'))
            compressed,n=raw,0
        elif e.nut_format in (0,2):
            compressed,n=encode_changed_blocks(raw,original,edit,e.nut_format)
        else:
            compressed,n=encode_uncompressed_changed(raw,original,edit,e.nut_format)
        output[e.pixel_offset:e.pixel_offset+e.base_length]=compressed
        changed_total+=n
        if n:changed_files+=1
    if not changed_total:
        return {'status':'Без змін — XFBIN не створено','changed_blocks':0,'identity_roundtrip':bytes(output)==doc.blob}
    dest=build/'data_win32'/rel
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists() and not overwrite:raise TextureError(T('У Build уже є файл. Збережи стару версію або увімкни дозволену заміну.', 'Build already contains this file. Save the old version or allow replacing it.'))
    if dest.exists():
        backup=dest.with_name(dest.name+'.bak')
        backup.write_bytes(dest.read_bytes())
    # Keep the outer container byte structure (sizes, offsets and all non-pixel data).
    temp=dest.with_suffix(dest.suffix+'.tmp')
    temp.write_bytes(output)
    try:
        reloaded,check_entries=load_entries(temp,skip_unsupported=True)
        if len(check_entries)!=len(entries) or reloaded.blob!=bytes(output):
            raise TextureError(T('Помилка контрольного читання зібраного файлу.', 'The read-back check of the built file failed.'))
        os.replace(temp,dest)
    finally:
        if temp.exists():temp.unlink()
    return {'status':'Зібрано. Потрібне випробування у грі','changed_blocks':changed_total,
            'changed_textures':changed_files,'destination':str(dest),'sha256':sha256(bytes(output)).hexdigest()}


# ============================================================================
# texture_batch
# ============================================================================

class BatchTextureError(ValueError):
    pass

# Only English resources and language-neutral assets are relevant to UA modding.
# Compare each directory name exactly, case-insensitively; never filter by
# substrings (e.g. an asset named ``gesture`` must not be skipped).
OTHER_LANG_DIRS = frozenset(('fre','ger','ita','spa','fra','deu','jpn','jap','rus'))


def is_relevant_path(relative):
    return not any(part.casefold() in OTHER_LANG_DIRS for part in Path(relative).parts[:-1])


def _source_and_workspace(source_dir, workspace_dir):
    source=Path(source_dir).expanduser()
    workspace=Path(workspace_dir).expanduser()
    if not source.is_dir():
        raise BatchTextureError(T('Не знайдено вхідну папку інтерфейсу: ' + str(source), 'Input interface folder not found: ' + str(source)))
    src_real=source.resolve()
    work_real=workspace.resolve()
    if src_real == work_real or src_real in work_real.parents or work_real in src_real.parents:
        raise BatchTextureError(T('Вхідна та робоча папки не можуть перетинатися.', 'The input folder and the workspace folder must not overlap.'))
    return source,workspace,src_real


def _game_relative(file: Path) -> str | None:
    """Recover relative path only from an actual extracted `data` directory."""
    parts = file.parts
    folded = [p.casefold() for p in parts]
    for i in range(len(parts) - 2):
        if folded[i] == 'dataregion_extracted' and folded[i+1] == 'data':
            return '/'.join(parts[i+2:])
    return None


def batch_export_textures(source_dir, workspace_dir, *, log=None, progress=None):
    """Resumable PNG extraction of ENG and language-neutral STORM1 assets.

    Repeated runs never overwrite PNGs. Even if a prior export is incomplete,
    missing images are filled and a valid per-XFBIN manifest is extended.
    """
    source,workspace,src_real = _source_and_workspace(source_dir,workspace_dir)
    try:
        files = sorted((p for p in source.rglob('*') if p.is_file() and p.suffix.casefold() == '.xfbin'),
                       key=lambda p: str(p.relative_to(source)).casefold())
    except (OSError, PermissionError) as exc:
        raise BatchTextureError(T('Не вдалося прочитати дерево файлів: '+str(exc), 'Could not read the file tree: '+str(exc))) from exc
    found_all = len(files)
    files = [p for p in files if is_relevant_path(p.relative_to(source))]
    skipped_other_languages = found_all-len(files)
    if not files:
        raise BatchTextureError(T('Не знайдено жодного XFBIN англійською або спільного для всіх мов.', 'No XFBIN in English or shared by all languages was found.'))
    workspace.mkdir(parents=True, exist_ok=True)
    base = workspace / source.name
    if base.is_symlink():
        raise BatchTextureError(T('Робоча папка не може бути символічним посиланням: '+str(base), 'The workspace folder cannot be a symbolic link: '+str(base)))
    base.mkdir(exist_ok=True)
    base_real=base.resolve()
    results=[]
    total_textures=new_png=kept_png=exported=updated=untouched=0
    skipped_no_texture=unsupported=failed=partial=protected_conflicts=0
    for idx, file in enumerate(files, 1):
        relative=file.relative_to(source)
        dest=base / relative
        game_rel=_game_relative(file)
        entry={'file':str(file),'relative_path':relative.as_posix(),
               'game_relative_path':game_rel or '', 'export_folder':str(dest),
               'textures':0,'new_png':0,'preserved_png':0,'status':'','detail':''}
        try:
            file.resolve().relative_to(src_real)
            dest.resolve().relative_to(base_real)
            if dest.is_symlink():
                raise BatchTextureError(T('Робочий каталог XFBIN є символічним посиланням.', 'The XFBIN workspace folder is a symbolic link.'))
            had_manifest=(dest/'textures_manifest.json').is_file()
            had_dir=dest.exists()
            info=export_textures(file,dest,export_dds=False,preserve_existing=True)
            total_textures += info['textures']
            new_png += info['new_png']
            kept_png += info['preserved_png']
            entry['textures']=info['textures']
            entry['new_png']=info['new_png']
            entry['preserved_png']=info['preserved_png']
            if not had_dir:
                entry['status']='exported'
                exported+=1
            elif info['new_png'] or info['new_manifest_entries']:
                entry['status']='supplemented'
                updated+=1
            elif not had_manifest and info['manifest_updated']:
                entry['status']='manifest_recreated'
                updated+=1
            else:
                entry['status']='already_exists'
                untouched+=1
            if info['skipped_chunks']:
                partial+=1
            entry['detail']=(f'Додано PNG: {info["new_png"]}; збережено наявних: {info["preserved_png"]}; '
                             f'нових записів маніфеста: {info["new_manifest_entries"]}; '
                             f'непідтримуваних чанків: {info["skipped_chunks"]}')
        except TextureError as exc:
            entry['detail']=str(exc)
            if exc.kind=='no_texture':
                entry['status']='no_texture'
                skipped_no_texture+=1
            elif exc.kind=='protected':
                entry['status']='protected_conflict'
                protected_conflicts+=1
            else:
                entry['status']='unsupported'
                unsupported+=1
        except Exception as exc:
            entry['status']='error';entry['detail']=f'{type(exc).__name__}: {exc}'
            failed+=1
        results.append(entry)
        if progress:
            progress(idx,len(files),
                     T(f'Нових PNG {new_png}; захищено PNG {kept_png}; доповнено XFBIN {updated}; помилок {failed} · {relative.as_posix()}', f'New PNG {new_png}; protected PNG {kept_png}; XFBIN supplemented {updated}; errors {failed} · {relative.as_posix()}'))
        if log and (idx % 25 == 0 or idx == len(files)):
            log(T(f'Текстури: {idx}/{len(files)}; нових PNG {new_png}; збережено старих {kept_png}; '
                f'доповнено папок {updated}; несумісних {unsupported}; конфліктів {protected_conflicts}.', f'Textures: {idx}/{len(files)}; new PNG {new_png}; old ones kept {kept_png}; '
                f'folders supplemented {updated}; incompatible {unsupported}; conflicts {protected_conflicts}.'))
    if progress:
        progress(len(files),len(files),T(f'Завершено: додано {new_png} PNG; збережено без змін {kept_png} PNG.', f'Done: {new_png} PNG added; {kept_png} PNG kept unchanged.'))
    summary={'version':'STORM1_BATCH_TEXTURES_V3_RESUMABLE_PNG','scanned_root':str(source),
             'output_root':str(base),'created_utc':datetime.now(timezone.utc).isoformat(),
             'found_xfbin':len(files),'found_total_before_filter':found_all,
             'excluded_other_languages':skipped_other_languages,
             'exported_xfbin':exported,'supplemented_xfbin':updated,
             'exported_textures':new_png,'preserved_png':kept_png,
             'available_textures':total_textures,
             'no_texture':skipped_no_texture,'unsupported':unsupported,
             'partially_exported':partial,'existing_untouched':untouched,
             'existing_png':kept_png,'protected_conflicts':protected_conflicts,
             'errors':failed,'files':results}
    manifest=base/'batch_manifest.json'
    temp_manifest=base/'batch_manifest.json.tmp'
    if manifest.is_symlink() or temp_manifest.is_symlink():
        raise BatchTextureError(T('Звіт експорту є символічним посиланням; перезапис заборонено.', 'The export report is a symbolic link; overwriting is not allowed.'))
    temp_manifest.write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    os.replace(temp_manifest,manifest)
    tsv=base/'batch_report.tsv'
    if tsv.is_symlink():
        raise BatchTextureError(T('Звіт TSV є символічним посиланням; перезапис заборонено.', 'The TSV report is a symbolic link; overwriting is not allowed.'))
    fields=['relative_path','game_relative_path','status','textures','new_png','preserved_png','export_folder','detail']
    with tsv.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,delimiter='\t',extrasaction='ignore')
        writer.writeheader();writer.writerows(results)
    summary.pop('files')
    summary['report_json']=str(manifest)
    summary['report_tsv']=str(tsv)
    return summary

def batch_build_textures(source_dir, workspace_dir, build_dir, *, log=None, progress=None):
    """Convert only edited PNGs back to original-format XFBIN in Build/data_win32.

    Uses original XFBIN files, not DDS exports, for texture encoding; untouched
    4x4 BC3 blocks, unknown chunks and all other fields are kept byte-for-byte.
    File-relative mappings are reconstructed from the source root, NEVER from
    editable batch JSON, to keep target paths predictable and traversal-safe.
    Existing Build outputs are backed up as *.xfbin.bak prior to replacement.
    """
    source,workspace,src_real=_source_and_workspace(source_dir,workspace_dir)
    build=Path(build_dir).expanduser()
    if build.resolve()==src_real or src_real in build.resolve().parents:
        raise BatchTextureError(T('Build не може бути у вхідній папці гри.', 'Build cannot be inside the game input folder.'))
    base=workspace/source.name
    if not base.is_dir():
        raise BatchTextureError(T('Спочатку експортуй PNG: немає папки ' + str(base), 'Export PNG first: there is no folder ' + str(base)))
    records=[]
    manifests=sorted(base.rglob('textures_manifest.json'),key=lambda p:str(p).casefold())
    if not manifests:
        raise BatchTextureError(T('Не знайдено маніфестів текстур. Спочатку експортуй PNG.', 'No texture manifests found. Export PNG first.'))
    counts={'built_xfbin':0,'changed_textures':0,'changed_blocks':0,'unchanged_xfbin':0,
            'missing_source':0,'skipped_other_languages':0,'failed':0,'stale_build_outputs':0}
    for idx,mp in enumerate(manifests,1):
        folder=mp.parent
        relative=folder.relative_to(base)
        rec={'relative_path':relative.as_posix(),'status':'','changed_textures':0,'changed_blocks':0,'detail':''}
        if not is_relevant_path(relative):
            rec['status']='skipped_language';counts['skipped_other_languages']+=1
        else:
            file=source/relative
            if not file.is_file():
                rec['status']='missing_source';counts['missing_source']+=1
                rec['detail']='Оригінальний XFBIN відсутній: '+str(file)
            else:
                try:
                    file.resolve().relative_to(src_real)
                    game_rel=_game_relative(file)
                    if not game_rel:
                        raise BatchTextureError(T('Не вдається визначити шлях data_win32 від dataRegion_extracted/data: '+str(file), 'Cannot work out the data_win32 path from dataRegion_extracted/data: '+str(file)))
                    result=build_textures(file,folder,build,game_rel,overwrite=True)
                    rec['detail']=result['status']
                    if result['changed_blocks']:
                        rec['status']='built'
                        rec['changed_textures']=result['changed_textures']
                        rec['changed_blocks']=result['changed_blocks']
                        counts['built_xfbin']+=1
                        counts['changed_textures']+=result['changed_textures']
                        counts['changed_blocks']+=result['changed_blocks']
                    else:
                        rec['status']='unchanged';counts['unchanged_xfbin']+=1
                        if (build/'data_win32'/Path(*game_rel.split('/'))).exists():
                            counts['stale_build_outputs']+=1
                            rec['detail']+='; У Build лишився раніше зібраний файл, перевір його вручну.'
                except Exception as exc:
                    rec['status']='error';counts['failed']+=1
                    rec['detail']=str(exc)
        records.append(rec)
        if progress:
            progress(idx, len(manifests),
                     T(f'Оновлено {counts["built_xfbin"]} XFBIN, без змін {counts["unchanged_xfbin"]}, помилок {counts["failed"]} · файл: {relative.as_posix()}', f'Updated {counts["built_xfbin"]} XFBIN, unchanged {counts["unchanged_xfbin"]}, errors {counts["failed"]} · file: {relative.as_posix()}'))
        if log and (idx%20==0 or idx==len(manifests)):
            log(T(f'Збирання PNG: {idx}/{len(manifests)}, оновлено XFBIN {counts["built_xfbin"]}, помилок {counts["failed"]}', f'Building PNG: {idx}/{len(manifests)}, XFBIN updated {counts["built_xfbin"]}, errors {counts["failed"]}'))
    if progress:
        progress(len(manifests), len(manifests), T(f'Збирання текстур завершено: оновлено {counts["built_xfbin"]} XFBIN.', f'Texture build finished: {counts["built_xfbin"]} XFBIN updated.'))
    report=build/('texture_build_report.tsv' if source.name.casefold()=='interface' else f'texture_build_report_{source.name}.tsv')
    build.mkdir(parents=True,exist_ok=True)
    with report.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['relative_path','status','changed_textures','changed_blocks','detail'],delimiter='\t')
        writer.writeheader();writer.writerows(records)
    return {'version':'STORM1_BATCH_TEXTURES_BUILD_V2','managed_workspaces':len(manifests),
            **counts,'output_root':str(build/'data_win32'),'report':str(report),
            'note':'Змінені XFBIN створено тільки в Build. Установлення в гру окреме.'}


def cleanup_managed_dds(workspace_dir, source_name='interface'):
    """Explicit opt-in: remove old v1.5 DDS, ONLY from managed PNG workspaces."""
    root=Path(workspace_dir).expanduser()/source_name
    if not root.is_dir():
        raise BatchTextureError(T('Немає папки текстур: '+str(root), 'There is no texture folder: '+str(root)))
    total=0
    for manifest in root.rglob('textures_manifest.json'):
        folder=manifest.parent
        if not is_relevant_path(folder.relative_to(root)):
            continue
        try:
            folder.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        try:
            info=json.loads(manifest.read_text(encoding='utf-8'))
        except (ValueError,OSError):
            continue
        for entry in info.get('entries',[]):
            filename=entry.get('png','')
            if not isinstance(filename,str) or not filename.startswith('chunk_') or not filename.endswith('.png'):
                continue
            stem=filename[:-4]
            if not stem[6:].isdigit():
                continue
            png=folder/filename
            dds=folder/(stem+'.dds')
            if png.is_file() and dds.is_file() and not dds.is_symlink():
                dds.unlink()
                total+=1
    return {'deleted_old_dds':total,'workspace':str(root),
            'note':'Видалено лише старі DDS для PNG із перевіреними маніфестами; оригінальні XFBIN не змінено.'}
