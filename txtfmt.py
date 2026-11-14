# -*- coding: utf-8 -*-
"""Shared translation-TXT format for all three games.

    ### N — <context> · EN: <english source>
    <translation, any number of lines>
    <one empty separator line>
    ###

Also holds the automatic separator repair that runs before every parse/pack,
and the per-game readiness counter used by the interface.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import tempfile
from i18n import T


class TxtError(ValueError):
    pass


def atomic_write(path: Path, data: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.natool_', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def escape_header_source(s: str) -> str:
    # Physical newlines are shown as visible backslash escapes in the header.
    return s.replace('\r\n', '\\r\\n').replace('\r', '\\r').replace('\n', '\\n')


def export_line(text: str) -> list[str]:
    # One logical line per physical line; leading # and \ are escaped so they
    # cannot collide with ### or #! directives.
    raw_lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return [('\\' + x) if x.startswith(('#', '\\')) else x for x in raw_lines]


def decode_body(lines: list[str], number: int) -> str:
    # The final separator line is always removed (interior blanks are kept).
    if not lines or lines[-1] != '':
        raise TxtError(T(f'Запис {number}: перед ### має бути порожній рядок-роздільник.', f'Record {number}: there must be an empty separator line before ###.'))
    result = []
    for x in lines[:-1]:
        if x.startswith('\\#') or x.startswith('\\\\'):
            x = x[1:]                 # escaped line: taken literally
        elif x.startswith('###') or x.startswith('#!'):
            raise TxtError(T(f'Запис {number}: рядок з # потрібно екранувати як \\#.', f'Record {number}: a line starting with # must be escaped as \\#.'))
        result.append(x)
    return '\n'.join(result)


# A text line glued directly to the closing ### (the translator deleted the
# empty separator line). Replacement restores the separator.
SEPARATOR_FIX = re.compile(r'([^\r\n])\r?\n(?=###(?:\r?\n|$))')
BACKUP_SUFFIX = '.before_autofix.bak'


def fix_separators(text: str) -> tuple[str, int]:
    """Apply the separator repair to already-decoded text."""
    return SEPARATOR_FIX.subn('\\1\r\n\r\n', text)


def normalize_txt_file(path: Path | str, log=None) -> int:
    """Repair missing separators in the translation TXT on disk.

    Runs automatically before parsing/packing. Rewrites the file only when
    something changed; the previous version is kept once as
    ``<name>.before_autofix.bak``. Returns the number of repaired records.
    """
    path = Path(path)
    raw = path.read_bytes()
    bom = raw.startswith(b'\xef\xbb\xbf')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise TxtError(T(f'{path.name}: файл має бути в UTF-8 ({exc}).', f'{path.name}: the file must be in UTF-8 ({exc}).')) from exc
    fixed, count = fix_separators(text)
    if not count:
        return 0
    shutil.copy2(path, path.with_name(path.name + BACKUP_SUFFIX))
    atomic_write(path, (b'\xef\xbb\xbf' if bom else b'') + fixed.encode('utf-8'))
    if log:
        log(T(f'Автовиправлення {path.name}: додано порожній рядок перед ### у {count} записах '
            f'(попередня версія — {path.name}{BACKUP_SUFFIX}).',
              f'Auto-fix of {path.name}: added an empty line before ### in {count} records '
              f'(previous version: {path.name}{BACKUP_SUFFIX}).'))
    return count


def parse_txt(path: Path | str, manifest: dict, *, autofix: bool = True, log=None) -> dict[int, str]:
    p = Path(path)
    if autofix:
        normalize_txt_file(p, log=log)
    # newline=None normalizes CRLF, CR and LF; in-game line endings are
    # reinstated later by each format's own validator.
    with p.open('r', encoding='utf-8-sig', newline=None) as fp:
        lines = fp.read().split('\n')
        if lines and lines[-1] == '':
            lines.pop()
    wanted = manifest['rows']
    result = {}
    pos = 0
    for row in wanted:
        while pos < len(lines) and lines[pos].startswith('#!'):
            pos += 1
        if pos >= len(lines):
            raise TxtError(T(f'Немає заголовка для запису {row["number"]}.', f'No header found for record {row["number"]}.'))
        h = lines[pos]; pos += 1
        if h != row['header']:
            raise TxtError(T(f'Змінено заголовок/порядок запису {row["number"]}:\n{h[:180]}', f'The header or order of record {row["number"]} was changed:\n{h[:180]}'))
        contents = []
        while pos < len(lines) and lines[pos] != '###':
            contents.append(lines[pos]); pos += 1
        if pos >= len(lines):
            raise TxtError(T(f'Запис {row["number"]}: немає завершального ###.', f'Record {row["number"]}: the closing ### is missing.'))
        pos += 1
        value = decode_body(contents, row['number'])
        if value:
            result[row['number']] = value
    # Blank lines after the last record are harmless (editors often add one).
    if any(line.strip() and not line.startswith('#!') for line in lines[pos:]):
        raise TxtError(T('Після останнього запису є невідомі додаткові дані.', 'There is unknown extra data after the last record.'))
    return result


def translation_progress(path: Path | str) -> dict | None:
    """Tolerant readiness counter: never raises on a half-edited TXT.

    A record counts as translated when its body is not empty and differs from
    the English source quoted in its header (so an English-prefilled export
    does not show up as 100 %). Returns None when there is no TXT yet.
    """
    p = Path(path)
    try:
        if not p.is_file():
            return None
        with p.open('r', encoding='utf-8-sig', errors='replace', newline=None) as fp:
            lines = fp.read().split('\n')
    except OSError:
        return None
    total = done = 0
    source = None      # English source of the record being read
    body: list[str] = []
    for line in lines:
        if line == '###':
            if source is not None:
                total += 1
                while body and body[-1] == '':
                    body.pop()
                text = '\\n'.join(x[1:] if x.startswith(('\\#', '\\\\')) else x for x in body)
                if text.strip() and text != source:
                    done += 1
            source = None; body = []
        elif source is None:
            if line.startswith('### '):
                source = line.partition(' · EN: ')[2]
                body = []
        else:
            body.append(line)
    percent = (100.0 * done / total) if total else 0.0
    return {'done': done, 'total': total, 'percent': percent}
