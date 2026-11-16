# -*- coding: utf-8 -*-
"""STORM 2 messageInfo: single-TXT export and test XFBIN build (no auto-install)."""
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

from xfbin import parse_xfbin, FormatError
from txtfmt import atomic_write, parse_txt, export_line, escape_header_source, fit_line_breaks, normalize_txt_file
from i18n import T


# ============================================================================
# messageinfo
# ============================================================================

VERSION = '2.8.1'
FORMAT = 'naruto-storm2-messageinfo-txt-v1'
SOURCE_NAME = 'messageInfo.bin.xfbin'
# STORM 2 loose-file override mirrors data/event/WIN64/eng under data_win32.
# Never write compiled messageInfo directly into the Build root.
BUILD_RELATIVE_PATH = Path('data_win32') / 'event' / 'WIN64' / 'eng' / SOURCE_NAME
TXT_NAME = 'STORM2.ALL.UA.txt'
MANIFEST_NAME = 'STORM2.ALL.manifest.json'
DEFAULT_ENG = r'E:\SteamLibrary\steamapps\common\Qwant2\data\disc\dataRegion_extracted\data\event\WIN64\eng'
DEFAULT_WORKSPACE = r'E:\Localization\Naruto\Storm2\UA_Workspace'
DEFAULT_BUILD = r'E:\Localization\Naruto\Storm2\Build'
HEADER_MAGIC = b'\xe9\x03\x00\x00'
TAG_RE = re.compile(r'<[^<>\r\n]{1,300}>')
MAX_STRING = 256 * 1024


class Storm2Error(ValueError):
    pass


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Field:
    number: int
    record: int
    slot: int
    hash_hex: str
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


def load(path: str | Path) -> Document:
    p = Path(path)
    if not p.is_file():
        raise Storm2Error(T(f'Відсутній оригінальний файл: {p}', f'Original file is missing: {p}'))
    try:
        xf = parse_xfbin(p)
    except (OSError, FormatError) as exc:
        raise Storm2Error(T(f'Помилка NUCC: {exc}', f'NUCC error: {exc}')) from exc
    if xf.nucc_version != 121:
        raise Storm2Error(T(f'Непідтримувана NUCC-версія: {xf.nucc_version}', f'Unsupported NUCC version: {xf.nucc_version}'))
    if len(xf.chunks) != 4 or [c.type_name for c in xf.chunks] != [
        'nuccChunkNull', 'nuccChunkNull', 'nuccChunkBinary', 'nuccChunkPage'
    ]:
        raise Storm2Error(T('Невідома структура контейнера: очікуються Null, Null, Binary, Page.', 'Unknown container structure: expected Null, Null, Binary, Page.'))
    ck = xf.chunks[2]
    if ck.name != 'messageInfo' or not ck.path.casefold().endswith('win64/eng/messageinfo.bin'):
        raise Storm2Error(T(f'Невідома таблиця: {ck.path} / {ck.name}', f'Unknown table: {ck.path} / {ck.name}'))
    b = xf.blob[ck.data_offset:ck.end]
    if len(b) < 20 or struct.unpack_from('>I', b)[0] != len(b)-4 or b[4:8] != HEADER_MAGIC:
        raise Storm2Error(T('Непідтримуваний заголовок messageInfo.', 'Unsupported messageInfo header.'))
    count = struct.unpack_from('<I', b, 8)[0]
    if not 1 <= count <= 80_000 or b[12:20] != b'\x08\x00\x00\x00\x00\x00\x00\x00':
        raise Storm2Error(T('Неперевірені службові поля/кількість записів messageInfo.', 'Unverified service fields or record count in messageInfo.'))
    pool_start = 0x14 + count * 40
    if pool_start >= len(b):
        raise Storm2Error(T('Таблиця записів виходить за межі блока.', 'The record table runs past the end of the block.'))
    fields = []
    seen_targets = set()
    for index in range(count):
        row = 0x14 + index * 40
        record_hash = b[row:row+4].hex().upper()
        for slot, field_off in ((1,8),(2,16)):
            ptr = row + field_off
            rel = struct.unpack_from('<I', b, ptr)[0]
            if rel == 0:
                continue
            target = ptr + rel
            if target < pool_start or target >= len(b) or target % 4:
                raise Storm2Error(T(f'Недійсний вказівник у записі {index+1}, полі {slot}.', f'Invalid pointer in record {index+1}, field {slot}.'))
            if target in seen_targets:
                raise Storm2Error(T(f'Спільний вказівник у записі {index+1}; потрібен окремий аналіз.', f'Shared pointer in record {index+1}; this needs a separate analysis.'))
            seen_targets.add(target)
            ending = b.find(b'\0', target)
            if ending < 0 or ending - target > MAX_STRING:
                raise Storm2Error(T(f'Неприпустима довжина рядка {index+1}/{slot}.', f'Invalid string length in {index+1}/{slot}.'))
            try:
                source = b[target:ending].decode('utf-8','strict')
            except UnicodeDecodeError as exc:
                raise Storm2Error(T(f'Неправильний UTF-8 у {index+1}/{slot}: {exc}', f'Invalid UTF-8 in {index+1}/{slot}: {exc}')) from exc
            if any(ord(c)<32 and c not in '\r\n\t' for c in source):
                raise Storm2Error(T(f'Керувальні символи в записі {index+1}/{slot}.', f'Control characters in record {index+1}/{slot}.'))
            key = f'MI{index+1:05d}-F{slot}'
            fields.append(Field(len(fields)+1, index, slot, record_hash, source, ptr, target, key))
    return Document(p, xf.blob, xf.sha256, ck.data_offset, ck.header_offset, len(b), count, fields)


