# -*- coding: utf-8 -*-
"""Runs ONE backend operation in its own process.

The interface starts `python jobs.py`, writes a JSON job description to its
standard input and reads JSON lines back:

    ["log", "text"]   ["progress", [current, total, "text"]]
    ["success", result]   ["error", "message"]

Heavy work in a separate process cannot slow the window down or keep it from
repainting, which a worker thread did (buttons went blank during a build).
"""
from __future__ import annotations

import importlib
import json
import sys

LOG = '<<log>>'              # replaced by a callback that reports a log line
PROGRESS = '<<progress>>'    # replaced by a callback that reports progress


# ---- small operations that need more than one backend call -----------------

def s1_font_inspect(path):
    import storm1_font as font
    from i18n import T
    doc = font.load_font(path)
    return {T('файл', 'file'): doc.path.name, T('гліфів', 'glyphs'): len(doc.glyphs),
            T('атлас', 'atlas'): f'{doc.width} × {doc.height}',
            T('бракує українських', 'missing Ukrainian letters'): font.missing_ukrainian(doc) or T('немає', 'none'),
            'sha256': doc.digest}


def s1_font_export(source, workspace, log=None):
    import storm1_font as font
    return font.export_font(font.load_font(source), workspace, log=log)


def s2_font_export(source, workspace, log=None):
    import storm2_font as font
    return font.export_font(font.load_font(source), workspace, log=log)


def texture_inspect(source):
    import textures
    return '\n'.join(f'  chunk {e.chunk_index}: {e.chunk_name} · {e.width}×{e.height} · '
                     f'{"BC1/DXT1" if e.nut_format == 0 else "BC3/DXT5"}'
                     for e in textures.load_entries(source, skip_unsupported=True)[1])


def s3_restore(build, game, log=None):
    from pathlib import Path
    import storm3_text as text
    if (Path(build) / 'STORM3_UNIFIED_LAST_INSTALL.json').exists():
        return text.restore_last(build, game, log=log)
    return text.restore_last_install(build, game, log=log)


def selftest(mode='ok', log=None, progress=None):
    """Diagnostic job used by the test suite."""
    if log:
        log('selftest: ' + mode)
    if progress:
        progress(1, 2, 'half')
    if mode == 'fail':
        raise ValueError('job failed')
    if mode == 'crash':
        import os
        os._exit(3)
    if mode == 'print':
        print('stray output must not break the protocol')
    return {'ok': 1, 'mode': mode, 'preview': 'missing.png'}


# ---- process entry point -----------------------------------------------------

def main():
    channel = sys.stdout.buffer
    sys.stdout = sys.stderr              # stray print() calls must not corrupt the protocol

    def emit(kind, payload):
        channel.write((json.dumps([kind, payload], ensure_ascii=False, default=str) + '\n').encode('utf-8'))
        channel.flush()

    try:
        spec = json.loads(sys.stdin.buffer.read().decode('utf-8'))
        import i18n
        i18n.set_language(spec.get('lang'))
        callbacks = {LOG: lambda message: emit('log', str(message)),
                     PROGRESS: lambda current, total, text='': emit('progress', [current, total, str(text)])}
        module, _, name = spec['target'].rpartition('.')
        function = getattr(sys.modules[__name__] if module == 'jobs' else importlib.import_module(module), name)
        args = [callbacks.get(a, a) if isinstance(a, str) else a for a in spec.get('args', [])]
        kwargs = {key: callbacks.get(value, value) if isinstance(value, str) else value
                  for key, value in spec.get('kwargs', {}).items()}
        emit('success', function(*args, **kwargs))
    except BaseException as exc:         # report everything, including SystemExit from a backend
        emit('error', str(exc) or exc.__class__.__name__)


if __name__ == '__main__':
    main()
