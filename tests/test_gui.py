"""The application host, driven over its local HTTP interface (no window needed)."""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

TXT = '### 1 — a · EN: Hi\r\nПривіт\r\n\r\n###\r\n### 2 — b · EN: Bye\r\n\r\n###\r\n'


class Client:
    def __init__(self, url):
        self.base, self.token = url.split('/?t=')

    def call(self, path, body=None, token=None, host=None):
        request = urllib.request.Request(self.base + path, headers={'X-Token': self.token if token is None else token})
        if host:
            request.add_header('Host', host)
        if body is not None:
            request.data = json.dumps(body).encode('utf-8')
            request.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(request, timeout=10) as reply:
            raw = reply.read()
            return json.loads(raw) if reply.headers.get_content_type() == 'application/json' else raw

    def state(self):
        return self.call('/api/state?log=0&epoch=-1')

    def wait(self):
        for _ in range(600):
            state = self.state()
            if not state['working']:
                return state
            time.sleep(0.05)
        raise AssertionError('job did not finish')


@pytest.fixture()
def host(tmp_path, monkeypatch):
    def start(**settings):
        config = tmp_path / 'config'
        (config / 'NarutoSTORM1UATool').mkdir(parents=True, exist_ok=True)
        (config / 'NarutoSTORM1UATool' / 'settings.json').write_text(
            json.dumps({'lang': 'uk', **settings}), encoding='utf-8')
        monkeypatch.setenv('XDG_CONFIG_HOME', str(config))
        monkeypatch.setenv('LOCALAPPDATA', str(config))
        import natool
        app = natool.App()
        server, url = natool.serve(app, port=0)
        servers.append(server)
        return app, Client(url)
    servers = []
    yield start
    for server in servers:
        server.shutdown(); server.server_close()


def write_txt(folder):
    import natool
    (folder / natool.text1.TXT_NAME).write_bytes(TXT.encode('utf-8-sig'))


def button(ui, label):
    """Action id of the button with this label."""
    import natool
    for page in ui['pages']:
        for node in natool.walk(page):
            for item in node.get('buttons', []):
                if item['label'] == label:
                    return item['id']
    raise AssertionError(label)


def test_page_description_in_both_languages(host):
    import natool
    app, client = host()
    ui = client.call('/api/ui')
    assert [p['key'] for p in ui['pages']] == ['home', 's1', 's2', 's3', 'tools', 'about']
    assert ui['version'] == natool.VERSION and ui['lang'] == 'uk'
    assert [item.get('label') for item in ui['nav'] if 'key' in item][:2] == ['Огляд', 'STORM 1']
    assert 'нінджя' in json.dumps(ui, ensure_ascii=False) and 'ніндзя' not in json.dumps(ui, ensure_ascii=False)
    kinds = {node.get('type') for page in ui['pages'] for node in natool.walk(page)}
    assert {'overview', 'steps', 'tabs', 'card', 'path', 'actions', 'check', 'more', 'ttf', 'chars', 'preview', 'about'} <= kinds
    client.call('/api/lang', {'lang': 'en'})
    english = client.call('/api/ui')
    assert english['lang'] == 'en' and english['nav'][0]['label'] == 'Overview'
    assert english['strings']['stop'] == 'Stop' and client.state()['status'].startswith('Ready')
    saved = json.loads(natool.config_path().read_text(encoding='utf-8'))
    assert saved['lang'] == 'en'


def test_only_the_program_itself_may_use_the_api(host):
    app, client = host()
    for call in (lambda: client.call('/api/state', token='wrong'),
                 lambda: client.call('/api/action', {'id': 'a1'}, token=''),
                 lambda: client.call('/api/state', host='evil.example')):
        with pytest.raises(urllib.error.HTTPError) as caught:
            call()
        assert caught.value.code == 403
    assert b'NARUTO UA' in client.call('/')                 # the page itself needs no token
    assert b'@font-face' in client.call('/app.css') and client.call('/fonts/ReggaeC-UA.ttf')[:4] == b'\x00\x01\x00\x00'
    with pytest.raises(urllib.error.HTTPError) as caught:
        client.call('/../natool.py')
    assert caught.value.code == 404