def make_header(field: Field) -> str:
    context = 'Заголовок' if field.slot == 1 else 'Основний текст'
    preview = escape_header_source(field.source)
    # Complete English source belongs in the header; never truncate narrative text.
    return (f'### {field.number} — messageInfo\\{field.record+1:05d}_{field.hash_hex}'
            f'\\F{field.slot}.txt · {context} · EN: {preview}')


def make_manifest(doc: Document) -> dict:
    return {'format':FORMAT, 'tool_version':VERSION,
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'source_filename':doc.path.name,'source_sha256':doc.sha256,
            'record_count':doc.count,'field_count':len(doc.fields),
            'rows':[{'number':f.number,'record':f.record,'slot':f.slot,
                     'hash_hex':f.hash_hex,'key':f.key,'source':f.source,
                     'header':make_header(f)} for f in doc.fields]}


def read_manifest(path: Path | str) -> dict:
    p=Path(path)
    if not p.is_file():raise Storm2Error(T(f'Немає маніфесту: {p}', f'Manifest not found: {p}'))
    try:
        m=json.loads(p.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError) as exc:
        raise Storm2Error(T(f'Невірний JSON-маніфест: {exc}', f'Invalid JSON manifest: {exc}')) from exc
    if m.get('format')!=FORMAT or not isinstance(m.get('rows'),list):
        raise Storm2Error(T('Це не маніфест текстів STORM 2.', 'This is not a STORM 2 text manifest.'))
    if len(m['rows'])!=m.get('field_count') or any(r.get('number')!=i for i,r in enumerate(m['rows'],1)):
        raise Storm2Error(T('Пошкоджена нумерація рядків маніфесту.', 'The row numbering in the manifest is damaged.'))
    return m


def ensure_same(doc: Document, manifest: dict) -> None:
    if manifest.get('source_sha256') != doc.sha256:
        raise Storm2Error(T('SHA-256 оригінального XFBIN змінився. Не збираємо за чужим маніфестом.', 'The SHA-256 of the original XFBIN has changed. Will not build with a manifest made for a different file.'))
    if manifest.get('record_count') != doc.count or manifest.get('field_count') != len(doc.fields):
        raise Storm2Error(T('Структура messageInfo змінилася.', 'The messageInfo structure has changed.'))
    for field,row in zip(doc.fields, manifest['rows']):
        if (field.number != row.get('number') or field.record != row.get('record') or
            field.slot != row.get('slot') or field.key != row.get('key') or
            field.hash_hex != row.get('hash_hex') or field.source != row.get('source') or
            make_header(field) != row.get('header')):
            raise Storm2Error(T(f'Маніфест не збігається з оригіналом на записі {field.number}.', f'The manifest does not match the original at record {field.number}.'))


