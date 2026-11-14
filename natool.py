#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NARUTO UA Toolkit — interface for the STORM 1 / 2 / 3 localization pipelines.

Only presentation and task orchestration live here; every binary format is
handled by the backend modules next to this file.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import i18n
from i18n import T

try:
    import customtkinter as ctk
except ImportError:                                    # pragma: no cover
    _root = tk.Tk(); _root.withdraw()
    messagebox.showerror('NARUTO UA Toolkit',
                         T('Не знайдено бібліотеку інтерфейсу.\n\nЗапустіть INSTALL_DEPENDENCIES.bat і повторіть.', 'The interface library is missing.\n\nRun INSTALL_DEPENDENCIES.bat and try again.'))
    raise SystemExit(1)

import audit
import jobs
import storm1_font as font1
import storm1_text as text1
import storm2_text as text2
import storm3_text as text3
from txtfmt import translation_progress

VERSION = '4.1.1'
APP_DIR = Path(__file__).resolve().parent
AUTHOR = 'Dmytro Bidlov («Little Bit» Team)'
TELEGRAM = 't.me/LittleBitUA'
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
}

GAMES = {
    's1': ('STORM 1', 'Ultimate Ninja STORM', 'workspace', text1.TXT_NAME),
    's2': ('STORM 2', 'Shippuden: Ultimate Ninja STORM 2', 'storm2_workspace', text2.TXT_NAME),
    's3': ('STORM 3', 'Full Burst + HD', 'storm3_workspace', text3.TXT),
}

# (light, dark)
BG = ('#EEF1F6', '#0D1016')
SIDEBAR = ('#FFFFFF', '#12151C')
CARD = ('#FFFFFF', '#171B24')
BORDER = ('#E1E6EE', '#242A38')
FIELD = ('#F6F8FB', '#10131A')
TEXT = ('#131A26', '#E9EDF4')
MUTED = ('#667085', '#8891A4')
ACCENT = '#F97316'
ACCENT_HOVER = '#EA580C'
SOFT = ('#EDF0F5', '#222837')
SOFT_HOVER = ('#E0E5ED', '#2C3446')
NAV_ACTIVE = ('#FFF1E6', '#2A1D12')
TRACK = ('#E4E8EF', '#232938')
WARN = ('#B45309', '#FBBF24')
DISABLED = ('#98A2B3', '#6B7385')       # dimmed but still readable on every button
FONT = 'Segoe UI'


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
            if isinstance(value, str) and (value.strip() or key == 'lang'):
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


def fmt_count(info) -> str:
    done, total = f'{info["done"]:,}', f'{info["total"]:,}'
    return T(f'{done} з {total} записів'.replace(',', ' '), f'{done} of {total} records')


