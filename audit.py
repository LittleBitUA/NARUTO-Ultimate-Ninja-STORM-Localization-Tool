# -*- coding: utf-8 -*-
"""Read-only research tools: STORM 3 resource audit, patch-coverage comparison, XFBIN forensics."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from collections import Counter
import struct

from xfbin import parse_xfbin, FormatError, MAX_INPUT_SIZE, Xfbin, Chunk, read_name_table, u32, MAX_ITEMS
from i18n import T


# ============================================================================
# audit
# ============================================================================

VERSION = '2.9.0-research'
DEFAULT_OUTPUT = str(Path.home() / 'Documents' / 'Naruto' / 'Storm3' / 'Format_Audit')
REPORT_JSON = 'STORM3_RESOURCE_AUDIT.json'
REPORT_TSV = 'STORM3_RESOURCE_AUDIT.tsv'
REPORT_MD = 'STORM3_RESOURCE_AUDIT_README.md'
MAX_FILES = 15000
CANDIDATE_RE = re.compile(r'message|text|talk|dialog|subtitle|scenario|script|mission|ninja|event|telop|menu|ui|quest|title', re.I)
LANGS = {'eng','fre','ger','ita','spa','rus','por','br','chi','cht','jpn','kor','ukr'}

class AuditError(ValueError): pass


def is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as reader:
        for block in iter(lambda: reader.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def locate_data_root(source: Path) -> Path | None:
    """Find extracted/data root without assuming platform is WIN32 or HD."""
    for item in [source, *source.parents]:
        if item.name.casefold() == 'data' and item.parent.name.casefold().endswith('_extracted'):
            return item
    return None


def analyze_folder(source: Path | str, *, log=None) -> dict:
    source = Path(source).expanduser().resolve()
    if not source.is_dir():
        raise AuditError(T(f'Папка з файлами STORM 3 відсутня: {source}', f'The folder with STORM 3 files is missing: {source}'))
    if source.name.casefold() != 'eng':
        raise AuditError(T('Виберіть саме папку eng, щоб не переплутати мовні гілки та інші ресурси.', 'Select the eng folder itself, so that language branches and other resources do not get mixed up.'))
    data_root = locate_data_root(source)
    # No assumption that "WIN32" directory implies an old 32-bit executable.
    maybe_hd = any(part.casefold()=='dlc' for part in source.parts)
    edition = ('У шляху є dlc — можливі ресурси HD, але перевір потрібний EXE.'
               if maybe_hd else 'У шляху немає dlc — ймовірно початкова Full Burst; HD потрібно перевірити окремо.')
    all_files = sorted((p for p in source.rglob('*') if p.is_file() and not p.is_symlink()),key=lambda x:str(x).casefold())
    if len(all_files)>MAX_FILES:
        raise AuditError(T(f'Забагато файлів ({len(all_files)}): обмеження дослідницького сканера {MAX_FILES}.', f'Too many files ({len(all_files)}): the research scanner limit is {MAX_FILES}.'))
    rows = []
    extension_counts={}
    for i,p in enumerate(all_files,1):
        rel=p.relative_to(source).as_posix()
        ext=''.join(p.suffixes).lower() or '(без розширення)'
        extension_counts[ext]=extension_counts.get(ext,0)+1
        st=p.stat()
        item={'path':rel,'size':st.st_size,'extension':ext,'sha256':sha256(p),
              'magic':'','nucc_version':None,'chunk_count':None,'chunks':[],
              'category':'Потрібне дослідження','error':''}
        with p.open('rb') as fh: magic=fh.read(4)
        item['magic']=magic.hex().upper()
        if magic==b'NUCC':
            if st.st_size>MAX_INPUT_SIZE:
                item['error']=f'XFBIN понад обмеження безпечного парсера {MAX_INPUT_SIZE//1048576} МіБ.'
            else:
                try:
                    xf=parse_xfbin(p)
                    item['nucc_version']=xf.nucc_version
                    item['chunk_count']=len(xf.chunks)
                    item['chunks']=[{'type':c.type_name,'name':c.name,'path':c.path,'size':c.size,
                                     'offset':c.data_offset} for c in xf.chunks]
                    joined=' '.join([rel]+[c.type_name+' '+c.path+' '+c.name for c in xf.chunks])
                    if CANDIDATE_RE.search(joined): item['category']='Кандидат на текст/інтерфейс'
                    else:item['category']='NUCC/XFBIN (тип перевірити)'
                    if xf.warnings: item['error']='; '.join(xf.warnings[:10])
                except (FormatError, OSError, ValueError, IndexError) as exc:
                    item['error']=f'Парсер STORM1/2 не розібрав структуру: {exc}'
            if item['category']=='Потрібне дослідження': item['category']='NUCC/XFBIN (невідомий профіль)'
        elif magic==b'CPK ':
            item['category']='CRIWARE/CPK (потрібен окремий розпакувальник)'
        elif CANDIDATE_RE.search(rel):
            item['category']='Кандидат за назвою'
        rows.append(item)
        if log and i%100==0: log(T(f'STORM 3: досліджено {i}/{len(all_files)} файлів.', f'STORM 3: examined {i}/{len(all_files)} files.'))
    siblings={}
    if source.parent.is_dir():
        for lang in sorted(source.parent.iterdir(),key=lambda x:x.name.casefold()):
            if lang.is_dir() and lang.name.casefold() in LANGS:
                n=sum(1 for p in lang.rglob('*') if p.is_file())
                siblings[lang.name]={'files':n}
    other_dirs=[]
    if data_root and data_root.is_dir():
        other_dirs=sorted(p.name for p in data_root.iterdir() if p.is_dir())
    return {'report_version':VERSION, 'created_utc':datetime.now(timezone.utc).isoformat(),
            'source':str(source),'edition_hint':edition,'data_root':str(data_root) if data_root else None,
            'other_resource_directories':other_dirs,'language_siblings':siblings,
            'files_total':len(rows),'file_extensions':extension_counts,
            'xfbin_total':sum(bool(r['magic']=='4E554343') for r in rows),
            'parsed_xfbin':sum(r['nucc_version'] is not None for r in rows),
            'potential_text_files':sum(r['category']=='Кандидат на текст/інтерфейс' for r in rows),
            'files':rows,
            'notes':['Лише аудит: не розпаковуємо CPK, не експортуємо текст, не збираємо XFBIN, не встановлюємо патч.',
                     'WIN32 у назві ресурсної теки не підтверджує архітектуру виконуваного файлу.',
                     'Для фактичної підтримки STORM 3 необхідно дослідити реальні двійкові зразки.']}


def write_report(result: dict, output: Path | str) -> dict:
    output=Path(output).expanduser().resolve()
    source=Path(result['source']).resolve()
    if is_within(output,source):
        raise AuditError(T('Звіти заборонено записувати всередину оригінальної папки eng.', 'Reports must not be written inside the original eng folder.'))
    # Write each run into a new unique folder; never overwrite earlier reports.
    now=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    destination=output/f'STORM3_AUDIT_{now}'
    destination.mkdir(parents=True,exist_ok=False)
    jpath=destination/REPORT_JSON
    jpath.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    table=destination/REPORT_TSV
    with table.open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.writer(fh,delimiter='\t',lineterminator='\r\n')
        w.writerow(['Файл','Байти','SHA256','Magic','NUCC-версія','Чанків','Категорія','Чанки: тип / назва / внутрішній шлях','Помилки/попередження'])
        for row in result['files']:
            w.writerow([row['path'],row['size'],row['sha256'],row['magic'],row['nucc_version'],row['chunk_count'],
                row['category'],'; '.join(f"{c['type']} / {c['name']} / {c['path']}" for c in row['chunks']),row['error']])
    md=destination/REPORT_MD
    md.write_text('\n'.join([
        '# NARUTO STORM 3 — дослідницький аудит ресурсів',
        '',f"- Папка: `{result['source']}`",
        f"- Версія сканера: `{result['report_version']}`",
        f"- Файлів: **{result['files_total']}**, NUCC: **{result['xfbin_total']}**, розібрано: **{result['parsed_xfbin']}**",
        f"- Кандидатів на текст / інтерфейс: **{result['potential_text_files']}**",
        f"- Тип збірки: {result['edition_hint']}",
        '', '## Мовні папки поруч',
        *[f'- `{k}`: {v["files"]} файлів' for k,v in result['language_siblings'].items()],
        '', '## Наступні кроки',
        '1. Відкрийте TSV та знайдіть XFBIN із текстовими чанками.',
        '2. Зіставте відповідні ENG/RUS/FRE файли; не плутайте класичну і HD-редакції.',
        '3. Передайте окремі зразки XFBIN для верифікації схем записів, кодування, pointer table, тегів та структури збирання.',
        '4. Доповніть дослідження `data/event`, `data/interface`, `data/ui`, відеотитрів та шрифтів.',
        '', '**Увага:** звіт містить назви файлів, внутрішні шляхи та хеші, але не містить вмісту гри. Файли не змінено.',
        '']) ,encoding='utf-8')
    return {'report_json':str(jpath),'report_tsv':str(table),'report_md':str(md),
            'files':result['files_total'],'xfbin':result['xfbin_total'],
            'candidates':result['potential_text_files'],'source':result['source']}


def run(source: Path|str, output: Path|str, *, log=None) -> dict:
    src=Path(source).expanduser().resolve()
    dst=Path(output).expanduser().resolve()
    if is_within(dst,src): raise AuditError(T('Обирайте папку звіту поза оригінальною папкою eng.', 'Choose a report folder outside the original eng folder.'))
    result=analyze_folder(src,log=log)
    written=write_report(result,dst)
    if log:log(T(f"Знайдено {written['files']} файлів, {written['xfbin']} XFBIN; звіт: {written['report_tsv']}", f"Found {written['files']} files, {written['xfbin']} XFBIN; report: {written['report_tsv']}"))
    return written


# ============================================================================
# patch_scope
# ============================================================================

SCOPE_NAME = 'PATCH_REFERENCE_SCOPE.json'
SCOPE_REPORT_JSON = 'NARUTO_PATCH_COVERAGE_REPORT.json'
SCOPE_REPORT_TSV = 'NARUTO_PATCH_COVERAGE_REPORT.tsv'
KNOWN_CATEGORIES = {
    'storm1_text': 'Текст STORM 1 — XFBIN/TXT уже реалізовано',
    'storm1_telop': 'Текст STORM 1 — Telop уже реалізовано',
    'storm2_text': 'Текст STORM 2 — messageInfo уже реалізовано',
    'font_requires_validation': 'Шрифт — окремий тест; STORM 2 не підтверджено',
    'graphics_candidate': 'Кандидат на графічні ресурси — перевірка BC1/BC3 на оригіналі',
    'binary_not_yet_supported': 'Службовий XFBIN — формат не досліджено',
    'unnecessary_windows_cache': 'Thumbs.db — кеш Windows, не є ігровим ресурсом',
}

class ScopeError(ValueError):
    pass


def load_scope(scope_path: str | Path | None = None) -> dict:
    path = Path(scope_path) if scope_path else Path(__file__).resolve().with_name(SCOPE_NAME)
    scope = json.loads(path.read_text(encoding='utf-8'))
    if scope.get('schema') != 'NARUTO_PATCH_SCOPE_REFERENCE_V1':
        raise ScopeError(T('Невідома схема каталогу.', 'Unknown catalog schema.'))
    entries=scope.get('entries')
    if not isinstance(entries, list) or not entries:
        raise ScopeError(T('Порожній каталог патча.', 'The patch catalog is empty.'))
    seen=set()
    for entry in entries:
        if not isinstance(entry,dict):raise ScopeError(T('Неправильний тип запису.', 'Wrong record type.'))
        game,rel,cat=entry.get('game'),entry.get('relative_path'),entry.get('category')
        if game not in ('STORM1','STORM2') or cat not in KNOWN_CATEGORIES or not isinstance(rel,str):
            raise ScopeError(T('Невідомий запис у каталозі патча.', 'Unknown record in the patch catalog.'))
        p=Path(rel.replace('\\','/'))
        if (p.is_absolute() or not p.parts or '..' in p.parts or ':' in rel or '\x00' in rel or
            '\\' in rel or rel.startswith('/') or len(rel)>1024):
            raise ScopeError(T('Небезпечний шлях у каталозі патча.', 'Unsafe path in the patch catalog.'))
        key=(game,rel.casefold())
        if key in seen:raise ScopeError(T('Повторний шлях у каталозі.', 'Duplicate path in the catalog.'))
        seen.add(key)
    return scope


def _safe_dest(output: Path, roots: dict[str, Path]):
    out=output.resolve()
    if out.exists() and not out.is_dir():raise ScopeError(T('Папка звітів — це файл.', 'The reports folder is a file.'))
    for root in roots.values():
        rr=root.resolve()
        if out == rr or rr in out.parents:
            raise ScopeError(T('Звіти не можна створювати в папці оригінальних ресурсів гри.', 'Reports cannot be created in the folder with the original game resources.'))
    return out


def inspect_reference_file(root: Path, entry: dict) -> tuple[str,int]:
    if entry['category']=='unnecessary_windows_cache':
        return 'ignore_cache',0
    rel=Path(entry['relative_path'])
    candidate=root.joinpath(*rel.parts)
    if candidate.is_symlink():return 'unsafe_symlink',0
    if not candidate.exists():return 'missing',0
    if not candidate.is_file():return 'not_regular_file',0
    try:
        if root.resolve() not in candidate.resolve().parents:
            return 'unsafe_outside_root',0
        size=candidate.stat().st_size
        with candidate.open('rb') as f:
            head=f.read(8)
        if len(head)!=8 or head[:4]!=b'NUCC':return 'unexpected_header',size
        ver=struct.unpack('>I',head[4:8])[0]
        if ver not in (104,121):return f'unknown_nucc_version_{ver}',size
        return 'present_nucc',size
    except (OSError,PermissionError):return 'unreadable',0


def audit_scope(storm1_data: str | Path, storm2_data: str | Path, output: str | Path,
                *, scope_path: str | Path | None = None, log=None) -> dict:
    """Compare only original files' existence and NUCC signatures; NEVER change them."""
    scope=load_scope(scope_path)
    roots={'STORM1':Path(storm1_data).expanduser(), 'STORM2':Path(storm2_data).expanduser()}
    if not any(x.is_dir() for x in roots.values()):
        raise ScopeError(T('Не знайдено жодної папки оригінальних ресурсів STORM 1/2.', 'No folder with original STORM 1/2 resources was found.'))
    out=_safe_dest(Path(output).expanduser(),roots)
    out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for e in scope['entries']:
        game=e['game']
        if not roots[game].is_dir():state,size='root_not_found',0
        else:state,size=inspect_reference_file(roots[game],e)
        rec={'game':game,'relative_path':e['relative_path'],'category':e['category'],
             'description_ua':KNOWN_CATEGORIES[e['category']],
             'existing_original_state':state,'original_size_bytes':size,
             'patch_size_bytes':e['patch_size_bytes'],
             'text_pipeline_confirmed':e['confirmed_text_pipeline'],
             'texture_support_not_guaranteed':e['category']=='graphics_candidate',
             'patch_probe_state':e.get('patch_probe',{}).get('state','not_tested'),
             'patch_probe_supported_bc1_bc3':e.get('patch_probe',{}).get('supported_textures',0),
             'patch_probe_unsupported_tex_chunks':e.get('patch_probe',{}).get('unsupported_tex_chunks',0),
             'sample_note':e.get('patch_probe',{}).get('error') or
                           e.get('patch_probe',{}).get('texture_error') or
                           ' | '.join(e.get('patch_probe',{}).get('texture_warnings',[])[:3])}
        rows.append(rec)
    counts=Counter((r['game'],r['existing_original_state']) for r in rows)
    categories=Counter((r['game'],r['category']) for r in rows)
    result={
      'schema':'NARUTO_PATCH_COVERAGE_AUDIT_V1',
      'created_utc':datetime.now(timezone.utc).isoformat(),
      'source_roots':{game:str(root) for game,root in roots.items()},
      'total_records':len(rows),
      'patch_entries_by_game':{game:sum(x['game']==game for x in rows) for game in roots},
      'verified_existing_by_game':{game:counts[(game,'present_nucc')] for game in roots},
      'source_roots_available':{g:r.is_dir() for g,r in roots.items()},
      'status_counts':{g:{s:n for (gg,s),n in counts.items() if gg==g} for g in roots},
      'category_counts':{g:{c:n for (gg,c),n in categories.items() if gg==g} for g in roots},
      'note':'Патч є лише орієнтиром охоплення. Підтримка PNG не гарантована для всіх XFBIN. Жодних RU файлів не встановлено. Дані гри не змінено.',
      'records':rows}
    json_path=out/SCOPE_REPORT_JSON
    tsv_path=out/SCOPE_REPORT_TSV
    if any(f.is_symlink() for f in (json_path,tsv_path)):
        raise ScopeError(T('Звіти не можна записувати через символічні посилання.', 'Reports cannot be written through symbolic links.'))
    # Write temp files first; replacement only concerns our own reports in a separate folder.
    data=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    temp_json=out/(SCOPE_REPORT_JSON+'.tmp');temp_tsv=out/(SCOPE_REPORT_TSV+'.tmp')
    if temp_json.is_symlink() or temp_tsv.is_symlink():raise ScopeError(T('Небезпечний тимчасовий звіт.', 'Unsafe temporary report.'))
    temp_json.write_text(data,encoding='utf-8')
    fields=list(rows[0].keys())
    with temp_tsv.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter='\t');w.writeheader();w.writerows(rows)
    temp_json.replace(json_path);temp_tsv.replace(tsv_path)
    if log:log(T('Покриття: '+str(result['verified_existing_by_game'])+'; деталі у '+str(tsv_path), 'Coverage: '+str(result['verified_existing_by_game'])+'; details in '+str(tsv_path)))
    return {'files_in_reference':len(rows),
            'confirmed_original_files':result['verified_existing_by_game'],
            'json_report':str(json_path),'tsv_report':str(tsv_path),
            'note':result['note']}


