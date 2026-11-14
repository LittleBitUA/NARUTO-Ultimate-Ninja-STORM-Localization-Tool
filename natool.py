#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NARUTO UA Toolkit — application host.

The interface is a web page (web/index.html, app.css, app.js) shown in its own
window by the Chromium engine that ships with Windows (Edge). This file is the
other half: it keeps the settings, describes the pages, starts every operation
in a separate process (jobs.py) and serves both to the page over a local,
token-protected HTTP connection on 127.0.0.1.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import i18n
from i18n import T

import audit
import jobs
import steam
import storm1_font as font1
import storm1_text as text1
import storm2_text as text2
import storm3_text as text3
from txtfmt import backup_project, translation_progress

VERSION = '5.0.0'
APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / 'web'
BUNDLED_TTF = WEB_DIR / 'fonts' / 'ReggaeC-UA.ttf'      # the team's font: interface headings and default letters
PORT = 47815                                # preferred; a free one is used if it is taken
AUTHOR = 'Dmytro Bidlov («Little Bit» Team)'
TELEGRAM = 't.me/LittleBitUA'
GITHUB = 'https://github.com/LittleBitUA/NARUTO-Ultimate-Ninja-STORM-Localization-Tool'
S1 = r'E:\SteamLibrary\steamapps\common\QWANT\data\disc\dataRegion_extracted\data'
S2 = r'E:\SteamLibrary\steamapps\common\Qwant2\data\disc\dataRegion_extracted\data'
LOC = r'E:\Localization\Naruto'

# Keys and file location are unchanged from v2/v3, so saved paths carry over.
DEFAULT_PATHS = {
    'eng': text1.DEFAULT_SOURCE, 'telop': text1.DEFAULT_TELOP,
    'workspace': text1.DEFAULT_WORKSPACE, 'output': text1.DEFAULT_OUTPUT, 'game': text1.GAME_ROOT,
    'texture_source': S1, 'texture_scan_root': S1 + r'\interface',
    'adv_title_scan_root': S1 + r'\adv_title',
    'texture_workspace': LOC + r'\Storm1\Textures_Workspace', 'texture_rel': '',
    'font_workspace': LOC + r'\Storm1\Font_Workspace', 'font_source': '', 'font_rel': '', 'ttf_source': '',
    'storm2_eng': text2.DEFAULT_ENG, 'storm2_workspace': text2.DEFAULT_WORKSPACE,
    'storm2_output': text2.DEFAULT_BUILD,
    'storm2_ui_root': S2 + r'\ui', 'storm2_pt_root': S2 + r'\pt',
    'storm2_texture_workspace': LOC + r'\Storm2\Textures_Workspace',
    'storm2_font_source': '', 'storm2_font_workspace': LOC + r'\Storm2\Font_Workspace',
    'storm2_font_rel': 'ui/font/xfgame_cmn.fnt.xfbin', 'storm2_font_repair_source': '',
    'storm2_ttf_source': '',
    'storm3_game': r'E:\SteamLibrary\steamapps\common\NARUTO SHIPPUDEN Ultimate Ninja STORM 3 Full Burst',
    'storm3_eng': '', 'storm3_hd1': '', 'storm3_hd2': '',
    'storm3_workspace': text3.DEFAULT_WORKSPACE, 'storm3_build': text3.DEFAULT_BUILD,
    'storm3_output': audit.DEFAULT_OUTPUT,
    'storm3_font_a': '', 'storm3_font_b': '', 'storm3_font_ttf': '',
    'storm3_font_work': LOC + r'\Storm3\Font_Workspace',
    'patch_scope_storm1': S1, 'patch_scope_storm2': S2,
    'patch_scope_report': LOC + r'\Patch_Coverage_Reports',
    'lang': '',            # '' = follow the system language
    's1_chars': font1.UKRAINIAN_ALPHABET + '—',     # characters to create in the STORM 1 font
    'ttf_mode_s1': '', 'ttf_mode_s2': '', 'ttf_mode_s3': '',   # 'bundled' | 'own'; '' = bundled when present
    'page': 'home',        # last opened page
}

GAMES = {
    's1': ('STORM 1', 'Ultimate Ninja STORM', 'workspace', text1.TXT_NAME),
    's2': ('STORM 2', 'Shippuden: Ultimate Ninja STORM 2', 'storm2_workspace', text2.TXT_NAME),
    's3': ('STORM 3', 'Full Burst + HD', 'storm3_workspace', text3.TXT),
}


# Folders the tool creates itself: a missing one is not a mistake.
OUTPUT_KEYS = {'workspace', 'output', 'texture_workspace', 'font_workspace', 'storm2_workspace', 'storm2_output',
               'storm2_texture_workspace', 'storm2_font_workspace', 'storm3_workspace', 'storm3_build',
               'storm3_output', 'storm3_font_work', 'patch_scope_report'}
WORKSPACE_KEYS = {game[2] for game in GAMES.values()}


def config_path() -> Path:
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return base / 'NarutoSTORM1UATool' / 'settings.json'


def saved_paths() -> dict[str, str]:
    result = DEFAULT_PATHS.copy()
    try:
        content = json.loads(config_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return result
    if isinstance(content, dict):
        for key in result:
            value = content.get(key)
            if isinstance(value, str) and (value.strip() or DEFAULT_PATHS[key] == ''):
                result[key] = value
    return result


def open_path(path) -> None:
    path = str(path)
    if os.name == 'nt':
        os.startfile(path)                             # noqa: S606
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


def summarize(result, limit: int = 2400) -> str:
    """Readable log text for a backend result; never dumps per-glyph tables."""
    if isinstance(result, dict):
        lines = []
        for key, value in result.items():
            if isinstance(value, (dict, list, tuple)):
                if not value:
                    continue
                if len(str(value)) > 160:
                    value = T(f'[{len(value)} елементів]', f'[{len(value)} items]')
            elif value in (None, ''):
                continue
            lines.append(f'  {key}: {value}')
        text = '\n'.join(lines)
    else:
        text = str(result)
    return text if len(text) <= limit else text[:limit].rstrip() + T('\n  … (повні дані — у JSON-звіті)', '\n  … (full data is in the JSON report)')


def fmt_percent(info) -> str:
    if not info or not info['total']:
        return '—'
    return f'{info["percent"]:.1f}%'.replace('.', T(',', '.'))


def fmt_time(seconds) -> str:
    seconds = int(seconds)
    return f'{seconds // 60}:{seconds % 60:02d}'


def fmt_count(info) -> str:
    done, total = f'{info["done"]:,}', f'{info["total"]:,}'
    return T(f'{done} з {total} записів'.replace(',', ' '), f'{done} of {total} records')



class Ask(Exception):
    """An action needs a yes/no answer before it can go on."""

    def __init__(self, title, text, key):
        super().__init__(text)
        self.title, self.text, self.key = title, text, key


class Var:
    """A setting with the get()/set() interface the action code expects."""

    def __init__(self, value, changed=None):
        self._value, self._changed = value, changed

    def get(self):
        return self._value

    def set(self, value):
        if value != self._value:
            self._value = value
            if self._changed:
                self._changed()


class Dialogs:
    """Stands in for message boxes: questions and notices are shown by the page."""

    def __init__(self):
        self.local = threading.local()
        self.app = None

    def askyesno(self, title, text):
        key = hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]
        if key in getattr(self.local, 'confirmed', ()):
            return True
        raise Ask(title, text, key)

    def _notice(self, kind, title, text):
        self.app.notify(kind, title, text)

    def showerror(self, title, text):
        self._notice('error', title, text)

    def showwarning(self, title, text):
        self._notice('warning', title, text)

    def showinfo(self, title, text):
        self._notice('info', title, text)


messagebox = Dialogs()