def test_readiness_paths_and_validation(host, tmp_path):
    app, client = host()
    write_txt(tmp_path)
    missing = str(tmp_path / 'missing.xfbin')
    client.call('/api/set', {'paths': {'workspace': str(tmp_path), 'font_source': missing, 'lang': 'en'},
                             'flags': {'prefill': True}})
    for _ in range(100):
        state = client.state()
        if state['games']['s1']['percent'] is not None:
            break
        time.sleep(0.05)
    game = state['games']['s1']
    assert game['percent'] == 50.0 and game['label'] == '50,0%' and game['count'] == '1 з 2 записів'
    assert 'змінено' in game['stamp'] and state['games']['s2']['percent'] is None
    assert state['lang'] == 'uk'                               # the language is not a path
    assert state['flags']['prefill'] is True and state['paths']['workspace'] == str(tmp_path)
    assert 'font_source' in state['bad'] and 'workspace' not in state['bad']
    real = tmp_path / 'font.xfbin'; real.write_bytes(b'x')
    client.call('/api/set', {'paths': {'font_source': str(real)}})
    assert 'font_source' not in client.state()['bad']


def test_font_source_choice(host, tmp_path):
    import natool
    app, client = host()
    state = client.state()
    assert state['bundled'] and state['ttf'] == {'ttf_mode_s1': 'bundled', 'ttf_mode_s2': 'bundled', 'ttf_mode_s3': 'bundled'}
    assert app.ttf_path('ttf_source', 'ttf_mode_s1') == str(natool.BUNDLED_TTF)
    own = tmp_path / 'mine.ttf'; own.write_bytes(b'x')
    client.call('/api/set', {'paths': {'ttf_mode_s1': 'own', 'ttf_source': str(own)}})
    assert client.state()['ttf']['ttf_mode_s1'] == 'own' and app.ttf_path('ttf_source', 'ttf_mode_s1') == str(own)
    client.call('/api/set', {'paths': {'ttf_source': str(tmp_path / 'gone.ttf')}})
    assert 'ttf_source' in client.state()['bad']
    assert app.p['s1_chars'].get() == natool.font1.UKRAINIAN_ALPHABET + '—'


def test_actions_ask_before_they_act(host, tmp_path):
    app, client = host()
    ui = client.call('/api/ui')
    reply = client.call('/api/action', {'id': button(ui, 'Стандартні шляхи')})
    assert set(reply['ask']) == {'title', 'text', 'key'}
    client.call('/api/set', {'paths': {'workspace': str(tmp_path)}})
    client.call('/api/action', {'id': button(ui, 'Стандартні шляхи'), 'confirmed': ['wrong']})
    assert client.state()['paths']['workspace'] == str(tmp_path)          # not confirmed: nothing changed
    assert client.call('/api/action', {'id': button(ui, 'Стандартні шляхи'), 'confirmed': [reply['ask']['key']]}) == {'ok': True}
    assert client.state()['paths']['workspace'] != str(tmp_path)
    assert client.call('/api/action', {'id': 'nope'}) == {'error': 'unknown action'}
    notices = []
    client.call('/api/open', {'game': 's2'})                              # no TXT yet: a warning, not a crash
    notices += client.state()['notices']
    assert notices and notices[0]['kind'] == 'warning'


