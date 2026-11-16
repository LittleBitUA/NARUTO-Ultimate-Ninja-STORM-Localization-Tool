"""Line breaks: a translation packs whatever way its breaks are written."""
from types import SimpleNamespace

import pytest

import storm1_text
import storm2_text
import storm3_text
from txtfmt import fit_line_breaks, parse_txt, translation_progress


@pytest.mark.parametrize('source, typed, packed', [
    # The TXT gives LF for every physical break; the game string uses CRLF.
    ('One\r\nTwo', 'Раз\nДва', 'Раз\r\nДва'),
    ('One\rTwo', 'Раз\nДва', 'Раз\rДва'),
    ('One\nTwo', 'Раз\r\nДва', 'Раз\nДва'),
    # Breaks written out with backslashes in the game string itself.
    ('One\\nTwo', 'Раз\\nДва', 'Раз\\nДва'),
    ('One\\r\\nTwo', 'Раз\\nДва', 'Раз\\r\\nДва'),
    ('One\\nTwo', 'Раз\nДва', 'Раз\\nДва'),
    # Typed by hand the way the header shows a physical break.
    ('One\r\nTwo', 'Раз\\r\\nДва', 'Раз\r\nДва'),
    ('One\r\nTwo', 'Раз\\nДва', 'Раз\r\nДва'),
    # Mixed forms keep their order.
    ('A\r\nB\nC\\nD', '1\n2\n3\n4', '1\r\n2\n3\\n4'),
    # A different number of breaks: the original's usual form.
    ('One\r\nTwo', 'Раз\nДва\nТри', 'Раз\r\nДва\r\nТри'),
    ('One\r\nTwo\r\nThree', 'Раз\nДва', 'Раз\r\nДва'),
    ('One\\nTwo', 'Раз\nДва\\nТри', 'Раз\\nДва\\nТри'),
    ('A\r\nB\\nC', '1\n2\n3\\n4', '1\r\n2\r\n3\\n4'),
    # Nothing to go by, or nothing to do.
    ('One', 'Раз\nДва', 'Раз\nДва'),
    ('One\r\nTwo', 'Раз Два', 'Раз Два'),
    ('One\r\nTwo', '', ''),
])
def test_fit_line_breaks(source, typed, packed):
    assert fit_line_breaks(typed, source) == packed
    assert fit_line_breaks(packed, source) == packed          # safe to apply twice


@pytest.mark.parametrize('module', [storm2_text, storm3_text])
@pytest.mark.parametrize('source, typed, packed', [
    ('Press <A>\r\nto jump', 'Натисни <A>\nщоб стрибнути', 'Натисни <A>\r\nщоб стрибнути'),
    ('One\\nTwo', 'Раз\\nДва\\nТри', 'Раз\\nДва\\nТри'),
    ('One\r\nTwo', 'Раз, два', 'Раз, два'),
    ('One\r\nTwo', 'One\nTwo', 'One\r\nTwo'),               # the English left as it was
])
def test_storm2_and_storm3_accept_any_line_breaks(module, source, typed, packed):
    field = SimpleNamespace(key='F1', source=source)
    assert module.validate_translation(field, typed) == packed.encode('utf-8')


@pytest.mark.parametrize('module', [storm2_text, storm3_text])
def test_tags_are_still_checked(module):
    field = SimpleNamespace(key='F1', source='Press <A>\r\nto jump')
    with pytest.raises(ValueError):
        module.validate_translation(field, 'Натисни\nщоб стрибнути')


def test_storm1_messages_accept_any_line_breaks():
    entry = SimpleNamespace(key='M001-0001-S1', source='One\\nTwo\r\nThree')
    assert storm1_text.validate_value(entry, 'Раз\nДва\nТри') == 'Раз\\nДва\r\nТри'.encode('utf-8')
    assert storm1_text.validate_value(entry, 'Раз і два') == 'Раз і два'.encode('utf-8')


def test_storm1_missions_accept_any_line_breaks():
    field = SimpleNamespace(key='MI0001', source='One\r\nTwo', is_id=False)
    assert storm1_text.validated_text(field, 'Раз\nДва\nТри') == 'Раз\r\nДва\r\nТри'.encode('utf-8')


def test_storm1_subtitles_accept_any_line_breaks():
    entry = SimpleNamespace(key='T1', source='One\r\nTwo')
    for typed in ('Раз\nДва', 'Раз\\r\\nДва', 'Раз\nДва\nТри', 'Раз два'):
        storm1_text.validate_telop_value(entry, fit_line_breaks(typed, entry.source))


def test_txt_round_trip_of_a_crlf_string(tmp_path):
    """Export a CRLF string, read it back untouched: the game gets its own bytes."""
    from txtfmt import escape_header_source, export_line
    source = 'First line\r\nSecond line\\nthird'
    header = f'### 1 — a · EN: {escape_header_source(source)}'
    lines = [header, *export_line(source), '', '###']
    txt = tmp_path / 'a.txt'
    txt.write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8-sig'))
    value = parse_txt(txt, {'rows': [{'number': 1, 'header': header}]})[1]
    assert value == 'First line\nSecond line\\nthird'        # what the TXT gives
    assert fit_line_breaks(value, source) == source          # what is packed
    assert translation_progress(txt)['done'] == 0            # and it is not a translation
