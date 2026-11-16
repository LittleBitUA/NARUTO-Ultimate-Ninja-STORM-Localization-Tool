// NARUTO UA Toolkit — the page. It draws what natool.py describes (/api/ui),
// shows what natool.py reports (/api/state) and sends back what the user does.
'use strict';

const TOKEN = new URLSearchParams(location.search).get('t') || '';
const $ = id => document.getElementById(id);

const ICONS = {
  scroll: '<svg viewBox="0 0 30 20" aria-hidden="true"><rect x="6" y="3" width="18" height="14" fill="#F1E3C4" stroke="#2A1B0E" stroke-width="1.6"/><path d="M10 8h10M10 12h7" stroke="#2A1B0E" stroke-width="1.8" stroke-linecap="round"/><rect x="2.5" y="1.5" width="4.5" height="17" rx="2.2" fill="#8A5A2E" stroke="#2A1B0E" stroke-width="1.6"/><rect x="23" y="1.5" width="4.5" height="17" rx="2.2" fill="#8A5A2E" stroke="#2A1B0E" stroke-width="1.6"/></svg>',
  plate: n => `<svg viewBox="0 0 30 20" aria-hidden="true"><rect x="1" y="2" width="28" height="16" rx="3" fill="#C9D1DB" stroke="#5E6978" stroke-width="2"/><circle cx="5.5" cy="10" r="1.4" fill="#5E6978"/><circle cx="24.5" cy="10" r="1.4" fill="#5E6978"/><text x="15" y="14.4" text-anchor="middle" font-family="Segoe UI, sans-serif" font-weight="800" font-size="12" fill="#1B2430">${n}</text></svg>`,
  eye: '<svg viewBox="0 0 30 20" aria-hidden="true" fill="none" stroke="#FF7B00" stroke-width="2.2" stroke-linejoin="round"><path d="M2 10c4-5.5 8.5-8 13-8s9 2.5 13 8c-4 5.5-8.5 8-13 8S6 15.5 2 10z"/><circle cx="15" cy="10" r="3.6"/></svg>',
  info: '<svg viewBox="0 0 30 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="15" cy="10" r="8"/><path d="M15 9v5M15 6v.4"/></svg>',
  star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 1l3 8 8 3-8 3-3 8-3-8-8-3 8-3z"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
};
const KANJI = { home: '嵐', s1: '壱', s2: '弐', s3: '参', tools: '探', about: '忍' };
const NUMERALS = ['壱', '弐', '参'];

let ui = null;            // page description from the host
let state = null;         // last state from the host
let page = 'home';
let logCursor = 0, logEpoch = -1;
let failures = 0;
const binders = [];       // functions that copy state into the page
const tabChoice = {};     // page key -> selected tab index
const dialogs = [];       // queued notices
let dialogOpen = false;

// ---- one fixed window size --------------------------------------------------
// The program window always has the same size, and every page is laid out to fit
// the area below without scrolling. Where the screen is too small for that window
// (a laptop, or Windows scaling above 100 %), the interface is scaled down as a
// whole, so nothing is ever cut off.
const DESIGN = { w: 1320, h: 800 };       // the smallest page area in which everything fits
let fitScale = 1;

function fit() {
  const app = $('app');
  fitScale = Math.min(1, innerWidth / DESIGN.w, innerHeight / DESIGN.h);
  document.documentElement.style.setProperty('--fit', fitScale);
  app.style.transform = fitScale < 1 ? `scale(${fitScale})` : '';
  app.style.width = fitScale < 1 ? innerWidth / fitScale + 'px' : '';
  app.style.height = fitScale < 1 ? innerHeight / fitScale + 'px' : '';
}