def test_jobs_run_in_a_separate_process(host):
    import jobs
    app, client = host()

    def finish(mode, done=None):
        with app.lock:
            app.run('test ' + mode, 'jobs.selftest', mode, done=done, log=jobs.LOG, progress=jobs.PROGRESS)
        assert app.working and app._process is not None
        running = client.state()
        assert running['working'] and running['dot'] == 'running' and 'test ' + mode in running['status']
        return client.wait()

    state = finish('ok')
    assert state['dot'] == 'good' and 'Місію виконано' in state['status'] and 'Тривалість' in state['detail']
    log = '\n'.join(state['log']['lines'])
    assert 'selftest: ok' in log and 'ok: 1' in log and not state['notices']
    state = finish('fail')
    assert state['dot'] == 'bad' and state['notices'][0]['kind'] == 'error' and 'job failed' in state['notices'][0]['text']
    state = finish('crash')                                   # the process dies without a result
    assert state['dot'] == 'bad' and '3' in state['notices'][0]['text']
    assert 'mode: print' in '\n'.join(finish('print')['log']['lines'])
    state = finish('ok', done=lambda result: 1 / 0)           # a failing callback must not break the host
    assert not state['working'] and 'ZeroDivisionError' in '\n'.join(state['log']['lines'])
    state = finish('ok', done=app._font_done('s1'))           # preview file is missing: nothing to show
    assert state['previews'] == {} and state['dot'] == 'good'


def test_second_start_is_ignored_and_stop_ends_the_job(host):
    app, client = host()
    with app.lock:
        app.run('long job', 'jobs.selftest', 'sleep')
        first = app._process
        app.run('second', 'jobs.selftest', 'ok')
    assert app._process is first and client.state()['detail']
    time.sleep(0.6)
    assert '0:0' in client.state()['status']                  # the running clock
    reply = client.call('/api/action', {'id': 'stop'})
    client.call('/api/action', {'id': 'stop', 'confirmed': [reply['ask']['key']]})
    state = client.wait()
    assert 'зупинено' in state['status'] and state['dot'] == 'idle' and not state['notices']
    assert 'ЗУПИНЕНО: long job' in '\n'.join(state['log']['lines'])


def test_log_cursor_and_clear(host):
    app, client = host()
    app._log('перший'); app._log('другий\nтретій')
    first = client.call('/api/state?log=0&epoch=-1')['log']
    assert first['lines'] == ['перший', 'другий', 'третій'] and first['cursor'] == 3
    assert client.call(f'/api/state?log=3&epoch={first["epoch"]}')['log']['lines'] == []
    app._log('четвертий')
    assert client.call(f'/api/state?log=3&epoch={first["epoch"]}')['log']['lines'] == ['четвертий']
    client.call('/api/log', {})
    cleared = client.call(f'/api/state?log=4&epoch={first["epoch"]}')['log']
    assert cleared['lines'] == [] and cleared['epoch'] != first['epoch'] and cleared['cursor'] == 0


def test_backup_tag_check_and_found_games(host, tmp_path, monkeypatch):
    import natool
    app, client = host()
    ui = client.call('/api/ui')
    write_txt(tmp_path)
    client.call('/api/set', {'paths': {'workspace': str(tmp_path)}})
    client.call('/api/action', {'id': button(ui, 'Резервна копія TXT')})
    copies = list((tmp_path / 'backups').iterdir())
    assert len(copies) == 1 and (copies[0] / natool.text1.TXT_NAME).is_file()
    client.call('/api/action', {'id': button(ui, 'Перевірити теги')})
    state = client.wait()
    assert state['wantLog'] is False                          # delivered once, with the first state after the click
    assert 'перевірено перекладених записів: 1' in '\n'.join(state['log']['lines'])
    root = tmp_path / 'common' / 'QWANT'
    (root / natool.steam.DATA).mkdir(parents=True)
    monkeypatch.setattr(natool.steam, 'find_games', lambda: {'s1': root})
    reply = client.call('/api/action', {'id': ui['findGames']})
    assert 'STORM 1: знайдено' in reply['ask']['text'] and 'STORM 2: не знайдено' in reply['ask']['text']
    client.call('/api/action', {'id': ui['findGames'], 'confirmed': [reply['ask']['key']]})
    assert client.state()['paths']['game'] == str(root)


def test_last_page_is_remembered(host):
    import natool
    app, client = host(page='s2')
    assert client.state()['page'] == 's2'
    client.call('/api/page', {'page': 's3'})
    assert json.loads(natool.config_path().read_text(encoding='utf-8'))['page'] == 's3'


