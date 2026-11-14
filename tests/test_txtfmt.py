"""Shared TXT format: automatic separator repair and readiness counter."""
import re

import pytest

import txtfmt

HEAD = '### {n} — message\\S{n}.txt · рядок 1 · EN: {en}'


def manifest(sources):
    return {'rows': [{'number': n, 'header': HEAD.format(n=n, en=en)} for n, en in enumerate(sources, 1)]}


def write(path, text):
    path.write_bytes(text.encode('utf-8-sig'))


def test_regex_is_the_requested_one():
    assert txtfmt.SEPARATOR_FIX.pattern == r'([^\r\n])\r?\n(?=###(?:\r?\n|$))'
    assert re.sub(txtfmt.SEPARATOR_FIX, '\\1\r\n\r\n', 'текст\r\n###\r\n') == 'текст\r\n\r\n###\r\n'


def test_missing_separator_is_repaired_on_disk(tmp_path):
    m = manifest(['Hello', 'Bye', 'Empty'])
    rows = m['rows']
    txt = tmp_path / 'ALL.txt'
    # record 1: separator deleted; record 2: correct; record 3: empty and glued to its header
    write(txt, '\r\n'.join(['#! comment', rows[0]['header'], 'Привіт', '###',
                            rows[1]['header'], 'Бувай', '', '###',
                            rows[2]['header'], '###']) + '\r\n')
    with pytest.raises(txtfmt.TxtError):
        txtfmt.parse_txt(txt, m, autofix=False)
    logs = []
    assert txtfmt.parse_txt(txt, m, log=logs.append) == {1: 'Привіт', 2: 'Бувай'}
    assert logs and '2 записах' in logs[0]
    assert (tmp_path / ('ALL.txt' + txtfmt.BACKUP_SUFFIX)).is_file()
    fixed = txt.read_bytes()
    assert fixed.startswith(b'\xef\xbb\xbf') and b'\n' not in fixed.replace(b'\r\n', b'')
    # second run changes nothing
    assert txtfmt.normalize_txt_file(txt) == 0 and txt.read_bytes() == fixed


def test_escaped_hash_and_inner_blank_lines_survive(tmp_path):
    m = manifest(['A'])
    txt = tmp_path / 'ALL.txt'
    write(txt, '\r\n'.join([m['rows'][0]['header'], 'рядок 1', '', '\\### не роздільник', '###']) + '\r\n')
    assert txtfmt.parse_txt(txt, m) == {1: 'рядок 1\n\n### не роздільник'}


def test_progress_counts_only_real_translations(tmp_path):
    txt = tmp_path / 'ALL.txt'
    lines = []
    for n in range(1, 11):
        body = [] if n > 4 else [f'Переклад {n}']
        if n == 5:
            body = ['Line 5']                 # prefilled English is not a translation
        lines += [HEAD.format(n=n, en=f'Line {n}'), *body, '', '###']
    write(txt, '\r\n'.join(lines) + '\r\n')
    assert txtfmt.translation_progress(txt) == {'done': 4, 'total': 10, 'percent': 40.0}
    assert txtfmt.translation_progress(tmp_path / 'missing.txt') is None


def test_blank_lines_after_last_record_are_accepted(tmp_path):
    m = manifest(['A'])
    txt = tmp_path / 'ALL.txt'
    write(txt, '\r\n'.join([m['rows'][0]['header'], 'текст', '', '###', '', '  ', '']) + '\r\n')
    assert txtfmt.parse_txt(txt, m) == {1: 'текст'}
    write(txt, '\r\n'.join([m['rows'][0]['header'], 'текст', '', '###', 'сміття']) + '\r\n')
    with pytest.raises(txtfmt.TxtError):
        txtfmt.parse_txt(txt, m)