# ============================================================================
# xfbin_forensics
# ============================================================================

def parse_forensics(path: Path):
    p=Path(path)
    if p.stat().st_size>MAX_INPUT_SIZE:raise FormatError(T('Файл перевищує захисний ліміт 64 МіБ.', 'The file exceeds the 64 MiB safety limit.'))
    b=p.read_bytes()
    if len(b)<68 or b[:4]!=b'NUCC':raise FormatError(T('Це не NUCC/XFBIN.', 'This is not a NUCC/XFBIN file.'))
    doc=Xfbin(p,b,hashlib.sha256(b).hexdigest(),u32(b,4))
    ts,ps,ns,ms=None,None,None,None
    tc,ts,pc,ps,nc,ns,mc,ms,ic,rc=struct.unpack_from('>10I',b,28)
    if max(tc,pc,nc,mc,ic,rc)>1000000 or ms<mc*12:raise FormatError(T('Некоректні індекси.', 'Invalid indexes.'))
    pos=68
    types=read_name_table(b,pos,ts,tc);pos+=ts
    paths=read_name_table(b,pos,ps,pc);pos+=ps
    names=read_name_table(b,pos,ns,nc);pos+=ns
    pos=(pos+3)&~3
    if pos+ms+rc*8+ic*4>len(b):raise FormatError(T('Індекси виходять за межі.', 'Indexes are out of bounds.'))
    maps=[struct.unpack_from('>III',b,pos+i*12) for i in range(mc)]
    pos+=ms+rc*8
    indices=[u32(b,pos+i*4) for i in range(ic)]
    pos+=ic*4
    doc.types=types;doc.file_paths=paths
    repairs=[];page=0;page_base=0;number=0
    while pos<len(b):
        if len(b)-pos<12:raise FormatError(T(f'Залишок {len(b)-pos} байтів поблизу 0x{pos:X}.', f'{len(b)-pos} leftover bytes near 0x{pos:X}.'))
        s,idx,v,u=struct.unpack_from('>IIHH',b,pos)
        # Detect an 8-byte, zero-filled inter-chunk gap ONLY if the following
        # header has a known NUCC chunk version and a bounded size.
        if v not in (121,122,123):
            recovered=False
            for gap in (8,4,12,16):
                if pos+gap+12>len(b):continue
                # In several patched files the NTP3 payload contains a trailing
                # 8-byte overhang beyond the declared chunk length. Only
                # tolerate nonzero overhang after a verified texture chunk.
                zero_gap = b[pos:pos+gap] == b'\0'*gap
                if not zero_gap and not (gap==8 and doc.chunks and doc.chunks[-1].type_name=='nuccChunkTexture'):
                    continue
                nsiz,nidx,nver,nunknown=struct.unpack_from('>IIHH',b,pos+gap)
                if nver in (121,122,123) and nsiz <= len(b)-(pos+gap+12) and nidx < ic:
                    repairs.append({'offset':pos,'kind':'zero_padding_between_chunks' if zero_gap else 'texture_payload_overhang','padding_bytes':gap})
                    pos+=gap;s,idx,v,u=nsiz,nidx,nver,nunknown
                    recovered=True
                    break
            if not recovered and v not in (121,122,123):
                raise FormatError(T(f'Недійсний заголовок чанка {number} за 0x{pos:X}: версія {v}.', f'Invalid header of chunk {number} at 0x{pos:X}: version {v}.'))
        if s>len(b)-pos-12:
            # 0x0F000008 may be a patcher-specific size-flag or a corrupt
            # leading byte. In either case, DO NOT mutate the source or write.
            if (s & 0xff000000)==0x0f000000 and 0<(s&0xffffff)<=len(b)-pos-12:
                repairs.append({'offset':pos,'kind':'anomalous_high_size_byte_0F',
                                'raw_size':s,'read_only_interpretation':s&0xffffff})
                s &= 0xffffff
            else:raise FormatError(T(f'Чанк {number}: розмір 0x{s:08X} виходить за межі (0x{pos:X}).', f'Chunk {number}: size 0x{s:08X} is out of bounds (0x{pos:X}).'))
        mp=page_base+idx;ty=fn=nm='<unknown>'
        if mp<len(indices):
            mi=indices[mp]
            if mi<len(maps):
                ti,pi,ni=maps[mi]
                ty=types[ti] if ti<len(types) else '<type?>'
                fn=paths[pi] if pi<len(paths) else '<path?>'
                nm=names[ni] if ni<len(names) else '<name?>'
        c=Chunk(number,page,ty,nm,fn,pos,pos+12,s,idx,v)
        doc.chunks.append(c)
        if ty=='nuccChunkPage':
            if s<8:raise FormatError(T(f'Чанк сторінки {number} надто короткий.', f'Page chunk {number} is too short.'))
            increment,ref=struct.unpack_from('>II',b,c.data_offset)
            if not 1<=increment<=ic:raise FormatError(T(f'Чанк сторінки {number}: некоректний індекс {increment}.', f'Page chunk {number}: invalid index {increment}.'))
            page_base+=increment;page+=1
        pos+=12+s
        number+=1
        if number>MAX_ITEMS:raise FormatError(T('Забагато чанків.', 'Too many chunks.'))
    return doc,repairs


