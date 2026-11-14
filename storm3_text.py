# -*- coding: utf-8 -*-
"""STORM 3 Full Burst + HD: one TXT, three XFBIN editions, staging, install and rollback."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
import zipfile
from collections import defaultdict

from xfbin import parse_xfbin, FormatError
from txtfmt import atomic_write, parse_txt, export_line, escape_header_source, normalize_txt_file
from storm3_font import generate_font_output
from i18n import T


# ============================================================================
# messageinfo
# ============================================================================

VERSION = '2.9.2'
SINGLE_FORMAT = 'naruto-storm3-messageinfo-txt-v1'
SOURCE_NAME = 'messageInfo.bin.xfbin'
# Tentative TEST-ONLY output path. Live engine loose-file override is UNVERIFIED.
# No installation is ever performed.
BUILD_RELATIVE_PATH = Path('data_win32') / 'message' / 'WIN32' / 'eng' / SOURCE_NAME
TXT_NAME = 'STORM3.ALL.UA.txt'
MANIFEST_NAME = 'STORM3.ALL.manifest.json'
DEFAULT_ENG = r'E:\SteamLibrary\steamapps\common\NARUTO SHIPPUDEN Ultimate Ninja STORM 3 Full Burst\data\disc\dataRegion_extracted\data\message\WIN32\eng'
DEFAULT_WORKSPACE = r'E:\Localization\Naruto\Storm3\UA_Workspace'
DEFAULT_BUILD = r'E:\Localization\Naruto\Storm3\Build'
HEADER_MAGIC = b'\xe9\x03\x00\x00'
TAG_RE = re.compile(r'<[^<>\r\n]{1,300}>')
MACRO_RE = re.compile(r'\\(?:r\\n|n|r|t)')
MAX_STRING = 256 * 1024


class Storm3Error(ValueError):
    pass


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Field:
    number: int
    record: int
    slot: int
    hash_hex: str
    metadata_hex: str
    source: str
    pointer_offset: int  # relative to start of binary chunk
    original_data_offset: int
    key: str


@dataclass
class Document:
    path: Path
    original: bytes
    sha256: str
    binary_offset: int
    binary_header_offset: int
    binary_size: int
    count: int
    fields: list[Field]


def load_messageinfo(path: str | Path) -> Document:
    p = Path(path)
    if not p.is_file():
        raise Storm3Error(T(f'Відсутній оригінальний файл: {p}', f'Original file is missing: {p}'))
    try:
        xf = parse_xfbin(p)
    except (OSError, FormatError) as exc:
        raise Storm3Error(T(f'Помилка NUCC: {exc}', f'NUCC error: {exc}')) from exc
    if xf.nucc_version != 121:
        raise Storm3Error(T(f'Непідтримувана NUCC-версія: {xf.nucc_version}', f'Unsupported NUCC version: {xf.nucc_version}'))
    if len(xf.chunks) != 4 or [c.type_name for c in xf.chunks] != [
        'nuccChunkNull', 'nuccChunkNull', 'nuccChunkBinary', 'nuccChunkPage'
    ]:
        raise Storm3Error(T('Невідома структура контейнера: очікуються Null, Null, Binary, Page.', 'Unknown container structure: expected Null, Null, Binary, Page.'))
    ck = xf.chunks[2]
    if ck.name != 'messageInfo' or not ck.path.casefold().endswith('win32/eng/messageinfo.bin'):
        raise Storm3Error(T(f'Невідома таблиця: {ck.path} / {ck.name}', f'Unknown table: {ck.path} / {ck.name}'))
    b = xf.blob[ck.data_offset:ck.end]
    if len(b) < 16 or struct.unpack_from('>I', b)[0] != len(b)-4 or b[4:8] != HEADER_MAGIC:
        raise Storm3Error(T('Непідтримуваний заголовок messageInfo.', 'Unsupported messageInfo header.'))
    count = struct.unpack_from('<I', b, 8)[0]
    if not 1 <= count <= 80_000 or b[12:16] != b'\x04\x00\x00\x00':
        raise Storm3Error(T('Неперевірені службові поля/кількість записів messageInfo.', 'Unverified service fields / record count in messageInfo.'))
    pool_start = 0x10 + count * 16
    if pool_start >= len(b):
        raise Storm3Error(T('Таблиця записів виходить за межі блока.', 'The record table runs past the end of the block.'))
    fields = []
    seen_targets = set()
    for index in range(count):
        row = 0x10 + index * 16
        record_hash = b[row:row+4].hex().upper()
        for slot, field_off in ((1,4),(2,8)):
            ptr = row + field_off
            rel = struct.unpack_from('<I', b, ptr)[0]
            if rel == 0:
                continue
            target = ptr + rel
            if target < pool_start or target >= len(b) or target % 4:
                raise Storm3Error(T(f'Недійсний вказівник у записі {index+1}, полі {slot}.', f'Invalid pointer in record {index+1}, field {slot}.'))
            if target in seen_targets:
                raise Storm3Error(T(f'Спільний вказівник у записі {index+1}; потрібен окремий аналіз.', f'Shared pointer in record {index+1}; this needs separate analysis.'))
            seen_targets.add(target)
            ending = b.find(b'\0', target)
            if ending < 0 or ending - target > MAX_STRING:
                raise Storm3Error(T(f'Неприпустима довжина рядка {index+1}/{slot}.', f'Invalid string length in {index+1}/{slot}.'))
            try:
                source = b[target:ending].decode('utf-8','strict')
            except UnicodeDecodeError as exc:
                raise Storm3Error(T(f'Неправильний UTF-8 у {index+1}/{slot}: {exc}', f'Invalid UTF-8 in {index+1}/{slot}: {exc}')) from exc
            if any(ord(c)<32 and c not in '\r\n\t' for c in source):
                raise Storm3Error(T(f'Керувальні символи в записі {index+1}/{slot}.', f'Control characters in record {index+1}/{slot}.'))
            key = f'MI{index+1:05d}-F{slot}'
            fields.append(Field(len(fields)+1, index, slot, record_hash, b[row+12:row+16].hex().upper(), source, ptr, target, key))
    if not fields or len({f.original_data_offset for f in fields}) != len(fields):
        raise Storm3Error(T('Неочікуваний спільний або відсутній текстовий пул.', 'Unexpected shared or missing text pool.'))
    return Document(p, xf.blob, xf.sha256, ck.data_offset, ck.header_offset, len(b), count, fields)


def make_header(field: Field) -> str:
    context = 'Заголовок' if field.slot == 1 else 'Основний текст'
    preview = escape_header_source(field.source)
    # Complete English source belongs in the header; never truncate narrative text.
    return (f'### {field.number} — messageInfo\\{field.record+1:05d}_{field.hash_hex}'
            f'\\F{field.slot}.txt · {context} · EN: {preview}')


def make_manifest(doc: Document) -> dict:
    return {'format':SINGLE_FORMAT, 'tool_version':VERSION,
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'source_filename':doc.path.name,'source_sha256':doc.sha256,
            'record_count':doc.count,'field_count':len(doc.fields),
            'rows':[{'number':f.number,'record':f.record,'slot':f.slot,
                     'hash_hex':f.hash_hex,'metadata_hex':f.metadata_hex,'key':f.key,'source':f.source,
                     'header':make_header(f)} for f in doc.fields]}


def read_manifest(path: Path | str) -> dict:
    p=Path(path)
    if not p.is_file():raise Storm3Error(T(f'Немає маніфесту: {p}', f'Manifest not found: {p}'))
    try:
        m=json.loads(p.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError) as exc:
        raise Storm3Error(T(f'Невірний JSON-маніфест: {exc}', f'Invalid JSON manifest: {exc}')) from exc
    if m.get('format')!=SINGLE_FORMAT or not isinstance(m.get('rows'),list):
        raise Storm3Error(T('Це не маніфест текстів STORM 3.', 'This is not a STORM 3 text manifest.'))
    if len(m['rows'])!=m.get('field_count') or any(r.get('number')!=i for i,r in enumerate(m['rows'],1)):
        raise Storm3Error(T('Пошкоджена нумерація рядків маніфесту.', 'The row numbering in the manifest is damaged.'))
    return m


def ensure_same(doc: Document, manifest: dict) -> None:
    if manifest.get('source_sha256') != doc.sha256:
        raise Storm3Error(T('SHA-256 оригінального XFBIN змінився. Не збираємо за чужим маніфестом.', 'The SHA-256 of the original XFBIN has changed. Will not build with a manifest made for a different file.'))
    if manifest.get('record_count') != doc.count or manifest.get('field_count') != len(doc.fields):
        raise Storm3Error(T('Структура messageInfo змінилася.', 'The messageInfo structure has changed.'))
    for field,row in zip(doc.fields, manifest['rows']):
        if (field.number != row.get('number') or field.record != row.get('record') or
            field.slot != row.get('slot') or field.key != row.get('key') or
            field.hash_hex != row.get('hash_hex') or field.metadata_hex != row.get('metadata_hex') or field.source != row.get('source') or
            make_header(field) != row.get('header')):
            raise Storm3Error(T(f'Маніфест не збігається з оригіналом на записі {field.number}.', f'The manifest does not match the original at record {field.number}.'))


def validate_translation(field: Field, translation: str) -> bytes:
    if not isinstance(translation,str):
        raise Storm3Error(T(f'{field.key}: переклад має бути рядком.', f'{field.key}: the translation must be a string.'))
    if not translation or translation==field.source:
        return field.source.encode('utf-8')
    if field.source.startswith('**No Translation (') and field.source.endswith(')**'):
        raise Storm3Error(T(f'{field.key}: захищений системний маркер No Translation.', f'{field.key}: protected system marker No Translation.'))
    if '\0' in translation or any(ord(c)<32 and c not in '\r\n\t' for c in translation):
        raise Storm3Error(T(f'{field.key}: неприпустимий керувальний символ.', f'{field.key}: control character not allowed.'))
    if len(translation.encode('utf-8'))>MAX_STRING:
        raise Storm3Error(T(f'{field.key}: текст завеликий.', f'{field.key}: the text is too long.'))
    if TAG_RE.findall(field.source)!=TAG_RE.findall(translation):
        raise Storm3Error(T(f'{field.key}: змінено теги <...> або їхній порядок.', f'{field.key}: the <...> tags or their order were changed.'))
    if MACRO_RE.findall(field.source)!=MACRO_RE.findall(translation):
        raise Storm3Error(T(f'{field.key}: змінено текстові escape-послідовності.', f'{field.key}: the text escape sequences were changed.'))
    src_breaks=re.findall(r'\r\n|\r|\n',field.source)
    new_breaks=re.findall(r'\r\n|\r|\n',translation)
    if new_breaks != src_breaks:
        raise Storm3Error(T(f'{field.key}: потрібно зберегти всі вихідні переноси рядків.', f'{field.key}: all original line breaks must be kept.'))
    return translation.encode('utf-8')


# ============================================================================
# deploy
# ============================================================================

TEXT_SUFFIX = Path('message')/'WIN32'/'eng'/SOURCE_NAME
FONT_SUFFIX = Path('ui')/'font'/'xfgame_cmn.fnt.xfbin'
PREFIXES = (Path('data_win32'), Path('dlc')/'data_win32')
ALLOWED = (TEXT_SUFFIX, FONT_SUFFIX)

class Storm3InstallError(RuntimeError):
    pass


def _sha(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def _atomic(path:Path, data:bytes)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, name=tempfile.mkstemp(prefix='.storm3_new_',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def _check_symlinks(root:Path,relative:Path)->None:
    if root.is_symlink():raise Storm3InstallError(T('Коренева тека гри є посиланням.', 'The game root folder is a link.'))
    at=root
    for part in relative.parts:
        at=at/part
        if at.is_symlink():raise Storm3InstallError(T('Символьне посилання заборонено: '+str(at), 'Symbolic links are not allowed: '+str(at)))


def _normalize_root(game_root:str|Path)->tuple[Path,tuple[Path,...]]:
    root=Path(game_root).expanduser().absolute()
    # User may choose the hd subfolder itself instead of parent.
    if root.name.casefold() == 'dlc' and ((root/'NSUNS3.exe').is_file() or (root/'data'/'disc'/'dataRegion.cpk').is_file()):
        root=root.parent
    if not root.is_dir() or root.is_symlink():
        raise Storm3InstallError(T('Не знайдено справжню папку встановленої STORM 3.', 'The real folder of the installed STORM 3 was not found.'))
    standard=any(p.is_file() for p in (root/'NSUNS3.exe',root/'data'/'disc'/'dataRegion.cpk'))
    hd=any(p.is_file() for p in (root/'dlc'/'NSUNS3.exe',root/'dlc'/'data'/'disc'/'dataRegion.cpk'))
    if not (standard or hd):
        raise Storm3InstallError(T('Папка не схожа на встановлену STORM 3: немає NSUNS3.exe або dataRegion.cpk (зокрема у dlc).', 'The folder does not look like an installed STORM 3: NSUNS3.exe or dataRegion.cpk is missing (including in dlc).'))
    return root,tuple(p for p,on in zip(PREFIXES,(standard,hd)) if on)


def _validate_paths(root:Path, build:Path, sources:tuple[Path,...])->None:
    r=root.resolve();b=build.resolve()
    if r==b or r.is_relative_to(b) or b.is_relative_to(r):
        raise Storm3InstallError(T('Build має бути поза папкою гри, щоб резервні копії не потрапили під установлення.', 'Build must be outside the game folder so that the backups are not affected by the installation.'))
    for path in sources:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(b):
            raise Storm3InstallError(T('Відсутній або сторонній файл збірки: '+str(path), 'Build file is missing or is not inside Build: '+str(path)))


def prepare_bundle(build_root:str|Path, suffix:Path, payload:bytes, *, log=None)->dict:
    """Only known resources. Keep both layouts in one Build; ZIP contains both."""
    if suffix not in ALLOWED:
        raise Storm3InstallError(T('Невідомий ресурс STORM 3: '+str(suffix), 'Unknown STORM 3 resource: '+str(suffix)))
    if not payload:
        raise Storm3InstallError(T('Неможливо пакувати порожній файл.', 'Cannot pack an empty file.'))
    if suffix==TEXT_SUFFIX:
        fd, temp=tempfile.mkstemp(prefix='storm3_stage_',suffix='.xfbin')
        try:
            with os.fdopen(fd,'wb') as f:f.write(payload)
            load_messageinfo(temp)
        finally:
            Path(temp).unlink(missing_ok=True)
    build=Path(build_root).expanduser().absolute()
    staged=[]
    for prefix in PREFIXES:
        relative=prefix/suffix
        dst=build/relative
        # Don't follow symlinks anywhere in Build.
        at=build
        for part in relative.parts:
            at=at/part
            if at.is_symlink():raise Storm3InstallError(T('Заборонене посилання у Build: '+str(at), 'Forbidden link in Build: '+str(at)))
        if dst.is_file() and dst.read_bytes()!=payload:
            backup=build/'build_backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')/relative
            _atomic(backup,dst.read_bytes())
        _atomic(dst,payload)
        staged.append(relative)
    # Construct one zip of the known staged resources; every existing staged
    # font/text override in BOTH layouts is included, never unrelated files.
    archive=build/'STORM3_UA_LOOSE_OVERRIDES.zip'
    fd,tmp=tempfile.mkstemp(prefix='.storm3_zip_',suffix='.zip',dir=build)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for prefix in PREFIXES:
                for item in ALLOWED:
                    rel=prefix/item
                    f=build/rel
                    if f.is_file() and not f.is_symlink():
                        z.write(f,arcname=rel.as_posix())
        os.replace(tmp,archive)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    if log:log(T('STORM 3: зібрано дві схеми в одній теці Build: '+str(build), 'STORM 3: both layouts built in one Build folder: '+str(build)))
    return {'build_dir':str(build),'stage_paths':[str(build/p) for p in staged],
            'zip':str(archive),'sha256':_sha(payload)}


def install_staged(build_root:str|Path, game_root:str|Path, suffixes:list[Path], *,log=None)->dict:
    """All backups before any game write; rollback on failure.

    Install into only the editions actually present, but stage both layouts.
    """
    build=Path(build_root).expanduser().absolute();game,prefixes=_normalize_root(game_root)
    if not suffixes or any(s not in ALLOWED for s in suffixes):
        raise Storm3InstallError(T('Підтримуються лише messageInfo і xfgame_cmn.fnt.', 'Only messageInfo and xfgame_cmn.fnt are supported.'))
    sources=tuple(build/Path('data_win32')/s for s in suffixes)
    _validate_paths(game,build,sources)
    changed=[]; unchanged=0
    for prefix in prefixes:
        for suffix in suffixes:
            relative=prefix/suffix
            src=build/relative
            _check_symlinks(build,relative)
            if src.is_symlink() or not src.is_file():
                raise Storm3InstallError(T('У Build немає ресурсу: '+str(src), 'Resource is missing from Build: '+str(src)))
            dst=game/relative
            _check_symlinks(game,relative)
            if dst.exists() and not dst.is_file():
                raise Storm3InstallError(T('Ціль не є файлом: '+str(dst), 'The target is not a file: '+str(dst)))
            new=src.read_bytes()
            old=dst.read_bytes() if dst.exists() else None
            if new==old:
                unchanged+=1
                continue
            changed.append({'relative':relative,'target':dst,'new':new,'old':old})
    if not changed:
        return {'installed':0,'already_current':unchanged,'game_root':str(game),'editions':[p.as_posix() for p in prefixes],
                'backup_dir':None,'engine_text_override_confirmed':False}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    backup_dir=build/'installation_backups'/stamp
    if backup_dir.exists():raise Storm3InstallError(T('Конфлікт назви теки резервних копій.', 'Backup folder name conflict.'))
    backup_dir.mkdir(parents=True,exist_ok=False)
    rows=[]
    for item in changed:
        backup=None
        if item['old'] is not None:
            backup=backup_dir/item['relative']
            _atomic(backup,item['old'])
            if _sha(backup.read_bytes())!=_sha(item['old']):
                raise Storm3InstallError(T('Збій перевірки резервної копії: '+str(backup), 'Backup verification failed: '+str(backup)))
        rows.append({'relative':item['relative'].as_posix(),
                     'original_exists':item['old'] is not None,
                     'previous_sha256':_sha(item['old']) if item['old'] is not None else None,
                     'new_sha256':_sha(item['new'])})
    report={'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'game_root':str(game),'build_root':str(build),'files':rows,
            'editions':[p.as_posix() for p in prefixes],
            'engine_text_override_confirmed':False,
            'note':'Установлення завершено, але використання текстового override рушієм ще потребує ігрової перевірки.'}
    # Save rollback manifest *before* changing game files.
    _atomic(backup_dir/'deployment.json',(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    written=[]
    try:
        for item in changed:
            # Register before the write to cover exceptions from os.replace/fsync.
            written.append(item)
            _check_symlinks(game,item['relative'])
            _atomic(item['target'],item['new'])
            if _sha(item['target'].read_bytes())!=_sha(item['new']):
                raise Storm3InstallError(T('SHA-256 після встановлення не збігся: '+str(item['target']), 'SHA-256 does not match after installation: '+str(item['target'])))
            if log:log(T('STORM 3 · Установлено: '+str(item['target']), 'STORM 3 · Installed: '+str(item['target'])))
    except Exception as exc:
        errors=[]
        for item in reversed(written):
            try:
                if item['old'] is None:
                    item['target'].unlink(missing_ok=True)
                else:
                    _atomic(item['target'],item['old'])
            except Exception as e:errors.append(str(e))
        raise Storm3InstallError(T('Установлення не завершено. Відкат: '+('НЕПОВНИЙ: '+';'.join(errors) if errors else 'успішний')+'; '+str(exc), 'Installation was not completed. Rollback: '+('INCOMPLETE: '+';'.join(errors) if errors else 'successful')+'; '+str(exc))) from exc
    _atomic(build/'STORM3_LAST_INSTALL.json',
            (json.dumps({'backup_manifest':str(backup_dir/'deployment.json')},ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    return {'installed':len(changed),'already_current':unchanged,'game_root':str(game),
            'editions':[p.as_posix() for p in prefixes], 'backup_dir':str(backup_dir),
            'engine_text_override_confirmed':False}


def restore_staged_last(build_root:str|Path, game_root:str|Path, *,log=None)->dict:
    """Restore only the last transaction; abort when current game files differ."""
    build=Path(build_root).expanduser().absolute();game,_=_normalize_root(game_root)
    marker=build/'STORM3_LAST_INSTALL.json'
    if not marker.is_file():raise Storm3InstallError(T('Не знайдено останнього встановлення.', 'The last installation was not found.'))
    manifest=Path(json.loads(marker.read_text(encoding='utf-8'))['backup_manifest'])
    if not manifest.is_file() or not manifest.resolve().is_relative_to((build/'installation_backups').resolve()):
        raise Storm3InstallError(T('Недійсний маніфест резервних копій.', 'Invalid backup manifest.'))
    rep=json.loads(manifest.read_text(encoding='utf-8'))
    if Path(rep['game_root']).resolve()!=game.resolve():
        raise Storm3InstallError(T('Резервна копія створена для іншої папки гри.', 'The backup was made for a different game folder.'))
    rows=[]
    for ent in rep['files']:
        relative=Path(ent['relative'])
        if relative not in [prefix/s for prefix in PREFIXES for s in ALLOWED]:
            raise Storm3InstallError(T('Сторонній шлях у маніфесті відкату.', 'Unexpected path in the rollback manifest.'))
        target=game/relative
        _check_symlinks(game,relative)
        if not target.is_file() or _sha(target.read_bytes())!=ent['new_sha256']:
            raise Storm3InstallError(T('Гра змінена після встановлення, відкат зупинено: '+str(target), 'The game was changed after installation, rollback stopped: '+str(target)))
        before=manifest.parent/relative
        payload=before.read_bytes() if ent['original_exists'] else None
        if payload is not None and _sha(payload)!=ent['previous_sha256']:
            raise Storm3InstallError(T('Змінена резервна копія: '+str(before), 'The backup was changed: '+str(before)))
        rows.append((target,payload))
    # All preconditions passed before edits.
    for target,data in reversed(rows):
        if data is None:target.unlink()
        else:_atomic(target,data)
        if log:log(T('STORM 3 · Відновлено: '+str(target), 'STORM 3 · Restored: '+str(target)))
    marker.unlink()
    return {'restored':len(rows),'game_root':str(game),'manifest':str(manifest)}


# ============================================================================
# unified
# ============================================================================

FORMAT = 'storm3-unified-3editions-v1'
TXT = 'STORM3.ALL.UA.txt'
MANIFEST = 'STORM3.ALL.manifest.json'
REPEATS = 'STORM3.REPEATS.json'
EDITION_MAP = 'STORM3.EDITION_MAP.json'
LABELS = ('original', 'hd_win32', 'hd_win64')
PROFILE = {
  'original': ('WIN32/eng/messageInfo.bin',4,16,16,4,8,4),
  'hd_win32': ('WIN32/eng/messageInfo.bin',4,16,24,4,8,4),
  'hd_win64': ('WIN64/eng/messageInfo.bin',8,20,40,8,16,8),
}
REL = {
  'original':Path('data_win32/message/WIN32/eng/messageInfo.bin.xfbin'),
  'hd_win32':Path('dlc/data_win32/message/WIN32/eng/messageInfo.bin.xfbin'),
  'hd_win64':Path('dlc/data_win32/message/WIN64/eng/messageInfo.bin.xfbin'),
}
class UnifiedError(ValueError):pass

def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()

@dataclass(frozen=True)
class UField:
    index: int
    slot: int
    hash_hex: str
    source: str
    ptr_at: int
    key: str
    metadata: str

@dataclass
class UDoc:
    label:str
    path:Path
    original:bytes
    sha256:str
    offset:int
    header_offset:int
    binary_size:int
    count:int
    width:int
    fields:list[UField]


def read_source(label:str,path:str|Path)->UDoc:
    if label not in PROFILE:raise UnifiedError(T('Невідома редакція: '+label, 'Unknown edition: '+label))
    p=Path(path).expanduser().absolute()
    if p.is_dir():p=p/SOURCE_NAME
    if not p.is_file():raise UnifiedError(T('Немає оригінального XFBIN: '+str(p), 'Original XFBIN not found: '+str(p)))
    xf=parse_xfbin(p)
    if xf.nucc_version!=121 or len(xf.chunks)!=4 or [c.type_name for c in xf.chunks]!=[
        'nuccChunkNull','nuccChunkNull','nuccChunkBinary','nuccChunkPage']:
        raise UnifiedError(T('Невідомий NUCC-профіль: '+str(p), 'Unknown NUCC profile: '+str(p)))
    expected,word,base,stride,poff1,poff2,width=PROFILE[label]
    ck=xf.chunks[2]
    if ck.name!='messageInfo' or ck.path.casefold()!=expected.casefold():
        raise UnifiedError(T(f'{label}: невірний внутрішній шлях {ck.path} (очікується {expected}).', f'{label}: wrong internal path {ck.path} (expected {expected}).'))
    blob=xf.blob[ck.data_offset:ck.end]
    if len(blob)<32 or struct.unpack_from('>I',blob,0)[0]!=len(blob)-4 or blob[4:8]!=b'\xe9\x03\x00\x00':
        raise UnifiedError(T(f'{label}: невідомий заголовок messageInfo.', f'{label}: unknown messageInfo header.'))
    count=struct.unpack_from('<I',blob,8)[0]
    if not 1<=count<=80000 or struct.unpack_from('<I',blob,12)[0]!=word:
        raise UnifiedError(T(f'{label}: невірні службові поля таблиці.', f'{label}: wrong service fields in the table.'))
    if label=='hd_win64' and blob[16:20]!=b'\0'*4:
        raise UnifiedError(T('WIN64: невідома область вирівнювання 0x10–0x14.', 'WIN64: unknown alignment area 0x10–0x14.'))
    pool=base+count*stride
    if pool>=len(blob):raise UnifiedError(T(f'{label}: таблиця записів виходить за межі чанка.', f'{label}: the record table runs past the end of the chunk.'))
    fields=[];seen=set();hashes=set();fmt='<I' if width==4 else '<Q'
    for i in range(count):
        row=base+i*stride
        h=blob[row:row+4].hex().upper()
        if h in hashes:raise UnifiedError(T(f'{label}: дубль HASH {h}; потрібен контекстний ключ.', f'{label}: duplicate HASH {h}; a context key is needed.'))
        hashes.add(h)
        if label=='hd_win64' and blob[row+4:row+8]!=b'\0'*4:
            raise UnifiedError(T(f'{label}: невідомий резерв у записі {i+1}.', f'{label}: unknown reserved data in record {i+1}.'))
        for slot,offset in ((1,poff1),(2,poff2)):
            ptr=row+offset
            rel=struct.unpack_from(fmt,blob,ptr)[0]
            if not rel:continue
            target=ptr+rel
            if not(pool<=target<len(blob)) or target%4:
                raise UnifiedError(T(f'{label}: вказівник поза пулом у записі {i+1}/F{slot}.', f'{label}: pointer outside the pool in record {i+1}/F{slot}.'))
            if target in seen:raise UnifiedError(T(f'{label}: повторний вказівник; потрібен окремий аналіз.', f'{label}: repeated pointer; this needs separate analysis.'))
            seen.add(target)
            end=blob.find(b'\0',target)
            if end<0 or end-target>MAX_STRING:raise UnifiedError(T(f'{label}: некоректний UTF-8-пул {i+1}/F{slot}.', f'{label}: malformed UTF-8 pool at {i+1}/F{slot}.'))
            try:text=blob[target:end].decode('utf-8','strict')
            except UnicodeDecodeError as exc:raise UnifiedError(T(f'{label}: недійсний UTF-8 у {i+1}/F{slot}.', f'{label}: invalid UTF-8 in {i+1}/F{slot}.')) from exc
            if any(ord(c)<32 and c not in '\r\n\t' for c in text):
                raise UnifiedError(T(f'{label}: недозволені керувальні символи у {i+1}/F{slot}.', f'{label}: control characters not allowed in {i+1}/F{slot}.'))
            non_pointer=(blob[row:row+poff1]+blob[row+poff1+width:row+poff2]+blob[row+poff2+width:row+stride]).hex().upper()
            fields.append(UField(i,slot,h,text,ptr,f'{h}-F{slot}',non_pointer))
    return UDoc(label,p,xf.blob,xf.sha256,ck.data_offset,ck.header_offset,len(blob),count,width,fields)


def read_sources(paths)->dict[str,UDoc]:
    if not isinstance(paths,dict) or set(paths)!=set(LABELS):
        raise UnifiedError(T('Виберіть три оригінали: Full Burst, HD WIN32 і HD WIN64.', 'Select three originals: Full Burst, HD WIN32 and HD WIN64.'))
    docs={label:read_source(label,paths[label]) for label in LABELS}
    if len({d.path.resolve() for d in docs.values()})!=3:
        raise UnifiedError(T('Три вихідні файли мають бути різними.', 'The three source files must be different.'))
    a,b=docs['hd_win32'],docs['hd_win64']
    if a.count!=b.count or len(a.fields)!=len(b.fields):
        raise UnifiedError(T('HD WIN32 та WIN64 мають різні кількості записів / полів.', 'HD WIN32 and WIN64 have different numbers of records / fields.'))
    for fa,fb in zip(a.fields,b.fields):
        if (fa.index,fa.slot,fa.hash_hex,fa.source)!=(fb.index,fb.slot,fb.hash_hex,fb.source):
            raise UnifiedError(T(f'HD WIN32/64 різняться у {fa.key}; потрібен незалежний переклад.', f'HD WIN32/64 differ at {fa.key}; a separate translation is needed.'))
    return docs


def layout(docs:dict[str,UDoc]):
    per={lbl:{(f.hash_hex,f.slot):f for f in docs[lbl].fields} for lbl in LABELS}
    # Keep narrative/resource order of original Full Burst, append HD-only entries.
    # Never sort by hash: that scrambles the text into a meaningless order.
    seen=set();keys=[]
    for lbl in ('original','hd_win32'):
        for f in docs[lbl].fields:
            k=(f.hash_hex,f.slot)
            if k not in seen:
                seen.add(k);keys.append(k)
    rows=[]; bindings={lbl:[] for lbl in LABELS}
    source_conflicts=[]
    def add(key,source,labels):
        n=len(rows)+1
        row={'number':n,'key':key,'source':source,
             'header':f'### {n} — {key} · EN: {escape_header_source(source)}',
             'editions':list(labels)}
        rows.append(row)
        for lbl in labels:
            f=per[lbl][logical]
            bindings[lbl].append({'number':n,'record':f.index,'slot':f.slot,'hash':f.hash_hex,
                                   'original_source':f.source})
    for logical in keys:
        key=f'{logical[0]}-F{logical[1]}'
        participants=[lbl for lbl in LABELS if logical in per[lbl]]
        old=per['original'].get(logical)
        hd=per['hd_win32'].get(logical)
        if old and hd and old.source.rstrip(' ' )!=hd.source.rstrip(' '):
            source_conflicts.append({'key':key,'full_burst':old.source,'hd':hd.source})
            add(key+'-ORIGINAL',old.source,['original'])
            add(key+'-HD',hd.source,['hd_win32','hd_win64'])
        else:
            # HD is preferred because its text has the latest minor spacing corrections.
            src=hd.source if hd else old.source
            add(key,src,participants)
    return rows,bindings,source_conflicts


def _meta_doc(d:UDoc)->dict:
    return {'sha256':d.sha256,'count':d.count,'fields':len(d.fields),'profile':d.label,
            'inner_path':PROFILE[d.label][0], 'source_filename':d.path.name}


def create_plan(paths):
    docs=read_sources(paths)
    rows,binds,conflicts=layout(docs)
    return {'format':FORMAT,'sources':{label:_meta_doc(doc) for label,doc in docs.items()},
            'rows':rows,'bindings':binds,'conflicts':conflicts,
            'summary':{'master_rows':len(rows),'conflicts_split':len(conflicts),
                       'full_burst_fields':len(docs['original'].fields),
                       'hd_fields':len(docs['hd_win32'].fields),
                       'hd_win32_hd_win64_texts_identical':True}}


def _get_current(project:Path,plan):
    txt=project/TXT; mfile=project/MANIFEST
    if txt.exists()!=mfile.exists():raise UnifiedError(T('Знайдено тільки TXT або тільки маніфест.', 'Found only the TXT or only the manifest.'))
    if not txt.exists():return {}
    old=json.loads(mfile.read_text(encoding='utf-8-sig'))
    if old.get('format')==FORMAT:
        if old['sources']!=plan['sources'] or old['rows']!=plan['rows'] or old['bindings']!=plan['bindings']:
            raise UnifiedError(T('Оригінали або карта відповідностей змінилися. Експорт зупинено, щоб не втратити переклад.', 'The originals or the edition map has changed. Export stopped so that the translation is not lost.'))
        return parse_txt(txt,old)
    # Migrate old single-edition translations only when their source identity matches.
    if old.get('format')=='naruto-storm3-messageinfo-txt-v1':
        old_doc=load_messageinfo(plan['_legacy_path'])
        ensure_same(old_doc,old)
        old_texts=parse_txt(txt,old)
        index={row['key']:row['number'] for row in plan['rows']}
        translated={}
        for f in old_doc.fields:
            value=old_texts.get(f.number,'')
            if not value:continue
            base=f'{f.hash_hex}-F{f.slot}'
            master_num=index.get(base+'-ORIGINAL') or index.get(base)
            if not master_num:raise UnifiedError(T('Не вдалося зіставити старий переклад: '+base, 'Could not match the old translation: '+base))
            if master_num in translated and translated[master_num]!=value:
                raise UnifiedError(T('Два різні переклади спроєктовано на той самий рядок: '+base, 'Two different translations map to the same row: '+base))
            translated[master_num]=value
        return translated
    raise UnifiedError(T('У папці знайдено інший формат TXT/маніфесту. Використайте нову робочу папку.', 'A different TXT/manifest format was found in the folder. Use a new workspace folder.'))


def export_project(paths, project_dir, *, log=None):
    docs=read_sources(paths); rows,binds,conflicts=layout(docs)
    plan={'format':FORMAT,'sources':{label:_meta_doc(doc) for label,doc in docs.items()},
          'rows':rows,'bindings':binds,'summary':{'master_rows':len(rows),'conflicts_split':len(conflicts),
          'full_burst_fields':len(docs['original'].fields),'hd_fields':len(docs['hd_win32'].fields),
          'hd_win32_hd_win64_texts_identical':True}}
    project=Path(project_dir).expanduser().absolute()
    for doc in docs.values():
        if project==doc.path.parent or project.is_relative_to(doc.path.parent):
            raise UnifiedError(T('Проєкт TXT має бути поза оригінальними ресурсами гри.', 'The TXT project must be outside the original game resources.'))
    temp=dict(plan);temp['_legacy_path']=docs['original'].path
    old=_get_current(project,temp)
    # Validate migrated values against all three editions before writing anything.
    for label,doc in docs.items():
        by_id={(f.index,f.slot):f for f in doc.fields}
        for bd in binds[label]:
            value=old.get(bd['number'])
            if value:
                try:validate_translation(by_id[(bd['record'],bd['slot'])],value)
                except ValueError as exc:
                    raise UnifiedError(T('Неможливо автоматично перенести переклад '+str(bd['number'])+
                                       ' у '+label+': '+str(exc), 'Cannot carry over translation '+str(bd['number'])+' to '+label+' automatically: '+str(exc))) from exc
    lines=['#! STORM 3 Full Burst + HD (WIN32 / WIN64) · єдиний переклад',
           '#! Відредагуйте лише тіло після ### N і перед завершальним ###.',
           '#! Не редагуйте заголовки, EN, ідентифікатори та службові символи.',
           '#! Теги <...>, текстові \\n і фізичні переноси рядків зберігайте.',
           '#! Порожнє поле перекладу = залишити оригінальний текст у відповідній редакції.',
           '#! Однакові англійські репліки різних мовців/контекстів НЕ об’єднано без підтвердження.']
    for row in rows:
        lines.append(row['header'])
        if old.get(row['number']):lines.extend(export_line(old[row['number']]))
        lines.extend(['','###'])
    # Candidate repeat list only, never semantically collapse unrelated dialogue.
    by_text=defaultdict(list)
    for row in rows:
        if row['source'] and not row['source'].startswith('**No Translation ('):
            by_text[row['source']].append(row['number'])
    repeated=[{'source':s,'master_row_numbers':ids} for s,ids in by_text.items() if len(ids)>1]
    repeats={'format':'storm3-repeat-candidates-v1','auto_merged':False,
             'note':'Тільки кандидати. Репліки з різним контекстом не склеєно автоматично.',
             'groups':repeated}
    edition_map={'format':'storm3-edition-map-v1','sources':plan['sources'],
                 'bindings':binds,'conflicts':conflicts}
    project.mkdir(parents=True,exist_ok=True)
    if (project/TXT).exists():
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        backup=project/'project_backups'/stamp
        for name in (TXT,MANIFEST,EDITION_MAP,REPEATS):
            if (project/name).exists():
                backup.mkdir(parents=True,exist_ok=True)
                shutil.copy2(project/name,backup/name)
    atomic_write(project/TXT,('\r\n'.join(lines)+'\r\n').encode('utf-8-sig'))
    atomic_write(project/MANIFEST,(json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    atomic_write(project/REPEATS,(json.dumps(repeats,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    atomic_write(project/EDITION_MAP,(json.dumps(edition_map,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    result={**plan['summary'],'preserved_translations':len(old),'repeat_candidate_groups':len(repeated),
            'txt':str(project/TXT),'manifest':str(project/MANIFEST)}
    if log:log(T('STORM 3: єдиний TXT: '+str(result), 'STORM 3: unified TXT: '+str(result)))
    return result


def _validated_project(docs,project:Path):
    planfile=project/MANIFEST
    if not planfile.is_file() or not (project/TXT).is_file():
        raise UnifiedError(T('Немає STORM3.ALL.UA.txt та відповідного маніфеста.', 'STORM3.ALL.UA.txt and its manifest were not found.'))
    plan=json.loads(planfile.read_text(encoding='utf-8-sig'))
    if plan.get('format')!=FORMAT:raise UnifiedError(T('Неправильний формат проєкту: спочатку створіть спільний TXT.', 'Wrong project format: create the shared TXT first.'))
    rows,binds,conflicts=layout(docs)
    if plan.get('sources')!={lbl:_meta_doc(d) for lbl,d in docs.items()} or plan.get('rows')!=rows or plan.get('bindings')!=binds:
        raise UnifiedError(T('Оригінали / карта відповідностей не збігаються з маніфестом.', 'The originals / edition map do not match the manifest.'))
    return plan,parse_txt(project/TXT,plan)


def _build_one(doc:UDoc,bds,translated):
    data=bytearray(doc.original[doc.offset:doc.offset+doc.binary_size]); changed=0
    fields={(f.index,f.slot):f for f in doc.fields}
    pointer_locations=set()
    for bd in bds:
        field=fields[(bd['record'],bd['slot'])]
        if field.hash_hex!=bd['hash'] or field.source!=bd['original_source']:
            raise UnifiedError(T(doc.label+': маніфест поля змінився: '+field.key, doc.label+': the field manifest has changed: '+field.key))
        tr=translated.get(bd['number'],'')
        payload=validate_translation(field,tr)
        if not tr or tr==field.source:continue
        target=len(data)
        relative=target-field.ptr_at
        if relative<=0 or relative>=(1<<(doc.width*8)):
            raise UnifiedError(T(doc.label+': переповнення відносного вказівника.', doc.label+': relative pointer overflow.'))
        if field.ptr_at in pointer_locations:raise UnifiedError(T('Виявлено повторний вказівник у цільовому XFBIN.', 'A repeated pointer was found in the target XFBIN.'))
        pointer_locations.add(field.ptr_at)
        struct.pack_into('<I' if doc.width==4 else '<Q',data,field.ptr_at,relative)
        data.extend(payload+b'\0')
        data.extend(b'\0'*((-len(data))%4))
        changed+=1
    if not changed:
        return doc.original,0
    if len(data)>0xffffffff:raise UnifiedError(T('Розмір XFBIN перевищив 32-бітний контейнер.', 'The XFBIN size exceeds the 32-bit container limit.'))
    struct.pack_into('>I',data,0,len(data)-4)
    prefix=bytearray(doc.original[:doc.offset])
    struct.pack_into('>I',prefix,doc.header_offset,len(data))
    output=bytes(prefix)+bytes(data)+doc.original[doc.offset+doc.binary_size:]
    return output,changed


def _verify_one(doc:UDoc,payload:bytes,bds,translated):
    fd,tmp=tempfile.mkstemp(prefix='storm3_multi_verify_',suffix='.xfbin')
    try:
        with os.fdopen(fd,'wb') as f:f.write(payload)
        again=read_source(doc.label,tmp)
        if len(again.fields)!=len(doc.fields) or again.count!=doc.count:
            raise UnifiedError(T(doc.label+': після збирання змінилася кількість записів.', doc.label+': the number of records changed after the build.'))
        expected={(bd['record'],bd['slot']):translated.get(bd['number']) or bd['original_source'] for bd in bds}
        for before,after in zip(doc.fields,again.fields):
            k=(before.index,before.slot)
            if after.source!=expected[k] or (before.hash_hex,before.metadata)!=(after.hash_hex,after.metadata):
                raise UnifiedError(T(f'{doc.label}: помилка після перепакування {before.key}.', f'{doc.label}: error after repacking {before.key}.'))
        # Independently verify that all original bytes except pointer/size words
        # stay intact. The original string pool must survive byte for byte.
        before_bin=doc.original[doc.offset:doc.offset+doc.binary_size]
        after_bin=payload[doc.offset:doc.offset+doc.binary_size]
        mutable=set(range(4))
        by_id={(f.index,f.slot):f for f in doc.fields}
        for bd in bds:
            if translated.get(bd['number']) and translated[bd['number']]!=bd['original_source']:
                fld=by_id[(bd['record'],bd['slot'])]
                mutable.update(range(fld.ptr_at,fld.ptr_at+doc.width))
        if any(p not in mutable and old!=new for p,(old,new) in enumerate(zip(before_bin,after_bin))):
            raise UnifiedError(T(f'{doc.label}: змінено сторонні байти вихідного пулу/таблиці.', f'{doc.label}: unrelated bytes of the source pool/table were changed.'))
        outer_header_before=doc.original[:doc.offset]
        outer_header_after=payload[:doc.offset]
        if any(old!=new and p not in range(doc.header_offset,doc.header_offset+4)
               for p,(old,new) in enumerate(zip(outer_header_before,outer_header_after))):
            raise UnifiedError(T(f'{doc.label}: стороння зміна заголовка NUCC.', f'{doc.label}: unexpected change in the NUCC header.'))
        if doc.original[doc.offset+doc.binary_size:]!=payload[doc.offset+again.binary_size:]:
            raise UnifiedError(T(f'{doc.label}: змінено завершальні NUCC-чанки.', f'{doc.label}: the trailing NUCC chunks were changed.'))
    finally:
        Path(tmp).unlink(missing_ok=True)


def compile_project(paths,project_dir,build_root,*,dry_run=False,allow_replace=True,install_root=None,log=None):
    docs=read_sources(paths)
    project=Path(project_dir).expanduser().absolute()
    build=Path(build_root).expanduser().absolute()
    if (project/TXT).is_file():
        normalize_txt_file(project/TXT,log=log)
    _,translations=_validated_project(docs,project)
    for doc in docs.values():
        if build==doc.path.parent or build.is_relative_to(doc.path.parent):
            raise UnifiedError(T('Build має бути поза оригінальними XFBIN.', 'Build must be outside the original XFBIN folders.'))
    if build==project or build.is_relative_to(project):
        raise UnifiedError(T('Build має бути поза робочою папкою TXT.', 'Build must be outside the TXT workspace folder.'))
    rows,bindings,_=layout(docs)
    out={};stats={}
    for lbl,doc in docs.items():
        data,changed=_build_one(doc,bindings[lbl],translations)
        _verify_one(doc,data,bindings[lbl],translations)
        out[lbl]=data
        stats[lbl]={'modified_fields':changed,'sha256':sha(data),'size':len(data),'relative_path':REL[lbl].as_posix()}
        if log:log(T(f'STORM 3 {lbl}: перевірено {len(doc.fields):,} полів; змінено {changed:,}.', f'STORM 3 {lbl}: verified {len(doc.fields):,} fields; changed {changed:,}.'))
    result={'format':'storm3-unified-build-v1','dry_run':dry_run,'master_rows':len(rows),
            'translated_master_rows':len(translations),'profiles':stats,'installed':None,
            'hd_texts_in_sources_identical':True}
    if dry_run:return result
    if not any(x['modified_fields'] for x in stats.values()):
        result['note']='Жодної зміни перекладу: ігрові файли не перепаковано.'
        return result
    build.mkdir(parents=True,exist_ok=True)
    # Prepare all bytes in memory and reject unsafe overwrites before any write.
    for lbl in LABELS:
        path=build/REL[lbl]
        if path.is_symlink():raise UnifiedError(T('Символьні посилання в Build заборонені.', 'Symbolic links are not allowed in Build.'))
        if path.exists() and path.read_bytes()!=out[lbl] and not allow_replace:
            raise UnifiedError(T('Зібраний файл вже існує: '+str(path), 'The built file already exists: '+str(path)))
    for lbl in LABELS:
        path=build/REL[lbl]
        if path.exists() and path.read_bytes()!=out[lbl]:
            timestamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
            backup=build/'build_backups'/timestamp/REL[lbl]
            atomic_write(backup,path.read_bytes())
        atomic_write(path,out[lbl])
    archive=build/'STORM3_UA_3_EDITIONS.zip'
    fd,tmp=tempfile.mkstemp(prefix='.storm3_unified_',suffix='.zip',dir=build);os.close(fd)
    try:
        with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for lbl in LABELS:z.write(build/REL[lbl],arcname=REL[lbl].as_posix())
        os.replace(tmp,archive)
    finally:Path(tmp).unlink(missing_ok=True)
    result['zip']=str(archive)
    if install_root is not None:
        result['installed']=install_project(build,install_root,log=log)
    atomic_write(build/'STORM3_UNIFIED_BUILD_REPORT.json', (json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    return result


def install_project(build_root,game_root,*,log=None):
    build=Path(build_root).expanduser().absolute();game,prefixes=_normalize_root(game_root)
    if game.resolve()==build.resolve() or game.resolve().is_relative_to(build.resolve()) or build.resolve().is_relative_to(game.resolve()):
        raise UnifiedError(T('Build мусить бути поза папкою гри.', 'Build must be outside the game folder.'))
    expected=[]
    if Path('data_win32') in prefixes:expected.append('original')
    if Path('dlc/data_win32') in prefixes:expected+=['hd_win32','hd_win64']
    if not expected:raise UnifiedError(T('Жодної встановленої редакції STORM 3 не знайдено.', 'No installed STORM 3 edition was found.'))
    modified=[];unchanged=0
    for label in expected:
        relative=REL[label]
        _check_symlinks(build,relative);_check_symlinks(game,relative)
        src=build/relative
        if not src.is_file():raise UnifiedError(T('Немає зібраного файла: '+str(src), 'Built file not found: '+str(src)))
        new=src.read_bytes()
        # Validate output profile independently before copying to the game.
        read_source(label,src)
        dest=game/relative
        if dest.exists() and not dest.is_file():raise UnifiedError(T('Місце встановлення не є файлом: '+str(dest), 'The install target is not a file: '+str(dest)))
        old=dest.read_bytes() if dest.exists() else None
        if old==new:unchanged+=1;continue
        modified.append((label,relative,dest,old,new))
    if not modified:return {'installed':0,'already_current':unchanged,'profiles':expected}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    backup=build/'installation_backups'/('unified_'+stamp)
    items=[]
    for label,relative,dest,old,new in modified:
        if old is not None:atomic_write(backup/relative,old)
        items.append({'edition':label,'relative':relative.as_posix(),'previous_sha256':sha(old) if old is not None else None,
                      'new_sha256':sha(new),'old_exists':old is not None})
    report={'game_root':str(game),'build_root':str(build),'items':items,'timestamp':stamp,
            'engine_hd_text_override_confirmed':False}
    atomic_write(backup/'deployment.json',(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    touched=[]
    try:
        for label,relative,dest,old,new in modified:
            touched.append((dest,old))
            _atomic(dest,new)
            if sha(dest.read_bytes())!=sha(new):raise UnifiedError(T('SHA-256 не збігся після встановлення: '+str(dest), 'SHA-256 does not match after installation: '+str(dest)))
            if log:log(T('STORM 3 · Встановлено: '+str(dest), 'STORM 3 · Installed: '+str(dest)))
    except Exception as exc:
        for dest,old in reversed(touched):
            if old is None:dest.unlink(missing_ok=True)
            else:_atomic(dest,old)
        raise UnifiedError(T('Установлення не вдалося; попередні файли відновлено: '+str(exc), 'Installation failed; the previous files were restored: '+str(exc))) from exc
    atomic_write(build/'STORM3_UNIFIED_LAST_INSTALL.json',(json.dumps({'manifest':str(backup/'deployment.json')},ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    return {'installed':len(modified),'already_current':unchanged,'editions':expected,'backup_dir':str(backup),
            'engine_hd_text_override_confirmed':False}


def restore_last(build_root,game_root,*,log=None):
    build=Path(build_root).expanduser().absolute();game,_=_normalize_root(game_root)
    marker=build/'STORM3_UNIFIED_LAST_INSTALL.json'
    if not marker.is_file():raise UnifiedError(T('Немає останнього встановлення трьох редакцій.', 'The last installation of the three editions was not found.'))
    b=Path(json.loads(marker.read_text(encoding='utf-8'))['manifest'])
    if not b.resolve().is_relative_to((build/'installation_backups').resolve()):raise UnifiedError(T('Невідома резервна копія.', 'Unknown backup.'))
    report=json.loads(b.read_text(encoding='utf-8'))
    if Path(report['game_root']).resolve()!=game.resolve():raise UnifiedError(T('Резервна копія іншої інсталяції.', 'The backup belongs to a different installation.'))
    planned=[]
    for item in report['items']:
        label=item['edition']
        if label not in REL or item['relative']!=REL[label].as_posix():raise UnifiedError(T('Невідомий шлях у маніфесті.', 'Unknown path in the manifest.'))
        target=game/REL[label]
        _check_symlinks(game,REL[label])
        if not target.is_file() or sha(target.read_bytes())!=item['new_sha256']:
            raise UnifiedError(T('Файл гри змінено після встановлення: '+str(target), 'The game file was changed after installation: '+str(target)))
        old=(b.parent/REL[label]).read_bytes() if item['old_exists'] else None
        if old is not None and sha(old)!=item['previous_sha256']:raise UnifiedError(T('Змінено резервну копію.', 'The backup was changed.'))
        planned.append((target,old))
    for dest,old in reversed(planned):
        if old is None:dest.unlink()
        else:_atomic(dest,old)
        if log:log(T('Відновлено: '+str(dest), 'Restored: '+str(dest)))
    marker.unlink()
    return {'restored':len(planned)}


def save_reports(paths, output_dir):
    """Read-only bilingual structural comparison for the three source XFBIN."""
    if isinstance(paths,dict):sources=paths
    elif len(paths)==3:sources=dict(zip(LABELS,paths))
    else:raise UnifiedError(T('Для звіту потрібні три XFBIN.', 'The report needs three XFBIN files.'))
    docs=read_sources(sources)
    rows,bindings,conflicts=layout(docs)
    a={(f.hash_hex,f.slot):f for f in docs['original'].fields}
    b={(f.hash_hex,f.slot):f for f in docs['hd_win32'].fields}
    common=set(a)&set(b)
    exact=sum(a[k].source==b[k].source for k in common)
    suffix=sum(a[k].source!=b[k].source and a[k].source.rstrip(' ')==b[k].source.rstrip(' ') for k in common)
    master_summary={'classic_records':docs['original'].count,'hd_records':docs['hd_win32'].count,
       'classic_fields':len(docs['original'].fields),'hd_fields':len(docs['hd_win32'].fields),
       'common_fields_by_hash_and_slot':len(common),
       'classic_only_fields':len(set(a)-set(b)), 'hd_only_fields':len(set(b)-set(a)),
       'same_text_fields_in_common':exact, 'difference_trailing_spaces_only':suffix,
       'different_content_or_breaks':len(conflicts),'master_rows':len(rows),
       'hd_win32_hd_win64_fields_equal':True,
       'hd_win32_hd_win64_binary_equal':False,
       'record_stride_bytes':{'original':16,'hd_win32':24,'hd_win64':40}}
    out=Path(output_dir).expanduser().absolute()
    for d in docs.values():
        if out==d.path.parent or out.is_relative_to(d.path.parent):
            raise UnifiedError(T('Звіти мають бути поза папками оригінальних XFBIN.', 'Reports must be outside the folders of the original XFBIN files.'))
    out.mkdir(parents=True,exist_ok=True)
    report={'format':'storm3-edition-comparison-v2','files':{lbl:_meta_doc(d) for lbl,d in docs.items()},
            'summary':master_summary,'source_differences_requiring_separate_master_rows':conflicts,
            'note':'Тексти HD WIN32/WIN64 збігаються, бінарне пакування відрізняється. '
                   'Порівняння ігрове встановлення не виконує.'}
    j=out/'STORM3_EDITION_COMPARISON.json'
    m=out/'STORM3_EDITION_COMPARISON.md'
    t=out/'STORM3_EDITION_DIFFERENCES.tsv'
    atomic_write(j,(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    import csv,io
    handle=io.StringIO(newline='')
    writer=csv.writer(handle,delimiter='\t',lineterminator='\r\n')
    writer.writerow(('hash_and_slot','full_burst_text','hd_text'))
    for entry in conflicts:writer.writerow((entry['key'],entry['full_burst'],entry['hd']))
    atomic_write(t,handle.getvalue().encode('utf-8-sig'))
    md=['# STORM 3 · звичайна Full Burst та Full Burst HD', '',
        'Проаналізовано 3 вихідні XFBIN. У грі ці XFBIN не змінювалися.','',
        '| Критерій | Значення |','|---|---:|']
    for key,val in master_summary.items():
        md.append(f'| {key} | {val} |')
    md+=['', '## Схеми записів',
      '- Оригінальна Full Burst: 16 байтів на запис; WIN32, 32-бітні відносні вказівники.',
      '- Full Burst HD WIN32: 24 байти на запис; 32-бітні відносні вказівники.',
      '- Full Burst HD WIN64: 40 байтів на запис; 64-бітні відносні вказівники.',
      '- HD WIN32 та HD WIN64 містять ідентичні тексти за всіма ID / F1 / F2, але це **різні бінарні файли**.',
      '- Спільний переклад за внутрішнім hash + F1/F2; не за фізичним порядком рядків.',
      '- Відмінності, що не є лише кінцевими пробілами, отримують два окремі записи в одному TXT.',
      '', 'Повний список розбіжностей: `STORM3_EDITION_DIFFERENCES.tsv`.','']
    atomic_write(m,('\n'.join(md)).encode('utf-8'))
    return {'json':str(j),'tsv':str(t),'md':str(m),'summary':master_summary}


# ============================================================================
# workflow
# ============================================================================

def build_font_and_install(source:str|Path,ttf:str|Path,workspace:str|Path,
                           build_root:str|Path,game_root:str|Path, *,
                           russian_reference:str|Path|None=None,
                           copy_native_lookalikes:bool=True,log=None)->dict:
    _normalize_root(game_root)
    # Generator creates a versioned isolated folder, so use temporary output
    # and put verified XFBIN in the same canonical STORM 3 Build as the text.
    with tempfile.TemporaryDirectory(prefix='storm3_ttf_intermediate_') as temp:
        temp_root=Path(temp)
        generated=generate_font_output(source,ttf,temp_root/'work',temp_root/'build',
            FONT_SUFFIX.as_posix(),russian_reference=russian_reference,
            copy_native_lookalikes=copy_native_lookalikes,log=log)
        payload=Path(generated['file']).read_bytes()
        bundle=prepare_bundle(build_root,FONT_SUFFIX,payload,log=log)
        install=install_staged(build_root,game_root,[FONT_SUFFIX],log=log)
        # Keep previews/reports in the user's workspace; repeated generation
        # with the same TTF must work and not leave nested build directories.
        work=Path(workspace)
        work.mkdir(parents=True,exist_ok=True)
        preview=work/'STORM3_UA_GLYPHS_PREVIEW.png'
        report=work/'STORM3_FONT_TTF_REPORT.json'
        atomic_write(preview,Path(generated['preview']).read_bytes())
        atomic_write(report,Path(generated['report']).read_bytes())
        return {'added_count':generated['added_count'],'preview':str(preview),
                'font_report':str(report),'package':bundle,
                'installation':install,'installed':install['installed'],
                'engine_tested':False}

def restore_last_install(build_root:str|Path,game_root:str|Path,*,log=None)->dict:
    return restore_staged_last(build_root,game_root,log=log)
