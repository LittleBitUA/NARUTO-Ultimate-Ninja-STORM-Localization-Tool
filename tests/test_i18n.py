"""Both interface languages: detection, switching, and language-independent behaviour."""
import ast
import re
from pathlib import Path

import pytest

import i18n

ROOT = Path(__file__).resolve().parents[1]
MODULES = sorted(p for p in ROOT.glob('*.py'))
CYRILLIC = re.compile('[а-яіїєґА-ЯІЇЄҐ]')


@pytest.fixture(autouse=True)
def restore_language():
    before = i18n.LANG
    yield
    i18n.set_language(before)


def test_switching():
    assert i18n.set_language('uk') == 'uk' and i18n.T('так', 'yes') == 'так'
    assert i18n.set_language('en') == 'en' and i18n.T('так', 'yes') == 'yes'
    assert i18n.set_language('') in ('uk', 'en')          # follows the system


def test_detection_from_environment(monkeypatch):
    if i18n.os.name == 'nt':
        pytest.skip('Windows uses the display language API')
    for name in ('LC_ALL', 'LC_MESSAGES', 'LANGUAGE', 'LANG'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('LANG', 'uk_UA.UTF-8')
    assert i18n.detect() == 'uk'
    monkeypatch.setenv('LANG', 'en_US.UTF-8')
    assert i18n.detect() == 'en'


@pytest.mark.parametrize('path', MODULES, ids=lambda p: p.name)
def test_every_pair_is_well_formed(path):
    """English side has no Cyrillic and the same {placeholders} as the Ukrainian side."""
    source = path.read_text(encoding='utf-8')
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'T'):
            continue
        assert len(node.args) == 2 and not node.keywords, f'{path.name}:{node.lineno}'
        uk, en = node.args
        english = ''.join(c.value for c in ast.walk(en) if isinstance(c, ast.Constant) and isinstance(c.value, str))
        allowed = english.replace('«р»', '').replace('«у»', '').replace('Ґ/ґ', '')
        assert not CYRILLIC.search(allowed), f'{path.name}:{node.lineno}: {english[:60]}'
        fields = lambda side: sorted(ast.dump(v.value) for v in ast.walk(side) if isinstance(v, ast.FormattedValue))
        assert fields(uk) == fields(en), f'{path.name}:{node.lineno}'


@pytest.mark.parametrize('lang', ['uk', 'en'])
def test_texture_error_kind_does_not_depend_on_language(tmp_path, lang):
    import textures
    i18n.set_language(lang)
    error = textures.TextureError(i18n.T('Захист активний', 'Protection is on'), kind='protected')
    assert error.kind == 'protected' and str(error)
    assert textures.TextureError('x').kind == ''


@pytest.mark.parametrize('lang,word', [('uk', 'роздільник'), ('en', 'separator')])
def test_backend_errors_follow_the_language(tmp_path, lang, word):
    import txtfmt
    i18n.set_language(lang)
    txt = tmp_path / 'a.txt'
    header = '### 1 — a · EN: Hi'
    txt.write_bytes((header + '\r\nтекст\r\n###\r\n').encode('utf-8-sig'))
    with pytest.raises(txtfmt.TxtError) as caught:
        txtfmt.parse_txt(txt, {'rows': [{'number': 1, 'header': header}]}, autofix=False)
    assert word in str(caught.value)
