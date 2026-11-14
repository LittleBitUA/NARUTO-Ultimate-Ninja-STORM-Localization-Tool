"""Interface smoke tests: pages, readiness, both languages, jobs in a separate process."""
import json
import time
import tkinter as tk

import pytest

ctk = pytest.importorskip('customtkinter')

TXT = '### 1 — a · EN: Hi\r\nПривіт\r\n\r\n###\r\n### 2 — b · EN: Bye\r\n\r\n###\r\n'


def make_app(tmp_path, monkeypatch, lang):
    config = tmp_path / 'config'
    (config / 'NarutoSTORM1UATool').mkdir(parents=True, exist_ok=True)
    (config / 'NarutoSTORM1UATool' / 'settings.json').write_text(json.dumps({'lang': lang}), encoding='utf-8')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config))
    monkeypatch.setenv('LOCALAPPDATA', str(config))
    import natool
    try:
        return natool.App()
    except tk.TclError:
        pytest.skip('No display available')


@pytest.fixture()
def app(tmp_path, monkeypatch):
    window = make_app(tmp_path, monkeypatch, 'uk')
    yield window
    window.destroy()


def write_txt(folder):
    import natool
    (folder / natool.text1.TXT_NAME).write_bytes(TXT.encode('utf-8-sig'))


def shown(app, game='s1'):
    return {kind: widget.cget('text') for kind, widget in app.readiness_widgets[game] if kind != 'bar'}


def wait(app):
    for _ in range(600):
        app.update(); app._poll()
        if not app.working:
            return
        time.sleep(0.02)
    raise AssertionError('job did not finish')


def test_pages_and_readiness(app, tmp_path):
    import natool
    assert set(app.pages) == {'home', 's1', 's2', 's3', 'tools', 'about'}
    for key in app.pages:
        app.show(key); app.update()
    write_txt(tmp_path)
    app.p['workspace'].set(str(tmp_path))
    app.readiness = {'s1': natool.translation_progress(app.txt_path('s1')), 's2': None, 's3': None}
    app._paint_readiness()
    assert shown(app) == {'badge': '50,0%', 'percent': '50,0%', 'count': '1 з 2 записів'}
    assert {kind for kind, _ in app.readiness_widgets['s3']} == {'badge', 'percent', 'bar', 'count'}


def test_language_switch_rebuilds_everything(app, tmp_path):
    import i18n
    import natool
    assert i18n.LANG == 'uk' and app.nav['home'][2].cget('text') == 'Огляд'
    write_txt(tmp_path)
    app.p['workspace'].set(str(tmp_path))
    app.readiness = {'s1': natool.translation_progress(app.txt_path('s1')), 's2': None, 's3': None}
    app._log('рядок журналу'); app._poll()
    app.show('s3')
    app._rebuild('en'); app.update()
    assert i18n.LANG == 'en' and app.p['lang'].get() == 'en'
    assert app.nav['home'][2].cget('text') == 'Overview' and app._current == 's3'
    assert shown(app) == {'badge': '50.0%', 'percent': '50.0%', 'count': '1 of 2 records'}
    assert 'рядок журналу' in app.log_box.get('1.0', 'end')
    assert all(control.winfo_exists() for control in app.controls)
    app._rebuild('uk'); app.update()
    assert app.nav['home'][2].cget('text') == 'Огляд'


def test_english_from_settings(tmp_path, monkeypatch):
    window = make_app(tmp_path, monkeypatch, 'en')
    try:
        assert window.nav['about'][2].cget('text') == 'About'
        assert ctk.get_appearance_mode() == 'Dark'
    finally:
        window.destroy()


def test_jobs_run_in_a_separate_process_and_buttons_are_never_restyled(app, monkeypatch):
    """Buttons went blank on Windows while a job ran in a thread and every
    button was restyled. Jobs now run in their own process and the buttons are
    left exactly as they are."""
    import jobs
    import natool
    errors = []
    monkeypatch.setattr(natool.messagebox, 'showerror', lambda title, text: errors.append(text))
    restyled = []
    for control in app.controls:
        monkeypatch.setattr(control, 'configure', lambda *a, **k: restyled.append(k))

    def finish(mode, done=None):
        app.run('test ' + mode, 'jobs.selftest', mode, done=done, log=jobs.LOG, progress=jobs.PROGRESS)
        assert app.working and app._process is not None
        wait(app)
        assert not app._spin and app._process is None
        return app.log_box.get('1.0', 'end')

    log = finish('ok')
    assert 'selftest: ok' in log and 'ok: 1' in log and not errors
    finish('fail')                                         # backend error
    assert 'job failed' in errors[-1]
    finish('crash')                                        # process dies without a result
    assert len(errors) == 2 and '3' in errors[-1]
    assert 'mode: print' in finish('print')                # stray print() in a backend
    assert 'ZeroDivisionError' in finish('ok', done=lambda result: 1 / 0)
    finish('ok', done=app._font_done('s1'))                # preview file is missing
    finish('ok')
    assert len(errors) == 2 and restyled == []


def test_second_click_while_running_is_ignored(app):
    import jobs
    app.run('first', 'jobs.selftest', 'ok', log=jobs.LOG)
    first = app._process
    app.run('second', 'jobs.selftest', 'ok')
    assert app._process is first and app.detail.get()
    wait(app)


def test_summary_never_dumps_big_tables():
    import natool
    text = natool.summarize({'ok': 1, 'metrics': {str(i): list(range(40)) for i in range(50)}, 'empty': ''})
    assert 'ok: 1' in text and '50' in text and 'empty' not in text
