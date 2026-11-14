# -*- coding: utf-8 -*-
"""STORM 1 text: message / MissionInfo / Telop parsers, single-TXT export, build and install."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import csv
import hashlib
import json
import os
import re
import struct
import tempfile
import base64
import zlib
from typing import Iterable
from dataclasses import dataclass
import zipfile
import shutil
from datetime import datetime, timezone
from typing import Callable

from xfbin import parse_xfbin, FormatError
from txtfmt import TxtError as PipelineError, atomic_write, escape_header_source, export_line, parse_txt, normalize_txt_file
from i18n import T


# ============================================================================
# messages
# ============================================================================

MESSAGES_VERSION = '0.3.0'
FORMAT = 'storm1-nucc-messages-ua-v1'
MAX_INPUT = 64 * 1024 * 1024
MAX_STRING = 256 * 1024
FAMILY_COUNTS = {'message': (93, 6640), 'message_btl': (4, 476), 'itemtext': (5, 391)}
# Set to the hashes of NON-TRANSLATABLE structure observed in all five language
# versions of each resource. Unknown revisions are rejected for rebuilding.
SCHEMA_HASHES = {
    'message': '993d566264456d82da24cd375ff2cf1f7eb6d180110e513e93f5ac81e6c0ecb7',
    'message_btl': '37dc68d08234adffa5428ec87feba4f86c528db40358c3fcd33dad269db57c61',
    'itemtext': '9721a4f39cda6276ca12a1c254e7f390a78863b2532556124b5effe0604e2adf',
}
TAG_RE = re.compile(r'<[^<>\r\n]{1,200}>')
# These 5 are literal, user-visible headings, NOT engine formatting commands.
# Keep the original protected tags byte-for-byte, but allow the exact Ukrainian
# text of the verified display headings. No arbitrary new <...> tokens allowed.
LITERAL_HEADING_TRANSLATIONS = {
    '<Prohibited Actions>': '<Заборонені дії>',
    '<Delivery Destinations>': '<Пункти доставки>',
    '<Destination>': '<Пункт призначення>',
    '<Flashback Mission Icons>': '<Позначки місій-спогадів>',
    '<Icon Types>': '<Типи позначок>',
}

MACRO_RE = re.compile(r'\\r\\n|\\n|\\r|\\t')

class MessageError(ValueError):
    pass

def family_prefix(family: str) -> str:
    try:
        return {'message':'MSG','message_btl':'BTL','itemtext':'ITM'}[family]
    except KeyError as exc:
        raise MessageError(T(f'Невідомий тип текстової таблиці: {family}', f'Unknown text table type: {family}')) from exc


def be32(data: bytes, at: int) -> int:
    if at < 0 or at + 4 > len(data):
        raise MessageError(T(f'Некоректне зміщення у структурі: 0x{at:X}', f'Invalid offset in the structure: 0x{at:X}'))
    return struct.unpack_from('>I', data, at)[0]


def _sbytes(data: bytes, at: int, length: int) -> bytes:
    if at < 0 or length < 0 or at + length > len(data):
        raise MessageError(T('Текст виходить за межі чанка.', 'The text runs past the end of the chunk.'))
    if length > MAX_STRING:
        raise MessageError(T('Неприпустимо довгий рядок.', 'The string is too long.'))
    raw = data[at:at + length]
    if b'\0' in raw:
        raise MessageError(T('Вбудований NUL у текстовому полі.', 'Embedded NUL in a text field.'))
    try:
        text = raw.decode('utf-8', 'strict')
    except UnicodeError as exc:
        raise MessageError(T(f'Некоректний UTF-8: {exc}', f'Invalid UTF-8: {exc}')) from exc
    if any(ord(ch) < 32 and ch not in '\n\r\t' for ch in text):
        raise MessageError(T('Неприпустимий керувальний символ у полі.', 'Invalid control character in the field.'))
    return raw


@dataclass(frozen=True)
class Entry:
    key: str
    chunk_number: int
    chunk_name: str
    record: int
    record_id: str
    slot: int
    source: str
    source_length: int
    offset: int

    @property
    def role(self) -> str:
        return 'Підпис' if self.slot == 1 else 'Основний текст'


@dataclass
class BinaryTable:
    chunk_number: int
    chunk_name: str
    fixed_head: bytes       # bytes 4:36
    fixed_control: bytes    # bytes 52:68, includes record count
    header_texts: tuple[bytes, bytes]
    records: list[list[bytes]]

    @property
    def count(self) -> int:
        return len(self.records)


@dataclass
class MessageDoc:
    path: Path
    blob: bytes
    sha256: str
    family: str
    index_prefix: bytes
    chunks: list
    binary_tables: dict[int, BinaryTable]
    entries: list[Entry]
    schema_sha256: str
    translations: dict[str, str] = field(default_factory=dict)

    @property
    def translated(self) -> int:
        return len(self.translations)


def read_binary_table(ck, blob: bytes) -> BinaryTable:
    b = blob[ck.data_offset:ck.end]
    if len(b) < 68:
        raise MessageError(T(f'{ck.name}: надто короткий бінарний чанк.', f'{ck.name}: the binary chunk is too short.'))
    if be32(b, 0) != len(b) - 4 or be32(b, 4) != 0x12FC5E4E:
        raise MessageError(T(f'{ck.name}: неправильний NUCC Binary заголовок.', f'{ck.name}: wrong NUCC Binary header.'))
    count = be32(b, 0x34)
    if count > 50_000 or 0x44 + 48 * count > len(b):
        raise MessageError(T(f'{ck.name}: некоректна кількість записів ({count}).', f'{ck.name}: invalid number of records ({count}).'))
    expected = 0x44 + 48 * count
    pairs = [(be32(b, pos), be32(b, pos + 4)) for pos in (0x24, 0x2c)]
    for i in range(count):
        for slot in range(6):
            pos = 0x44 + i * 48 + slot * 8
            pairs.append((be32(b, pos), be32(b, pos + 4)))
    strings = []
    for idx, (offset, length) in enumerate(pairs):
        if offset != expected - 4:
            raise MessageError(T(f'{ck.name}: помилкове зміщення поля {idx}: 0x{offset:X} != 0x{expected - 4:X}.', f'{ck.name}: wrong offset of field {idx}: 0x{offset:X} != 0x{expected - 4:X}.'))
        raw = _sbytes(b, expected, length)
        strings.append(raw)
        expected += length
    if expected != len(b):
        raise MessageError(T(f'{ck.name}: залишилися невраховані дані ({len(b)-expected} байтів).', f'{ck.name}: unaccounted data left over ({len(b)-expected} bytes).'))
    records = [strings[2 + 6 * i:2 + 6 * (i + 1)] for i in range(count)]
    return BinaryTable(ck.number, ck.name, b[4:0x24], b[0x34:0x44],
                       (strings[0], strings[1]), records)


def compute_schema(prefix: bytes, chunks: list, blob: bytes, tables: dict[int, BinaryTable]) -> str:
    h = hashlib.sha256()
    # Language-specific bytes in NUCC header (0x18..0x1B) are excluded.
    h.update(prefix[:0x18]); h.update(prefix[0x1c:])
    for ck in chunks:
        h.update(ck.type_name.encode() + b'\0' + ck.name.encode('utf-8') + b'\0')
        if ck.number in tables:
            t = tables[ck.number]
            h.update(t.fixed_head)
            h.update(t.fixed_control)
            for s in t.header_texts:
                h.update(struct.pack('>I', len(s)));h.update(s)
            for record in t.records:
                for role in (0,3,4,5):
                    s=record[role]
                    h.update(struct.pack('>I', len(s)));h.update(s)
        else:
            h.update(blob[ck.data_offset:ck.end])
    return h.hexdigest()


def load_messages(path: Path | str, *, allow_unverified: bool = False) -> MessageDoc:
    path=Path(path)
    if path.stat().st_size > MAX_INPUT:
        raise MessageError(T('Файл перевищує обмеження 64 МіБ.', 'The file exceeds the 64 MiB limit.'))
    try:
        container=parse_xfbin(path)
    except (FormatError,OSError) as exc:
        raise MessageError(str(exc)) from exc
    if container.nucc_version != 121:
        raise MessageError(T('Потрібен контейнер NUCC версії 121.', 'A NUCC container of version 121 is required.'))
    binary=[ck for ck in container.chunks if ck.type_name=='nuccChunkBinary']
    if len(binary)==FAMILY_COUNTS['message'][0]:family='message'
    elif len(binary)==FAMILY_COUNTS['message_btl'][0]:family='message_btl'
    else:raise MessageError(T('Це не перевірений формат message/message_btl першої STORM.', 'This is not a verified message/message_btl format of the first STORM.'))
    tables={ck.number:read_binary_table(ck,container.blob) for ck in binary}
    n=sum(t.count for t in tables.values())
    if n!=FAMILY_COUNTS[family][1]:
        raise MessageError(T(f'{family}: невідома кількість записів ({n}).', f'{family}: unknown number of records ({n}).'))
    entries=[]
    for ck in binary:
        t=tables[ck.number]
        absolute=ck.data_offset + 0x44 + t.count*48 + sum(map(len,t.header_texts))
        for i, r in enumerate(t.records):
            ident=r[0].decode('utf-8')
            for slot,s in enumerate(r):
                if slot in (1,2) and s:
                    key=f'{family_prefix(family)}{ck.number:03d}-{i:04d}-S{slot}'
                    entries.append(Entry(key,ck.number,ck.name,i,ident,slot,
                                         s.decode('utf-8'),len(s),absolute))
                absolute+=len(s)
    index_prefix=container.blob[:container.chunks[0].header_offset]
    schema=compute_schema(index_prefix,container.chunks,container.blob,tables)
    if not allow_unverified and schema!=SCHEMA_HASHES[family]:
        raise MessageError(T(f'Неперевірена схема {family}: {schema}. Збирання заблоковано.', f'Unverified schema {family}: {schema}. Build blocked.'))
    return MessageDoc(path,container.blob,container.sha256,family,index_prefix,
                      container.chunks,tables,entries,schema)


def normalized(value: str, source: str) -> str:
    if '\r\n' in source and '\n' not in source.replace('\r\n',''):
        value=value.replace('\r\n','\n').replace('\n','\r\n')
    return value


def validate_value(entry: Entry, value: str) -> bytes:
    if not isinstance(value,str):raise MessageError(T(f'{entry.key}: текст має бути рядком.', f'{entry.key}: the text must be a string.'))
    if not value:value=entry.source
    value=normalized(value,entry.source)
    if '\0' in value or any(ord(ch)<32 and ch not in '\t\r\n' for ch in value):
        raise MessageError(T(f'{entry.key}: неприпустимий керувальний символ.', f'{entry.key}: invalid control character.'))
    original_tags = TAG_RE.findall(entry.source)
    translated_tags = TAG_RE.findall(value)
    allowable_localized = [LITERAL_HEADING_TRANSLATIONS.get(t, t) for t in original_tags]
    if translated_tags != original_tags and translated_tags != allowable_localized:
        raise MessageError(T(f'{entry.key}: змінено ігрові теги або невідомий заголовок у <...>.', f'{entry.key}: game tags were changed, or there is an unknown heading in <...>.'))
    if MACRO_RE.findall(entry.source)!=MACRO_RE.findall(value):
        raise MessageError(T(f'{entry.key}: змінено буквальні \\n / \\r\\n тощо.', f'{entry.key}: literal \\n / \\r\\n etc. were changed.'))
    enc=value.encode('utf-8','strict')
    if len(enc)>MAX_STRING:raise MessageError(T(f'{entry.key}: текст завеликий.', f'{entry.key}: the text is too large.'))
    return enc


def make_export(doc: MessageDoc) -> dict:
    return {'format':FORMAT,'version':MESSAGES_VERSION,'family':doc.family,
            'source_file':doc.path.name,'source_sha256':doc.sha256,
            'schema_sha256':doc.schema_sha256,'encoding':'utf-8',
            'entries':[{'id':e.key,'chunk':e.chunk_name,'record_id':e.record_id,
                        'record':e.record,'slot':e.slot,'type':e.role,
                        'source':e.source,'translation':doc.translations.get(e.key,'')}
                       for e in doc.entries]}


def export_file(doc:MessageDoc,path:Path|str)->None:
    p=Path(path)
    if p.suffix.lower()=='.json':
        p.write_text(json.dumps(make_export(doc),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    elif p.suffix.lower()=='.tsv':
        with p.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f,delimiter='\t')
            w.writerow(('ID','Chunk','RecordID','Record','Slot','Type','Source','Translation','SourceSHA256','SchemaSHA256'))
            for e in doc.entries:
                w.writerow((e.key,e.chunk_name,e.record_id,e.record,e.slot,e.role,e.source,
                            doc.translations.get(e.key,''),doc.sha256,doc.schema_sha256))
    else:raise MessageError(T('Експорт лише до JSON або TSV.', 'Export is only possible to JSON or TSV.'))


def import_file(doc:MessageDoc,path:Path|str)->int:
    p=Path(path)
    if p.suffix.lower()=='.json':
        src=json.loads(p.read_text(encoding='utf-8-sig'))
        if (src.get('format')!=FORMAT or src.get('family')!=doc.family or
            src.get('source_sha256')!=doc.sha256 or src.get('schema_sha256')!=doc.schema_sha256):
            raise MessageError(T('Неправильний формат, родина або SHA-256 у JSON.', 'Wrong format, family or SHA-256 in the JSON.'))
        items=src.get('entries')
    elif p.suffix.lower()=='.tsv':
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            items=list(csv.DictReader(f,delimiter='\t'))
    else:raise MessageError(T('Підтримуються JSON/TSV.', 'Only JSON/TSV are supported.'))
    if not isinstance(items,list) or len(items)!=len(doc.entries):
        raise MessageError(T(f'Не збігається кількість полів (очікується {len(doc.entries)}).', f'The number of fields does not match (expected {len(doc.entries)}).'))
    temp={}
    for e,row in zip(doc.entries,items):
        if not isinstance(row,dict):raise MessageError(T(f'{e.key}: неправильний запис.', f'{e.key}: invalid record.'))
        get=lambda a,b:row.get(a,row.get(b))
        if (get('id','ID')!=e.key or get('source','Source')!=e.source or
            get('chunk','Chunk')!=e.chunk_name or get('record_id','RecordID')!=e.record_id or
            str(get('slot','Slot'))!=str(e.slot) or str(get('record','Record'))!=str(e.record)):
            raise MessageError(T(f'{e.key}: змінено ідентифікатор, джерело чи порядок.', f'{e.key}: the identifier, source or order was changed.'))
        if p.suffix.lower()=='.tsv' and (row.get('SourceSHA256')!=doc.sha256 or
                                          row.get('SchemaSHA256')!=doc.schema_sha256):
            raise MessageError(T(f'{e.key}: не збігаються контрольні SHA-256.', f'{e.key}: the SHA-256 checksums do not match.'))
        val=get('translation','Translation')
        if val is None: val=''
        validate_value(e,val)
        if val:temp[e.key]=val
    doc.translations=temp
    return len(temp)


def assemble_table(t:BinaryTable,translations:dict[str,str],family:str)->bytes:
    ordered=[]
    ordered.extend(t.header_texts)
    for rec_i,record in enumerate(t.records):
        for slot,s in enumerate(record):
            if slot in (1,2) and s:
                key=f'{family_prefix(family)}{t.chunk_number:03d}-{rec_i:04d}-S{slot}'
                if key in translations:
                    entry=Entry(key,t.chunk_number,t.chunk_name,rec_i,record[0].decode('utf-8'),slot,s.decode('utf-8'),len(s),0)
                    s=validate_value(entry,translations[key])
            ordered.append(s)
    cursor=0x44 + t.count*48
    pairs=bytearray()
    pool=bytearray()
    for s in ordered:
        pairs.extend(struct.pack('>II',cursor-4,len(s)))
        pool.extend(s)
        cursor+=len(s)
    out=bytearray(b'\0'*4+t.fixed_head+pairs[:16]+t.fixed_control+pairs[16:]+pool)
    if len(out)!=cursor:
        raise MessageError(T(f'{t.chunk_name}: внутрішня помилка довжин.', f'{t.chunk_name}: internal length error.'))
    struct.pack_into('>I',out,0,len(out)-4)
    return bytes(out)


def build_messages(doc:MessageDoc)->bytes:
    if hashlib.sha256(doc.path.read_bytes()).hexdigest()!=doc.sha256:
        raise MessageError(T('Оригінал змінений після відкриття: збирання заборонено.', 'The original was changed after it was opened: build is not allowed.'))
    validkeys={e.key for e in doc.entries}
    if set(doc.translations)-validkeys:
        raise MessageError(T('Є невідомі ідентифікатори перекладу.', 'There are unknown translation identifiers.'))
    out=bytearray(doc.index_prefix)
    for ck in doc.chunks:
        old_header=doc.blob[ck.header_offset:ck.data_offset]
        if ck.number in doc.binary_tables:
            body=assemble_table(doc.binary_tables[ck.number],doc.translations,doc.family)
            header=bytearray(old_header)
            struct.pack_into('>I',header,0,len(body))
            out.extend(header);out.extend(body)
        else:
            out.extend(doc.blob[ck.header_offset:ck.end])
    result=bytes(out)
    # Reparse every chunk from the generated container. Check all textual roles
    # and all preserved internal metadata; do not trust offset arithmetic alone.
    pos=len(doc.index_prefix)
    for ck in doc.chunks:
        size=be32(result,pos)
        if result[pos+4:pos+12]!=doc.blob[ck.header_offset+4:ck.data_offset]:
            raise MessageError(T(f'Пошкоджено заголовок чанка {ck.number}.', f'The header of chunk {ck.number} is damaged.'))
        start=pos+12
        end=start+size
        if end>len(result):raise MessageError(T('Згенерований чанк виходить за межі файлу.', 'The generated chunk runs past the end of the file.'))
        if ck.number in doc.binary_tables:
            class C: pass
            x=C();x.data_offset=start;x.end=end;x.name=ck.name;x.number=ck.number
            rebuilt=read_binary_table(x,result)
            old=doc.binary_tables[ck.number]
            if (rebuilt.fixed_head!=old.fixed_head or rebuilt.fixed_control!=old.fixed_control or
                rebuilt.header_texts!=old.header_texts or len(rebuilt.records)!=len(old.records)):
                raise MessageError(T(f'{ck.name}: змінилися службові байти.', f'{ck.name}: service bytes changed.'))
            for i,(before,after) in enumerate(zip(old.records,rebuilt.records)):
                for role in range(6):
                    if role not in (1,2) or not before[role]:
                        if before[role]!=after[role]:
                            raise MessageError(T(f'{ck.name}/{i}: змінено службові дані.', f'{ck.name}/{i}: service data was changed.'))
                    else:
                        key=f'{family_prefix(doc.family)}{ck.number:03d}-{i:04d}-S{role}'
                        item=Entry(key,ck.number,ck.name,i,before[0].decode('utf-8'),role,before[role].decode('utf-8'),len(before[role]),0)
                        if after[role]!=validate_value(item,doc.translations.get(key,'')):
                            raise MessageError(T(f'{key}: помилка перевірки перекладу.', f'{key}: translation check failed.'))
        else:
            if result[start:end]!=doc.blob[ck.data_offset:ck.end]:
                raise MessageError(T(f'Змінено нетекстовий чанк {ck.number}.', f'Non-text chunk {ck.number} was changed.'))
        pos=end
    if pos!=len(result):raise MessageError(T('Після останнього чанка є зайві байти.', 'There are extra bytes after the last chunk.'))
    if not doc.translations and result!=doc.blob:
        raise MessageError(T('Нульовий переклад має давати побайтово тотожний файл.', 'An empty translation must produce a byte-for-byte identical file.'))
    return result


def save_rebuilt(doc:MessageDoc,path:Path|str)->tuple[int,int,str]:
    p=Path(path)
    if p.resolve()==doc.path.resolve():raise MessageError(T('Перезапис оригіналу заборонений.', 'Overwriting the original is not allowed.'))
    out=build_messages(doc)
    p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.storm_ua_',suffix='.tmp',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(out);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return len(doc.translations),len(out),hashlib.sha256(out).hexdigest()


def load_itemtext(path: Path | str) -> MessageDoc:
    """Verified against five uploaded language variants of STORM 1 itemtext.
    Structural hash pins non-translatable fields; never accept unknown revisions.
    """
    path=Path(path)
    if path.stat().st_size>MAX_INPUT:
        raise MessageError(T('itemtext перевищує обмеження 64 МіБ.', 'itemtext exceeds the 64 MiB limit.'))
    try:
        container=parse_xfbin(path)
    except (FormatError,OSError) as exc:
        raise MessageError(str(exc)) from exc
    if container.nucc_version!=121:
        raise MessageError(T('itemtext має іншу версію NUCC.', 'itemtext has a different NUCC version.'))
    binary=[ck for ck in container.chunks if ck.type_name=='nuccChunkBinary']
    if len(binary)!=FAMILY_COUNTS['itemtext'][0]:
        raise MessageError(T('itemtext: незвична кількість бінарних чанків.', 'itemtext: unusual number of binary chunks.'))
    tables={ck.number:read_binary_table(ck,container.blob) for ck in binary}
    if [t.count for t in tables.values()] != [49,21,158,161,2]:
        raise MessageError(T('itemtext: невідома структура 5 текстових таблиць.', 'itemtext: unknown structure of the 5 text tables.'))
    entries=[]
    for ck in binary:
        t=tables[ck.number]
        absolute=ck.data_offset+0x44+t.count*48+sum(map(len,t.header_texts))
        for i,record in enumerate(t.records):
            ident=record[0].decode('utf-8')
            for slot,s in enumerate(record):
                if slot in (1,2) and s:
                    key=f'ITM{ck.number:03d}-{i:04d}-S{slot}'
                    entries.append(Entry(key,ck.number,ck.name,i,ident,slot,
                                         s.decode('utf-8'),len(s),absolute))
                absolute+=len(s)
    if not entries:
        raise MessageError(T('itemtext: у слотах S1/S2 немає текстів.', 'itemtext: there are no texts in slots S1/S2.'))
    index_prefix=container.blob[:container.chunks[0].header_offset]
    schema=compute_schema(index_prefix,container.chunks,container.blob,tables)
    if schema!=SCHEMA_HASHES['itemtext'] or len(entries)!=749:
        raise MessageError(T(f'itemtext: неперевірена структура: {schema}, {len(entries)} полів.', f'itemtext: unverified structure: {schema}, {len(entries)} fields.'))
    doc=MessageDoc(path,container.blob,container.sha256,'itemtext',index_prefix,
                   container.chunks,tables,entries,schema)
    if build_messages(doc)!=doc.blob:
        raise MessageError(T('itemtext: побайтовий round-trip не пройдено.', 'itemtext: the byte-for-byte round-trip check failed.'))
    return doc


# ============================================================================
# missioninfo
# ============================================================================

MISSION_VERSION = '0.2.0'
SCHEMA_SHA256 = 'b3e955d29b8ff6a88bd0f197eec7dc515a4fdc9aaa295bc1a0973a8b784427fb'
EXPECTED_FIELDS = 915
EXPECTED_MISSIONS = 111
EXPECTED_GAP_BYTES = 12456
MI_MAX_INPUT = 32 * 1024 * 1024
MI_MAX_TEXT_BYTES = 65535
EXPORT_FORMAT = 'storm1-missioninfo-ua-v2'
EVENT_RE = re.compile(r'ev[A-Za-z][A-Za-z0-9_]*\Z')
# 916 structural gap widths (bytes), same in all five verified language files.
# Only numerical metadata is stored; no game text or assets are bundled.
GAP_LENGTHS = zlib.decompress(base64.b85decode(
    'c-j+SU|``W0TCPw3=9z{j2sXlzz~8&!Q}|?iDA%74|xm<e27yiIj%2(6D&Ch5{MZfhJjfTq=;cR452&*i$*jtbVngf0J1}fbW99EpK;)J0jeY(e<Ac`;M0djFyJSFL;|{dkcAMe6f9!cWDx-r0RWeiFsT'
))
assert len(GAP_LENGTHS) == EXPECTED_FIELDS + 1
assert sum(GAP_LENGTHS) == EXPECTED_GAP_BYTES

class MissionInfoError(ValueError):
    pass


def _mi_be32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise MissionInfoError(T('Неприпустиме зміщення 32-бітного числа.', 'Invalid offset of a 32-bit number.'))
    return struct.unpack_from('>I', data, offset)[0]


@dataclass(frozen=True)
class Field:
    index: int
    offset: int  # within nuccChunkBinary payload, start of 32-bit length
    byte_length: int
    source: str
    is_id: bool

    @property
    def key(self) -> str:
        return f'MI{self.index:04d}'


@dataclass
class MissionInfo:
    path: Path
    raw: bytes
    sha256: str
    binary_offset: int
    binary_header_offset: int
    original_binary_length: int
    head: bytes
    tail: bytes
    fields: list[Field]
    gaps: list[bytes]
    schema_sha256: str
    translations: dict[int, str] = field(default_factory=dict)

    @property
    def translated(self) -> int:
        return sum(bool(x) for i, x in self.translations.items() if not self.fields[i].is_id)


def _find_fields(payload: bytes) -> tuple[list[Field], list[bytes]]:
    """Strict schema-guided read: no guessing where a field begins.

    Verified gap *lengths* provide 915 deterministic positions. Every UTF-8
    string is preceded by its exact byte length (big-endian uint32).
    This also handles translations like '!' that contain no letters.
    """
    entries: list[Field] = []
    gaps: list[bytes] = []
    offset = 0
    for i, gap_length in enumerate(GAP_LENGTHS[:-1]):
        if offset + gap_length + 4 > len(payload):
            raise MissionInfoError(T(f'Схема обірвана перед полем MI{i:04d}.', f'The schema is cut off before field MI{i:04d}.'))
        gaps.append(payload[offset:offset + gap_length])
        offset += gap_length
        n = _mi_be32(payload, offset)
        if n > MI_MAX_TEXT_BYTES or n > len(payload) - offset - 4:
            raise MissionInfoError(T(f'MI{i:04d}: некоректна довжина UTF-8-поля.', f'MI{i:04d}: invalid length of the UTF-8 field.'))
        raw = payload[offset + 4:offset + 4 + n]
        if b'\0' in raw:
            raise MissionInfoError(T(f'MI{i:04d}: неочікуваний NUL усередині рядка.', f'MI{i:04d}: unexpected NUL inside the string.'))
        try:
            string = raw.decode('utf-8', 'strict')
        except UnicodeDecodeError as exc:
            raise MissionInfoError(T(f'MI{i:04d}: некоректний UTF-8: {exc}', f'MI{i:04d}: invalid UTF-8: {exc}')) from exc
        if any(ord(c) < 32 and c not in '\r\n\t' for c in string):
            raise MissionInfoError(T(f'MI{i:04d}: недозволений керувальний символ.', f'MI{i:04d}: forbidden control character.'))
        entries.append(Field(i, offset, n, string, bool(EVENT_RE.fullmatch(string))))
        offset += 4 + n
    if len(payload) - offset != GAP_LENGTHS[-1]:
        raise MissionInfoError(T('Хвостова секція MissionInfo має неправильний розмір.', 'The tail section of MissionInfo has the wrong size.'))
    gaps.append(payload[offset:])
    return entries, gaps


def _schema_hash(gaps: list[bytes]) -> str:
    if not gaps or len(gaps[0]) < 4:
        raise MissionInfoError(T('Немає заголовка даних місій.', 'The mission data header is missing.'))
    first = b'\0\0\0\0' + gaps[0][4:]
    return hashlib.sha256(first + b''.join(gaps[1:])).hexdigest()


def load_missioninfo(path: str | Path) -> MissionInfo:
    path = Path(path)
    if path.stat().st_size > MI_MAX_INPUT:
        raise MissionInfoError(T('Файл перевищує допустимий розмір 32 МіБ.', 'The file exceeds the allowed size of 32 MiB.'))
    raw = path.read_bytes()
    if len(raw) < 292 or raw[:4] != b'NUCC':
        raise MissionInfoError(T('Це не контейнер NUCC/XFBIN.', 'This is not a NUCC/XFBIN container.'))
    if _mi_be32(raw, 4) != 121:
        raise MissionInfoError(T('Непідтримувана версія NUCC (потрібна 121).', 'Unsupported NUCC version (121 is required).'))
    # The 5 verified language files have an identical container index. Its
    # canonical binary chunk begins at absolute offset 272 (0x110).
    # Require the observed layout rather than trusting guessed chunk metadata.
    binary_offset = 272
    binary_header_offset = 260
    if (raw[68:81] != b'nuccChunkNull' or
            raw[82:97] != b'nuccChunkBinary'):
        # This is a diagnostic convenience, not an alternative parser.
        raise MissionInfoError(T('Невідомий індекс XFBIN; потрібна перевірка вручну.', 'Unknown XFBIN index; it needs to be checked by hand.'))
    binary_size = _mi_be32(raw, binary_header_offset)
    if binary_size < 20 or binary_offset + binary_size + 20 != len(raw):
        raise MissionInfoError(T('Розмір nuccChunkBinary або кінцевого чанка не відповідає схемі.', 'The size of nuccChunkBinary or of the final chunk does not match the schema.'))
    if raw[binary_offset + binary_size:] != bytes.fromhex('0000000800000002007918000000000400000000'):
        # Page chunk local index = 0, checked against these five variants.
        raise MissionInfoError(T('Непередбачений заголовок кінцевої сторінки.', 'Unexpected header of the final page.'))
    body = raw[binary_offset:binary_offset + binary_size]
    if _mi_be32(body, 0) != len(body) - 4:
        raise MissionInfoError(T('Внутрішній розмір MissionInfo не збігається.', 'The internal size of MissionInfo does not match.'))
    if _mi_be32(body, 4) != EXPECTED_MISSIONS:
        raise MissionInfoError(T('Кількість місій не дорівнює 111.', 'The number of missions is not 111.'))
    fields, gaps = _find_fields(body)
    if len(fields) != EXPECTED_FIELDS:
        raise MissionInfoError(T(f'Знайдено {len(fields)} текстових полів замість 915. Збирання заблоковано.', f'Found {len(fields)} text fields instead of 915. Build blocked.'))
    if sum(x.is_id for x in fields) != EXPECTED_MISSIONS:
        raise MissionInfoError(T('Не вдалося підтвердити 111 службових ID місій.', 'Could not confirm the 111 service mission IDs.'))
    if sum(map(len, gaps)) != EXPECTED_GAP_BYTES:
        raise MissionInfoError(T('Не збігається службовий обсяг таблиць MissionInfo.', 'The size of the service data in the MissionInfo tables does not match.'))
    schema = _schema_hash(gaps)
    if schema != SCHEMA_SHA256:
        raise MissionInfoError(T(f'Невідома схема MissionInfo: {schema}. Збирання заблоковано.', f'Unknown MissionInfo schema: {schema}. Build blocked.'))
    return MissionInfo(path, raw, hashlib.sha256(raw).hexdigest(), binary_offset,
                       binary_header_offset, binary_size, raw[:binary_offset],
                       raw[binary_offset + binary_size:], fields, gaps, schema)


def _normalize_newlines(translation: str, source: str) -> str:
    # Preserve native line-ending convention if a text editor converted CRLF to LF.
    if '\r\n' in source and '\n' not in source.replace('\r\n', '') and '\r' not in source.replace('\r\n', ''):
        return translation.replace('\r\n', '\n').replace('\n', '\r\n')
    return translation


def validated_text(item: Field, translation: str) -> bytes:
    if item.is_id:
        if translation and translation != item.source:
            raise MissionInfoError(T(f'{item.key}: службовий ідентифікатор місії змінювати не можна.', f'{item.key}: the service mission identifier must not be changed.'))
        translation = item.source
    elif not translation:
        translation = item.source
    else:
        translation = _normalize_newlines(translation, item.source)
    if '\0' in translation or any(ord(c) < 32 and c not in '\r\n\t' for c in translation):
        raise MissionInfoError(T(f'{item.key}: у тексті є неприпустимі керувальні символи.', f'{item.key}: the text contains invalid control characters.'))
    try:
        encoded = translation.encode('utf-8', 'strict')
    except UnicodeError as exc:
        raise MissionInfoError(T(f'{item.key}: неможливо закодувати UTF-8: {exc}', f'{item.key}: cannot encode as UTF-8: {exc}')) from exc
    if len(encoded) > MI_MAX_TEXT_BYTES:
        raise MissionInfoError(T(f'{item.key}: понад 65535 байтів у рядку.', f'{item.key}: more than 65535 bytes in the string.'))
    return encoded


def make_json(doc: MissionInfo) -> dict:
    return {
        'format': EXPORT_FORMAT,
        'tool_version': MISSION_VERSION,
        'source_file': doc.path.name,
        'source_sha256': doc.sha256,
        'schema_sha256': doc.schema_sha256,
        'encoding': 'utf-8',
        'entries': [
            {'id': e.key, 'index': e.index, 'type': 'mission_id' if e.is_id else 'text',
             'offset_hex': f'0x{e.offset:X}', 'source_bytes': e.byte_length,
             'source': e.source, 'translation': doc.translations.get(e.index, '')}
            for e in doc.fields
        ],
    }


def export_json(doc: MissionInfo, filename: str | Path) -> None:
    Path(filename).write_text(json.dumps(make_json(doc), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def export_tsv(doc: MissionInfo, filename: str | Path) -> None:
    with open(filename, 'w', encoding='utf-8-sig', newline='') as fp:
        writer = csv.writer(fp, delimiter='\t', quoting=csv.QUOTE_MINIMAL)
        writer.writerow(('ID', 'Index', 'Type', 'Source', 'Translation', 'SourceBytes', 'SourceSHA256'))
        for e in doc.fields:
            writer.writerow((e.key, e.index, 'mission_id' if e.is_id else 'text', e.source,
                            doc.translations.get(e.index, ''), e.byte_length, doc.sha256))


def import_mission_file(doc: MissionInfo, filename: str | Path) -> int:
    filename = Path(filename)
    data: list[dict] = []
    if filename.suffix.lower() == '.json':
        payload = json.loads(filename.read_text(encoding='utf-8-sig'))
        if payload.get('format') != EXPORT_FORMAT or payload.get('schema_sha256') != doc.schema_sha256:
            raise MissionInfoError(T('Невідомий формат JSON або невідповідна структура MissionInfo.', 'Unknown JSON format, or the MissionInfo structure does not match.'))
        if payload.get('source_sha256') != doc.sha256:
            raise MissionInfoError(T('SHA-256 оригіналу не збігається: імпорт заборонено.', 'The SHA-256 of the original does not match: import is not allowed.'))
        data = payload.get('entries')
    elif filename.suffix.lower() == '.tsv':
        with open(filename, 'r', encoding='utf-8-sig', newline='') as fp:
            data = list(csv.DictReader(fp, delimiter='\t'))
    else:
        raise MissionInfoError(T('Імпорт можливий лише з JSON або TSV.', 'Import is only possible from JSON or TSV.'))
    if not isinstance(data, list) or len(data) != EXPECTED_FIELDS:
        raise MissionInfoError(T('У файлі імпорту має бути рівно 915 полів.', 'The import file must contain exactly 915 fields.'))
    new_values = {}
    for i, record in enumerate(data):
        if not isinstance(record, dict):
            raise MissionInfoError(T(f'Поле {i} має неправильну структуру.', f'Field {i} has the wrong structure.'))
        entry = doc.fields[i]
        ident = record.get('id', record.get('ID'))
        index = record.get('index', record.get('Index'))
        original = record.get('source', record.get('Source'))
        original_hash = record.get('SourceSHA256', doc.sha256)
        if ident != entry.key or str(index) != str(i) or original != entry.source or original_hash != doc.sha256:
            raise MissionInfoError(T(f'Не збігається порядок/джерело/хеш {entry.key}.', f'The order/source/hash of {entry.key} does not match.'))
        value = record.get('translation', record.get('Translation', ''))
        if not isinstance(value, str):
            raise MissionInfoError(T(f'{entry.key}: translation має бути рядком.', f'{entry.key}: translation must be a string.'))
        if entry.is_id and value not in ('', entry.source):
            raise MissionInfoError(T(f'{entry.key}: не можна перекладати службовий ID.', f'{entry.key}: a service ID must not be translated.'))
        validated_text(entry, value)
        if value and not entry.is_id:
            new_values[i] = value
    # All-or-nothing import: mutate doc only after validating the whole file.
    doc.translations = new_values
    return len(new_values)


def build_mission(doc: MissionInfo) -> bytes:
    if hashlib.sha256(doc.path.read_bytes()).hexdigest() != doc.sha256:
        raise MissionInfoError(T('Оригінальний файл змінився після відкриття.', 'The original file changed after it was opened.'))
    parts = []
    for i, e in enumerate(doc.fields):
        parts.append(doc.gaps[i])
        text = validated_text(e, doc.translations.get(i, ''))
        parts.append(struct.pack('>I', len(text)))
        parts.append(text)
    parts.append(doc.gaps[-1])
    body = bytearray(b''.join(parts))
    struct.pack_into('>I', body, 0, len(body) - 4)
    out = bytearray(doc.head + bytes(body) + doc.tail)
    struct.pack_into('>I', out, doc.binary_header_offset, len(body))
    # Verify the preserved binary structure and every string against the plan,
    # including translations that contain punctuation and no letters.
    expected = bytes(out)
    assert out[:4] == b'NUCC'
    assert _mi_be32(out, doc.binary_header_offset) == len(body)
    assert _mi_be32(out, doc.binary_offset) == len(body) - 4
    check_body = memoryview(out)[doc.binary_offset:doc.binary_offset + len(body)]
    p = 0
    for i, field in enumerate(doc.fields):
        gap = doc.gaps[i]
        if i == 0:
            if bytes(check_body[4:len(gap)]) != gap[4:]:
                raise MissionInfoError(T('Пошкоджено заголовок MissionInfo.', 'The MissionInfo header is damaged.'))
        elif bytes(check_body[p:p + len(gap)]) != gap:
            raise MissionInfoError(T(f'{field.key}: пошкоджено службові байти.', f'{field.key}: service bytes are damaged.'))
        p += len(gap)
        n = _mi_be32(out, doc.binary_offset + p)
        payload = validated_text(field, doc.translations.get(i, ''))
        if n != len(payload) or bytes(check_body[p + 4:p + 4 + n]) != payload:
            raise MissionInfoError(T(f'{field.key}: помилка зворотного збирання.', f'{field.key}: round-trip build error.'))
        p += 4 + n
    if bytes(check_body[p:]) != doc.gaps[-1]:
        raise MissionInfoError(T('Не збігається кінець таблиці MissionInfo.', 'The end of the MissionInfo table does not match.'))
    if not doc.translations and expected != doc.raw:
        raise MissionInfoError(T('Round-trip без перекладів мав бути побайтово ідентичний.', 'A round-trip without translations should have been byte-for-byte identical.'))
    return expected


def save_mission_rebuilt(doc: MissionInfo, filename: str | Path) -> tuple[int, int, str]:
    filename = Path(filename)
    if filename.resolve() == doc.path.resolve():
        raise MissionInfoError(T('Заборонено перезаписувати оригінальний файл.', 'Overwriting the original file is not allowed.'))
    content = build_mission(doc)
    filename.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.mi_ua_', suffix='.xfbin', dir=filename.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic replacement of the designated OUTPUT, never the source.
        os.replace(temporary, filename)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return doc.translated, len(content), hashlib.sha256(content).hexdigest()


def export_comparison(docs: Iterable[MissionInfo], filename: str | Path) -> None:
    docs = list(docs)
    if not docs:
        raise MissionInfoError(T('Не вибрано мовних файлів для порівняння.', 'No language files were selected for comparison.'))
    if len({d.schema_sha256 for d in docs}) != 1:
        raise MissionInfoError(T('Файли мають різні схеми MissionInfo.', 'The files have different MissionInfo schemas.'))
    with open(filename, 'w', encoding='utf-8-sig', newline='') as fp:
        w = csv.writer(fp, delimiter='\t')
        w.writerow(['ID', 'Type', *(doc.path.name for doc in docs)])
        for i in range(EXPECTED_FIELDS):
            w.writerow([docs[0].fields[i].key,
                        'mission_id' if docs[0].fields[i].is_id else 'text',
                        *(doc.fields[i].source for doc in docs)])


# ============================================================================
# telop
# ============================================================================

TELOP_VERSION = '1.0.0'
TELOP_FILES = ('movieTelopBin.xfbin','dioramaTelopBin.xfbin',
             'iaTelopBin.xfbin','spSkillTelopBin.xfbin')
TELOP_MAX_TEXT_BYTES = 128 * 1024
TELOP_TAG_RE = re.compile(r'<[^<>\r\n]{1,200}>')
ESC_RE = re.compile(r'\\r\\n|\\n|\\r')


class TelopError(ValueError):
    pass


def _u32(b: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(b):
        raise TelopError(T('Неповний BE32 в telop.', 'Incomplete BE32 in telop.'))
    return struct.unpack_from('>I', b, offset)[0]


@dataclass(frozen=True)
class TelopEntry:
    key: str
    source: str
    chunk_number: int
    chunk_name: str
    slot: int
    record: int
    start: int
    end: int
    mode: int
    offset: int
    raw: bytes


@dataclass
class TelopDoc:
    path: Path
    blob: bytes
    sha256: str
    schema_sha256: str
    entries: list[TelopEntry]
    chunks: list
    translations: dict[str, str] = field(default_factory=dict)


def _parse_chunk(chunk, data: bytes):
    if len(data) < 12 or _u32(data, 0) != len(data) - 4:
        raise TelopError(T(f'{chunk.name}: неправильний розмір вмісту.', f'{chunk.name}: wrong content size.'))
    name_len = _u32(data, 4)
    if not (1 <= name_len <= 256) or name_len + 12 > len(data):
        raise TelopError(T(f'{chunk.name}: неправильна довжина назви.', f'{chunk.name}: wrong name length.'))
    try:
        name = data[8:8 + name_len].decode('ascii', 'strict')
    except UnicodeError as exc:
        raise TelopError(T(f'{chunk.name}: назва має недопустиме кодування.', f'{chunk.name}: the name has an invalid encoding.')) from exc
    if name != chunk.name:
        raise TelopError(T(f'{chunk.name}: назва payload відрізняється від індексу.', f'{chunk.name}: the payload name differs from the index.'))
    pos = 8 + name_len
    if _u32(data, pos) == 0xffffffff:
        if pos + 4 != len(data):
            raise TelopError(T(f'{name}: незнайомий вміст після 0xffffffff.', f'{name}: unknown content after 0xffffffff.'))
        return name, (), True
    result = []
    while pos < len(data):
        if len(data) - pos < 16:
            raise TelopError(T(f'{name}: обірваний заголовок репліки.', f'{name}: the subtitle line header is cut off.'))
        start, end, mode, size = struct.unpack_from('>4I', data, pos)
        pos += 16
        if mode not in (0, 1):
            raise TelopError(T(f'{name}: невідомий режим репліки: {mode}.', f'{name}: unknown subtitle line mode: {mode}.'))
        if size > TELOP_MAX_TEXT_BYTES or size > len(data) - pos or size == 0:
            raise TelopError(T(f'{name}: некоректна довжина тексту: {size}.', f'{name}: invalid text length: {size}.'))
        raw = data[pos:pos + size]
        try:
            value = raw.decode('utf-8', 'strict')
        except UnicodeError as exc:
            raise TelopError(T(f'{name}: невалідний UTF-8.', f'{name}: invalid UTF-8.')) from exc
        if b'\x00' in raw or any(ord(c) < 32 and c not in '\r\n\t' for c in value):
            raise TelopError(T(f'{name}: службові керівні символи у тексті.', f'{name}: control characters in the text.'))
        result.append((start, end, mode, pos, raw, value))
        pos += size
    if pos != len(data):
        raise TelopError(T(f'{name}: неправильна довжина чанка.', f'{name}: wrong chunk length.'))
    return name, tuple(result), False


def load_telop(path: Path | str) -> TelopDoc:
    path = Path(path)
    if path.name not in TELOP_FILES:
        raise TelopError(T(f'Не підтримується telop-ресурс {path.name}.', f'The telop resource {path.name} is not supported.'))
    xf = parse_xfbin(path)
    if xf.nucc_version != 121:
        raise TelopError(T(f'{path.name}: неперевірена NUCC-версія {xf.nucc_version}.', f'{path.name}: unverified NUCC version {xf.nucc_version}.'))
    if xf.warnings:
        raise TelopError(T(f'{path.name}: попередження щодо структури: {xf.warnings}', f'{path.name}: structure warnings: {xf.warnings}'))
    entries = []
    fingerprint = []
    binary_count = 0
    for chunk in xf.chunks:
        if chunk.type_name == 'nuccChunkBinary':
            binary_count += 1
            body = xf.blob[chunk.data_offset:chunk.end]
            name, fields, empty = _parse_chunk(chunk, body)
            fingerprint.append([chunk.number, name, chunk.version, empty,
                                [[a,b,m] for a,b,m,*_ in fields]])
            for index, (start, end, mode, offset, raw, value) in enumerate(fields):
                key = f'TEL{chunk.number:04d}-{index:04d}'
                entries.append(TelopEntry(key, value, chunk.number, name, index+1,
                                          index, start, end, mode,
                                          chunk.data_offset + offset, raw))
        elif chunk.type_name not in ('nuccChunkNull', 'nuccChunkPage'):
            raise TelopError(T(f'Неочікуваний тип чанка {chunk.type_name}.', f'Unexpected chunk type {chunk.type_name}.'))
    if binary_count == 0 or not entries:
        raise TelopError(T(f'{path.name}: не знайдено підтримуваних текстових полів.', f'{path.name}: no supported text fields were found.'))
    schema = hashlib.sha256(json.dumps(fingerprint,ensure_ascii=False,
                                       separators=(',', ':')).encode('utf-8')).hexdigest()
    return TelopDoc(path, xf.blob, xf.sha256, schema, entries, xf.chunks)


def validate_telop_value(entry: TelopEntry, text: str):
    if not isinstance(text, str) or not text.strip():
        raise TelopError(T(f'{entry.key}: переклад порожній.', f'{entry.key}: the translation is empty.'))
    if '\x00' in text or '\ufeff' in text or any(ord(c) < 32 and c not in '\r\n\t' for c in text):
        raise TelopError(T(f'{entry.key}: заборонений керівний символ.', f'{entry.key}: forbidden control character.'))
    b = text.encode('utf-8', 'strict')
    if len(b) > TELOP_MAX_TEXT_BYTES:
        raise TelopError(T(f'{entry.key}: текст довший за {TELOP_MAX_TEXT_BYTES} байтів.', f'{entry.key}: the text is longer than {TELOP_MAX_TEXT_BYTES} bytes.'))
    source = entry.source
    if (source.count('\r\n'), source.count('\n'), source.count('\r')) != (text.count('\r\n'), text.count('\n'), text.count('\r')):
        raise TelopError(T(f'{entry.key}: змінено кількість або стиль фізичних переносів рядків.', f'{entry.key}: the number or style of physical line breaks was changed.'))
    if TELOP_TAG_RE.findall(source) != TELOP_TAG_RE.findall(text):
        raise TelopError(T(f'{entry.key}: змінено форматувальні теги.', f'{entry.key}: formatting tags were changed.'))
    if ESC_RE.findall(source) != ESC_RE.findall(text):
        raise TelopError(T(f'{entry.key}: змінено буквальні \\n / \\r\\n.', f'{entry.key}: literal \\n / \\r\\n were changed.'))


def build_telop(doc: TelopDoc) -> bytes:
    known = {e.key: e for e in doc.entries}
    extra = set(doc.translations) - set(known)
    if extra:
        raise TelopError(T(f'Переклад містить сторонні ключі: {sorted(extra)[:3]}', f'The translation contains unknown keys: {sorted(extra)[:3]}'))
    for key, value in doc.translations.items():
        validate_telop_value(known[key], value)
    by_chunk = {}
    for e in doc.entries:
        by_chunk.setdefault(e.chunk_number, []).append(e)
    start = doc.chunks[0].header_offset
    result = bytearray(doc.blob[:start])
    for c in doc.chunks:
        original_header = doc.blob[c.header_offset:c.data_offset]
        original_body = doc.blob[c.data_offset:c.end]
        if c.type_name != 'nuccChunkBinary' or not any(
                e.key in doc.translations and doc.translations[e.key] != e.source
                for e in by_chunk.get(c.number, [])):
            result += original_header + original_body
            continue
        # Rebuild only the length-prefixed English text. Timing and mode are opaque.
        name_len = _u32(original_body, 4)
        pos = 8 + name_len
        out = bytearray(original_body[4:pos])
        for e in by_chunk[c.number]:
            start_time, end_time, mode, original_size = struct.unpack_from('>4I',original_body,pos)
            pos += 16
            original_raw = original_body[pos:pos + original_size]
            if original_raw != e.raw or (start_time, end_time, mode) != (e.start, e.end, e.mode):
                raise TelopError(T(f'{e.key}: структура змінилася між аналізом і записом.', f'{e.key}: the structure changed between analysis and writing.'))
            pos += original_size
            updated = doc.translations.get(e.key, e.source).encode('utf-8')
            out.extend(struct.pack('>4I',start_time,end_time,mode,len(updated)))
            out.extend(updated)
        if pos != len(original_body):
            raise TelopError(T(f'{c.name}: непередбачені байти після текстів.', f'{c.name}: unexpected bytes after the texts.'))
        body = struct.pack('>I',len(out)) + out
        result += struct.pack('>I',len(body)) + original_header[4:] + body
    built = bytes(result)
    if not doc.translations or all(doc.translations[k] == known[k].source for k in doc.translations):
        if built != doc.blob:
            raise TelopError(T('Порожня/тотожна збірка відрізняється від оригіналу.', 'An empty/identical build differs from the original.'))
    return built


def verify_telop_roundtrip(doc: TelopDoc, data: bytes) -> dict:
    """Re-open generated NUCC/XFBIN and verify every text/timeline/chunk."""
    with tempfile.TemporaryDirectory(prefix='storm1_telop_check_') as td:
        temp = Path(td) / doc.path.name
        temp.write_bytes(data)
        parsed = load_telop(temp)
    source = {e.key:e for e in doc.entries}
    rebuilt = {e.key:e for e in parsed.entries}
    if set(source) != set(rebuilt):
        raise TelopError(T('Повторний розбір змінив кількість/ключі полів.', 'Re-parsing changed the number/keys of fields.'))
    for key, e in source.items():
        expected = doc.translations.get(key, e.source)
        test = rebuilt[key]
        if test.source != expected or (test.start,test.end,test.mode,test.chunk_name) != (e.start,e.end,e.mode,e.chunk_name):
            raise TelopError(T(f'{key}: round-trip змінив текст або службові метадані.', f'{key}: the round-trip changed the text or service metadata.'))
    if parsed.schema_sha256 != doc.schema_sha256:
        raise TelopError(T('Змінено структуру чанків під час round-trip.', 'The chunk structure changed during the round-trip.'))
    return {'fields_verified':len(source),'sha256':hashlib.sha256(data).hexdigest(),
            'chunks_verified':len(doc.chunks)}


# ============================================================================
# txt_pipeline
# ============================================================================

VERSION = '2.8.0'
FORMAT_ID_V1 = 'naruto-storm1-nmh-txt-v1'
FORMAT_ID = 'naruto-storm1-nmh-txt-v2'
FILES = ('message.bin.xfbin', 'message_btl.bin.xfbin',
         'itemtext.bin.xfbin', 'MissionInfo.bin.xfbin')
DEFAULT_SOURCE = r'E:\SteamLibrary\steamapps\common\QWANT\data\disc\dataRegion_extracted\data\event\eng'
DEFAULT_TELOP = r'E:\SteamLibrary\steamapps\common\QWANT\data\disc\dataRegion_extracted\data\interface\telop\eng'
DEFAULT_WORKSPACE = r'E:\Localization\Naruto\Storm1\UA_Workspace'
DEFAULT_OUTPUT = r'E:\Localization\Naruto\Storm1\Build'
TXT_NAME = 'STORM1.ALL.UA.txt'
MANIFEST_NAME = 'STORM1.ALL.manifest.json'


@dataclass
class TextItem:
    key: str
    source: str
    virtual_file: str
    logical_line: int
    editable_id: object
    family: str


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_part(part: str) -> str:
    part = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', part).strip(' .')
    return part[:90] or 'unnamed'


def clean_relpath(rel: str) -> Path:
    p = Path(rel.replace('\\', '/'))
    if p.is_absolute() or not p.parts or any(x in ('.','..') for x in p.parts) or ':' in rel:
        raise PipelineError(T(f'Небезпечний відносний шлях: {rel}', f'Unsafe relative path: {rel}'))
    return p


def load_doc(path: Path, family: str):
    if family in TELOP_FILES:
        return load_telop(path)
    if family == 'MissionInfo.bin.xfbin':
        return load_missioninfo(path)
    if family in ('message.bin.xfbin', 'message_btl.bin.xfbin'):
        doc = load_messages(path)
        want = 'message' if family == 'message.bin.xfbin' else 'message_btl'
        if doc.family != want:
            raise PipelineError(T(f'{path.name}: очікується {want}, знайдено {doc.family}.', f'{path.name}: expected {want}, found {doc.family}.'))
        return doc
    if family == 'itemtext.bin.xfbin':
        return load_itemtext(path)
    raise PipelineError(T('Невідомий тип ресурсу.', 'Unknown resource type.'))


def list_items(doc, filename: str, relname: str | None = None) -> list[TextItem]:
    items = []
    display = (relname or filename).replace('/', '\\')
    if filename in TELOP_FILES:
        # Telop chunk names are metadata; only the length-prefixed UTF-8 fields are translated.
        for e in doc.entries:
            virt = f'interface\\telop\\eng\\{filename}\\{e.chunk_number:04d}_{safe_part(e.chunk_name)}\\Telop.txt'
            items.append(TextItem(e.key, e.source, virt, e.record+1, e.key, filename))
    elif filename == 'MissionInfo.bin.xfbin':
        for f in doc.fields:
            if f.is_id or not f.source:
                continue
            items.append(TextItem(f.key, f.source,
                f'{display}\\MissionInfo.txt', f.index+1, f.index, filename))
    else:
        for e in doc.entries:
            virt = f'{display}\\{e.chunk_number:03d}_{safe_part(e.chunk_name)}\\S{e.slot}.txt'
            items.append(TextItem(e.key,e.source,virt,e.record+1,e.key,filename))
    return items


def header_for(number: int, row: TextItem) -> str:
    return (f'### {number} — {row.virtual_file} · рядок {row.logical_line} '
            f'· EN: {escape_header_source(row.source)}')


def logcall(log: Callable[[str],None] | None, msg: str):
    if log:
        log(msg)


def locate_sources(eng_dir: Path, telop_dir: Path | None = None) -> tuple[list[tuple[str, Path, Path]], list[str]]:
    eng_dir = Path(eng_dir)
    if not eng_dir.is_dir():
        raise PipelineError(T(f'Немає вхідної папки: {eng_dir}', f'Input folder not found: {eng_dir}'))
    results, messages = [], []
    for filename in FILES:
        # Within the selected eng directory only. Do not scan other languages.
        matches = sorted(p for p in eng_dir.rglob('*')
                         if p.is_file() and p.name.lower() == filename.lower())
        if not matches:
            messages.append(f'ВІДСУТНІЙ: {filename}')
            continue
        for p in matches:
            rel = p.relative_to(eng_dir)
            if p.resolve().is_relative_to(eng_dir.resolve()) is False:
                raise PipelineError(T(f'Символічне посилання за межі папки: {p}', f'Symbolic link pointing outside the folder: {p}'))
            results.append((filename, p, rel))
    if telop_dir is not None:
        telop_dir = Path(telop_dir)
        if not telop_dir.is_dir():
            messages.append(f'НЕМАЄ ПАПКИ TELOP: {telop_dir}')
        else:
            for filename in TELOP_FILES:
                path = telop_dir / filename
                if not path.is_file():
                    messages.append(f'ВІДСУТНІЙ TELOP: {filename}')
                    continue
                if path.is_symlink():
                    raise PipelineError(T(f'Не можна читати Telop через символьне посилання: {path}', f'Telop cannot be read through a symbolic link: {path}'))
                results.append((filename, path, Path(filename)))
    return results, messages


def _dummy(source: str) -> bool:
    """A system sentinel, never exposed to the translator or modified."""
    return source.strip().casefold() == 'dummy'


def plan_export(files_and_items: list[tuple[dict, list[TextItem]]]):
    """Group only speaker-label values (message S1), preserve every physical key.

    Exact matches to speaker labels in *other* resources also share the name
    translation. Dialogue/description duplicates are kept separate, even when
    their English source strings are identical.
    """
    physical: list[tuple[str, str, TextItem]] = []
    speakers: dict[str, tuple[str, TextItem]] = {}
    for meta, items in files_and_items:
        for it in items:
            physical.append((meta['relpath'], meta['filename'], it))
            if (meta['filename'] == 'message.bin.xfbin'
                    and it.key.endswith('-S1') and not _dummy(it.source)):
                speakers.setdefault(it.source, (meta['relpath'], it))

    rows = []
    bindings = []
    speakers_ordered = list(speakers)
    name_to_row = {}
    # Speaker labels appear first in a convenient single translator section.
    for source in speakers_ordered:
        rel, it = speakers[source]
        number = len(rows) + 1
        name_to_row[source] = number
        rows.append({'number': number, 'file': rel, 'entry_key': it.key,
                     'source': it.source, 'virtual_file': it.virtual_file,
                     'logical_line': it.logical_line,
                     'header': header_for(number, it),
                     'family': 'message.bin.xfbin',
                     'kind': 'speaker-name', 'uses': 0})
    for rel, family, it in physical:
        if _dummy(it.source):
            number = None
        elif family not in TELOP_FILES and it.source in name_to_row:
            number = name_to_row[it.source]
        else:
            number = len(rows) + 1
            rows.append({'number': number, 'file': rel, 'entry_key': it.key,
                         'source': it.source, 'virtual_file': it.virtual_file,
                         'logical_line': it.logical_line,
                         'header': header_for(number, it), 'family': family,
                         'kind': 'text', 'uses': 0})
        bindings.append({'file': rel, 'entry_key': it.key, 'number': number})
        if number is not None:
            rows[number - 1]['uses'] += 1
    return rows, bindings, {
        'physical_fields': len(physical),
        'hidden_dummy': sum(1 for b in bindings if b['number'] is None),
        'speaker_names': len(name_to_row),
        'speaker_reuses': sum(r['uses'] - 1 for r in rows if r['kind'] == 'speaker-name'),
        'visible_rows': len(rows),
    }


def old_translations(project_dir: Path, log=None) -> tuple[dict, dict]:
    """Migrate v0.6 and v0.7 workspaces losslessly, rejecting conflicts."""
    txt = project_dir / TXT_NAME
    manifest = project_dir / MANIFEST_NAME
    if txt.exists() != manifest.exists():
        raise PipelineError(T('У робочій папці є лише TXT або лише маніфест. Відновіть пару.', 'The workspace folder has only the TXT or only the manifest. Restore the pair.'))
    if not txt.exists():
        return {}, {'old_records': 0, 'ignored_dummy': 0}
    previous = read_manifest(manifest)
    values = parse_txt(txt, previous)
    by_key = {}
    ignored_dummy = 0
    if previous['format'] == FORMAT_ID_V1:
        for r in previous['rows']:
            value = values.get(r['number'], '')
            if not value: continue
            if _dummy(r['source']):
                ignored_dummy += 1
                continue
            by_key[(r['file'], r['entry_key'], r['source'])] = value
    else:
        sources_by_num = {r['number']: r['source'] for r in previous['rows']}
        for b in previous['bindings']:
            num = b['number']
            if num is None or not values.get(num): continue
            by_key[(b['file'], b['entry_key'], sources_by_num[num])] = values[num]
    logcall(log, T(f'Попередній проєкт: {len(by_key)} заповнених фізичних полів.', f'Previous project: {len(by_key)} filled physical fields.'))
    if ignored_dummy:
        logcall(log, T(f'У старому TXT було {ignored_dummy} Dummy-перекладів: не перенесено (резервна копія залишиться).', f'The old TXT had {ignored_dummy} Dummy translations: not carried over (the backup will remain).'))
    return by_key, {'old_records': len(by_key), 'ignored_dummy': ignored_dummy}


def export_project(eng_dir: Path|str, output_dir: Path|str, *,
                   telop_dir: Path|str|None=None, prefill_english: bool=False,
                   log: Callable[[str],None]|None=None) -> dict:
    eng_dir, output_dir = Path(eng_dir), Path(output_dir)
    if output_dir.resolve().is_relative_to(eng_dir.resolve()):
        raise PipelineError(T('Робочу папку треба створити ПОЗА папкою з оригінальними XFBIN.', 'The workspace folder must be created OUTSIDE the folder with the original XFBIN files.'))
    telop_dir = Path(telop_dir) if telop_dir else None
    if telop_dir is not None and output_dir.resolve().is_relative_to(telop_dir.resolve()):
        raise PipelineError(T('Робоча папка не може бути всередині оригінальної папки Telop.', 'The workspace folder cannot be inside the original Telop folder.'))
    matches, warnings = locate_sources(eng_dir, telop_dir)
    if not matches:
        raise PipelineError(T('У папці eng не знайдено жодного потрібного XFBIN.', 'None of the required XFBIN files were found in the eng folder.'))
    all_items = []
    seen_keys = set()
    for family, path, rel in matches:
        logcall(log, T(f'Читання: {rel.as_posix()}', f'Reading: {rel.as_posix()}'))
        try: doc = load_doc(path, family)
        except Exception as exc:
            warnings.append(f'НЕ ПІДТРИМУЄТЬСЯ: {rel.as_posix()} — {exc}')
            continue
        items = list_items(doc, family, rel.as_posix())
        meta = {'filename': family, 'relpath': rel.as_posix(),
                'sha256': doc.sha256, 'schema_sha256': doc.schema_sha256,
                'entries': len(items)}
        if family in TELOP_FILES:
            meta['root'] = 'telop'
        if family == 'itemtext.bin.xfbin':
            warnings.append('itemtext: структурно перевірено; ще потрібен тест у грі.')
        for it in items:
            logical_key = (meta['relpath'], it.key)
            if logical_key in seen_keys:
                raise PipelineError(T(f'Повтор поля {logical_key}', f'Duplicate field {logical_key}'))
            seen_keys.add(logical_key)
        all_items.append((meta, items))
        logcall(log, T(f'   Фізичних текстових полів: {len(items)}', f'   Physical text fields: {len(items)}'))
    if not all_items:
        raise PipelineError(T('Не вдалося безпечно відкрити жоден XFBIN.', 'Could not safely open any XFBIN.'))

    rows, bindings, stats = plan_export(all_items)
    files = [meta for meta, items in all_items]
    old_by_key, transfer_stats = old_translations(output_dir, log)
    per_num: dict[int, set[str]] = {}
    migrated_keys = set()
    for b in bindings:
        num = b['number']
        if num is None: continue
        source = rows[num - 1]['source']
        physical_key = (b['file'], b['entry_key'], source)
        value = old_by_key.get(physical_key)
        if value:
            per_num.setdefault(num, set()).add(value)
            migrated_keys.add(physical_key)
    # Crucially never pick an arbitrary speaker translation if an existing
    # workspace contains context-dependent versions for the same source.
    conflicts = []
    for num, variants in per_num.items():
        if len(variants) > 1:
            r = rows[num - 1]
            conflicts.append(f'EN: {r["source"]!r} — {len(variants)} різні переклади: '
                             + ' | '.join(repr(v[:70]) for v in sorted(variants)[:4]))
    if conflicts:
        raise PipelineError(T('Не можна об’єднати різні старі переклади того самого імені. '
                            'Вирівняйте відповідні рядки у старому TXT і повторіть експорт.\n'
                            + '\n'.join(conflicts[:20]), 'Different old translations of the same name cannot be merged. '
                            'Make the matching lines in the old TXT the same and run the export again.\n'
                            + '\n'.join(conflicts[:20])))
    if old_by_key and len(migrated_keys) != len(old_by_key):
        missing = len(old_by_key) - len(migrated_keys)
        # Refuse to discard any non-Dummy translation, even if source files
        # were added/removed or the new grouping has fewer visible rows.
        raise PipelineError(T(f'Перенесено не всі старі переклади: бракує {missing} полів. '
                            'Збережіть старий проєкт в іншій папці або перевірте джерела.', f'Not all old translations were carried over: {missing} fields are missing. '
                            'Save the old project in another folder or check the sources.'))

    manifest = {
        'format': FORMAT_ID, 'version': VERSION,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'source_dir_hint': str(eng_dir), 'telop_source_dir_hint': str(telop_dir) if telop_dir else None, 'files': files,
        'rows': rows, 'bindings': bindings, 'statistics': stats,
        'warnings': warnings,
        'output_mapping': 'event/eng and interface/telop/eng under data_win32; determined by file.root'
    }
    lines = [
        '#! STORM1.ALL · записів '+str(len(rows))+' · маніфест: '+MANIFEST_NAME,
        '#! Тільки англійські ресурси. Dummy пропущено, підписи мовців об’єднано.',
        '#! Редагуйте ЛИШЕ текст під «### N — …» до завершального «###».',
        '#! Підписи та імена перекладаються ОДИН РАЗ і вставляються всюди автоматично.',
        '#! Заголовки і порядок записів змінювати не можна.',
        '#! Порожній рядок перед ### — роздільник; внутрішні порожні рядки дозволено.',
        r'#! Рядок, що починається з # або \, екрануйте як \# або \\.',
        '#! Порожній переклад означає «залишити англійський оригінал».',
        '#! UTF-8 BOM / CRLF; внутрішні перенесення зберігаються.',
    ]
    last_virt = None
    for r in rows:
        if r['kind'] == 'speaker-name' and last_virt != '__names__':
            lines.append('#! ======== ІМЕНА / ПІДПИСИ МОВЦІВ (ОДИН ПЕРЕКЛАД ДЛЯ ВСІХ ВХОДЖЕНЬ) ========')
            last_virt = '__names__'
        elif r['kind'] != 'speaker-name' and last_virt != r['virtual_file']:
            lines.append('#! ======== '+r['virtual_file']+' ========')
            last_virt = r['virtual_file']
        lines.append(r['header'])
        prev = per_num.get(r['number'])
        value = next(iter(prev)) if prev else (r['source'] if prefill_english else '')
        if value: lines.extend(export_line(value))
        lines.append('')
        lines.append('###')

    output_dir.mkdir(parents=True, exist_ok=True)
    old_txt = output_dir / TXT_NAME
    old_manifest = output_dir / MANIFEST_NAME
    if old_txt.exists():
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        for old in (old_txt, old_manifest):
            backup = old.with_name(old.name + '.backup_' + stamp)
            n = 1
            while backup.exists():
                backup = old.with_name(old.name + '.backup_' + stamp + f'_{n}')
                n += 1
            shutil.copy2(old, backup)
        logcall(log, T('Старий TXT і маніфест скопійовано в .backup_...', 'The old TXT and manifest were copied to .backup_...'))
    atomic_write(old_manifest, (json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    atomic_write(old_txt, ('\r\n'.join(lines)+'\r\n').encode('utf-8-sig'))
    logcall(log, T(f'Експортовано {stats["visible_rows"]} рядків замість {stats["physical_fields"]}.', f'Exported {stats["visible_rows"]} lines instead of {stats["physical_fields"]}.'))
    logcall(log, T(f'Імен/підписів: {stats["speaker_names"]}; пропущено Dummy: {stats["hidden_dummy"]}.', f'Names/labels: {stats["speaker_names"]}; Dummy skipped: {stats["hidden_dummy"]}.'))
    logcall(log, T(f'Автоматичних повторних підстановок імен: {stats["speaker_reuses"]}.', f'Automatic repeated name substitutions: {stats["speaker_reuses"]}.'))
    return {**stats, 'files': len(files), 'warnings': warnings,
            'txt': str(old_txt), 'manifest': str(old_manifest)}


def augment_existing_telop(project_dir: Path|str, telop_dir: Path|str, output_dir: Path|str|None=None,
                           *, log:Callable[[str],None]|None=None) -> dict:
    """Append telop resources to a verified ALL TXT project without EN event originals.

    This is a migration only, not a build. The existing 7,921 logical rows,
    headers and all translations are byte-for-byte retained in the TXT prefix.
    """
    project_dir=Path(project_dir)
    telop_dir=Path(telop_dir)
    output_dir=Path(output_dir) if output_dir else project_dir
    manifest=read_manifest(project_dir/MANIFEST_NAME)
    if manifest['format'] != FORMAT_ID:
        raise PipelineError(T('Для доповнення Telop потрібен сучасний формат маніфеста v2.', 'Adding Telop requires the current manifest format v2.'))
    old_values=parse_txt(project_dir/TXT_NAME,manifest,log=log)
    old_bytes=(project_dir/TXT_NAME).read_bytes()
    if not old_bytes.startswith(b'\xef\xbb\xbf'):
        raise PipelineError(T('TXT не має очікуваного UTF-8 BOM.', 'The TXT does not have the expected UTF-8 BOM.'))
    if b'\n' in old_bytes.replace(b'\r\n', b''):
        raise PipelineError(T('TXT містить неочікувані LF/CR; переноси не переписуватимуться.', 'The TXT contains unexpected LF/CR; line breaks will not be rewritten.'))
    if not old_bytes.endswith(b'\r\n'):
        raise PipelineError(T('Вихідний TXT повинен завершуватися CRLF.', 'The source TXT must end with CRLF.'))
    seen={f['filename'] for f in manifest['files']}
    if any(fn in seen for fn in TELOP_FILES):
        raise PipelineError(T('Принаймні один Telop уже внесено. Повторне доповнення заблоковано.', 'At least one Telop has already been added. Adding again is blocked.'))
    meta_new=[];rows_new=[];bindings_new=[]
    number=len(manifest['rows'])
    for filename in TELOP_FILES:
        src=telop_dir/filename
        if not src.is_file() or src.is_symlink():
            raise PipelineError(T(f'Відсутній оригінальний Telop: {src}', f'The original Telop is missing: {src}'))
        doc=load_telop(src)
        items=list_items(doc,filename)
        meta_new.append({'filename':filename,'relpath':filename,
                         'sha256':doc.sha256,'schema_sha256':doc.schema_sha256,
                         'entries':len(items),'root':'telop'})
        for it in items:
            number+=1
            rows_new.append({'number':number,'file':filename,'entry_key':it.key,
                             'source':it.source,'virtual_file':it.virtual_file,
                             'logical_line':it.logical_line,'header':header_for(number,it),
                             'family':filename,'kind':'text','uses':1})
            bindings_new.append({'file':filename,'entry_key':it.key,'number':number})
        logcall(log,T(f'Telop: {filename} — {len(items)} нових текстових полів.', f'Telop: {filename} — {len(items)} new text fields.'))
    if not rows_new:
        raise PipelineError(T('У Telop немає текстових полів для додавання.', 'Telop has no text fields to add.'))
    new_manifest=json.loads(json.dumps(manifest,ensure_ascii=False))
    new_manifest['version']=VERSION
    new_manifest['created_utc']=datetime.now(timezone.utc).isoformat()
    new_manifest['telop_source_dir_hint']=str(telop_dir)
    new_manifest['files'].extend(meta_new)
    new_manifest['rows'].extend(rows_new)
    new_manifest['bindings'].extend(bindings_new)
    new_manifest['statistics']['physical_fields']+=len(bindings_new)
    new_manifest['statistics']['visible_rows']+=len(rows_new)
    new_manifest['output_mapping']='event/eng and interface/telop/eng under data_win32; determined by file.root'
    new_manifest['warnings']=[*manifest.get('warnings',[]),
                              'Telop: структурні тести виконано; реальний ігровий LQA ще потрібен.']
    tail=['#! ======== STORM 1 · TELOP · 4 окремі файли ========']
    last=None
    for r in rows_new:
        if r['virtual_file']!=last:
            tail.append('#! ======== '+r['virtual_file']+' ========')
            last=r['virtual_file']
        tail += [r['header'],'','###']
    # Preserve ALL original bytes, including the user's existing CRLF and 1,880 translations.
    final_txt=old_bytes+('\r\n'.join(tail)+'\r\n').encode('utf-8')
    final_manifest=(json.dumps(new_manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    if output_dir.resolve()==telop_dir.resolve() or output_dir.resolve().is_relative_to(telop_dir.resolve()):
        raise PipelineError(T('Не можна записувати робочий TXT поверх оригінальних Telop XFBIN.', 'The working TXT cannot be written over the original Telop XFBIN files.'))
    # Validate the staged pair before allowing any disk mutation.
    with tempfile.TemporaryDirectory(prefix='storm1_telop_aug_') as td:
        trial=Path(td)
        (trial/TXT_NAME).write_bytes(final_txt)
        (trial/MANIFEST_NAME).write_bytes(final_manifest)
        checked=read_manifest(trial/MANIFEST_NAME)
        values=parse_txt(trial/TXT_NAME,checked)
        if values!=old_values:
            raise PipelineError(T('Доповнення непередбачено змінило старі переклади.', 'Adding Telop unexpectedly changed the old translations.'))
        if checked['rows'][:len(manifest['rows'])]!=manifest['rows']:
            raise PipelineError(T('Доповнення непередбачено змінило старі заголовки.', 'Adding Telop unexpectedly changed the old headers.'))
    output_dir.mkdir(parents=True,exist_ok=True)
    if output_dir.resolve()==project_dir.resolve():
        stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        for name in (TXT_NAME,MANIFEST_NAME):
            backup=output_dir/(name+'.backup_telop_'+stamp)
            shutil.copy2(output_dir/name,backup)
            logcall(log,T('Захищена копія: '+str(backup), 'Safety copy: '+str(backup)))
    else:
        for name in (TXT_NAME,MANIFEST_NAME):
            if (output_dir/name).exists():
                raise PipelineError(T(f'Ціль уже існує: {output_dir/name}', f'The target already exists: {output_dir/name}'))
    atomic_write(output_dir/MANIFEST_NAME,final_manifest)
    atomic_write(output_dir/TXT_NAME,final_txt)
    return {'old_rows':len(manifest['rows']),'added_rows':len(rows_new),
            'total_rows':len(new_manifest['rows']), 'old_translations_preserved':len(old_values),
            'txt':str(output_dir/TXT_NAME),'manifest':str(output_dir/MANIFEST_NAME),
            'telop_files':len(meta_new)}


def read_manifest(path: Path|str) -> dict:
    p = Path(path)
    if not p.is_file(): raise PipelineError(T(f'Немає маніфесту {p}.', f'Manifest {p} not found.'))
    raw = json.loads(p.read_text(encoding='utf-8-sig'))
    fmt = raw.get('format')
    if fmt not in (FORMAT_ID_V1, FORMAT_ID):
        raise PipelineError(T('Непідтримуваний маніфест TXT-проєкту.', 'Unsupported TXT project manifest.'))
    rows = raw.get('rows', [])
    if any(r.get('number') != n for n, r in enumerate(rows, 1)):
        raise PipelineError(T('Пошкоджено нумерацію TXT-записів у маніфесті.', 'The numbering of TXT records in the manifest is damaged.'))
    total = sum(f['entries'] for f in raw.get('files', []))
    if fmt == FORMAT_ID_V1:
        if len(rows) != total: raise PipelineError(T('Зіпсовано кількість записів старого маніфесту.', 'The record count of the old manifest is corrupted.'))
    else:
        bindings = raw.get('bindings', [])
        if len(bindings) != total:
            raise PipelineError(T('Не збігається кількість бінарних полів у маніфесті.', 'The number of binary fields in the manifest does not match.'))
        all_nums = {r['number'] for r in rows}
        if any(b.get('number') is not None and b['number'] not in all_nums for b in bindings):
            raise PipelineError(T('Неправильне посилання на запис у маніфесті.', 'Wrong reference to a record in the manifest.'))
    return raw


def verify_sources(manifest: dict, eng_dir: Path, log=None, telop_dir: Path | None = None) -> dict[str, object]:
    docs = {}
    all_items = []
    for f in manifest['files']:
        rel = clean_relpath(f['relpath'])
        if f.get('root') == 'telop':
            if telop_dir is None:
                raise PipelineError(T('Маніфест містить Telop. Укажіть папку interface/telop/eng.', 'The manifest contains Telop. Specify the interface/telop/eng folder.'))
            src = telop_dir / rel
        elif f.get('root') in (None, 'event'):
            src = eng_dir / rel
        else:
            raise PipelineError(T('Невідомий корінь ресурсу в маніфесті.', 'Unknown resource root in the manifest.'))
        if not src.is_file(): raise PipelineError(T(f'Зник вихідний файл: {src}', f'The source file is gone: {src}'))
        if digest(src.read_bytes()) != f['sha256']:
            raise PipelineError(T(f'Оригінал змінився (SHA-256): {src}', f'The original changed (SHA-256): {src}'))
        doc = load_doc(src, f['filename'])
        if doc.schema_sha256 != f['schema_sha256']:
            raise PipelineError(T(f'Змінено схему: {src}', f'The schema changed: {src}'))
        actual = list_items(doc, f['filename'], f['relpath'])
        if len(actual) != f['entries']:
            raise PipelineError(T(f'Змінено кількість текстів: {src}', f'The number of texts changed: {src}'))
        docs[f['relpath']] = doc
        all_items.append((f, actual))
        logcall(log, T(f'Підтверджено SHA-256: {rel.as_posix()}', f'SHA-256 confirmed: {rel.as_posix()}'))
    if manifest['format'] == FORMAT_ID_V1:
        for f, actual in all_items:
            rows = [r for r in manifest['rows'] if r['file'] == f['relpath']]
            for r, it in zip(rows, actual):
                if (r['entry_key'] != it.key or r['source'] != it.source or
                        r['header'] != header_for(r['number'], it)):
                    raise PipelineError(T(f'Не збігається англійський рядок у {f["relpath"]}: {r["number"]}', f'The English line in {f["relpath"]} does not match: {r["number"]}'))
    else:
        expected_rows, expected_bindings, expected_stats = plan_export(all_items)
        # Recompute the entire English-to-binding layout from authenticated
        # sources. The manifest cannot silently redirect a name translation.
        if expected_rows != manifest['rows'] or expected_bindings != manifest['bindings']:
            raise PipelineError(T('Маніфест не відповідає англійським джерелам / правилам об’єднання.', 'The manifest does not match the English sources / merging rules.'))
        if manifest.get('statistics') != expected_stats:
            raise PipelineError(T('Пошкоджено статистику маніфесту.', 'The manifest statistics are damaged.'))
    return docs


def build_project(eng_dir:Path|str, project_dir:Path|str, output_root:Path|str, *,
                  telop_dir:Path|str|None=None, dry_run:bool=False, allow_replace:bool=False, log:Callable[[str],None]|None=None)->dict:
    eng_dir=Path(eng_dir);project_dir=Path(project_dir);output_root=Path(output_root)
    telop_dir = Path(telop_dir) if telop_dir else None
    if not output_root.is_absolute():output_root=output_root.absolute()
    if output_root.resolve().is_relative_to(eng_dir.resolve()):
        raise PipelineError(T('Вивід не може бути всередині вхідної папки eng.', 'The output cannot be inside the eng input folder.'))
    if telop_dir is not None and output_root.resolve().is_relative_to(telop_dir.resolve()):
        raise PipelineError(T('Вивід не може бути всередині папки оригінальних Telop.', 'The output cannot be inside the folder with the original Telop files.'))
    manifest=read_manifest(project_dir/MANIFEST_NAME)
    if manifest['format'] == FORMAT_ID_V1:
        raise PipelineError(T('Це маніфест старого формату. Виконайте «Експорт TXT» ще раз — переклади буде перенесено автоматично.', 'This manifest is in the old format. Run "Export TXT" again — the translations will be carried over automatically.'))
    normalize_txt_file(project_dir/TXT_NAME,log=log)
    translation=parse_txt(project_dir/TXT_NAME,manifest)
    logcall(log,T(f'Перевірка {len(manifest["rows"])} записів; заповнено: {len(translation)}', f'Checking {len(manifest["rows"])} records; filled: {len(translation)}'))
    docs=verify_sources(manifest,eng_dir,log=log,telop_dir=telop_dir)
    index={}
    for binding in manifest['bindings']:
        number = binding['number']
        if number is not None and number in translation:
            index[(binding['file'], binding['entry_key'])] = translation[number]
    planned=[]
    for f in manifest['files']:
        rel=f['relpath'];doc=docs[rel]
        selected={key:value for (name,key),value in index.items() if name==rel}
        if f['filename'] in TELOP_FILES:
            valid={e.key:e for e in doc.entries}
            for key,value in selected.items():
                validate_telop_value(valid[key],value)
            doc.translations=selected
            result=build_telop(doc)
            verify_telop_roundtrip(doc,result)
        elif f['filename']=='MissionInfo.bin.xfbin':
            valid={e.key:e for e in doc.fields if not e.is_id and e.source}
            for key,value in selected.items():
                validated_text(valid[key],value)
            doc.translations={valid[key].index:value for key,value in selected.items()}
            result=build_mission(doc)
        else:
            valid={e.key:e for e in doc.entries}
            for key,value in selected.items():
                validate_value(valid[key],value)
            doc.translations=selected
            result=build_messages(doc)
        if not selected:
            original = doc.raw if f['filename']=='MissionInfo.bin.xfbin' else doc.blob
            if result != original:
                raise PipelineError(T(f'Порожній переклад не відтворює оригінал: {rel}', f'An empty translation does not reproduce the original: {rel}'))
            logcall(log,T(f'Без змін: {rel}', f'No changes: {rel}'))
            continue
        # Translation = original also fine but redundant; omit if identical.
        original=doc.raw if f['filename']=='MissionInfo.bin.xfbin' else doc.blob
        if result==original:
            logcall(log,T(f'Без фактичних змін: {rel}', f'No actual changes: {rel}'))
            continue
        # dataRegion archive path: data/event/eng/... => loose data_win32/event/eng/...
        prefix = Path('interface/telop/eng') if f.get('root') == 'telop' else Path('event/eng')
        target=output_root/'data_win32'/prefix/clean_relpath(rel)
        if target.resolve()==doc.path.resolve():
            raise PipelineError(T(f'Вивід перекриває оригінал: {target}', f'The output would overwrite the original: {target}'))
        planned.append((target,result,len(selected),rel))
    # Check ALL destinations before performing ANY write.
    for target,data,count,rel in planned:
        if target.exists() and not target.is_file():
            raise PipelineError(T(f'Вихідна ціль не є файлом: {target}', f'The output target is not a file: {target}'))
        if target.exists() and digest(target.read_bytes()) != digest(data) and not allow_replace:
            raise PipelineError(T('Вихід уже містить іншу версію: '+str(target)+'\n'
                                'Для перезапису ввімкніть «Створити .bak і замінити».', 'The output already contains a different version: '+str(target)+'\n'
                                'To overwrite it, turn on "Create .bak and replace".'))
    if not dry_run and not planned:
        logcall(log,T('Немає нових українських змін: вихідні файли не створено.', 'No new Ukrainian changes: no output files were created.'))
    if not dry_run and planned:
        output_root.mkdir(parents=True,exist_ok=True)
        for target,data,count,rel in planned:
            if target.exists() and digest(target.read_bytes())==digest(data):
                logcall(log,T(f'Вже актуальний: {target}', f'Already up to date: {target}'))
                continue
            if target.exists():
                base = target.with_name(target.name+'.bak')
                backup = base
                n=1
                while backup.exists():
                    backup=target.with_name(target.name+f'.bak{n}')
                    n+=1
                shutil.copy2(target,backup)
                logcall(log,T(f'Резервна копія: {backup}', f'Backup: {backup}'))
            atomic_write(target,data)
            logcall(log,T(f'Зібрано: {target} ({count} рядків, {len(data)} байтів)', f'Built: {target} ({count} lines, {len(data)} bytes)'))
        # Portable deployment archive: identical relative path under data_win32.
        zip_target = output_root/'STORM1_UA_data_win32.zip'
        fd, tmp = tempfile.mkstemp(prefix='.storm1_zip_',suffix='.tmp',dir=output_root)
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                for target,data,count,rel in planned:
                    z.writestr(target.relative_to(output_root).as_posix(),data)
            os.replace(tmp,zip_target)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        report={'format':FORMAT_ID,'tool_version':VERSION,'created_utc':datetime.now(timezone.utc).isoformat(),
                'paths_unverified_in_game':True,
                'files':[{'relpath':r,'output':str(target),'sha256':digest(data),'edited_fields':n}
                         for target,data,n,r in planned]}
        atomic_write(output_root/'STORM1_BUILD_REPORT.json',
                     (json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    return {'rows':len(manifest['rows']),'translations':len(translation),
            'physical_fields_modified':len(index), 'files_built':len(planned),'outputs':[str(x[0]) for x in planned],
            'warnings':manifest['warnings']}


# ============================================================================
# deploy
# ============================================================================

GAME_ROOT = r'E:\SteamLibrary\steamapps\common\QWANT'
REL_PREFIX = Path('data_win32') / 'event' / 'eng'
TELOP_PREFIX = Path('data_win32') / 'interface' / 'telop' / 'eng'
ALLOWED = {'message.bin.xfbin','message_btl.bin.xfbin',
           'itemtext.bin.xfbin','MissionInfo.bin.xfbin'}

class InstallError(RuntimeError):
    pass


def _sha(data: bytes)->str:
    return hashlib.sha256(data).hexdigest()


def _atomic_bytes(dst: Path, data: bytes):
    dst.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.storm1_install_',suffix='.tmp',dir=dst.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name,dst)
    finally:
        if os.path.exists(name): os.unlink(name)


def _check_not_symlink_path(root:Path, target:Path):
    # Check each component; a directory symlink could redirect game overrides
    # outside the intended game tree.
    if root.is_symlink():
        raise InstallError(T('Коренева папка гри є символьним посиланням. Установлення заблоковано.', 'The game root folder is a symbolic link. Installation blocked.'))
    item=root
    for name in target.relative_to(root).parts:
        item=item/name
        if item.is_symlink():
            raise InstallError(T('Заборонено символьні посилання у шляху встановлення: '+str(item), 'Symbolic links are not allowed in the installation path: '+str(item)))


def install_text_build(build_root: str|Path, game_root: str|Path, built_files: list[str], log=None)->dict:
    build_root=Path(build_root).resolve()
    game_root=Path(game_root).resolve()
    if not game_root.is_dir() or not (game_root/'data').is_dir():
        raise InstallError(T('Немає встановленої гри: очікується QWANT із папкою data. Перевір шлях до гри.', 'The installed game was not found: QWANT with a data folder is expected. Check the game path.'))
    if not build_root.is_dir():
        raise InstallError(T('Не знайдено теку Build після збирання.', 'The Build folder was not found after the build.'))
    if not game_root.is_absolute() or build_root==game_root:
        raise InstallError(T('Некоректні папки для встановлення.', 'Invalid folders for installation.'))
    staged=[]
    seen=set()
    # Read and verify all outputs first; no write has happened yet.
    for filepath in built_files:
        source=Path(filepath)
        if source.is_symlink() or not source.is_file():
            raise InstallError(T('Зібраний файл відсутній або є посиланням: '+str(source), 'The built file is missing or is a link: '+str(source)))
        resolved=source.resolve()
        if not resolved.is_relative_to(build_root):
            raise InstallError(T('Файл збирання поза Build: '+str(source), 'The built file is outside Build: '+str(source)))
        relative=resolved.relative_to(build_root)
        parts=relative.parts
        standard=(len(parts)==4 and parts[:3]==REL_PREFIX.parts and parts[3] in ALLOWED)
        telop=(len(parts)==5 and parts[:4]==TELOP_PREFIX.parts and parts[4] in TELOP_FILES)
        if not (standard or telop):
            raise InstallError(T('Заборонений шлях у пакеті: '+str(relative), 'Path not allowed in the package: '+str(relative)))
        if relative in seen:
            raise InstallError(T('Повторний шлях у пакеті: '+str(relative), 'Duplicate path in the package: '+str(relative)))
        seen.add(relative)
        target=game_root/relative
        _check_not_symlink_path(game_root,target)
        if target.exists() and not target.is_file():
            raise InstallError(T('Ціль не є файлом: '+str(target), 'The target is not a file: '+str(target)))
        data=source.read_bytes()
        existing=target.read_bytes() if target.exists() else None
        staged.append({'rel':relative,'dst':target,'data':data,'before':existing,'changed':existing != data})
    changing=[f for f in staged if f['changed']]
    if not changing:
        return {'installed':0,'already_current':len(staged),'backups':[],
                'game_path':str(game_root/'data_win32'/'event'/'eng'),
                'note':'У грі вже встановлено таку саму версію, або змінених файлів немає.'}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    backup_dir=build_root/'installation_backups'/stamp
    if backup_dir.exists(): raise InstallError(T('Збіг шляху резервних копій.', 'A backup folder with this path already exists.'))
    backup_dir.mkdir(parents=True)
    installed=[]
    backup_list=[]
    try:
        for row in changing:
            if row['before'] is not None:
                backup=backup_dir/row['rel']
                _atomic_bytes(backup,row['before'])
                if _sha(backup.read_bytes())!=_sha(row['before']):
                    raise InstallError(T('Резервна копія не пройшла перевірку: '+str(backup), 'The backup failed verification: '+str(backup)))
                backup_list.append(str(backup))
        # All backups have been secured before the first game-file mutation.
        for row in changing:
            _check_not_symlink_path(game_root,row['dst'])
            _atomic_bytes(row['dst'],row['data'])
            installed.append(row)
            if _sha(row['dst'].read_bytes())!=_sha(row['data']):
                raise InstallError(T('Не збігається SHA-256 після встановлення: '+str(row['dst']), 'SHA-256 does not match after installation: '+str(row['dst'])))
            if log:log(T('Установлено: '+str(row['dst']), 'Installed: '+str(row['dst'])))
    except Exception as exc:
        rollback_errors=[]
        for row in reversed(installed):
            try:
                if row['before'] is None:
                    row['dst'].unlink(missing_ok=True)
                else:
                    _atomic_bytes(row['dst'],row['before'])
            except Exception as rb_exc:
                rollback_errors.append(str(row['dst'])+': '+str(rb_exc))
        msg='Установлення перервано. Зміни відкочено.'
        if rollback_errors:
            msg+=' ДЕЯКІ ФАЙЛИ НЕ ВДАЛОСЯ ВІДНОВИТИ: '+'; '.join(rollback_errors)
        raise InstallError(msg+' Початкова помилка: '+str(exc)) from exc
    report={
        'created_utc':datetime.now(timezone.utc).isoformat(),
        'game_root':str(game_root), 'build_root':str(build_root),
        'installed':len(changing), 'already_current':len(staged)-len(changing),
        'backups':backup_list,
        'files':[{'target':str(f['dst']),'relative':f['rel'].as_posix(),
                  'sha256':_sha(f['data']),'previous_sha256':_sha(f['before']) if f['before'] is not None else None}
                 for f in changing],
        'note':'Змінено лише підтримувані текстові XFBIN і Telop. Шрифти та інші файли не змінено.'
    }
    _atomic_bytes(backup_dir/'deployment_report.json',
                  (json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    if log:log(T('Резервні копії: '+str(backup_dir), 'Backups: '+str(backup_dir)))
    return {'installed':len(changing),'already_current':len(staged)-len(changing),
            'backup_directory':str(backup_dir), 'backups':backup_list,
            'game_path':str(game_root/'data_win32'/'event'/'eng'),
            'note':'Установлено лише текстові XFBIN/Telop; шрифти не зачеплено.'}


def build_and_install(eng_dir, project_dir, build_root, game_root=GAME_ROOT, *, telop_dir=None, log=None)->dict:
    # Safe overwrite in Build: original build_project preserves .bak of replaced output.
    report=build_project(eng_dir,project_dir,build_root,telop_dir=telop_dir,allow_replace=True,log=log)
    deployed=install_text_build(build_root,game_root,report['outputs'],log=log)
    return {'build':report,'installation':deployed}