let snapTimer = 0, snappedAt = -1e9;
function snapWindow() {
  if (!ui) return;
  const WINDOW = ui.window;                // outer size of the program window, set by the host
  const area = { x: screen.availLeft || 0, y: screen.availTop || 0, w: screen.availWidth, h: screen.availHeight };
  if (outerWidth >= area.w && outerHeight >= area.h) return;         // maximized: leave it to the user
  const w = Math.min(WINDOW.w, area.w), h = Math.min(WINDOW.h, area.h);
  if (Math.abs(outerWidth - w) < 2 && Math.abs(outerHeight - h) < 2) return;
  if (performance.now() - snappedAt < 1500) return;                  // the browser refused: do not fight it
  snappedAt = performance.now();
  try {
    resizeTo(w, h);                                                  // ignored in an ordinary browser tab
    const x = Math.max(area.x, Math.min(screenX, area.x + area.w - w));
    const y = Math.max(area.y, Math.min(screenY, area.y + area.h - h));
    if (x !== screenX || y !== screenY) moveTo(x, y);                // keep the whole window on the screen
  } catch (error) { /* nothing to do */ }
}
addEventListener('resize', () => { fit(); clearTimeout(snapTimer); snapTimer = setTimeout(snapWindow, 200); });
// Browser zoom would change the size of everything inside the fixed window.
addEventListener('wheel', event => { if (event.ctrlKey) event.preventDefault(); }, { passive: false });
addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && ['+', '-', '=', '0'].includes(event.key)) event.preventDefault();
});