class App:
    def __init__(self):
        paths = saved_paths()
        i18n.set_language(paths['lang'])
        self.lock = threading.RLock()
        messagebox.app = self
        self._current = paths.pop('page')
        self.p = {key: Var(value, self._paths_changed) for key, value in paths.items()}
        self.flags = {name: Var(value) for name, value in (
            ('prefill', False), ('replace', False), ('s1_p', True), ('s1_y', True),
            ('s1_native_metrics', True), ('s2_twins', True), ('s3_twins', True))}
        self.status = Var(T('Готово. Оберіть місію ліворуч.', 'Ready. Pick a mission on the left.'))
        self.detail = Var('')
        self.dot = 'idle'                       # idle | running | good | bad
        self.working = False
        self._process = None
        self._stopping = False
        self._started = 0.0
        self._running = ''
        self.progress = None                    # None = indeterminate, else 0..1
        self.readiness: dict = {}
        self._scan_busy = False
        self._scan_again = False
        self._previews: dict = {}               # font preview: key -> (path, version)
        self.log_lines: list[str] = []
        self.log_epoch = 0
        self.notices: list[dict] = []
        self._notice_id = 0
        self.want_log = False
        self.last_seen = time.monotonic()
        self.closed = False
        self._bad_cache = (0.0, [])
        self.build_ui()
        self.refresh_readiness()

    # ---------------------------------------------------------------- page schema
    # The pages are described once, here; the web page only draws what it is given.
    def build_ui(self):
        self._actions: dict = {}
        self.pages: list = []
        self._page_home(); self._page_s1(); self._page_s2(); self._page_s3(); self._page_tools()
        self._page_about()
        if self._current not in {page['key'] for page in self.pages}:
            self._current = 'home'

    def _register(self, command) -> str:
        name = f'a{len(self._actions) + 1}'
        self._actions[name] = command
        return name

    def _page(self, key, title, subtitle, game=None):
        page = {'key': key, 'title': title, 'subtitle': subtitle, 'game': game, 'children': []}
        self.pages.append(page)
        return page

    def _tabs(self, page, names):
        tabs = [{'title': name, 'children': []} for name in names]
        page['children'].append({'type': 'tabs', 'tabs': tabs})
        return tabs

    def card(self, parent, title=None, hint=None):
        node = {'type': 'card', 'title': title, 'hint': hint, 'children': []}
        parent['children'].append(node)
        return node

    def path(self, parent, caption, key, kind='dir'):
        parent['children'].append({'type': 'path', 'label': caption, 'key': key, 'kind': kind})

    def actions(self, parent, items, pady=None):
        parent['children'].append({'type': 'actions', 'buttons': [
            {'label': text, 'id': self._register(command), 'kind': kind} for text, command, kind in items]})

    def check(self, parent, text, flag):
        parent['children'].append({'type': 'check', 'label': text, 'key': flag})

    def note(self, parent, text, warn=False):
        parent['children'].append({'type': 'note', 'text': text, 'warn': warn})

    def more(self, parent, title=None):
        node = {'type': 'more', 'title': title or T('Шляхи та параметри', 'Paths and options'), 'children': []}
        parent['children'].append(node)
        return node

    def ttf(self, parent, key, mode_key):
        """Where the letters come from: the bundled team font or the user's own TTF / OTF."""
        parent['children'].append({
            'type': 'ttf', 'key': key, 'mode': mode_key,
            'label': T('Звідки брати літери', 'Where the letters come from'),
            'bundled': T('шрифт перекладу Little Bit — уже є в програмі', 'the Little Bit translation font — already in the program'),
            'own': T('Свій шрифт TTF / OTF', 'My own TTF / OTF font'),
            'placeholder': T('Оберіть файл .ttf або .otf', 'Choose a .ttf or .otf file'),
            'hint': T('Якщо ReggaeC UA у вас немає, оберіть будь-який шрифт з українськими літерами.',
                      'If you do not have ReggaeC UA, choose any font with Ukrainian letters.')})

    def chars(self, parent, key):
        """Free choice of the characters to create, with quick-add sets."""
        parent['children'].append({
            'type': 'chars', 'key': key, 'label': T('Які символи створити', 'Characters to create'),
            'count': [T('символ', 'character'), T('символи', 'characters'), T('символів', 'characters')],
            'sets': [{'label': T('+ Українська абетка', '+ Ukrainian alphabet'), 'add': font1.UKRAINIAN_ALPHABET},
                     {'label': T('+ Тире —', '+ Dash —'), 'add': '—'},
                     {'label': T('+ Лапки « » „ “', '+ Quotes « » „ “'), 'add': '«»„“'},
                     {'label': '+ № ₴ ʼ', 'add': '№₴ʼ'}],
            'clear': T('Очистити', 'Clear'),
            'hint': T('Впишіть будь-які символи. Створюються лише ті, яких ще немає у шрифті гри і які є у вибраному TTF. '
                      'Знаки поза кирилицею — експериментально.',
                      'Type any characters. Only those missing from the game font and present in the chosen TTF '
                      'are created. Signs outside Cyrillic are experimental.')})

    def ttf_mode(self, mode_key) -> str:
        mode = self.p[mode_key].get()
        if mode not in ('bundled', 'own'):
            mode = 'bundled'
        return mode if BUNDLED_TTF.is_file() or mode == 'own' else 'own'

    def ttf_path(self, key, mode_key) -> str:
        """The font file to draw letters from."""
        return str(BUNDLED_TTF) if self.ttf_mode(mode_key) == 'bundled' else self.p[key].get().strip()

    def need_ttf(self, key, mode_key) -> bool:
        if Path(self.ttf_path(key, mode_key) or '?').is_file():
            return True
        messagebox.showwarning(T('Не вистачає даних', 'Missing data'),
                               T('Оберіть шрифт TTF / OTF, з якого брати літери.', 'Choose the TTF / OTF font to take the letters from.'))
        return False

    def preview_slot(self, parent, key, title):
        parent['children'].append({'type': 'preview', 'key': key, 'title': title,
                                   'hint': T('Зображення зібрано з уже запакованого атласу — так гліфи бачить гра.',
                                             'Rendered from the already packed atlas — this is what the game sees.'),
                                   'empty': T('Прев’ю з’явиться після створення шрифту.',
                                              'The preview appears after the font is created.')})

    def show_preview(self, key, path):
        if path and Path(path).is_file():
            self._previews[key] = (str(path), self._previews.get(key, ('', 0))[1] + 1)

    def ui(self) -> dict:
        """Everything the page needs to draw itself in the current language."""
        return {
            'version': VERSION, 'lang': i18n.LANG, 'languages': i18n.LANGUAGES,
            'games': {game: {'name': name, 'full': full} for game, (name, full, *_rest) in GAMES.items()},
            'nav': [{'key': 'home', 'label': T('Огляд', 'Overview'), 'icon': 'scroll'},
                    {'section': T('Місії', 'Missions')},
                    *({'key': game, 'label': name, 'icon': f'plate{number}', 'game': game}
                      for number, (game, (name, *_rest)) in enumerate(GAMES.items(), 1)),
                    {'section': T('Розвідка', 'Recon')},
                    {'key': 'tools', 'label': T('Звірка ресурсів', 'Resource check'), 'icon': 'eye'},
                    {'key': 'about', 'label': T('Про програму', 'About'), 'icon': 'info', 'bottom': True}],
            'pages': self.pages, 'findGames': self._find_games,
            'strings': {
                'brand': T('Toolkit локалізації', 'Localization toolkit'),
                'browse': T('Огляд', 'Browse'), 'log': T('Журнал', 'Log'), 'hide': T('Сховати', 'Hide'),
                'stop': T('Зупинити', 'Stop'), 'copy': T('Копіювати', 'Copy'), 'clear': T('Очистити', 'Clear'),
                'copied': T('Журнал скопійовано.', 'Log copied.'), 'logTitle': T('Журнал операцій', 'Operation log'),
                'logEmpty': T('Тут з’являться повідомлення операцій.', 'Messages from operations appear here.'),
                'yes': T('Так', 'Yes'), 'no': T('Скасувати', 'Cancel'), 'ok': T('Гаразд', 'OK'),
                'progress': T('Готовність перекладу', 'Translation progress'),
                'running': T('Техніка виконується', 'Technique in progress'),
                'open': T('До місії', 'To the mission'), 'openTxt': T('Відкрити TXT', 'Open TXT'),
                'mission': [T('Місія перша', 'Mission one'), T('Місія друга', 'Mission two'), T('Місія третя', 'Mission three')],
                'ready': T('Готовність', 'Progress'), 'findGames': T('Знайти ігри в Steam', 'Find the games in Steam'),
                'author': 'Little Bit Team',
                'paste': T('Вставити', 'Paste'), 'openFolder': T('Відкрити папку', 'Open folder'),
                'missing': T('Такого шляху не існує', 'This path does not exist'),
                'openImage': T('Відкрити PNG', 'Open PNG'),
                'leave': T('Операція ще триває.', 'An operation is still running.'),
                'offline': T('Програму закрито. Запустіть START_WINDOWS.bat знову.',
                             'The program was closed. Start START_WINDOWS.bat again.'),
            },
        }

    # ---------------------------------------------------------------------- pages
    def _page_home(self):
        page = self._page('home', T('Огляд', 'Overview'),
                          T('Стан української локалізації трьох ігор', 'Status of the Ukrainian localization of the three games'))
        page['children'].append({'type': 'overview'})
        page['children'].append({
            'type': 'steps', 'title': T('Шлях нінджя: з чого почати', 'The ninja way: where to start'),
            'steps': [[T('Знайдіть ігри', 'Find the games'),
                       T('Кнопка «Знайти ігри в Steam» сама заповнить шляхи.', '"Find the games in Steam" fills in the paths for you.')],
                      [T('Розгорніть сувій', 'Unroll the scroll'),
                       T('«Експорт TXT» — увесь текст гри в одному файлі.', '"Export TXT" — all the text of a game in one file.')],
                      [T('Виконайте техніку', 'Perform the technique'),
                       T('«Перевірити», потім «Зібрати» — TXT копіюється в backups.', '"Verify", then "Build" — the TXT is copied to backups.')]]})
        self._find_games = self._register(self.detect_games)

    def _page_s1(self):
        page = self._page('s1', 'STORM 1', T('Тексти, Telop, текстури та шрифт першої гри', 'Text, Telop, textures and font of the first game'), 's1')
        texts, tex, font = self._tabs(page, [T('Тексти', 'Text'), T('Текстури', 'Textures'), T('Шрифт', 'Font')])
        c = self.card(texts, T('Сувій перекладу', 'Translation scroll'), T('Діалоги, інтерфейс і Telop в одному TXT.', 'Dialogue, interface and Telop in one TXT.'))
        self.path(c, T('Папка перекладу', 'Translation folder'), 'workspace')
        self.actions(c, [(T('Експорт TXT', 'Export TXT'), self.s1_export, 'soft'), (T('Перевірити', 'Verify'), self.s1_verify, 'soft'),
                         (T('Зібрати й установити', 'Build and install'), self.s1_build, 'primary'),
                         (T('Відкрити TXT', 'Open TXT'), lambda: self.open_txt('s1'), 'ghost')])
        self.actions(c, [(T('Перевірити теги', 'Check tags'), lambda: self.check_tags('s1'), 'ghost'),
                         (T('Резервна копія TXT', 'Back up TXT'), lambda: self.backup('s1'), 'ghost')])
        m = self.more(c)
        self.path(m, T('Англійські ресурси (event\\eng)', 'English resources (event\\eng)'), 'eng')
        self.path(m, 'Telop (interface\\telop\\eng)', 'telop')
        self.path(m, T('Папка Build', 'Build folder'), 'output')
        self.path(m, T('Папка гри', 'Game folder'), 'game')
        self.check(m, T('Підставляти англійський текст під час експорту', 'Prefill with the English text on export'), 'prefill')
        self.check(m, T('Дозволити заміну готових файлів шрифтів і текстур (з копією .bak)', 'Allow replacing built font and texture files (a .bak copy is kept)'), 'replace')
        self.actions(m, [(T('Додати 4 Telop до наявного TXT', 'Add the 4 Telop files to the existing TXT'), self.s1_telop, 'soft'),
                         (T('Стандартні шляхи', 'Default paths'), self.reset_paths, 'ghost')])
        self.note(c, T('Під час установлення створюються резервні копії; оригінальні ресурси не змінюються.', 'Backups are made on install; the original resources are never changed.'))

        c = self.card(tex, T('Інтерфейс', 'Interface'), T('Усі підпапки interface; готові PNG повторний експорт не перезаписує.', 'All interface subfolders; a repeated export never overwrites existing PNGs.'))
        self.path(c, T('Папка interface', 'interface folder'), 'texture_scan_root')
        self.path(c, T('Робочі PNG', 'Working PNGs'), 'texture_workspace')
        self.actions(c, [(T('Експортувати PNG', 'Export PNG'), lambda: self.tex_export('texture_scan_root', 'texture_workspace'), 'soft'),
                         (T('Зібрати змінені PNG', 'Build changed PNGs'), lambda: self.tex_build('texture_scan_root', 'texture_workspace', 'output'), 'primary'),
                         (T('Прибрати старі DDS', 'Remove old DDS'), self.tex_clean, 'ghost')])
        c = self.card(tex, T('Сюжетні титри', 'Story titles'), T('adv_title → PNG → XFBIN у Build.', 'adv_title → PNG → XFBIN in Build.'))
        self.path(c, T('Папка adv_title', 'adv_title folder'), 'adv_title_scan_root')
        self.actions(c, [(T('Експортувати PNG', 'Export PNG'), lambda: self.tex_export('adv_title_scan_root', 'texture_workspace'), 'soft'),
                         (T('Зібрати змінені PNG', 'Build changed PNGs'), lambda: self.tex_build('adv_title_scan_root', 'texture_workspace', 'output'), 'primary')])
        c = self.card(tex, T('Окремий XFBIN', 'Single XFBIN'), T('Розмір PNG під час редагування не змінюйте.', 'Do not change the PNG size while editing.'))
        self.path(c, T('Вихідний XFBIN', 'Source XFBIN'), 'texture_source', 'xfbin')
        self.path(c, T('Шлях у data_win32', 'Path inside data_win32'), 'texture_rel', 'text')
        self.actions(c, [(T('Експортувати PNG', 'Export PNG'), self.tex_one_export, 'soft'), (T('Перевірити', 'Verify'), self.tex_one_inspect, 'soft'),
                         (T('Зібрати PNG', 'Build PNG'), self.tex_one_build, 'primary')])

        c = self.card(font, T('Техніка письма', 'Lettering technique'),
                      T('Українські літери з TTF / OTF. Оригінальні гліфи зберігаються; нові додаються в окремі рядки атласу з проміжками.', 'Ukrainian letters from a TTF / OTF. Original glyphs are kept; new ones go into separate atlas rows with gaps between cells.'))
        self.path(c, T('Оригінальний шрифт гри', 'Original game font'), 'font_source', 'xfbin')
        self.ttf(c, 'ttf_source', 'ttf_mode_s1')
        self.path(c, T('Шлях у data_win32', 'Path inside data_win32'), 'font_rel', 'text')
        self.chars(c, 's1_chars')
        self.actions(c, [(T('Створити шрифт', 'Create font'), self.s1_font_make, 'primary'),
                         (T('Відкрити прев’ю', 'Open preview'), lambda: self.open_preview('s1'), 'ghost')])
        m = self.more(c, T('Печаті техніки', 'Glyph options'))
        self.check(m, T('Мала «р» — форма оригінальної латинської p', 'Lowercase «р» — shape of the original Latin p'), 's1_p')
        self.check(m, T('Мала «у» — форма оригінальної латинської y', 'Lowercase «у» — shape of the original Latin y'), 's1_y')
        self.check(m, T('Метрики за найближчою латинською літерою (інакше — базові A / a)', 'Metrics from the closest Latin letter (otherwise the basic A / a)'), 's1_native_metrics')
        self.note(c, T('Після пакування атлас зчитується назад: якщо біля комірки є сторонні пікселі, шрифт не буде записано.', 'After packing, the atlas is read back: if stray pixels sit next to a cell, the font is not written.'))
        self.preview_slot(font, 's1', T('Як це виглядає у грі', 'How it looks in the game'))
        c = self.card(font, T('Атлас PNG + таблиця Unicode', 'PNG atlas + Unicode table'), T('Ручне редагування: гліфи білі з прозорістю, розмір не змінювати.', 'Manual editing: white glyphs with transparency, keep the size.'))
        self.path(c, T('Робоча папка шрифту', 'Font workspace folder'), 'font_workspace')
        self.actions(c, [(T('Аналізувати', 'Analyze'), self.s1_font_inspect, 'soft'), (T('Експортувати PNG + TSV', 'Export PNG + TSV'), self.s1_font_export, 'soft'),
                         (T('Зібрати зі змінених файлів', 'Build from edited files'), self.s1_font_build, 'soft')])

    def _page_s2(self):
        page = self._page('s2', 'STORM 2', T('messageInfo, текстури та шрифт другої гри', 'messageInfo, textures and font of the second game'), 's2')
        texts, tex, font = self._tabs(page, [T('Тексти', 'Text'), T('Текстури', 'Textures'), T('Шрифт', 'Font')])
        c = self.card(texts, T('Сувій перекладу', 'Translation scroll'), T('Один TXT для messageInfo; збірка тестова, без установлення в гру.', 'One TXT for messageInfo; a test build, nothing is installed into the game.'))
        self.path(c, T('Папка перекладу', 'Translation folder'), 'storm2_workspace')
        self.actions(c, [(T('Експорт TXT', 'Export TXT'), self.s2_export, 'soft'), (T('Перевірити', 'Verify'), self.s2_verify, 'soft'),
                         (T('Зібрати XFBIN', 'Build XFBIN'), self.s2_build, 'primary'),
                         (T('Відкрити TXT', 'Open TXT'), lambda: self.open_txt('s2'), 'ghost')])
        self.actions(c, [(T('Перевірити теги', 'Check tags'), lambda: self.check_tags('s2'), 'ghost'),
                         (T('Резервна копія TXT', 'Back up TXT'), lambda: self.backup('s2'), 'ghost')])
        m = self.more(c)
        self.path(m, T('Джерело (event\\WIN64\\eng)', 'Source (event\\WIN64\\eng)'), 'storm2_eng')
        self.path(m, T('Папка Build', 'Build folder'), 'storm2_output')

        c = self.card(tex, T('Робоча папка', 'Workspace'))
        self.path(c, T('Робочі PNG', 'Working PNGs'), 'storm2_texture_workspace')
        self.path(c, T('Папка Build', 'Build folder'), 'storm2_output')
        for title, key in ((T('Інтерфейс (ui)', 'Interface (ui)'), 'storm2_ui_root'), (T('Сюжетні титри (pt)', 'Story titles (pt)'), 'storm2_pt_root')):
            c = self.card(tex, title)
            self.path(c, T('Оригінальні ресурси', 'Original resources'), key)
            self.actions(c, [(T('Експортувати PNG', 'Export PNG'), lambda k=key: self.tex_export(k, 'storm2_texture_workspace'), 'soft'),
                             (T('Зібрати змінені PNG', 'Build changed PNGs'), lambda k=key: self.tex_build(k, 'storm2_texture_workspace', 'storm2_output'), 'primary')])

        c = self.card(font, T('Українські літери з TTF / OTF', 'Ukrainian letters from TTF / OTF'), T('Нові гліфи — у вільних рядках атласу 512×512.', 'New glyphs go into the free rows of the 512×512 atlas.'))
        self.path(c, T('Оригінальний шрифт гри', 'Original game font'), 'storm2_font_source', 'xfbin')
        self.ttf(c, 'storm2_ttf_source', 'ttf_mode_s2')
        self.path(c, T('Робоча папка шрифту', 'Font workspace folder'), 'storm2_font_workspace')
        self.path(c, T('Шлях у data_win32', 'Path inside data_win32'), 'storm2_font_rel', 'text')
        self.check(c, T('Однакові за формою літери брати з оригінального шрифту гри', 'Take identically shaped letters from the original game font'), 's2_twins')
        self.actions(c, [(T('Створити шрифт', 'Create font'), self.s2_font_make, 'primary'),
                         (T('Відкрити прев’ю', 'Open preview'), lambda: self.open_preview('s2'), 'ghost')])
        self.preview_slot(font, 's2', T('Додані гліфи', 'Added glyphs'))
        c = self.card(font, T('Ремонт раніше зібраного шрифту', 'Repair a previously built font'), T('Темні літери, апостроф і висота Ґ/ґ — без TTF.', 'Dark letters, apostrophe and Ґ/ґ height — no TTF needed.'))
        self.path(c, T('Зібраний XFBIN', 'Built XFBIN'), 'storm2_font_repair_source', 'xfbin')
        self.actions(c, [(T('Виправити', 'Repair'), self.s2_font_repair, 'soft')])
        c = self.card(font, T('Атлас PNG + таблиця Unicode', 'PNG atlas + Unicode table'))
        self.actions(c, [(T('Аналізувати', 'Analyze'), self.s2_font_inspect, 'soft'), (T('Експортувати PNG + TSV', 'Export PNG + TSV'), self.s2_font_export, 'soft'),
                         (T('Зібрати зі змінених файлів', 'Build from edited files'), self.s2_font_build, 'soft')], pady=(0, 0))

    def _page_s3(self):
        page = self._page('s3', 'STORM 3', T('Full Burst + HD: один TXT, три окремі XFBIN', 'Full Burst + HD: one TXT, three separate XFBIN files'), 's3')
        texts, font, research = self._tabs(page, [T('Тексти', 'Text'), T('Шрифт', 'Font'), T('Аналіз', 'Analysis')])
        c = self.card(texts, T('Сувій перекладу', 'Translation scroll'), T('Спільний TXT збирається у Full Burst, HD WIN32 та HD WIN64.', 'The shared TXT is built for Full Burst, HD WIN32 and HD WIN64.'))
        self.path(c, T('Папка перекладу', 'Translation folder'), 'storm3_workspace')
        self.actions(c, [(T('Експорт TXT', 'Export TXT'), self.s3_export, 'soft'), (T('Перевірити', 'Verify'), self.s3_verify, 'soft'),
                         (T('Зібрати й установити', 'Build and install'), self.s3_build, 'primary'),
                         (T('Відкрити TXT', 'Open TXT'), lambda: self.open_txt('s3'), 'ghost')])
        self.actions(c, [(T('Відкрити Build', 'Open Build'), lambda: self.open_dir('storm3_build'), 'ghost'),
                         (T('Відкотити встановлення', 'Roll back install'), self.s3_restore, 'ghost')])
        self.actions(c, [(T('Перевірити теги', 'Check tags'), lambda: self.check_tags('s3'), 'ghost'),
                         (T('Резервна копія TXT', 'Back up TXT'), lambda: self.backup('s3'), 'ghost')])
        c = self.card(texts, T('Джерела', 'Sources'), T('Кожна редакція має власний формат записів — файли не взаємозамінні.', 'Each edition has its own record format — the files are not interchangeable.'))
        self.path(c, T('Папка гри', 'Game folder'), 'storm3_game')
        self.path(c, 'Full Burst · WIN32', 'storm3_eng', 'xfbin')
        self.path(c, 'HD · WIN32', 'storm3_hd1', 'xfbin')
        self.path(c, 'HD · WIN64', 'storm3_hd2', 'xfbin')
        self.path(c, T('Папка Build', 'Build folder'), 'storm3_build')
        self.actions(c, [(T('Знайти три файли в папці гри', 'Find the three files in the game folder'), self.s3_detect, 'soft')])
        self.note(c, T('Підміна текстового XFBIN loose-файлами в HD ще не підтверджена в грі; CPK не змінюються.', 'Overriding the text XFBIN with loose files in HD is not yet confirmed in game; CPK archives are not changed.'), warn=True)

        c = self.card(font, T('Український шрифт із TTF / OTF', 'Ukrainian font from TTF / OTF'), T('66 літер додаються до оригінального NFH; атлас стає 512×512.', '66 letters are added to the original NFH; the atlas becomes 512×512.'))
        self.path(c, T('Оригінальний шрифт гри', 'Original game font'), 'storm3_font_a', 'xfbin')
        self.ttf(c, 'storm3_font_ttf', 'ttf_mode_s3')
        self.path(c, T('Папка PNG і звітів', 'PNG and reports folder'), 'storm3_font_work')
        self.check(c, T('Однакові за формою літери брати з оригінального шрифту гри', 'Take identically shaped letters from the original game font'), 's3_twins')
        self.actions(c, [(T('Створити й установити', 'Create and install'), self.s3_font_make, 'primary'),
                         (T('Відкрити прев’ю', 'Open preview'), lambda: self.open_preview('s3'), 'ghost')])
        self.note(c, T('Збільшений атлас 512×512 ще не підтверджено рушієм — перевірте в грі.', 'The enlarged 512×512 atlas is not yet confirmed by the engine — check it in game.'), warn=True)
        self.preview_slot(font, 's3', T('Додані гліфи', 'Added glyphs'))

        c = self.card(research, T('Звіти', 'Reports'), T('Лише читання: файли гри не змінюються.', 'Read-only: game files are not changed.'))
        self.path(c, T('Папка звітів', 'Reports folder'), 'storm3_output')
        self.path(c, T('Шрифт для порівняння (RUS)', 'Font to compare with (RUS)'), 'storm3_font_b', 'xfbin')
        self.actions(c, [(T('Аудит ресурсів', 'Resource audit'), self.s3_scan, 'soft'), (T('Порівняти шрифти', 'Compare fonts'), self.s3_font_compare, 'soft'),
                         (T('Порівняти три редакції', 'Compare the three editions'), self.s3_editions, 'soft')])
        self.actions(c, [(T('Відкрити папку звітів', 'Open reports folder'), lambda: self.open_dir('storm3_output'), 'ghost')])

    def _page_tools(self):
        page = self._page('tools', T('Звірка ресурсів', 'Resource check'), T('Які файли зачіпали сторонні патчі STORM 1 і 2 — лише читання', 'Which files third-party STORM 1 and 2 patches touched — read-only'))
        c = self.card(page, T('Перевірка', 'Check'))
        self.path(c, T('STORM 1 · ресурси', 'STORM 1 · resources'), 'patch_scope_storm1')
        self.path(c, T('STORM 2 · ресурси', 'STORM 2 · resources'), 'patch_scope_storm2')
        self.path(c, T('Папка звітів', 'Reports folder'), 'patch_scope_report')
        self.actions(c, [(T('Звірити ресурси', 'Check resources'), self.scope_audit, 'primary'), (T('Діагностика XFBIN', 'XFBIN diagnostics'), self.scope_forensics, 'soft'),
                         (T('Відкрити звіти', 'Open reports'), lambda: self.open_dir('patch_scope_report'), 'ghost')])
        self.note(c, T('Звіти TSV / JSON зберігаються поза папками ігор. Нічого не встановлюється.', 'TSV / JSON reports are saved outside the game folders. Nothing is installed.'))


    def _page_about(self):
        page = self._page('about', T('Про програму', 'About'),
                          T('Інструмент української локалізації NARUTO: Ultimate Ninja STORM 1, 2 і 3',
                            'Ukrainian localization toolkit for NARUTO: Ultimate Ninja STORM 1, 2 and 3'))
        page['children'].append({'type': 'about', 'title': f'NARUTO UA Toolkit {VERSION}',
                                 'rows': [[T('Автор', 'Author'), AUTHOR], ['Telegram', TELEGRAM]],
                                 'badge': 'Made in Ukraine'})
        c = self.card(page, T('Посилання', 'Links'))
        self.actions(c, [(TELEGRAM, lambda: webbrowser.open('https://' + TELEGRAM), 'primary'),
                         ('GitHub', lambda: webbrowser.open(GITHUB), 'soft'),
                         (T('Перевірити оновлення', 'Check for updates'),
                          lambda: webbrowser.open(GITHUB + '/releases/latest'), 'ghost')])
        self.note(c, T('«Перевірити оновлення» відкриває сторінку останнього релізу в браузері; сама програма '
                       'в мережу не виходить.',
                       '"Check for updates" opens the latest release page in your browser; the program itself '
                       'does not go online.'))

    # ------------------------------------------------------------- infrastructure
    def _paths_changed(self):
        self._save()
        self._bad_cache = (0.0, [])
        self.refresh_readiness()

    def _save(self):
        try:
            target = config_path(); target.parent.mkdir(parents=True, exist_ok=True)
            data = {k: v.get() for k, v in self.p.items()}
            data['page'] = self._current
            tmp = target.with_name('settings.json.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            tmp.replace(target)
        except OSError as exc:
            self._log(T('Не вдалося зберегти налаштування: ', 'Could not save settings: ') + str(exc))

    def set_language(self, code):
        if code in i18n.LANGUAGES and code != i18n.LANG and not self.working:
            i18n.set_language(code); self.p['lang'].set(code)
            self.build_ui()
            self.status.set(T('Готово. Оберіть місію ліворуч.', 'Ready. Pick a mission on the left.')); self.detail.set('')

    def reset_paths(self):
        if messagebox.askyesno(T('Стандартні шляхи', 'Default paths'),
                               T('Повернути всі шляхи до початкових значень?', 'Reset all paths to their defaults?')):
            for key, value in DEFAULT_PATHS.items():
                if key in self.p and key != 'lang':
                    self.p[key].set(value)

    def browse(self, key, kind):
        """Native folder/file dialog, shown by a short helper process."""
        current = Path(self.p[key].get() or Path.home())
        start = current if current.is_dir() else current.parent
        start = str(start if start.is_dir() else Path.home())
        try:
            done = subprocess.run([sys.executable, '-c', DIALOG, kind, start], capture_output=True, timeout=900,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired):
            return
        picked = done.stdout.decode('utf-8', 'replace').strip()
        if not picked:
            return
        with self.lock:
            self.p[key].set(picked)
            guess = {'font_source': 'font_rel', 'storm2_font_source': 'storm2_font_rel',
                     'texture_source': 'texture_rel'}.get(key)
            if guess:
                rel = font1.relative_font_path(picked)
                if rel:
                    self.p[guess].set(rel)

    def open_folder(self, key):
        value = self.p[key].get().strip()
        folder = Path(value) if value else None
        if folder is not None and not folder.is_dir():
            folder = folder.parent
        if folder is not None and folder.is_dir() and str(folder) not in ('', '.'):
            open_path(folder)

    def notify(self, kind, title, text):
        with self.lock:
            self._notice_id += 1
            self.notices.append({'id': self._notice_id, 'kind': kind, 'title': str(title), 'text': str(text)})
            del self.notices[:-20]

    def _log(self, value):
        with self.lock:
            self.log_lines.extend(str(value).split('\n'))
            if len(self.log_lines) > 6000:
                del self.log_lines[:1000]; self.log_epoch += 1

    _append_log = _log

    def clear_log(self):
        with self.lock:
            self.log_lines.clear(); self.log_epoch += 1

    def bad_paths(self) -> list:
        """Source paths that do not exist (checked at most every two seconds)."""
        stamp, cached = self._bad_cache
        if time.monotonic() - stamp < 2:
            return cached
        bad = []
        for page in self.pages:
            for node in walk(page):
                if node.get('type') == 'ttf':
                    kind = 'ttf' if self.ttf_mode(node['mode']) == 'own' else None
                elif node.get('type') == 'path' and node['kind'] != 'text' and node['key'] not in OUTPUT_KEYS:
                    kind = node['kind']
                else:
                    kind = None
                if kind is None:
                    continue
                value = self.p[node['key']].get().strip()
                try:
                    ok = not value or (Path(value).is_dir() if kind == 'dir' else Path(value).is_file())
                except OSError:
                    ok = False
                if not ok and node['key'] not in bad:
                    bad.append(node['key'])
        self._bad_cache = (time.monotonic(), bad)
        return bad

    def state(self, cursor=0, epoch=-1) -> dict:
        """What the page shows right now; called a few times per second."""
        with self.lock:
            self.last_seen = time.monotonic()
            if epoch != self.log_epoch:
                cursor = 0
            lines = self.log_lines[cursor:]
            notices, self.notices = self.notices, []
            want_log, self.want_log = self.want_log, False
            status = self.status.get()
            if self.working:
                status = f'{self._running}  ·  {fmt_time(time.monotonic() - self._started)}'
            games = {}
            for game in GAMES:
                info = self.readiness.get(game)
                games[game] = {'percent': info['percent'] if info and info['total'] else None,
                               'label': fmt_percent(info),
                               'count': fmt_count(info) if info and info['total'] else T('TXT ще не створено', 'No TXT yet'),
                               'stamp': self._stamp(game)}
            return {'lang': i18n.LANG, 'page': self._current, 'working': self.working, 'status': status,
                    'detail': self.detail.get(), 'dot': self.dot, 'progress': self.progress,
                    'paths': {k: v.get() for k, v in self.p.items()},
                    'flags': {k: v.get() for k, v in self.flags.items()}, 'bad': self.bad_paths(),
                    'games': games, 'previews': {k: v[1] for k, v in self._previews.items()},
                    'bundled': BUNDLED_TTF.is_file(),
                    'ttf': {key: self.ttf_mode(key) for key in ('ttf_mode_s1', 'ttf_mode_s2', 'ttf_mode_s3')},
                    'log': {'epoch': self.log_epoch, 'cursor': cursor + len(lines), 'lines': lines},
                    'notices': notices, 'wantLog': want_log}

    def act(self, name, confirmed=()) -> dict:
        """Run the action behind a button. Returns {'ask': …} when it needs a yes/no first."""
        command = self._actions.get(name) or {'stop': self.stop, 'refresh': self.refresh_readiness}.get(name)
        if command is None:
            return {'error': 'unknown action'}
        return self.act_call(command, confirmed)

    def act_call(self, command, confirmed=()) -> dict:
        messagebox.local.confirmed = set(confirmed)
        try:
            with self.lock:
                command()
        except Ask as question:
            return {'ask': {'title': question.title, 'text': question.text, 'key': question.key}}
        except Exception as exc:                       # never let a page action kill the host
            self.notify('error', T('Помилка', 'Error'), f'{exc.__class__.__name__}: {exc}')
        finally:
            messagebox.local.confirmed = set()
        return {'ok': True}

    # ------------------------------------------------------------------- jobs
    def run(self, label, target, *args, done=None, **kwargs):
        """Start a backend operation in its own process (see jobs.py).

        `target` is 'module.function'; jobs.LOG / jobs.PROGRESS among the
        arguments become reporting callbacks. `done(result)` runs on success.
        """
        if self.working:
            self.detail.set(T('Зачекайте: попередня операція ще триває.',
                              'Please wait: the previous operation is still running.'))
            return
        self._save()
        spec = json.dumps({'lang': i18n.LANG, 'target': target, 'args': args, 'kwargs': kwargs},
                          ensure_ascii=False, default=str)
        try:
            self._process = subprocess.Popen(
                [sys.executable, str(APP_DIR / 'jobs.py')], cwd=str(APP_DIR),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as exc:
            messagebox.showerror(label, str(exc)); return
        self.working = True
        self._stopping = False
        self._started = time.monotonic(); self._running = T('Техніка виконується: ', 'In progress: ') + label
        self.status.set(self._running); self.detail.set('')
        self.dot = 'running'; self.progress = None
        threading.Thread(target=self._pump, args=(self._process, spec, label, done), daemon=True).start()

    def stop(self):
        """Stop the running operation on request."""
        process = self._process
        if not self.working or process is None or process.poll() is not None:
            return
        if messagebox.askyesno(T('Зупинити операцію', 'Stop the operation'), T(
                'Перервати операцію? Файли, які вже записано, залишаться; резервні копії не зачіпаються.',
                'Stop the operation? Files that are already written stay; backups are not touched.')):
            self._stopping = True
            process.terminate()

    def _pump(self, process, spec, label, done):
        """Reader thread: forwards the job's messages. Only pipe I/O happens here."""
        errors = []
        reader = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
        reader.start()
        final = None
        try:
            process.stdin.write(spec.encode('utf-8')); process.stdin.close()
            for line in process.stdout:
                try:
                    kind, payload = json.loads(line.decode('utf-8'))
                except ValueError:
                    continue
                if kind == 'log':
                    self._log(payload)
                elif kind == 'progress':
                    self._on_progress(payload)
                else:
                    final = (kind, payload)
        except OSError as exc:
            final = final or ('error', str(exc))
        code = process.wait(); reader.join(3)
        if final is None:
            tail = (errors[0] if errors else b'').decode('utf-8', 'replace').strip().splitlines()[-6:]
            final = ('error', T(f'Процес операції завершився без результату (код {code}).',
                                f'The operation process ended without a result (code {code}).')
                     + ('\n' + '\n'.join(tail) if tail else ''))
        try:
            self._finish(final[0], label, final[1], done)
        except Exception as exc:                       # the host must survive a failing callback
            self._log(T(f'Внутрішня помилка інтерфейсу: {exc!r}', f'Internal interface error: {exc!r}'))

    def _on_progress(self, payload):
        try:
            current, total, text = int(payload[0]), int(payload[1]), str(payload[2])
        except (TypeError, ValueError, IndexError):
            return
        with self.lock:
            if total > 0:
                self.progress = min(1.0, current / total)
                text = f'{current} / {total} · {text}'
            self.detail.set(text[:160])

    def _finish(self, kind, label, result, done):
        with self.lock:
            self.working = False                       # first, so nothing below can leave it stuck
            self._process = None
            self.progress = 0.0
            took = time.monotonic() - self._started
            if kind == 'success':
                self.dot = 'good'
                self.status.set(T('Місію виконано: ', 'Mission complete: ') + label)
                self.detail.set(T(f'Тривалість {fmt_time(took)}. Подробиці — у журналі.',
                                  f'Took {fmt_time(took)}. Details are in the log.'))
                self._log(T(f'ГОТОВО: {label}\n{summarize(result)}', f'DONE: {label}\n{summarize(result)}'))
            elif self._stopping:
                self._stopping = False
                self.dot = 'idle'
                self.status.set(label + T(' — зупинено.', ' — stopped.')); self.detail.set('')
                self._log(T(f'ЗУПИНЕНО: {label}', f'STOPPED: {label}'))
            else:
                self.dot = 'bad'
                self.status.set(label + T(' — помилка.', ' — failed.'))
                self.detail.set(T('Текст помилки — у журналі.', 'The error text is in the log.'))
                self._log(T(f'ПОМИЛКА: {label}\n  {result}', f'ERROR: {label}\n  {result}'))
                self.notify('error', label, str(result)[:1800])
        self.refresh_readiness()
        if kind == 'success' and done:
            with self.lock:
                done(result)

    # ------------------------------------------------------------------ readiness
    def txt_path(self, game) -> Path:
        _name, _full, key, filename = GAMES[game]
        return Path(self.p[key].get()) / filename

    def refresh_readiness(self):
        with self.lock:
            if self._scan_busy:
                self._scan_again = True
                return
            self._scan_busy = True
            self._scan_again = False
            targets = {game: self.txt_path(game) for game in GAMES}

        def worker():
            result = {game: translation_progress(path) for game, path in targets.items()}
            with self.lock:
                self.readiness = result
                self._scan_busy = False
                again = self._scan_again
            if again:
                self.refresh_readiness()
        threading.Thread(target=worker, daemon=True).start()

    def _stamp(self, game) -> str:
        path = self.txt_path(game)
        try:
            changed = time.strftime('%d.%m.%Y %H:%M', time.localtime(path.stat().st_mtime))
        except OSError:
            return T('Почніть з «Експорт TXT» на сторінці гри.', 'Start with "Export TXT" on the game page.')
        return T(f'{path.name} · змінено {changed}', f'{path.name} · changed {changed}')

    # -------------------------------------------------------------- common actions
    def need(self, *keys, files=False) -> bool:
        for key in keys:
            value = self.p[key].get().strip()
            if not value or (files and not Path(value).is_file()):
                messagebox.showwarning(T('Не вистачає даних', 'Missing data'), T('Заповніть поле шляху й повторіть дію.', 'Fill in the path field and try again.'))
                return False
        return True

    def open_txt(self, game):
        path = self.txt_path(game)
        if not path.is_file():
            messagebox.showwarning(GAMES[game][0], T('TXT ще немає. Спершу виконайте «Експорт TXT».', 'There is no TXT yet. Run "Export TXT" first.')); return
        open_path(path)

    def backup(self, game, quiet=False):
        """Copy the TXT and manifest into <workspace>/backups. Quiet mode is used before a build."""
        try:
            result = backup_project(self.txt_path(game))
        except OSError as exc:
            self._log(T(f'Резервну копію не створено: {exc}', f'Backup failed: {exc}'))
            if not quiet:
                messagebox.showerror(GAMES[game][0], str(exc))
            return
        if result['created']:
            self._log(T(f'Резервна копія TXT: {result["created"]}', f'TXT backup: {result["created"]}'))
            if not quiet:
                self.detail.set(T(f'Резервну копію створено: {result["created"]}',
                                  f'Backup created: {result["created"]}'))
        elif not quiet:
            self.detail.set(T('TXT не змінювався від останньої копії.', 'The TXT has not changed since the last backup.')
                            if result.get('same_as') else T('TXT ще немає.', 'There is no TXT yet.'))

    def check_tags(self, game):
        path = self.txt_path(game)
        if not path.is_file():
            messagebox.showwarning(GAMES[game][0], T('TXT ще немає. Спершу виконайте «Експорт TXT».', 'There is no TXT yet. Run "Export TXT" first.')); return
        self.want_log = True                           # the list of records goes to the log
        self.run(GAMES[game][0] + T(' · Перевірка тегів', ' · Tag check'), 'jobs.check_tags', str(path), game,
                 log=jobs.LOG)

    def detect_games(self):
        """Find the installed games in the Steam libraries and offer to fill in the paths."""
        try:
            games = steam.find_games()
        except OSError as exc:
            messagebox.showerror('Steam', str(exc)); return
        paths, state = steam.proposed_paths(games)
        moved = steam.movable_outputs({k: v.get() for k, v in self.p.items()}, DEFAULT_PATHS, OUTPUT_KEYS,
                                      Path.home() / 'Documents' / 'Naruto UA')
        words = {'ready': T('знайдено', 'found'),
                 'not_extracted': T('знайдено, але ресурси не розпаковано (немає dataRegion_extracted)',
                                    'found, but its resources are not extracted (no dataRegion_extracted)'),
                 'missing': T('не знайдено', 'not found')}
        report = '\n'.join(f'{GAMES[game][0]}: {words[state[game]]}' for game in GAMES)
        if not paths and not moved:
            messagebox.showinfo('Steam', report); return
        question = report + '\n\n' + T(f'Заповнити {len(paths) + len(moved)} шляхів автоматично?',
                                        f'Fill in {len(paths) + len(moved)} paths automatically?')
        if moved:
            question += '\n' + T('Робочі папки буде перенесено до Documents\\Naruto UA, бо диска зі стандартними шляхами немає.',
                                 'Working folders will move to Documents\\Naruto UA because the drive of the default paths does not exist.')
        if messagebox.askyesno('Steam', question):
            for key, value in {**moved, **paths}.items():
                self.p[key].set(value)
            self.status.set(T('Шляхи заповнено.', 'Paths filled in.'))

    def open_dir(self, key):
        path = Path(self.p[key].get())
        if not path.is_dir():
            messagebox.showwarning(T('Папка', 'Folder'), T(f'Папки ще немає:\n{path}', f'The folder does not exist yet:\n{path}')); return
        open_path(path)

    def open_preview(self, key):
        path = getattr(self, f'_preview_{key}', None)
        if not path or not Path(path).is_file():
            messagebox.showwarning(T('Прев’ю', 'Preview'), T('Спочатку створіть шрифт.', 'Create the font first.')); return
        open_path(path)

    def _font_done(self, key, field='preview'):
        def done(result):
            path = result.get(field) if isinstance(result, dict) else None
            setattr(self, f'_preview_{key}', path)
            self.show_preview(key, path)
        return done

    def confirm_reexport(self, game) -> bool:
        return (not self.txt_path(game).is_file()) or messagebox.askyesno(
            GAMES[game][0] + T(' · повторний експорт', ' · repeated export'),
            T('TXT уже існує. Наявні переклади буде перенесено, а стару пару TXT + маніфест збережено як резервну копію.\n\nПродовжити?', 'The TXT already exists. Existing translations will be carried over and the old TXT + manifest pair kept as a backup.\n\nContinue?'))

    # --------------------------------------------------------------------- STORM 1
    def s1_export(self):
        if not self.confirm_reexport('s1'):
            return
        p = self.p
        self.run(T('STORM 1 · Експорт TXT', 'STORM 1 · Export TXT'), 'storm1_text.export_project', p['eng'].get(), p['workspace'].get(), telop_dir=p['telop'].get(), prefill_english=self.flags['prefill'].get(), log=jobs.LOG)

    def s1_verify(self):
        p = self.p
        self.run(T('STORM 1 · Перевірка TXT', 'STORM 1 · Verify TXT'), 'storm1_text.build_project', p['eng'].get(), p['workspace'].get(), p['output'].get(), telop_dir=p['telop'].get(), dry_run=True, allow_replace=True, log=jobs.LOG)

    def s1_build(self):
        p = self.p
        self.backup('s1', quiet=True)
        self.run(T('STORM 1 · Збирання й установлення', 'STORM 1 · Build and install'), 'storm1_text.build_and_install', p['eng'].get(), p['workspace'].get(), p['output'].get(), p['game'].get(), telop_dir=p['telop'].get(), log=jobs.LOG)

    def s1_telop(self):
        p = self.p
        self.run(T('STORM 1 · Додавання Telop', 'STORM 1 · Add Telop'), 'storm1_text.augment_existing_telop', p['workspace'].get(), p['telop'].get(), log=jobs.LOG)

    def s1_font_make(self):
        if not self.need('font_source', files=True) or not self.need_ttf('ttf_source', 'ttf_mode_s1') or not self.need('font_rel'):
            return
        p, fl = self.p, self.flags
        native = fl['s1_native_metrics'].get()
        self.run(T('STORM 1 · Український шрифт', 'STORM 1 · Ukrainian font'), 'storm1_font.generate_additive_font', p['font_source'].get(), self.ttf_path('ttf_source', 'ttf_mode_s1'), p['workspace'].get(), p['output'].get(), p['font_rel'].get(), characters=p['s1_chars'].get(), override=True, log=jobs.LOG, latin_p_for_r=fl['s1_p'].get(), latin_y_for_u=fl['s1_y'].get(), metrics_mode='native_matched' if native else 'stable', done=self._font_done('s1'))

    def s1_font_inspect(self):
        if self.need('font_source', files=True):
            self.run(T('STORM 1 · Аналіз шрифту', 'STORM 1 · Font analysis'), 'jobs.s1_font_inspect',
                     self.p['font_source'].get())

    def s1_font_export(self):
        if not self.need('font_source', files=True) or not self.need('font_workspace'):
            return
        self.run(T('STORM 1 · Експорт атласу', 'STORM 1 · Atlas export'), 'jobs.s1_font_export',
                 self.p['font_source'].get(), self.p['font_workspace'].get(), log=jobs.LOG)

    def s1_font_build(self):
        if not self.need('font_source', files=True) or not self.need('font_workspace', 'font_rel'):
            return
        if not messagebox.askyesno(T('STORM 1 · шрифт', 'STORM 1 · font'), T('Зібрати шрифт зі змінених PNG / TSV? Результат потрібно перевірити в грі.', 'Build the font from the edited PNG / TSV? The result must be checked in game.')):
            return
        p = self.p
        self.run(T('STORM 1 · Збирання шрифту', 'STORM 1 · Font build'), 'storm1_font.build_font_output', p['font_source'].get(), p['font_workspace'].get(), p['output'].get(), p['font_rel'].get(), override=self.flags['replace'].get(), log=jobs.LOG)

    # -------------------------------------------------------------------- textures
    def tex_export(self, root_key, work_key):
        if not Path(self.p[root_key].get()).is_dir():
            messagebox.showerror(T('Текстури', 'Textures'), T('Папки з оригінальними ресурсами не існує.', 'The folder with the original resources does not exist.')); return
        root, work = self.p[root_key].get(), self.p[work_key].get()
        self.run(T('Експорт текстур', 'Texture export'), 'textures.batch_export_textures', root, work, log=jobs.LOG, progress=jobs.PROGRESS)

    def tex_build(self, root_key, work_key, out_key):
        if not Path(self.p[root_key].get()).is_dir():
            messagebox.showerror(T('Текстури', 'Textures'), T('Папки з оригінальними ресурсами не існує.', 'The folder with the original resources does not exist.')); return
        root, work, out = self.p[root_key].get(), self.p[work_key].get(), self.p[out_key].get()
        self.run(T('Збирання текстур', 'Texture build'), 'textures.batch_build_textures', root, work, out, log=jobs.LOG, progress=jobs.PROGRESS)

    def tex_clean(self):
        if not messagebox.askyesno(T('Старі DDS', 'Old DDS'), T('Видалити chunk_NNN.dds попередніх експортів? PNG і оригінали не змінюються.', 'Delete chunk_NNN.dds files left by earlier exports? PNGs and originals are not touched.')):
            return
        work, name = self.p['texture_workspace'].get(), Path(self.p['texture_scan_root'].get()).name
        self.run(T('Очищення DDS', 'DDS cleanup'), 'textures.cleanup_managed_dds', work, name)

    def tex_one_export(self):
        if not self.need('texture_source', files=True):
            return
        source, work = self.p['texture_source'].get(), self.p['texture_workspace'].get()
        self.run(T('Експорт XFBIN → PNG', 'Export XFBIN → PNG'), 'textures.export_textures', source, work, export_dds=False)

    def tex_one_inspect(self):
        if not self.need('texture_source', files=True):
            return
        source = self.p['texture_source'].get()
        self.run(T('Перевірка XFBIN', 'XFBIN check'), 'jobs.texture_inspect', source)

    def tex_one_build(self):
        if not self.need('texture_source', files=True) or not self.need('texture_rel'):
            return
        p = self.p
        self.run(T('Збирання PNG → XFBIN', 'Build PNG → XFBIN'), 'textures.build_textures', p['texture_source'].get(), p['texture_workspace'].get(), p['output'].get(), p['texture_rel'].get().strip(), overwrite=self.flags['replace'].get())

    # --------------------------------------------------------------------- STORM 2
    def s2_export(self):
        if not self.confirm_reexport('s2'):
            return
        p = self.p
        self.run(T('STORM 2 · Експорт TXT', 'STORM 2 · Export TXT'), 'storm2_text.export_project', p['storm2_eng'].get(), p['storm2_workspace'].get(), log=jobs.LOG)

    def _s2_compile(self, label, dry):
        p = self.p
        if not dry:
            self.backup('s2', quiet=True)
        self.run(label, 'storm2_text.compile_project', p['storm2_eng'].get(), p['storm2_workspace'].get(), p['storm2_output'].get(), dry_run=dry, allow_replace=True, log=jobs.LOG)

    def s2_verify(self):
        self._s2_compile(T('STORM 2 · Перевірка TXT', 'STORM 2 · Verify TXT'), True)

    def s2_build(self):
        self._s2_compile(T('STORM 2 · Збирання XFBIN', 'STORM 2 · Build XFBIN'), False)

    def s2_font_make(self):
        if not self.need('storm2_font_source', files=True) or not self.need_ttf('storm2_ttf_source', 'ttf_mode_s2') or not self.need('storm2_font_rel'):
            return
        p = self.p
        self.run(T('STORM 2 · Український шрифт', 'STORM 2 · Ukrainian font'), 'storm2_font.generate_additive_font', p['storm2_font_source'].get(), self.ttf_path('storm2_ttf_source', 'ttf_mode_s2'), p['storm2_font_workspace'].get(), p['storm2_output'].get(), p['storm2_font_rel'].get(), override=True, log=jobs.LOG, copy_native_lookalikes=self.flags['s2_twins'].get(), done=self._font_done('s2'))

    def s2_font_repair(self):
        if not self.need('storm2_font_repair_source', files=True) or not self.need('storm2_font_rel'):
            return
        p = self.p
        self.run(T('STORM 2 · Ремонт шрифту', 'STORM 2 · Font repair'), 'storm2_font.repair_existing_font_output', p['storm2_font_repair_source'].get(), p['storm2_output'].get(), p['storm2_font_rel'].get(), override=True, log=jobs.LOG, done=self._font_done('s2'))

    def s2_font_inspect(self):
        if self.need('storm2_font_source', files=True):
            self.run(T('STORM 2 · Аналіз шрифту', 'STORM 2 · Font analysis'), 'storm2_font.analyze_font', self.p['storm2_font_source'].get())

    def s2_font_export(self):
        if self.need('storm2_font_source', files=True) and self.need('storm2_font_workspace'):
            self.run(T('STORM 2 · Експорт атласу', 'STORM 2 · Atlas export'), 'jobs.s2_font_export',
                     self.p['storm2_font_source'].get(), self.p['storm2_font_workspace'].get(), log=jobs.LOG)

    def s2_font_build(self):
        if not self.need('storm2_font_source', files=True) or not self.need('storm2_font_workspace', 'storm2_font_rel'):
            return
        if not messagebox.askyesno(T('STORM 2 · шрифт', 'STORM 2 · font'), T('Зібрати шрифт зі змінених PNG / TSV? Результат потрібно перевірити в грі.', 'Build the font from the edited PNG / TSV? The result must be checked in game.')):
            return
        p = self.p
        self.run(T('STORM 2 · Збирання шрифту', 'STORM 2 · Font build'), 'storm2_font.build_font_output', p['storm2_font_source'].get(), p['storm2_font_workspace'].get(), p['storm2_output'].get(), p['storm2_font_rel'].get(), override=self.flags['replace'].get(), log=jobs.LOG)

    # --------------------------------------------------------------------- STORM 3
    def s3_sources(self):
        paths = {'original': self.p['storm3_eng'].get().strip(), 'hd_win32': self.p['storm3_hd1'].get().strip(),
                 'hd_win64': self.p['storm3_hd2'].get().strip()}
        if not all(paths.values()):
            messagebox.showwarning('STORM 3', T('Вкажіть три оригінальні XFBIN у блоці «Джерела».', 'Set the three original XFBIN files in the "Sources" block.'))
            return None
        return paths

    def s3_detect(self):
        root = Path(self.p['storm3_game'].get().strip())
        if root.name.casefold() == 'dlc':
            root = root.parent
        base = Path('data/disc/dataRegion_extracted/data/message')
        missing = []
        for key, path in (('storm3_eng', root / base / 'WIN32/eng/messageInfo.bin.xfbin'),
                          ('storm3_hd1', root / 'dlc' / base / 'WIN32/eng/messageInfo.bin.xfbin'),
                          ('storm3_hd2', root / 'dlc' / base / 'WIN64/eng/messageInfo.bin.xfbin')):
            if path.is_file():
                self.p[key].set(str(path))
            else:
                missing.append(str(path))
        if missing:
            messagebox.showwarning('STORM 3', T('Не знайдено:\n\n', 'Not found:\n\n') + '\n'.join(missing))
        else:
            self.status.set(T('STORM 3 · усі три оригінали знайдено.', 'STORM 3 · all three originals found.'))

    def s3_export(self):
        paths = self.s3_sources()
        if paths and self.confirm_reexport('s3'):
            work = self.p['storm3_workspace'].get()
            self.run(T('STORM 3 · Експорт TXT', 'STORM 3 · Export TXT'), 'storm3_text.export_project', paths, work, log=jobs.LOG)

    def s3_verify(self):
        paths = self.s3_sources()
        if paths:
            p = self.p
            self.run(T('STORM 3 · Перевірка TXT', 'STORM 3 · Verify TXT'), 'storm3_text.compile_project', paths, p['storm3_workspace'].get(), p['storm3_build'].get(), dry_run=True, log=jobs.LOG)

    def s3_build(self):
        paths = self.s3_sources()
        if not paths or not messagebox.askyesno(
                T('STORM 3 · установлення', 'STORM 3 · install'), T('Зібрати три XFBIN і встановити їх у знайдені редакції гри?\n\nCPK не змінюються; попередні loose-файли зберігаються для відкату.', 'Build the three XFBIN files and install them into the detected game editions?\n\nCPK archives are not changed; previous loose files are kept for rollback.')):
            return
        p = self.p
        self.backup('s3', quiet=True)
        self.run(T('STORM 3 · Збирання й установлення', 'STORM 3 · Build and install'), 'storm3_text.compile_project', paths, p['storm3_workspace'].get(), p['storm3_build'].get(), dry_run=False, install_root=p['storm3_game'].get(), log=jobs.LOG)

    def s3_restore(self):
        if not messagebox.askyesno(T('STORM 3 · відкат', 'STORM 3 · rollback'), T('Відновити файли гри з резервних копій останнього встановлення?', 'Restore the game files from the backups of the last install?')):
            return
        self.run(T('STORM 3 · Відкат', 'STORM 3 · Rollback'), 'jobs.s3_restore',
                 self.p['storm3_build'].get(), self.p['storm3_game'].get(), log=jobs.LOG)

    def s3_font_make(self):
        if not self.need('storm3_font_a', files=True) or not self.need_ttf('storm3_font_ttf', 'ttf_mode_s3'):
            return
        if not messagebox.askyesno(T('STORM 3 · шрифт', 'STORM 3 · font'), T('Створити шрифт і встановити його у знайдені редакції гри?\n\nПопередні файли збережуться в Build для відкату.', 'Create the font and install it into the detected game editions?\n\nPrevious files are kept in Build for rollback.')):
            return
        p = self.p
        self.run(T('STORM 3 · Шрифт і встановлення', 'STORM 3 · Font and install'), 'storm3_text.build_font_and_install', p['storm3_font_a'].get(), self.ttf_path('storm3_font_ttf', 'ttf_mode_s3'), p['storm3_font_work'].get(), p['storm3_build'].get(), p['storm3_game'].get(), russian_reference=p['storm3_font_b'].get().strip() or None, copy_native_lookalikes=self.flags['s3_twins'].get(), log=jobs.LOG, done=self._font_done('s3'))

    def s3_scan(self):
        if not self.need('storm3_eng', 'storm3_output'):
            return
        source = Path(self.p['storm3_eng'].get())
        folder = source.parent if source.is_file() else source
        self.run(T('STORM 3 · Аудит ресурсів', 'STORM 3 · Resource audit'), 'audit.run', folder, self.p['storm3_output'].get(), log=jobs.LOG)

    def s3_font_compare(self):
        if self.need('storm3_font_a', 'storm3_font_b', files=True) and self.need('storm3_output'):
            p = self.p
            self.run(T('STORM 3 · Порівняння шрифтів', 'STORM 3 · Font comparison'), 'storm3_font.run', p['storm3_font_a'].get(), p['storm3_font_b'].get(), p['storm3_output'].get(), log=jobs.LOG)

    def s3_editions(self):
        paths = self.s3_sources()
        if paths and self.need('storm3_output'):
            output = self.p['storm3_output'].get()
            self.run(T('STORM 3 · Порівняння редакцій', 'STORM 3 · Edition comparison'), 'storm3_text.save_reports', tuple(paths.values()), output)

    # ----------------------------------------------------------------------- tools
    def _scope(self):
        p = self.p
        return p['patch_scope_storm1'].get(), p['patch_scope_storm2'].get(), p['patch_scope_report'].get()

    def scope_audit(self):
        a, b, c = self._scope()
        self.run(T('Звірка ресурсів', 'Resource check'), 'audit.audit_scope', a, b, c, log=jobs.LOG)

    def scope_forensics(self):
        a, b, c = self._scope()
        self.run(T('Діагностика XFBIN', 'XFBIN diagnostics'), 'audit.diagnostic_scope', a, b, c, log=jobs.LOG)



def walk(node):
    """Every node of a page description, depth first."""
    yield node
    for child in node.get('children', []):
        yield from walk(child)
    for tab in node.get('tabs', []):
        yield from walk(tab)


# A tiny Tk dialog run in its own process: the page cannot open native folder pickers.
DIALOG = r'''
import sys, tkinter as tk
from tkinter import filedialog
kind, start = sys.argv[1], sys.argv[2]
root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True); root.update()
if kind == 'dir':
    picked = filedialog.askdirectory(initialdir=start, parent=root)
elif kind == 'ttf':
    picked = filedialog.askopenfilename(initialdir=start, parent=root, filetypes=[('TTF / OTF', '*.ttf *.otf'), ('*', '*.*')])
else:
    picked = filedialog.askopenfilename(initialdir=start, parent=root, filetypes=[('XFBIN', '*.xfbin'), ('*', '*.*')])
sys.stdout.buffer.write((picked or '').encode('utf-8'))
'''


# ---- local server ------------------------------------------------------------------

def make_handler(app: App, token: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):                   # keep the console quiet
            pass

        def _send(self, code, body: bytes, kind='application/json; charset=utf-8'):
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code=200):
            self._send(code, json.dumps(data, ensure_ascii=False).encode('utf-8'))

        def _allowed(self, query) -> bool:
            """Only this program's own page may talk to the API."""
            host = (self.headers.get('Host') or '').split(':')[0]
            if host not in ('127.0.0.1', 'localhost'):
                return False
            given = self.headers.get('X-Token') or (query.get('t') or [''])[0]
            return secrets.compare_digest(given, token)

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path.startswith('/api/'):
                if not self._allowed(query):
                    return self._json({'error': 'forbidden'}, 403)
                if url.path == '/api/ui':
                    return self._json(app.ui())
                if url.path == '/api/state':
                    number = lambda name, default: int((query.get(name) or [default])[0])
                    return self._json(app.state(number('log', 0), number('epoch', -1)))
                if url.path == '/api/preview':
                    path = app._previews.get((query.get('key') or [''])[0], ('',))[0]
                    if path and Path(path).is_file():
                        return self._send(200, Path(path).read_bytes(), 'image/png')
                return self._json({'error': 'not found'}, 404)
            name = 'index.html' if url.path in ('/', '') else url.path.lstrip('/')
            file = (WEB_DIR / name).resolve()
            if name == 'favicon.ico':
                file = APP_DIR / 'natool.ico'
            if WEB_DIR.resolve() not in file.parents and file != APP_DIR / 'natool.ico' or not file.is_file():
                return self._send(404, b'not found', 'text/plain')
            kind = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            if kind.startswith('text/') or kind.endswith('javascript'):
                kind += '; charset=utf-8'
            self._send(200, file.read_bytes(), kind)

        def do_POST(self):
            url = urlparse(self.path)
            if not url.path.startswith('/api/') or not self._allowed(parse_qs(url.query)):
                return self._json({'error': 'forbidden'}, 403)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            except ValueError:
                return self._json({'error': 'bad request'}, 400)
            reply = {'ok': True}
            if url.path == '/api/action':
                reply = app.act(str(body.get('id')), body.get('confirmed') or ())
            elif url.path == '/api/set':
                with app.lock:
                    for key, value in (body.get('paths') or {}).items():
                        if key in app.p and key != 'lang' and isinstance(value, str):
                            app.p[key].set(value)
                    for key, value in (body.get('flags') or {}).items():
                        if key in app.flags:
                            app.flags[key].set(bool(value))
            elif url.path == '/api/browse':
                if body.get('key') in app.p:
                    app.browse(body['key'], str(body.get('kind') or 'dir'))
            elif url.path == '/api/folder':
                if body.get('key') in app.p:
                    app.open_folder(body['key'])
            elif url.path == '/api/open':
                game = body.get('game')
                if game in GAMES:
                    reply = app.act_call(lambda: app.open_txt(game))
            elif url.path == '/api/lang':
                with app.lock:
                    app.set_language(body.get('lang'))
            elif url.path == '/api/page':
                with app.lock:
                    app._current = str(body.get('page') or 'home'); app._save()
            elif url.path == '/api/log':
                app.clear_log()
            elif url.path == '/api/preview/open':
                reply = app.act_call(lambda: app.open_preview(str(body.get('key'))))
            elif url.path == '/api/bye':
                app.closed = True
            else:
                return self._json({'error': 'not found'}, 404)
            self._json(reply)

    return Handler


def serve(app: App, port=PORT):
    """Start the local server; returns (server, url)."""
    token = secrets.token_urlsafe(18)
    handler = make_handler(app, token)
    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), handler)
    except OSError:
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_address[1]}/?t={token}'


