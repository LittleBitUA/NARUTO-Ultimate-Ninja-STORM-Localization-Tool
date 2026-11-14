# -*- coding: utf-8 -*-
"""Interface language: Ukrainian or English, picked from the operating system.

Usage everywhere:  T('Український текст', 'English text')
"""
from __future__ import annotations

import locale
import os

LANGUAGES = {'uk': 'Українська', 'en': 'English'}
LANG = 'en'


def detect() -> str:
    """Ukrainian when the system UI language is Ukrainian, otherwise English."""
    if os.name == 'nt':
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return 'uk' if (langid & 0x3FF) == 0x22 else 'en'      # 0x22 = LANG_UKRAINIAN
        except Exception:
            pass
    for name in ('LC_ALL', 'LC_MESSAGES', 'LANGUAGE', 'LANG'):
        value = os.environ.get(name, '')
        if value and value not in ('C', 'POSIX'):
            return 'uk' if value.lower().startswith('uk') else 'en'
    try:
        code = locale.getlocale()[0] or ''
    except (ValueError, TypeError):
        code = ''
    return 'uk' if code.lower().startswith(('uk', 'ukrainian')) else 'en'


def set_language(code: str | None) -> str:
    """Set the active language; anything but 'uk' / 'en' means "follow the system"."""
    global LANG
    LANG = code if code in LANGUAGES else detect()
    return LANG


def T(uk: str, en: str) -> str:
    return uk if LANG == 'uk' else en


set_language(None)
