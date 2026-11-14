"""The job process protocol, without any window."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_job(target, *args, lang='en', **kwargs):
    spec = json.dumps({'lang': lang, 'target': target, 'args': args, 'kwargs': kwargs}, ensure_ascii=False)
    done = subprocess.run([sys.executable, str(ROOT / 'jobs.py')], input=spec.encode('utf-8'),
                          capture_output=True, cwd=str(ROOT), timeout=120)
    return [json.loads(line) for line in done.stdout.decode('utf-8').splitlines()], done


def test_messages_and_result():
    import jobs
    lines, done = run_job('jobs.selftest', 'print', log=jobs.LOG, progress=jobs.PROGRESS)
    assert lines == [['log', 'selftest: print'], ['progress', [1, 2, 'half']],
                     ['success', {'ok': 1, 'mode': 'print', 'preview': 'missing.png'}]]
    assert b'stray output' in done.stderr and done.returncode == 0


def test_backend_error_is_reported_in_the_chosen_language(tmp_path):
    header = '### 1 — a · EN: Hi'
    (tmp_path / 'a.txt').write_bytes((header + '\r\nтекст\r\n###\r\n').encode('utf-8-sig'))
    manifest = {'rows': [{'number': 1, 'header': header}]}
    for lang, word in (('uk', 'роздільник'), ('en', 'separator')):
        lines, _ = run_job('txtfmt.parse_txt', str(tmp_path / 'a.txt'), manifest, lang=lang, autofix=False)
        assert lines[-1][0] == 'error' and word in lines[-1][1]


def test_non_json_values_survive(tmp_path):
    """Results with paths, tuples and bytes-free objects come back as plain data."""
    txt = tmp_path / 'p.txt'
    txt.write_bytes('### 1 — a · EN: Hi\r\nПривіт\r\n\r\n###\r\n'.encode('utf-8-sig'))
    lines, _ = run_job('txtfmt.translation_progress', str(txt))
    assert lines == [['success', {'done': 1, 'total': 1, 'percent': 100.0}]]


@pytest.mark.parametrize('target', ['jobs.s1_font_inspect', 'storm1_text.export_project'])
def test_missing_input_is_an_error_not_a_crash(target, tmp_path):
    lines, done = run_job(target, str(tmp_path / 'missing'), str(tmp_path / 'out'))
    assert lines and lines[-1][0] == 'error' and done.returncode == 0


def test_every_job_started_by_the_interface_matches_its_backend_function():
    """Each self.run(label, 'module.function', ...) must name a real function
    and pass arguments that function accepts."""
    import ast
    import importlib
    import inspect
    source = (ROOT / 'natool.py').read_text(encoding='utf-8')
    checked = 0
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'run'
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self'):
            continue
        assert isinstance(node.args[1], ast.Constant), f'line {node.lineno}: target must be a literal'
        module, _, name = node.args[1].value.rpartition('.')
        function = getattr(importlib.import_module(module), name)
        positional = [object()] * (len(node.args) - 2)
        keywords = {k.arg: object() for k in node.keywords if k.arg != 'done'}
        inspect.signature(function).bind(*positional, **keywords)      # raises TypeError on a mismatch
        checked += 1
    assert checked >= 30


def test_real_font_build_through_the_job_process(storm1_font_file, ttf, tmp_path):
    """The whole path the «Створити шрифт» button takes, minus the window."""
    import jobs
    lines, done = run_job('storm1_font.generate_additive_font', str(storm1_font_file), str(ttf),
                          str(tmp_path / 'work'), str(tmp_path / 'build'), 'font/xfgame_cmn.fnt.xfbin',
                          include_symbols=True, include_other_symbols=False, override=True, log=jobs.LOG,
                          latin_p_for_r=True, latin_y_for_u=True, metrics_mode='native_matched', lang='uk')
    kind, result = lines[-1]
    assert kind == 'success', lines[-1]
    assert Path(result['preview']).is_file() and Path(result['new_font']).is_file()
    assert result['added_glyph_count'] == 67 and any(line[0] == 'log' for line in lines)
    assert done.returncode == 0