def diagnose(path):
    p=Path(path)
    try:
        doc,notes=parse_forensics(p)
        types=dict(Counter(ch.type_name for ch in doc.chunks))
        result={'file':p.name,'original_sha256':doc.sha256,'status':'read_only_forensics',
                'chunks_identified':len(doc.chunks),'types':types,
                'anomalies':notes,'anomalies_count':len(notes),
                'build_allowed':False,'warning':'Це лише розбір. Ніколи не використовуй відновлені межі для пакування без EN-оригіналу.'}
        return result
    except Exception as exc:
        return {'file':p.name,'status':'unresolved','error':f'{type(exc).__name__}: {exc}', 'build_allowed':False}


def diagnostic_scope(storm1_source, storm2_source, report_dir, *, log=None):
    """Read-only inspection of 35 historically anomalous patch paths in actual EN roots."""
    import csv
    roots={'STORM1':Path(storm1_source).resolve(), 'STORM2':Path(storm2_source).resolve()}
    for root in roots.values():
        if not root.is_dir():
            raise ValueError(T('Не знайдено папку оригінальних ресурсів: '+str(root), 'Folder with original resources not found: '+str(root)))
    output=Path(report_dir).resolve()
    if any(output==root or output.is_relative_to(root) for root in roots.values()):
        raise ValueError(T('Звіти заборонено записувати всередину каталогу оригінальних ресурсів.', 'Reports must not be written inside the folder with original resources.'))
    manifest=load_scope()
    target=[v for v in manifest['entries'] if v.get('patch_probe',{}).get('v26_forensic_status')]
    if len(target)!=35:
        raise ValueError(T('Перелік 35 аномальних файлів відсутній або змінений.', 'The list of 35 anomalous files is missing or has been changed.'))
    results=[]
    for entry in target:
        src=roots[entry['game']]/Path(entry['relative_path'])
        row={'game':entry['game'],'relative_path':entry['relative_path'],
             'patch_had_anomalies':len(entry['patch_probe'].get('v26_forensic_anomalies',[]))}
        if not src.is_file():
            row.update(status='missing_original',anomalies_count=0,error='Оригінальний файл не знайдено')
        else:
            if not src.resolve().is_relative_to(roots[entry['game']]):
                raise ValueError(T('Символічне посилання виходить поза папку гри: '+str(src), 'The symbolic link points outside the game folder: '+str(src)))
            value=diagnose(src)
            row.update(status=value['status'], anomalies_count=value.get('anomalies_count',0),
                       error=value.get('error',''), types=value.get('types',{}),
                       anomalies=value.get('anomalies',[]), chunks_identified=value.get('chunks_identified',0),
                       source_sha256=value.get('original_sha256',''))
        results.append(row)
    output.mkdir(parents=True,exist_ok=True)
    js=output/'NARUTO_35_XFBIN_EN_ORIGINAL_FORENSICS.json'
    tsv=output/'NARUTO_35_XFBIN_EN_ORIGINAL_FORENSICS.tsv'
    js.write_text(json.dumps({'read_only':True,'files':results},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    with tsv.open('w',encoding='utf-8-sig',newline='') as f:
        columns=['game','relative_path','patch_had_anomalies','status','anomalies_count','chunks_identified','source_sha256','error']
        w=csv.DictWriter(f,fieldnames=columns,delimiter='\t',extrasaction='ignore')
        w.writeheader();w.writerows(results)
    summary={'checked':len(results),'originals_found':sum(x['status']!='missing_original' for x in results),
             'unresolved':sum(x['status']=='unresolved' for x in results),
             'original_anomalies_found':sum(x.get('anomalies_count',0) for x in results),
             'report_json':str(js),'report_tsv':str(tsv)}
    if log:log(T(f'Форензика 35 XFBIN завершена: {summary["originals_found"]} оригіналів, {summary["unresolved"]} помилок; звіти в {output}', f'Forensics of 35 XFBIN finished: {summary["originals_found"]} originals, {summary["unresolved"]} errors; reports in {output}'))
    return summary