class App(ctk.CTk):
    def __init__(self):
        paths = saved_paths()
        i18n.set_language(paths['lang'])
        ctk.set_appearance_mode('dark')
        super().__init__(fg_color=BG)
        self.title(f'NARUTO UA Toolkit {VERSION}')
        self.geometry('1240x800'); self.minsize(1020, 660)
        self.p = {key: tk.StringVar(self, value=value) for key, value in paths.items()}
        self.flags = {name: tk.BooleanVar(self, value=value) for name, value in (
            ('prefill', False), ('replace', False), ('s1_p', True), ('s1_y', True), ('s1_dash', True),
            ('s1_symbols', False), ('s1_native_metrics', True), ('s2_twins', True), ('s3_twins', True))}
        self.status = tk.StringVar(self, value=T('Готово. Оберіть гру ліворуч.', 'Ready. Pick a game on the left.'))
        self.detail = tk.StringVar(self, value='')
        self.events: queue.Queue = queue.Queue()
        self._spin = False
        self._process = None
        self.working = False
        self.controls: list = []
        self.readiness: dict = {}
        self.readiness_widgets: dict = {g: [] for g in GAMES}
        self.previews: dict = {}
        self._save_job = self._scan_job = None
        self._current = 'home'
        self._shown_previews: dict = {}
        self._scan_busy = False
        self._build()
        for key, var in self.p.items():
            var.trace_add('write', self._paths_changed)
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.after(120, self._poll)
        self.after(300, self.refresh_readiness)

    # ------------------------------------------------------------------ widgets
    def f(self, size=13, weight='normal'):
        return ctk.CTkFont(family=FONT, size=size, weight=weight)

    def label(self, parent, text='', size=13, weight='normal', color=TEXT, **kw):
        return ctk.CTkLabel(parent, text=text, font=self.f(size, weight), text_color=color,
                            anchor='w', justify='left', **kw)

    def button(self, parent, text, command, kind='soft', width=0, lock=True):
        styles = {'primary': dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color='#FFFFFF',
                                  text_color_disabled='#FFE3CC'),
                  'soft': dict(fg_color=SOFT, hover_color=SOFT_HOVER, text_color=TEXT,
                               text_color_disabled=DISABLED),
                  'ghost': dict(fg_color='transparent', hover_color=SOFT, text_color=MUTED,
                                text_color_disabled=DISABLED, border_width=1, border_color=BORDER)}[kind]
        btn = ctk.CTkButton(parent, text=text, command=command, height=36, corner_radius=9, width=width,
                            font=self.f(13, 'bold' if kind == 'primary' else 'normal'), **styles)
        if lock:
            self.controls.append(btn)
        return btn

    def card(self, parent, title=None, hint=None):
        outer = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        outer.pack(fill='x', pady=(0, 12))
        body = ctk.CTkFrame(outer, fg_color='transparent')
        body.pack(fill='x', padx=18, pady=14)
        if title:
            self.label(body, title, 15, 'bold').pack(fill='x')
        if hint:
            self.label(body, hint, 12, color=MUTED, wraplength=820).pack(fill='x', pady=(2, 0))
        if title or hint:
            ctk.CTkFrame(body, height=8, fg_color='transparent').pack()
        return body

    def path(self, parent, caption, key, kind='dir'):
        """One labelled path field. kind: dir | xfbin | ttf | text."""
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', pady=3)
        self.label(row, caption, 12, color=MUTED, width=190).pack(side='left')
        entry = ctk.CTkEntry(row, textvariable=self.p[key], height=34, corner_radius=8, font=self.f(12),
                             fg_color=FIELD, border_color=BORDER, border_width=1, text_color=TEXT)
        entry.pack(side='left', fill='x', expand=True)
        if kind != 'text':
            self.button(row, T('Огляд', 'Overview'), lambda: self._browse(key, kind), 'ghost', width=74,
                        lock=False).pack(side='left', padx=(8, 0))
        return row

    def actions(self, parent, items, pady=(10, 0)):
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', pady=pady)
        for text, command, kind in items:
            self.button(row, text, command, kind).pack(side='left', padx=(0, 8))
        return row

    def check(self, parent, text, flag):
        box = ctk.CTkCheckBox(parent, text=text, variable=self.flags[flag], font=self.f(12), text_color=TEXT,
                              fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=MUTED,
                              checkbox_width=18, checkbox_height=18, corner_radius=5, border_width=2)
        box.pack(anchor='w', pady=4)
        return box

    def note(self, parent, text, warn=False):
        self.label(parent, text, 12, color=WARN if warn else MUTED, wraplength=820).pack(fill='x', pady=(8, 0))

    def more(self, parent, title=None):
        """Collapsible block for rarely changed settings."""
        title = title or T('Шляхи та параметри', 'Paths and options')
        holder = ctk.CTkFrame(parent, fg_color='transparent')
        holder.pack(fill='x', pady=(10, 0))
        body = ctk.CTkFrame(holder, fg_color='transparent')
        toggle = ctk.CTkButton(holder, text='▸  ' + title, anchor='w', height=28, fg_color='transparent',
                               hover_color=SOFT, text_color=MUTED, font=self.f(12), corner_radius=8)
        toggle.pack(anchor='w')

        def flip():
            if body.winfo_manager():
                body.pack_forget(); toggle.configure(text='▸  ' + title)
            else:
                body.pack(fill='x', pady=(6, 0)); toggle.configure(text='▾  ' + title)
        toggle.configure(command=flip)
        return body

    def preview_slot(self, parent, key, title):
        """Place where a generated font preview is shown inside the app."""
        box = self.card(parent, title, T('Зображення зібрано з уже запакованого атласу — так гліфи бачить гра.', 'Rendered from the already packed atlas — this is what the game sees.'))
        image = ctk.CTkLabel(box, text=T('Прев’ю з’явиться після створення шрифту.', 'The preview appears after the font is created.'), font=self.f(12),
                             text_color=MUTED, anchor='w')
        image.pack(fill='x')
        self.previews[key] = image

    def show_preview(self, key, path):
        if not path or not Path(path).is_file():
            return
        try:
            from PIL import Image
            picture = Image.open(path); picture.load()
        except Exception as exc:                       # preview is optional
            self._log(T(f'Прев’ю не показано: {exc}', f'Preview not shown: {exc}')); return
        width = min(860, picture.width)
        size = (width, round(picture.height * width / picture.width))
        self.previews[key].configure(text='', image=ctk.CTkImage(picture, picture, size=size))
        self._shown_previews[key] = path

    # ------------------------------------------------------------------- layout
    def _build(self):
        self.grid_columnconfigure(1, weight=1); self.grid_rowconfigure(0, weight=1)
        side = ctk.CTkFrame(self, width=232, corner_radius=0, fg_color=SIDEBAR)
        side.grid(row=0, column=0, sticky='nsw'); side.grid_propagate(False); side.pack_propagate(False)
        brand = ctk.CTkFrame(side, fg_color='transparent')
        brand.pack(fill='x', padx=20, pady=(22, 18))
        ctk.CTkLabel(brand, text='N', width=38, height=38, corner_radius=10, fg_color=ACCENT,
                     text_color='#FFFFFF', font=self.f(20, 'bold')).pack(side='left')
        names = ctk.CTkFrame(brand, fg_color='transparent'); names.pack(side='left', padx=10)
        self.label(names, 'NARUTO UA', 15, 'bold').pack(anchor='w')
        self.label(names, T('Toolkit локалізації', 'Localization toolkit'), 11, color=MUTED).pack(anchor='w')

        self.nav: dict = {}
        self.pages: dict = {}
        self._nav_item(side, 'home', T('Огляд', 'Overview'))
        self.label(side, T('ІГРИ', 'GAMES'), 10, 'bold', MUTED).pack(fill='x', padx=24, pady=(16, 4))
        for game, (name, *_rest) in GAMES.items():
            self._nav_item(side, game, name, game)
        self.label(side, T('ІНСТРУМЕНТИ', 'TOOLS'), 10, 'bold', MUTED).pack(fill='x', padx=24, pady=(16, 4))
        self._nav_item(side, 'tools', T('Звірка ресурсів', 'Resource check'))

        foot = ctk.CTkFrame(side, fg_color='transparent')
        foot.pack(side='bottom', fill='x', padx=18, pady=16)
        self.language = ctk.CTkSegmentedButton(foot, values=list(i18n.LANGUAGES.values()),
                                               command=self._pick_language, font=self.f(12),
                                               selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                               fg_color=SOFT, unselected_color=SOFT,
                                               unselected_hover_color=SOFT_HOVER, text_color=TEXT)
        self.language.set(i18n.LANGUAGES[i18n.LANG])
        self.language.pack(fill='x')
        self.label(foot, f'v{VERSION}', 11, color=MUTED).pack(anchor='w', pady=(10, 0))
        about = ctk.CTkFrame(side, fg_color='transparent')
        about.pack(side='bottom', fill='x')
        self._nav_item(about, 'about', T('Про програму', 'About'))

        main = ctk.CTkFrame(self, fg_color='transparent')
        main.grid(row=0, column=1, sticky='nsew')
        main.grid_rowconfigure(0, weight=1); main.grid_columnconfigure(0, weight=1)
        self.host = ctk.CTkFrame(main, fg_color='transparent')
        self.host.grid(row=0, column=0, sticky='nsew', padx=24, pady=(20, 8))
        self.host.grid_rowconfigure(0, weight=1); self.host.grid_columnconfigure(0, weight=1)

        self.log_box = ctk.CTkTextbox(main, height=170, font=ctk.CTkFont(family='Consolas', size=12),
                                      fg_color=CARD, border_color=BORDER, border_width=1, corner_radius=12,
                                      text_color=TEXT, state='disabled', wrap='word')
        bar = ctk.CTkFrame(main, fg_color=CARD, corner_radius=0, height=58)
        bar.grid(row=2, column=0, sticky='ew'); bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(bar, height=3, corner_radius=0, progress_color=ACCENT, fg_color=TRACK,
                                           indeterminate_speed=4)
        self.progress.grid(row=0, column=0, columnspan=2, sticky='ew'); self.progress.set(0)
        info = ctk.CTkFrame(bar, fg_color='transparent')
        info.grid(row=1, column=0, sticky='ew', padx=22, pady=(7, 0))
        ctk.CTkLabel(info, textvariable=self.status, font=self.f(13), text_color=TEXT, anchor='w').pack(fill='x')
        ctk.CTkLabel(info, textvariable=self.detail, font=self.f(11), text_color=MUTED, anchor='w',
                     height=16).pack(fill='x')
        self.log_toggle = self.button(bar, T('Журнал', 'Log'), self._toggle_log, 'ghost', width=96, lock=False)
        self.log_toggle.grid(row=1, column=1, padx=20, pady=(8, 0))

        self._page_home(); self._page_s1(); self._page_s2(); self._page_s3(); self._page_tools()
        self._page_about()
        self.show(self._current)
        for index, key in enumerate(('home', 's1', 's2', 's3', 'tools')):
            self.bind_all(f'<Control-Key-{index + 1}>', lambda _e, k=key: self.show(k))

    def _nav_item(self, parent, key, text, game=None):
        row = ctk.CTkFrame(parent, fg_color='transparent', corner_radius=10, height=42)
        row.pack(fill='x', padx=12, pady=2); row.pack_propagate(False)
        mark = ctk.CTkFrame(row, width=3, height=18, corner_radius=2, fg_color='transparent')
        mark.pack(side='left', padx=(6, 9))
        name = self.label(row, text, 13); name.pack(side='left')
        widgets = [row, name]
        if game:
            badge = self.label(row, '—', 12, 'bold', MUTED); badge.pack(side='right', padx=12)
            self.readiness_widgets[game].append(('badge', badge)); widgets.append(badge)
        for widget in widgets:
            widget.bind('<Button-1>', lambda _e, k=key: self.show(k))
            widget.configure(cursor='hand2')
        self.nav[key] = (row, mark, name)

    def show(self, key):
        self._current = key
        for name, page in self.pages.items():
            if name == key:
                page.grid(row=0, column=0, sticky='nsew')
            else:
                page.grid_forget()
        for name, (row, mark, text) in self.nav.items():
            active = name == key
            row.configure(fg_color=NAV_ACTIVE if active else 'transparent')
            mark.configure(fg_color=ACCENT if active else 'transparent')
            text.configure(font=self.f(13, 'bold' if active else 'normal'))

    def _page(self, key, title, subtitle, game=None):
        page = ctk.CTkFrame(self.host, fg_color='transparent')
        self.pages[key] = page
        head = ctk.CTkFrame(page, fg_color='transparent'); head.pack(fill='x', pady=(0, 12))
        left = ctk.CTkFrame(head, fg_color='transparent'); left.pack(side='left', fill='x', expand=True)
        self.label(left, title, 24, 'bold').pack(anchor='w')
        self.label(left, subtitle, 13, color=MUTED).pack(anchor='w')
        if game:
            box = ctk.CTkFrame(head, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
            box.pack(side='right')
            inner = ctk.CTkFrame(box, fg_color='transparent'); inner.pack(padx=16, pady=10)
            top = ctk.CTkFrame(inner, fg_color='transparent'); top.pack(fill='x')
            self.label(top, T('Готовність перекладу', 'Translation progress'), 11, color=MUTED).pack(side='left')
            percent = self.label(top, '—', 15, 'bold', ACCENT); percent.pack(side='right', padx=(24, 0))
            meter = ctk.CTkProgressBar(inner, width=250, height=7, progress_color=ACCENT, fg_color=TRACK)
            meter.set(0); meter.pack(pady=(6, 4))
            count = self.label(inner, T('TXT ще не створено', 'No TXT yet'), 11, color=MUTED); count.pack(anchor='w')
            self.readiness_widgets[game] += [('percent', percent), ('bar', meter), ('count', count)]
        return page

    def _tabs(self, page, names):
        view = ctk.CTkTabview(page, fg_color='transparent', corner_radius=12, border_width=0,
                              segmented_button_fg_color=SOFT, segmented_button_selected_color=ACCENT,
                              segmented_button_selected_hover_color=ACCENT_HOVER,
                              segmented_button_unselected_color=SOFT,
                              segmented_button_unselected_hover_color=SOFT_HOVER, text_color=TEXT, anchor='w')
        view.pack(fill='both', expand=True)
        view._segmented_button.configure(font=self.f(13))
        frames = []
        for name in names:
            tab = view.add(name)
            scroll = ctk.CTkScrollableFrame(tab, fg_color='transparent', scrollbar_button_color=SOFT,
                                            scrollbar_button_hover_color=SOFT_HOVER)
            scroll.pack(fill='both', expand=True)
            frames.append(scroll)
        return frames

    # -------------------------------------------------------------------- pages
    def _page_home(self):
        page = self._page('home', T('Огляд', 'Overview'), T('Стан української локалізації трьох ігор', 'Status of the Ukrainian localization of the three games'))
        grid = ctk.CTkFrame(page, fg_color='transparent'); grid.pack(fill='x')
        for column, (game, (name, full, *_rest)) in enumerate(GAMES.items()):
            grid.grid_columnconfigure(column, weight=1, uniform='g')
            card = ctk.CTkFrame(grid, fg_color=CARD, corner_radius=16, border_width=1, border_color=BORDER)
            card.grid(row=0, column=column, sticky='nsew', padx=(0 if column == 0 else 12, 0))
            body = ctk.CTkFrame(card, fg_color='transparent'); body.pack(fill='both', padx=20, pady=18)
            self.label(body, name, 17, 'bold').pack(anchor='w')
            self.label(body, full, 12, color=MUTED).pack(anchor='w')
            percent = self.label(body, '—', 38, 'bold', ACCENT); percent.pack(anchor='w', pady=(14, 2))
            meter = ctk.CTkProgressBar(body, height=8, progress_color=ACCENT, fg_color=TRACK)
            meter.set(0); meter.pack(fill='x', pady=(0, 8))
            count = self.label(body, T('TXT ще не створено', 'No TXT yet'), 12, color=MUTED); count.pack(anchor='w')
            self.button(body, T('Відкрити', 'Open'), lambda g=game: self.show(g), 'soft', lock=False).pack(anchor='w', pady=(16, 0))
            self.readiness_widgets[game] += [('percent', percent), ('bar', meter), ('count', count)]
        tips = self.card(page, T('Як рахується готовність', 'How progress is counted'))
        tips.master.pack_configure(pady=(14, 0))
        self.note(tips, T('Відсоток — частка записів спільного TXT, де є переклад, відмінний від англійського оригіналу. Значення оновлюється після кожної операції та зміни робочої папки.', 'The percentage is the share of TXT records whose translation is present and differs from the English source. It is refreshed after every operation and whenever the workspace folder changes.'))
        self.note(tips, T('Перед перевіркою й пакуванням програма сама відновлює порожній рядок перед «###», якщо його випадково видалили; попередня версія файлу лишається поруч як .before_autofix.bak.', 'Before verifying and packing, the tool restores the empty line before "###" if it was deleted by accident; the previous version of the file is kept next to it as .before_autofix.bak.'))
        self.button(tips, T('Перерахувати зараз', 'Recount now'), self.refresh_readiness, 'ghost', lock=False).pack(anchor='w', pady=(12, 0))

    def _page_s1(self):
        page = self._page('s1', 'STORM 1', T('Тексти, Telop, текстури та шрифт першої гри', 'Text, Telop, textures and font of the first game'), 's1')
        texts, tex, font = self._tabs(page, [T('Тексти', 'Text'), T('Текстури', 'Textures'), T('Шрифт', 'Font')])
        c = self.card(texts, T('Переклад', 'Translation'), T('Діалоги, інтерфейс і Telop в одному TXT.', 'Dialogue, interface and Telop in one TXT.'))
        self.path(c, T('Папка перекладу', 'Translation folder'), 'workspace')
        self.actions(c, [(T('Експорт TXT', 'Export TXT'), self.s1_export, 'soft'), (T('Перевірити', 'Verify'), self.s1_verify, 'soft'),
                         (T('Зібрати й установити', 'Build and install'), self.s1_build, 'primary'),
                         (T('Відкрити TXT', 'Open TXT'), lambda: self.open_txt('s1'), 'ghost')])
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

        c = self.card(font, T('Українські літери з TTF / OTF', 'Ukrainian letters from TTF / OTF'),
                      T('Оригінальні гліфи зберігаються; нові додаються в окремі рядки атласу з проміжками.', 'Original glyphs are kept; new ones go into separate atlas rows with gaps between cells.'))
        self.path(c, T('Оригінальний шрифт гри', 'Original game font'), 'font_source', 'xfbin')
        self.path(c, T('Шрифт TTF / OTF', 'TTF / OTF font'), 'ttf_source', 'ttf')
        self.path(c, T('Шлях у data_win32', 'Path inside data_win32'), 'font_rel', 'text')
        self.actions(c, [(T('Створити шрифт', 'Create font'), self.s1_font_make, 'primary'),
                         (T('Відкрити прев’ю', 'Open preview'), lambda: self.open_preview('s1'), 'ghost')])
        m = self.more(c, T('Параметри гліфів', 'Glyph options'))
        self.check(m, T('Мала «р» — форма оригінальної латинської p', 'Lowercase «р» — shape of the original Latin p'), 's1_p')
        self.check(m, T('Мала «у» — форма оригінальної латинської y', 'Lowercase «у» — shape of the original Latin y'), 's1_y')
        self.check(m, T('Додати довге тире «—» (копія оригінального U+2015)', 'Add the em dash «—» (a copy of the original U+2015)'), 's1_dash')
        self.check(m, T('Додати інші знаки: ', 'Add other signs: ') + font1.OPTIONAL_SYMBOLS.replace('—', ''), 's1_symbols')
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
        c = self.card(texts, T('Переклад', 'Translation'), T('Один TXT для messageInfo; збірка тестова, без установлення в гру.', 'One TXT for messageInfo; a test build, nothing is installed into the game.'))
        self.path(c, T('Папка перекладу', 'Translation folder'), 'storm2_workspace')
        self.actions(c, [(T('Експорт TXT', 'Export TXT'), self.s2_export, 'soft'), (T('Перевірити', 'Verify'), self.s2_verify, 'soft'),
                         (T('Зібрати XFBIN', 'Build XFBIN'), self.s2_build, 'primary'),
                         (T('Відкрити TXT', 'Open TXT'), lambda: self.open_txt('s2'), 'ghost')])
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
        self.path(c, T('Шрифт TTF / OTF', 'TTF / OTF font'), 'storm2_ttf_source', 'ttf')
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
        c = self.card(texts, T('Переклад', 'Translation'), T('Спільний TXT збирається у Full Burst, HD WIN32 та HD WIN64.', 'The shared TXT is built for Full Burst, HD WIN32 and HD WIN64.'))
        self.path(c, T('Папка перекладу', 'Translation folder'), 'storm3_workspace')
        self.actions(c, [(T('Експорт TXT', 'Export TXT'), self.s3_export, 'soft'), (T('Перевірити', 'Verify'), self.s3_verify, 'soft'),
                         (T('Зібрати й установити', 'Build and install'), self.s3_build, 'primary'),
                         (T('Відкрити TXT', 'Open TXT'), lambda: self.open_txt('s3'), 'ghost')])
        self.actions(c, [(T('Відкрити Build', 'Open Build'), lambda: self.open_dir('storm3_build'), 'ghost'),
                         (T('Відкотити встановлення', 'Roll back install'), self.s3_restore, 'ghost')])
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
        self.path(c, T('Шрифт TTF / OTF', 'TTF / OTF font'), 'storm3_font_ttf', 'ttf')
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
        c = self.card(page, f'NARUTO UA Toolkit {VERSION}')
        for caption, value in ((T('Автор', 'Author'), AUTHOR), ('Telegram', TELEGRAM)):
            row = ctk.CTkFrame(c, fg_color='transparent'); row.pack(fill='x', pady=3)
            self.label(row, caption, 12, color=MUTED, width=110).pack(side='left')
            self.label(row, value, 14, 'bold').pack(side='left')
        self.label(c, 'Made in Ukraine', 14, 'bold', ACCENT).pack(anchor='w', pady=(12, 0))
        self.actions(c, [(TELEGRAM, lambda: webbrowser.open('https://' + TELEGRAM), 'primary')], pady=(14, 0))
        self.controls.pop()        # the link stays clickable while a task runs

    # ------------------------------------------------------------ infrastructure
    def _browse(self, key, kind):
        current = Path(self.p[key].get() or Path.home())
        start = current if current.is_dir() else current.parent
        start = str(start if start.is_dir() else Path.home())
        if kind == 'dir':
            picked = filedialog.askdirectory(initialdir=start)
        elif kind == 'ttf':
            picked = filedialog.askopenfilename(initialdir=start, filetypes=[('TTF / OTF', '*.ttf *.otf'), (T('Усі файли', 'All files'), '*.*')])
        else:
            picked = filedialog.askopenfilename(initialdir=start, filetypes=[('XFBIN', '*.xfbin'), (T('Усі файли', 'All files'), '*.*')])
        if not picked:
            return
        self.p[key].set(picked)
        guess = {'font_source': 'font_rel', 'storm2_font_source': 'storm2_font_rel', 'texture_source': 'texture_rel'}.get(key)
        if guess:
            rel = font1.relative_font_path(picked)
            if rel:
                self.p[guess].set(rel)

    def _paths_changed(self, *_):
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(700, self._save)
        if self._scan_job:
            self.after_cancel(self._scan_job)
        self._scan_job = self.after(1500, self.refresh_readiness)

    def _save(self):
        self._save_job = None
        try:
            target = config_path(); target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name('settings.json.tmp')
            tmp.write_text(json.dumps({k: v.get() for k, v in self.p.items()}, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
            tmp.replace(target)
        except OSError as exc:
            self._log(T('Не вдалося зберегти налаштування: ', 'Could not save settings: ') + str(exc))

    def _close(self):
        process = self._process
        if self.working and process is not None and process.poll() is None:
            if not messagebox.askyesno('NARUTO UA Toolkit', T(
                    'Операція ще триває. Закрити програму й перервати її?',
                    'An operation is still running. Close the program and stop it?')):
                return
            process.terminate()
        self._save(); self.destroy()

    def _pick_language(self, value):
        code = next(k for k, name in i18n.LANGUAGES.items() if name == value)
        if code == i18n.LANG:
            return
        if self.working:                               # never rebuild the window under a running task
            self.language.set(i18n.LANGUAGES[i18n.LANG]); return
        self.after(20, lambda: self._rebuild(code))    # let the button finish its own click first

    def _rebuild(self, code):
        """Recreate every widget in the other language; paths and state are kept."""
        i18n.set_language(code); self.p['lang'].set(code)
        log_text = self.log_box.get('1.0', 'end-1c')
        log_open = bool(self.log_box.winfo_manager())
        for child in self.winfo_children():
            child.destroy()
        self.controls.clear(); self.previews.clear()
        self.readiness_widgets = {g: [] for g in GAMES}
        self._build()
        if log_text:
            self.log_box.configure(state='normal'); self.log_box.insert('end', log_text + '\n')
            self.log_box.configure(state='disabled')
        if log_open:
            self._toggle_log()
        for key, path in list(self._shown_previews.items()):
            self.show_preview(key, path)
        self.status.set(T('Готово. Оберіть гру ліворуч.', 'Ready. Pick a game on the left.')); self.detail.set('')
        self._paint_readiness()

    def reset_paths(self):
        if messagebox.askyesno(T('Стандартні шляхи', 'Default paths'), T('Повернути всі шляхи до початкових значень?', 'Reset all paths to their defaults?')):
            for key, value in DEFAULT_PATHS.items():
                if key != 'lang':
                    self.p[key].set(value)

    def _toggle_log(self):
        if self.log_box.winfo_manager():
            self.log_box.grid_forget(); self.log_toggle.configure(text=T('Журнал', 'Log'))
        else:
            self.log_box.grid(row=1, column=0, sticky='ew', padx=24, pady=(0, 10))
            self.log_toggle.configure(text=T('Сховати', 'Hide'))

    def _log(self, value):
        self.events.put(('log', str(value)))

    def run(self, label, target, *args, done=None, **kwargs):
        """Start a backend operation in its own process (see jobs.py).

        `target` is 'module.function'; jobs.LOG / jobs.PROGRESS among the
        arguments become reporting callbacks. `done(result)` runs on success.
        Buttons are deliberately NOT restyled while the job runs: on Windows a
        restyled button could stay blank until the job ended.
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
        self.status.set(T('Виконується: ', 'Running: ') + label); self.detail.set('')
        self.progress.configure(mode='indeterminate'); self._spin = True
        threading.Thread(target=self._pump, args=(self._process, spec, label, done), daemon=True).start()

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
                if kind in ('log', 'progress'):
                    self.events.put((kind, payload))
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
        self.events.put((final[0], (label, final[1], done if final[0] == 'success' else None)))

    def _poll(self):
        """Drain job events. One bad event must never stop the loop, or the
        interface would stop noticing that operations finish."""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                try:
                    self._handle(kind, payload)
                except Exception as exc:               # keep the interface alive
                    self._append_log(T(f'Внутрішня помилка інтерфейсу: {exc!r}', f'Internal interface error: {exc!r}'))
        except queue.Empty:
            pass
        finally:
            try:
                if self.working and self._spin:
                    self.progress.step()               # animated here, not by a 20 ms timer of its own
                self.after(120, self._poll)
            except tk.TclError:                        # window already closed
                pass

    def _append_log(self, text):
        self.log_box.configure(state='normal'); self.log_box.insert('end', text + '\n')
        self.log_box.see('end'); self.log_box.configure(state='disabled')

    def _handle(self, kind, payload):
        if kind == 'log':
            self._append_log(payload)
        elif kind == 'progress':
            current, total, text = int(payload[0]), int(payload[1]), str(payload[2])
            if total > 0:
                self._spin = False; self.progress.configure(mode='determinate')
                self.progress.set(min(1.0, current / total))
                text = f'{current} / {total} · {text}'
            self.detail.set(text[:160])
        elif kind == 'readiness':
            self._scan_busy = False
            self.readiness = payload; self._paint_readiness()
        else:
            label, result, done = payload
            self.working = False                       # first, so nothing below can leave it stuck
            self._process = None
            self._spin = False; self.progress.configure(mode='determinate'); self.progress.set(0)
            self.refresh_readiness()
            if kind == 'success':
                self.status.set(label + T(' — виконано.', ' — done.'))
                self.detail.set(T('Подробиці — у журналі.', 'Details are in the log.'))
                self._append_log(T(f'ГОТОВО: {label}\n{summarize(result)}', f'DONE: {label}\n{summarize(result)}'))
                if done:
                    done(result)
            else:
                self.status.set(label + T(' — помилка.', ' — failed.')); self.detail.set('')
                self._append_log(T(f'ПОМИЛКА: {label}\n  {result}', f'ERROR: {label}\n  {result}'))
                messagebox.showerror(label, str(result)[:1800])

    # ------------------------------------------------------------------ readiness
    def txt_path(self, game) -> Path:
        _name, _full, key, filename = GAMES[game]
        return Path(self.p[key].get()) / filename

    def refresh_readiness(self):
        self._scan_job = None
        if self._scan_busy:
            return
        self._scan_busy = True
        targets = {game: self.txt_path(game) for game in GAMES}

        def worker():
            self.events.put(('readiness', {game: translation_progress(path) for game, path in targets.items()}))
        threading.Thread(target=worker, daemon=True).start()

    def _paint_readiness(self):
        for game, widgets in self.readiness_widgets.items():
            info = self.readiness.get(game)
            for kind, widget in widgets:
                if kind in ('percent', 'badge'):
                    widget.configure(text=fmt_percent(info))
                elif kind == 'bar':
                    widget.set(info['percent'] / 100 if info and info['total'] else 0)
                elif info and info['total']:
                    widget.configure(text=fmt_count(info))
                else:
                    widget.configure(text=T('TXT ще не створено', 'No TXT yet'))

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
        self.run(T('STORM 1 · Збирання й установлення', 'STORM 1 · Build and install'), 'storm1_text.build_and_install', p['eng'].get(), p['workspace'].get(), p['output'].get(), p['game'].get(), telop_dir=p['telop'].get(), log=jobs.LOG)

    def s1_telop(self):
        p = self.p
        self.run(T('STORM 1 · Додавання Telop', 'STORM 1 · Add Telop'), 'storm1_text.augment_existing_telop', p['workspace'].get(), p['telop'].get(), log=jobs.LOG)

    def s1_font_make(self):
        if not self.need('font_source', 'ttf_source', files=True) or not self.need('font_rel'):
            return
        p, fl = self.p, self.flags
        native = fl['s1_native_metrics'].get()
        self.run(T('STORM 1 · Український шрифт', 'STORM 1 · Ukrainian font'), 'storm1_font.generate_additive_font', p['font_source'].get(), p['ttf_source'].get(), p['workspace'].get(), p['output'].get(), p['font_rel'].get(), include_symbols=fl['s1_dash'].get(), include_other_symbols=fl['s1_symbols'].get(), override=True, log=jobs.LOG, latin_p_for_r=fl['s1_p'].get(), latin_y_for_u=fl['s1_y'].get(), metrics_mode='native_matched' if native else 'stable', done=self._font_done('s1'))

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
        self.run(label, 'storm2_text.compile_project', p['storm2_eng'].get(), p['storm2_workspace'].get(), p['storm2_output'].get(), dry_run=dry, allow_replace=True, log=jobs.LOG)

    def s2_verify(self):
        self._s2_compile(T('STORM 2 · Перевірка TXT', 'STORM 2 · Verify TXT'), True)

    def s2_build(self):
        self._s2_compile(T('STORM 2 · Збирання XFBIN', 'STORM 2 · Build XFBIN'), False)

    def s2_font_make(self):
        if not self.need('storm2_font_source', 'storm2_ttf_source', files=True) or not self.need('storm2_font_rel'):
            return
        p = self.p
        self.run(T('STORM 2 · Український шрифт', 'STORM 2 · Ukrainian font'), 'storm2_font.generate_additive_font', p['storm2_font_source'].get(), p['storm2_ttf_source'].get(), p['storm2_font_workspace'].get(), p['storm2_output'].get(), p['storm2_font_rel'].get(), override=True, log=jobs.LOG, copy_native_lookalikes=self.flags['s2_twins'].get(), done=self._font_done('s2'))

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
        self.run(T('STORM 3 · Збирання й установлення', 'STORM 3 · Build and install'), 'storm3_text.compile_project', paths, p['storm3_workspace'].get(), p['storm3_build'].get(), dry_run=False, install_root=p['storm3_game'].get(), log=jobs.LOG)

    def s3_restore(self):
        if not messagebox.askyesno(T('STORM 3 · відкат', 'STORM 3 · rollback'), T('Відновити файли гри з резервних копій останнього встановлення?', 'Restore the game files from the backups of the last install?')):
            return
        self.run(T('STORM 3 · Відкат', 'STORM 3 · Rollback'), 'jobs.s3_restore',
                 self.p['storm3_build'].get(), self.p['storm3_game'].get(), log=jobs.LOG)

    def s3_font_make(self):
        if not self.need('storm3_font_a', 'storm3_font_ttf', files=True):
            return
        if not messagebox.askyesno(T('STORM 3 · шрифт', 'STORM 3 · font'), T('Створити шрифт і встановити його у знайдені редакції гри?\n\nПопередні файли збережуться в Build для відкату.', 'Create the font and install it into the detected game editions?\n\nPrevious files are kept in Build for rollback.')):
            return
        p = self.p
        self.run(T('STORM 3 · Шрифт і встановлення', 'STORM 3 · Font and install'), 'storm3_text.build_font_and_install', p['storm3_font_a'].get(), p['storm3_font_ttf'].get(), p['storm3_font_work'].get(), p['storm3_build'].get(), p['storm3_game'].get(), russian_reference=p['storm3_font_b'].get().strip() or None, copy_native_lookalikes=self.flags['s3_twins'].get(), log=jobs.LOG, done=self._font_done('s3'))

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


if __name__ == '__main__':
    App().mainloop()