// ---- talking to the host ---------------------------------------------------
async function api(path, body) {
  const options = { headers: { 'X-Token': TOKEN } };
  if (body !== undefined) {
    options.method = 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const reply = await fetch(path, options);
  if (!reply.ok) throw new Error(path + ' → ' + reply.status);
  return reply.json();
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;          // constants from ICONS only
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

// ---- dialogs ----------------------------------------------------------------
function showDialog({ title, text, kind, buttons }) {
  return new Promise(resolve => {
    const box = $('veil').firstElementChild;
    box.className = 'dialog ' + (kind || '');
    $('dialog-title').textContent = title;
    $('dialog-text').textContent = text;
    const row = $('dialog-actions');
    row.replaceChildren();
    for (const button of buttons) {
      row.append(el('button', { type: 'button', class: 'btn ' + (button.primary ? 'primary' : 'ghost'),
        onclick: () => { $('veil').hidden = true; dialogOpen = false; resolve(button.value); pumpDialogs(); } }, button.label));
    }
    $('veil').hidden = false; dialogOpen = true;
    row.lastElementChild.focus();
  });
}
function pumpDialogs() {
  if (dialogOpen || !dialogs.length) return;
  const notice = dialogs.shift();
  showDialog({ title: notice.title, text: notice.text, kind: notice.kind === 'error' ? 'error' : '',
    buttons: [{ label: ui.strings.ok, value: true, primary: true }] });
}

// Runs a host action; the host may first ask a yes/no question.
async function act(id, extra) {
  const confirmed = [];
  for (let round = 0; round < 6; round++) {
    const reply = await api(extra ? extra.path : '/api/action', { id, confirmed, ...(extra ? extra.body : {}) });
    if (!reply.ask) break;
    const yes = await showDialog({ title: reply.ask.title, text: reply.ask.text,
      buttons: [{ label: ui.strings.no, value: false }, { label: ui.strings.yes, value: true, primary: true }] });
    if (!yes) return;
    confirmed.push(reply.ask.key);
  }
  refresh();
}

// ---- settings ---------------------------------------------------------------
const pending = {};
function setPath(key, value, now) {
  clearTimeout(pending[key]);
  const send = () => api('/api/set', { paths: { [key]: value } }).then(refresh);
  if (now) send(); else pending[key] = setTimeout(send, 350);
}

function pathInput(node, kind) {
  const input = el('input', { type: 'text', class: 'input', spellcheck: 'false', autocomplete: 'off',
    oninput: () => setPath(node.key, input.value),
    oncontextmenu: event => { event.preventDefault(); openMenu(event, node.key, input); } });
  binders.push(s => {
    if (document.activeElement !== input) input.value = s.paths[node.key] ?? '';
  });
  return input;
}
function browseButton(key, kind) {
  return el('button', { type: 'button', class: 'btn ghost small',
    onclick: () => api('/api/browse', { key, kind }).then(refresh) }, ui.strings.browse);
}

function openMenu(event, key, input) {
  const menu = $('menu');
  const value = input.value.trim();
  const item = (label, run, enabled = true) => el('li', { role: 'none' },
    el('button', { type: 'button', role: 'menuitem', disabled: !enabled, onclick: () => { menu.hidden = true; run(); } }, label));
  menu.replaceChildren(
    item(ui.strings.paste, async () => {
      try {
        const text = (await navigator.clipboard.readText()).trim().replace(/^"|"$/g, '');
        input.value = text; setPath(key, text, true);
      } catch (error) { input.focus(); }
    }),
    item(ui.strings.copy, () => navigator.clipboard.writeText(value), !!value),
    item(ui.strings.clear, () => { input.value = ''; setPath(key, '', true); }, !!value),
    item(ui.strings.openFolder, () => api('/api/folder', { key }), !!value));
  menu.hidden = false;
  const x = Math.min(event.clientX, innerWidth - menu.offsetWidth * fitScale - 8);
  const y = Math.min(event.clientY, innerHeight - menu.offsetHeight * fitScale - 8);
  menu.style.left = x + 'px'; menu.style.top = y + 'px';
}
document.addEventListener('click', event => { if (!$('menu').contains(event.target)) $('menu').hidden = true; });
document.addEventListener('keydown', event => { if (event.key === 'Escape') $('menu').hidden = true; });

// ---- drawing the page nodes -------------------------------------------------
let uid = 0;
const renderers = {
  card(node) {
    const head = (node.title || node.hint) && el('div', { class: 'card-head' },
      node.title && el('h2', {}, node.title), node.hint && el('p', {}, node.hint));
    return el('section', { class: 'card' }, head, renderAll(node.children));
  },
  path(node) {
    const id = 'f' + (++uid);
    const input = pathInput(node, node.kind); input.id = id;
    const error = el('span', { class: 'field-error', hidden: true }, ui.strings.missing);
    binders.push(s => {
      const bad = s.bad.includes(node.key);
      input.classList.toggle('bad', bad); error.hidden = !bad;
      if (bad) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
    });
    return el('div', { class: 'field' },
      el('label', { for: id }, node.label),
      el('div', { class: 'field-box' }, input, error),
      node.kind !== 'text' && browseButton(node.key, node.kind));
  },
  actions(node) {
    return el('div', { class: 'actions' }, node.buttons.map(button =>
      el('button', { type: 'button', class: 'btn ' + (button.kind === 'soft' ? '' : button.kind),
        onclick: () => act(button.id) },
        button.kind === 'primary' && el('span', { html: ICONS.star, style: 'display:contents' }), button.label)));
  },
  check(node) {
    const box = el('input', { type: 'checkbox',
      onchange: () => api('/api/set', { flags: { [node.key]: box.checked } }).then(refresh) });
    binders.push(s => { box.checked = !!s.flags[node.key]; });
    return el('label', { class: 'check' }, box, el('span', {}, node.label));
  },
  note(node) { return el('p', { class: 'note' + (node.warn ? ' warn' : '') }, node.text); },
  more(node) {
    const body = el('div', { class: 'more-body', hidden: true }, renderAll(node.children));
    const toggle = el('button', { type: 'button', class: 'more-toggle', 'aria-expanded': 'false',
      onclick: () => { body.hidden = !body.hidden; toggle.setAttribute('aria-expanded', String(!body.hidden)); } },
      el('span', { html: ICONS.chevron, style: 'display:contents' }), node.title);
    return el('div', { class: 'more' }, toggle, body);
  },
  ttf(node) {
    const name = 'ttf' + (++uid);
    const radio = value => el('input', { type: 'radio', name, value,
      onchange: () => setPath(node.mode, value, true) });
    const bundled = radio('bundled'), own = radio('own');
    const bundledRow = el('label', { class: 'choice' }, bundled,
      el('span', {}, el('b', {}, 'ReggaeC UA'), el('small', {}, node.bundled)));
    const ownRow = el('label', { class: 'choice' }, own, el('span', {}, node.own));
    const input = pathInput(node, 'ttf'); input.placeholder = node.placeholder;
    const error = el('span', { class: 'field-error', hidden: true }, ui.strings.missing);
    const line = el('div', { class: 'inline' }, el('div', { class: 'field-box' }, input, error), browseButton(node.key, 'ttf'));
    const hint = el('p', { class: 'note' }, node.hint);
    binders.push(s => {
      const mode = s.ttf[node.mode];
      bundled.checked = mode === 'bundled'; own.checked = mode === 'own';
      bundled.disabled = !s.bundled;
      bundledRow.className = 'choice' + (mode === 'bundled' ? ' on' : '') + (s.bundled ? '' : ' off');
      ownRow.className = 'choice' + (mode === 'own' ? ' on' : '');
      line.hidden = mode !== 'own';
      hint.hidden = s.bundled;                       // only needed when the bundled font is missing
      const bad = s.bad.includes(node.key);
      input.classList.toggle('bad', bad); error.hidden = !bad || mode !== 'own';
    });
    return el('fieldset', { class: 'group' }, el('legend', {}, node.label),
      el('div', { class: 'choices' }, bundledRow, ownRow), line, hint);
  },
  chars(node) {
    const id = 'f' + (++uid);
    const area = el('textarea', { id, class: 'input', rows: '2', spellcheck: 'false',
      oninput: () => { setPath(node.key, area.value); count(); } });
    const counter = el('span', {});
    const plural = n => {
      if (ui.lang !== 'uk') return node.count[n === 1 ? 0 : 1];
      const d = n % 10, h = n % 100;
      return node.count[d === 1 && h !== 11 ? 0 : d >= 2 && d <= 4 && (h < 12 || h > 14) ? 1 : 2];
    };
    const unique = text => [...new Set([...text].filter(ch => !/\s/.test(ch)))];
    function count() { const n = unique(area.value).length; counter.textContent = n + ' ' + plural(n); }
    const put = text => { area.value = text; setPath(node.key, text, true); count(); };
    binders.push(s => { if (document.activeElement !== area) { area.value = s.paths[node.key] ?? ''; count(); } });
    return el('div', { class: 'group' },
      el('div', { class: 'chars-head' }, el('label', { for: id }, node.label), counter),
      area,
      el('div', { class: 'actions' },
        node.sets.map((set, index) => el('button', { type: 'button', class: 'btn chip' + (index ? '' : ' lead'), title: set.title,
          onclick: () => put(unique(area.value + set.add).join('')) }, set.label)),
        el('button', { type: 'button', class: 'btn chip', onclick: () => put('') }, node.clear)),
      el('p', { class: 'note' }, node.hint));
  },
  preview(node) {
    const box = el('div', { class: 'preview-box' }, el('span', {}, node.empty));
    let shown = 0;
    binders.push(s => {
      const version = s.previews[node.key] || 0;
      if (version && version !== shown) {
        shown = version;
        box.replaceChildren(el('img', { alt: node.title,
          src: `/api/preview?key=${encodeURIComponent(node.key)}&v=${version}&t=${encodeURIComponent(TOKEN)}` }));
      }
    });
    return scrollSheet('忍', true,
      el('div', { class: 'sheet-top' },
        el('div', {}, el('h2', { class: 'plain' }, node.title), el('p', { class: 'sub' }, node.hint)),
        el('div', { class: 'seal kanji', 'aria-hidden': 'true' }, '済')),
      box,
      el('div', { class: 'actions' }, el('button', { type: 'button', class: 'btn ghost',
        onclick: () => act(node.key, { path: '/api/preview/open', body: { key: node.key } }) }, ui.strings.openImage)),
      node.note && el('p', { class: 'sub' }, node.note));
  },
  tabs(node, owner) {
    const buttons = [], panels = [];
    const choose = index => {
      tabChoice[owner] = index;
      buttons.forEach((b, i) => b.setAttribute('aria-selected', String(i === index)));
      panels.forEach((p, i) => { p.hidden = i !== index; });
    };
    node.tabs.forEach((tab, index) => {
      buttons.push(el('button', { type: 'button', class: 'tab', role: 'tab', onclick: () => choose(index) }, tab.title));
      const right = tab.children.filter(child => child.col === 2);
      panels.push(right.length
        ? el('div', { class: 'tab-panel two', role: 'tabpanel' },
            el('div', { class: 'col' }, renderAll(tab.children.filter(child => child.col !== 2))),
            el('div', { class: 'col' }, renderAll(right)))
        : el('div', { class: 'tab-panel', role: 'tabpanel' }, renderAll(tab.children)));
    });
    choose(Math.min(tabChoice[owner] || 0, buttons.length - 1));
    return [el('div', { class: 'tabs', role: 'tablist' }, buttons), ...panels];
  },
  overview() {
    return el('div', { class: 'scrolls' }, Object.entries(ui.games).map(([game, info], index) => {
      const seal = el('div', { class: 'seal' });
      const fill = el('div', {});
      const bar = el('div', { class: 'paper-bar' }, fill);
      const count = el('div', { class: 'count' }), stamp = el('div', { class: 'stamp' });
      binders.push(s => {
        const g = s.games[game], empty = g.percent === null;
        seal.textContent = g.label; seal.className = 'seal' + (empty ? ' none' : '');
        seal.setAttribute('role', 'img'); seal.setAttribute('aria-label', ui.strings.ready + ' ' + g.label);
        bar.className = 'paper-bar' + (empty ? ' none' : '');
        fill.style.width = (g.percent || 0) + '%';
        count.textContent = g.count; stamp.textContent = g.stamp;
      });
      return scrollSheet(NUMERALS[index], false,
        el('div', { class: 'sheet-top' },
          el('div', {}, el('div', { class: 'eyebrow' }, ui.strings.mission[index]),
            el('h2', {}, info.name), el('div', { class: 'sub' }, info.full)),
          seal),
        el('div', {}, el('div', { class: 'ready-label' }, ui.strings.ready), bar),
        el('div', {}, count, stamp),
        el('div', { class: 'actions' },
          el('button', { type: 'button', class: 'btn primary', onclick: () => show(game) }, ui.strings.open),
          el('button', { type: 'button', class: 'btn ghost',
            onclick: () => act(game, { path: '/api/open', body: { game } }) }, ui.strings.openTxt)));
    }));
  },
  steps(node) {
    return el('section', { class: 'steps' }, el('h2', {}, node.title),
      el('ol', {}, node.steps.map(([title, text], index) => el('li', {},
        el('span', { class: 'num', 'aria-hidden': 'true' }, NUMERALS[index]),
        el('div', {}, el('b', {}, title), el('span', {}, text))))));
  },
  about(node) {
    return scrollSheet('忍', true,
      el('h2', { class: 'plain' }, node.title),
      el('dl', { class: 'about' }, node.rows.flatMap(([key, value]) => [el('dt', {}, key), el('dd', {}, value)])),
      el('div', { class: 'made' }, node.badge));
  },
};

function scrollSheet(kanji, low, ...content) {
  const sheet = el('div', { class: 'sheet' },
    el('span', { class: 'sheet-kanji' + (low ? ' low' : ''), 'aria-hidden': 'true' }, kanji), ...content);
  return el('article', { class: 'scroll' }, el('div', { class: 'roller', 'aria-hidden': 'true' }), sheet,
    el('div', { class: 'roller', 'aria-hidden': 'true' }));
}

function renderAll(nodes, owner) {
  return (nodes || []).flatMap(node => {
    const draw = renderers[node.type];
    return draw ? draw(node, owner) : [];
  });
}

// ---- shell ------------------------------------------------------------------
function build() {
  binders.length = 0; uid = 0;
  document.documentElement.lang = ui.lang;
  const S = ui.strings;
  $('brand-sub').textContent = S.brand;
  $('version').textContent = `v${ui.version} · ${S.author}`;
  $('log-title').textContent = S.logTitle;
  $('log-copy').textContent = S.copy; $('log-clear').textContent = S.clear;
  $('log-body').dataset.empty = S.logEmpty;
  $('stop').textContent = S.stop;
  $('log-toggle').textContent = $('log').hidden ? S.log : S.hide;

  const nav = $('nav'), bottom = $('nav-bottom');
  nav.replaceChildren(); bottom.replaceChildren();
  for (const item of ui.nav) {
    if (item.section) { nav.append(el('div', { class: 'nav-section' }, item.section)); continue; }
    const icon = item.icon.startsWith('plate') ? ICONS.plate(item.icon.slice(5)) : ICONS[item.icon];
    const badge = item.game && el('span', { class: 'nav-badge' });
    if (badge) binders.push(s => {
      badge.textContent = s.games[item.game].label;
      badge.classList.toggle('none', s.games[item.game].percent === null);
    });
    const button = el('button', { type: 'button', class: 'nav-item', 'data-page': item.key, onclick: () => show(item.key) },
      el('span', { html: icon, style: 'display:contents' }), el('span', { class: 'nav-label' }, item.label), badge);
    (item.bottom ? bottom : nav).append(button);
  }

  const lang = $('lang'); lang.replaceChildren();
  for (const [code, name] of Object.entries(ui.languages)) {
    lang.append(el('button', { type: 'button', 'aria-pressed': String(code === ui.lang), title: name,
      onclick: () => changeLanguage(code) }, code === 'uk' ? 'УКР' : 'ENG'));
  }

  const pages = $('pages'); pages.replaceChildren();
  for (const p of ui.pages) {
    let side = null;
    if (p.game) {
      const label = el('span', {}), fill = el('div', {}), count = el('div', { class: 'gauge-count' });
      binders.push(s => { const g = s.games[p.game]; label.textContent = g.label; fill.style.width = (g.percent || 0) + '%'; count.textContent = g.count; });
      side = el('div', { class: 'gauge' }, el('div', { class: 'gauge-top' }, el('span', {}, S.progress), label),
        el('div', { class: 'bar' }, fill), count);
    } else if (p.key === 'home') {
      side = el('button', { type: 'button', class: 'btn primary', style: 'position:relative;box-shadow:5px 5px 0 #0B2247',
        onclick: () => act(ui.findGames) }, S.findGames);
    }
    pages.append(el('section', { class: 'page', 'data-page': p.key, hidden: true },
      el('header', { class: 'band' },
        el('span', { class: 'band-kanji', 'aria-hidden': 'true' }, KANJI[p.key] || '忍'),
        el('div', { class: 'plate' }, el('h1', {}, p.title.toUpperCase())),
        el('p', { class: 'band-sub' }, p.subtitle), side),
      el('div', { class: 'page-body' }, el('div', { class: 'page-inner' }, renderAll(p.children, p.key)))));
  }
  $('boot').hidden = true; $('app').hidden = false;
  show(page, true);
  if (state) paint(state);
}

function show(key, silent) {
  if (!ui.pages.some(p => p.key === key)) key = 'home';
  page = key;
  document.querySelectorAll('.page').forEach(node => { node.hidden = node.dataset.page !== key; });
  document.querySelectorAll('.nav-item').forEach(node => {
    if (node.dataset.page === key) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
  });
  if (!silent) api('/api/page', { page: key }).catch(() => {});
}

async function changeLanguage(code) {
  if (code === ui.lang) return;
  await api('/api/lang', { lang: code });
  ui = await api('/api/ui');
  if (ui.lang === code) { state = null; build(); refresh(); }
}

// ---- state → page -----------------------------------------------------------
function paint(s) {
  for (const bind of binders) bind(s);
  $('status-main').textContent = s.status;
  $('status-detail').textContent = s.detail;
  $('status-mark').setAttribute('class', 'status-mark ' + s.dot);
  $('stop').hidden = !s.working;
  const bar = $('progress'), fill = $('progress-fill');
  const spin = s.working && s.progress === null;
  bar.classList.toggle('spin', spin);
  fill.style.width = spin ? '' : Math.round((s.working ? s.progress : 0) * 100) + '%';

  const body = $('log-body');
  if (s.log.epoch !== logEpoch) { body.textContent = ''; logEpoch = s.log.epoch; }
  if (s.log.lines.length) {
    const stick = body.scrollTop + body.clientHeight >= body.scrollHeight - 24;
    body.append(s.log.lines.join('\n') + '\n');
    if (stick) body.scrollTop = body.scrollHeight;
  }
  logCursor = s.log.cursor;
  if (s.wantLog && $('log').hidden) toggleLog();
  for (const notice of s.notices) dialogs.push(notice);
  pumpDialogs();
}

let polling = false;
async function refresh() {
  if (polling || !ui) return;
  polling = true;
  try {
    state = await api(`/api/state?log=${logCursor}&epoch=${logEpoch}`);
    failures = 0;
    if (state.lang !== ui.lang) { ui = await api('/api/ui'); build(); }
    paint(state);
  } catch (error) {
    if (++failures === 6) {
      showDialog({ title: 'NARUTO UA Toolkit', text: ui.strings.offline, kind: 'error',
        buttons: [{ label: ui.strings.ok, value: true, primary: true }] });
    }
  } finally { polling = false; }
}

function toggleLog() {
  const log = $('log');
  log.hidden = !log.hidden;
  $('log-toggle').textContent = log.hidden ? ui.strings.log : ui.strings.hide;
  $('log-toggle').setAttribute('aria-expanded', String(!log.hidden));
  if (!log.hidden) $('log-body').scrollTop = $('log-body').scrollHeight;
}

$('log-toggle').addEventListener('click', toggleLog);
$('stop').addEventListener('click', () => act('stop'));
$('log-clear').addEventListener('click', () => api('/api/log', {}).then(refresh));
$('log-copy').addEventListener('click', async () => {
  await navigator.clipboard.writeText($('log-body').textContent.trim());
  $('status-detail').textContent = ui.strings.copied;
});

document.addEventListener('keydown', event => {
  if (event.key === 'F5') { event.preventDefault(); act('refresh'); return; }
  if ((event.ctrlKey || event.metaKey) && /^[1-5]$/.test(event.key) && ui) {
    event.preventDefault();
    show(['home', 's1', 's2', 's3', 'tools'][Number(event.key) - 1]);
  }
});
addEventListener('beforeunload', event => {
  if (state && state.working) { event.preventDefault(); event.returnValue = ui.strings.leave; }
});
addEventListener('pagehide', () => {
  navigator.sendBeacon('/api/bye?t=' + encodeURIComponent(TOKEN), new Blob(['{}'], { type: 'application/json' }));
});

(async function start() {
  try {
    ui = await api('/api/ui');
    state = await api('/api/state?log=0&epoch=-1');
    page = state.page || 'home';
    const query = new URLSearchParams(location.search);      // ?tab=N&log=1 open a given view directly
    if (query.has('tab')) for (const p of ui.pages) tabChoice[p.key] = Number(query.get('tab')) || 0;
    fit(); snapWindow();
    build();
    if (query.get('log') === '1') toggleLog();
    logEpoch = -1; logCursor = 0;          // the first paint already consumed nothing: read the log from the start
    refresh();
    setInterval(refresh, 500);
  } catch (error) {
    $('boot').textContent = 'NARUTO UA Toolkit: ' + error.message;
  }
})();