def _existing(project_dir: Path, doc: Document) -> dict[int,str]:
    txt = project_dir / TXT_NAME
    manifest = project_dir / MANIFEST_NAME
    if txt.exists() != manifest.exists():
        raise Storm2Error(T('Є лише один із TXT/JSON. Не перезаписуємо незавершений проєкт.', 'Only one of the TXT/JSON files exists. Will not overwrite an unfinished project.'))
    if not txt.exists():return {}
    old = read_manifest(manifest)
    ensure_same(doc, old)
    try:return parse_txt(txt,old)
    except Exception as exc:raise Storm2Error(T(f'Не вдалося зберегти старі переклади: {exc}', f'Could not keep the old translations: {exc}')) from exc


def export_project(eng_path: Path | str, project_dir: Path | str, *, log=None) -> dict:
    src=Path(eng_path)
    if src.is_dir():src=src/SOURCE_NAME
    project_dir=Path(project_dir)
    if project_dir.resolve()==src.resolve() or project_dir.resolve().is_relative_to(src.resolve().parent):
        raise Storm2Error(T('Робочу папку TXT розміщуйте поза оригінальними ігровими ресурсами.', 'Place the TXT workspace folder outside the original game resources.'))
    doc=load(src)
    old=_existing(project_dir,doc)
    manifest=make_manifest(doc)
    lines=[
        f'#! STORM 2 · messageInfo · {doc.count} записів · {len(doc.fields)} текстових полів',
        f'#! Робоча пара: {TXT_NAME} + {MANIFEST_NAME}. Обидва файли зберігати разом.',
        '#! Редагуйте тільки текст МІЖ заголовком ### N та завершальним ###.',
        '#! Заголовки й порядок не змінювати. Кожне F1/F2 перекладається окремо.',
        '#! Порожнє поле перекладу означає «залишити англійською».',
        '#! Теги <...> з оригіналу повинні залишитися незмінними.',
        '#! Перенос рядка — новий рядок або \\n: у гру він запишеться так, як в оригіналі.',
        '#! Рядок перед ### — роздільник; внутрішні порожні рядки дозволені.',
        r'#! Для буквальних початкових # або \ використовуйте \# та \\.',
    ]
    for f in doc.fields:
        lines.append(make_header(f))
        if old.get(f.number): lines.extend(export_line(old[f.number]))
        lines.append('')
        lines.append('###')
    project_dir.mkdir(parents=True,exist_ok=True)
    if (project_dir/TXT_NAME).exists():
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
        for name in (TXT_NAME,MANIFEST_NAME):
            before=project_dir/name
            dst=project_dir/(name+'.backup_'+stamp)
            if dst.exists():raise Storm2Error(T(f'Резервна копія вже існує: {dst}', f'Backup already exists: {dst}'))
            shutil.copy2(before,dst)
    atomic_write(project_dir/MANIFEST_NAME,(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    atomic_write(project_dir/TXT_NAME,('\r\n'.join(lines)+'\r\n').encode('utf-8-sig'))
    if log:log(T(f'STORM 2: {doc.count:,} записів / {len(doc.fields):,} текстових полів; збережено {len(old):,} старих перекладів.', f'STORM 2: {doc.count:,} records / {len(doc.fields):,} text fields; kept {len(old):,} old translations.'))
    return {'records':doc.count, 'fields':len(doc.fields),'preserved_translations':len(old),
            'source_sha256':doc.sha256,'txt':str(project_dir/TXT_NAME),
            'manifest':str(project_dir/MANIFEST_NAME)}


def validate_translation(field: Field, translation: str) -> bytes:
    if not isinstance(translation,str):
        raise Storm2Error(T(f'{field.key}: переклад має бути рядком.', f'{field.key}: the translation must be a text string.'))
    translation=fit_line_breaks(translation,field.source)
    if not translation or translation==field.source:
        return field.source.encode('utf-8')
    if field.source.startswith('**No Translation (') and field.source.endswith(')**'):
        raise Storm2Error(T(f'{field.key}: захищений системний маркер No Translation.', f'{field.key}: protected system marker No Translation.'))
    if '\0' in translation or any(ord(c)<32 and c not in '\r\n\t' for c in translation):
        raise Storm2Error(T(f'{field.key}: неприпустимий керувальний символ.', f'{field.key}: control character that is not allowed.'))
    if len(translation.encode('utf-8'))>MAX_STRING:
        raise Storm2Error(T(f'{field.key}: текст завеликий.', f'{field.key}: the text is too large.'))
    if TAG_RE.findall(field.source)!=TAG_RE.findall(translation):
        raise Storm2Error(T(f'{field.key}: змінено теги <...> або їхній порядок.', f'{field.key}: the <...> tags or their order were changed.'))
    return translation.encode('utf-8')


def _load_project(eng_path: Path|str, project_dir: Path|str) -> tuple[Document,dict[int,str]]:
    p=Path(eng_path)
    if p.is_dir():p=p/SOURCE_NAME
    doc=load(p)
    project_dir=Path(project_dir)
    manifest=read_manifest(project_dir/MANIFEST_NAME)
    ensure_same(doc,manifest)
    if not (project_dir/TXT_NAME).is_file():
        raise Storm2Error(T(f'Немає TXT: {project_dir/TXT_NAME}', f'TXT not found: {project_dir/TXT_NAME}'))
    try:translations=parse_txt(project_dir/TXT_NAME,manifest)
    except Exception as exc:raise Storm2Error(T(f'Пошкоджено формат TXT: {exc}', f'The TXT format is damaged: {exc}')) from exc
    sources={f.number:f.source for f in doc.fields}
    translations={n:fit_line_breaks(v,sources[n]) if n in sources else v for n,v in translations.items()}
    return doc,translations


def build_bytes(doc: Document, translations:dict[int,str]) -> tuple[bytes,dict]:
    # Never mutate the source blob; rewrite only pointer words, size words,
    # and append new NUL-terminated 4-byte-aligned strings.
    b = bytearray(doc.original[doc.binary_offset:doc.binary_offset+doc.binary_size])
    edited={}
    for field in doc.fields:
        tr=fit_line_breaks(translations.get(field.number,''),field.source)
        payload=validate_translation(field,tr)
        if not tr or tr==field.source:continue
        if field.pointer_offset in edited:
            raise Storm2Error(T(f'Спільне поле {field.key} — збірку зупинено.', f'Shared field {field.key} — build stopped.'))
        if len(b)%4:
            raise Storm2Error(T('Неочікуване вирівнювання текстового пулу.', 'Unexpected alignment of the text pool.'))
        target=len(b)
        relative=target-field.pointer_offset
        if relative<=0 or relative>0xffffffff:
            raise Storm2Error(T('Вказівник виходить за 32-бітний діапазон.', 'The pointer is outside the 32-bit range.'))
        struct.pack_into('<I',b,field.pointer_offset,relative)
        b+=payload+b'\0'
        b+=bytes((-len(b))%4)
        edited[field.pointer_offset]=tr
    if not edited:
        return doc.original,{'modified_fields':0,'bytes_appended':0,'unchanged':True}
    if len(b)>0xffffffff:
        raise Storm2Error(T('Розмір бінарного чанка завеликий.', 'The binary chunk is too large.'))
    struct.pack_into('>I',b,0,len(b)-4)
    before=bytearray(doc.original[:doc.binary_offset])
    struct.pack_into('>I',before,doc.binary_header_offset,len(b))
    result=bytes(before)+bytes(b)+doc.original[doc.binary_offset+doc.binary_size:]
    return result,{'modified_fields':len(edited),'bytes_appended':len(b)-doc.binary_size,'unchanged':False}


def verify_build(doc: Document, result: bytes, translations:dict[int,str]) -> None:
    # Validate using the same strict NUCC parser and independently compare
    # every field against source or translated text after repacking.
    if result == doc.original and not any(translations.get(f.number,'') and
                                          translations[f.number]!=f.source for f in doc.fields):
        return
    fd, temporary=tempfile.mkstemp(prefix='storm2_check_',suffix='.xfbin')
    try:
        with os.fdopen(fd,'wb') as fp:fp.write(result)
        again=load(temporary)
        if again.count!=doc.count or len(again.fields)!=len(doc.fields):
            raise Storm2Error(T('Round-trip: змінилася кількість записів.', 'Round-trip: the number of records has changed.'))
        for o,n in zip(doc.fields,again.fields):
            wanted=translations.get(o.number) or o.source
            if n.source != wanted or (n.record,n.slot,n.hash_hex)!=(o.record,o.slot,o.hash_hex):
                raise Storm2Error(T(f'Round-trip: не збігається {o.key}.', f'Round-trip: {o.key} does not match.'))
        # Confirm byte preservation of every original byte outside the
        # intentionally modified pointer/size locations. Original pool survives.
        original_bin=doc.original[doc.binary_offset:doc.binary_offset+doc.binary_size]
        new_bin=result[doc.binary_offset:doc.binary_offset+doc.binary_size]
        mutable=set(range(4))
        for f in doc.fields:
            if translations.get(f.number) and translations[f.number]!=f.source:
                mutable.update(range(f.pointer_offset,f.pointer_offset+4))
        for pos,(a,bv) in enumerate(zip(original_bin,new_bin)):
            if a!=bv and pos not in mutable:
                raise Storm2Error(T(f'Round-trip: непередбачено змінено байт оригіналу 0x{pos:X}.', f'Round-trip: original byte 0x{pos:X} was changed unexpectedly.'))
    finally:
        try:os.unlink(temporary)
        except OSError:pass


def compile_project(eng_path:Path|str, project_dir:Path|str, output_dir:Path|str, *,
                    dry_run:bool=True, allow_replace:bool=False, log=None) -> dict:
    if (Path(project_dir)/TXT_NAME).is_file():
        normalize_txt_file(Path(project_dir)/TXT_NAME,log=log)
    doc, translations=_load_project(eng_path,project_dir)
    if Path(output_dir).resolve()==doc.path.parent.resolve() or Path(output_dir).resolve().is_relative_to(doc.path.parent.resolve()):
        raise Storm2Error(T('Вихідний каталог має бути ПОЗА оригінальними файлами STORM 2.', 'The output folder must be OUTSIDE the original STORM 2 files.'))
    data,stat=build_bytes(doc,translations)
    verify_build(doc,data,translations)
    if log:log(T('STORM 2: структурний round-trip успішний. Змінено: '+str(stat['modified_fields']), 'STORM 2: structural round-trip succeeded. Changed: '+str(stat['modified_fields'])))
    build_root=Path(output_dir)
    dest=build_root/BUILD_RELATIVE_PATH
    old_root_file=build_root/SOURCE_NAME
    if not dry_run and stat['modified_fields']:
        if dest.exists() and digest(dest.read_bytes()) != digest(data):
            if not allow_replace:
                raise Storm2Error(T('Збірка вже існує. Виберіть інший каталог або дозвольте .bak: '+str(dest), 'The build already exists. Choose another folder or allow a .bak backup: '+str(dest)))
            backup=dest.with_name(dest.name+'.bak_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
            if backup.exists():raise Storm2Error(T('Резервна копія вже існує: '+str(backup), 'Backup already exists: '+str(backup)))
            shutil.copy2(dest,backup)
        atomic_write(dest,data)
        zip_path=build_root/'STORM2_messageInfo_TEST_ONLY.zip'
        with zipfile.ZipFile(zip_path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            z.write(dest,arcname=BUILD_RELATIVE_PATH.as_posix())
        report={'source_sha256':doc.sha256,'build_sha256':digest(data),
                'game_tested':False,'installed':False,'only_messageInfo':True,
                'record_count':doc.count,'field_count':len(doc.fields),**stat,
                'file':str(dest),'archive_path':BUILD_RELATIVE_PATH.as_posix(),
                'zip':str(zip_path),
                'legacy_root_file':str(old_root_file) if old_root_file.is_file() else None,
                'warning':'Не встановлювати автоматично: loose override для STORM 2 ще не підтверджено.'}
        atomic_write(Path(output_dir)/'STORM2_BUILD_REPORT.json',
                     (json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    if log and not dry_run and stat['modified_fields']:
        log('STORM 2 XFBIN → '+str(dest))
        log('STORM 2 ZIP → '+str(build_root/'STORM2_messageInfo_TEST_ONLY.zip'))
        if old_root_file.is_file():
            log(T('УВАГА: у корені Build залишився СТАРИЙ файл '+str(old_root_file)+'; він не оновлювався. Після резервного копіювання його можна видалити вручну.', 'WARNING: an OLD file is still in the Build root: '+str(old_root_file)+'; it was not updated. After backing it up you can delete it manually.'))
    return {'records':doc.count,'fields':len(doc.fields), 'nonempty_translations':len(translations),
            'modified_fields':stat['modified_fields'],'bytes_appended':stat['bytes_appended'],
            'round_trip':'OK','dry_run':dry_run,
            'output':str(dest) if not dry_run and stat['modified_fields'] else None,
            'archive_path':BUILD_RELATIVE_PATH.as_posix(),
            'legacy_root_file':str(old_root_file) if not dry_run and old_root_file.is_file() else None,
            'game_installed':False}