# ---- window -------------------------------------------------------------------------

def find_browser():
    """A Chromium browser that can show the page in its own window (Edge is part of Windows)."""
    candidates = []
    if os.name == 'nt':
        for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
            if base:
                candidates += [Path(base) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe',
                               Path(base) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe']
    elif sys.platform == 'darwin':
        candidates += [Path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'),
                       Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')]
    else:
        candidates += [Path(found) for name in ('microsoft-edge', 'google-chrome', 'chromium', 'chromium-browser')
                       if (found := shutil.which(name))]
    return next((path for path in candidates if path.is_file()), None)


def open_window(url):
    """Show the page as an application window; returns the browser process or None."""
    browser = find_browser()
    if browser is None:
        webbrowser.open(url)
        return None
    profile = config_path().parent / 'window'
    command = [str(browser), f'--app={url}', f'--user-data-dir={profile}', '--no-first-run',
               '--no-default-browser-check', '--disable-features=Translate,TranslateUI', '--disable-sync']
    if not profile.exists():
        command.append('--window-size=1280,860')       # later runs reopen where the window was left
    try:
        return subprocess.Popen(command)
    except OSError:
        webbrowser.open(url)
        return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    app = App()
    server, url = serve(app)
    if '--serve' in argv:                              # for development: no window, print the address
        print(url, flush=True)
        window = None
    else:
        window = open_window(url)
    opened = time.monotonic()
    try:
        while True:
            time.sleep(0.4)
            quiet = time.monotonic() - app.last_seen
            if '--serve' in argv:
                continue
            if window is not None and window.poll() is None:
                continue                               # the window is open
            # The window is gone (or the page lives in an ordinary browser tab).
            if app.working:
                continue                               # let a running operation finish
            if app.closed and quiet > 1.5:
                break
            if window is not None and time.monotonic() - opened > 6 and quiet > 4:
                break
            if quiet > 180:
                break
    except KeyboardInterrupt:
        pass
    finally:
        with app.lock:
            app._save()
        server.shutdown()


if __name__ == '__main__':
    main()