def test_preview_is_served(host, tmp_path):
    app, client = host()
    picture = tmp_path / 'p.png'; picture.write_bytes(b'\x89PNG fake')
    app._font_done('s1')({'preview': str(picture)})
    assert client.state()['previews'] == {'s1': 1}
    assert client.call('/api/preview?key=s1') == b'\x89PNG fake'
    with pytest.raises(urllib.error.HTTPError):
        client.call('/api/preview?key=s2')


def test_summary_never_dumps_big_tables():
    import natool
    text = natool.summarize({'ok': 1, 'metrics': {str(i): list(range(40)) for i in range(50)}, 'empty': ''})
    assert 'ok: 1' in text and '50' in text and 'empty' not in text


def test_web_files_exist_and_use_no_network():
    web = Path(__file__).resolve().parents[1] / 'web'
    for name in ('index.html', 'app.css', 'app.js', 'fonts/ReggaeC-UA.ttf'):
        assert (web / name).is_file(), name
    for name in ('index.html', 'app.css', 'app.js'):
        text = (web / name).read_text(encoding='utf-8')
        assert 'https://' not in text and 'http://' not in text, name      # the interface never goes online


def test_the_window_has_one_fixed_size(host):
    import natool
    app, client = host()
    assert client.call('/api/ui')['window'] == {'w': natool.WINDOW_SIZE[0], 'h': natool.WINDOW_SIZE[1]}


def test_a_resized_or_maximized_window_is_forgotten(tmp_path):
    """The browser keeps the last window; before each start its size is put back."""
    import natool
    prefs = tmp_path / 'Default' / 'Preferences'
    prefs.parent.mkdir()
    saved = {'browser': {'app_window_placement': {'_127.0.0.1_/': {
        'left': 300, 'top': 120, 'right': 2220, 'bottom': 1160, 'maximized': True, 'work_area_left': 0}}},
        'other': {'kept': 'as it was'}}
    prefs.write_text(json.dumps(saved), encoding='utf-8')
    natool.reset_window_placement(tmp_path)
    data = json.loads(prefs.read_text(encoding='utf-8'))
    place = data['browser']['app_window_placement']['_127.0.0.1_/']
    width, height = natool.WINDOW_SIZE
    assert (place['left'], place['top']) == (300, 120)                       # where it stood is kept
    assert (place['right'], place['bottom'], place['maximized']) == (300 + width, 120 + height, False)
    assert place['work_area_left'] == 0 and data['other'] == {'kept': 'as it was'}

    before = prefs.read_bytes()
    natool.reset_window_placement(tmp_path)                                   # nothing to change: not rewritten
    assert prefs.read_bytes() == before

    for broken in ('not json', '{"browser": 5}', '{"browser": {"app_window_placement": {"x": {"left": "?"}}}}'):
        prefs.write_text(broken, encoding='utf-8')
        natool.reset_window_placement(tmp_path)                               # unknown layout: left alone
        assert prefs.read_text(encoding='utf-8') == broken
    natool.reset_window_placement(tmp_path / 'no such profile')


def test_the_window_always_opens_at_the_fixed_size(tmp_path, monkeypatch):
    import natool
    started = []
    monkeypatch.setattr(natool, 'find_browser', lambda: Path('browser.exe'))
    monkeypatch.setattr(natool, 'config_path', lambda: tmp_path / 'settings.json')
    monkeypatch.setattr(natool.subprocess, 'Popen', lambda command: started.append(command) or 'process')
    (tmp_path / 'window').mkdir()                                             # not the first start
    assert natool.open_window('http://127.0.0.1:1/?t=x') == 'process'
    assert '--window-size={},{}'.format(*natool.WINDOW_SIZE) in started[0]


def test_every_tab_keeps_to_two_columns(host):
    """Cards carry the column they belong to; the page lays a tab out from that."""
    app, client = host()
    pages = client.call('/api/ui')['pages']
    tabs = [tab for page in pages for node in page['children'] if node['type'] == 'tabs' for tab in node['tabs']]
    assert tabs and all(child.get('col', 1) in (1, 2) for tab in tabs for child in tab['children'])
    assert [tab['title'] for page in pages if page['key'] == 's1' for node in page['children'] for tab in node['tabs']][-1] in ('Атлас', 'Atlas')
