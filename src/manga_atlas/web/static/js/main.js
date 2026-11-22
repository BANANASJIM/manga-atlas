// Entry point: header, routing between the browse and work pages, global keys.

import * as api from './api.js';
import { renderBrowse } from './browse.js';
import { goBrowse, navigate, startRouter } from './router.js';
import { initSearchBox } from './search-box.js';
import { initShell, shell } from './shell.js';
import { applyCommit, emptyState } from './state.js';
import { onStatus, startPolling } from './status.js';
import { rememberPerson } from './store.js';
import { renderWork } from './work.js';

const WORK_PATH = /^\/work\/(\d+)\/?$/;
let page = null;

function render(loc, hstate, opts) {
  page?.cleanup();
  page = null;
  shell.app.inert = true; // stale controls must not act until the new page swaps in
  const m = loc.pathname.match(WORK_PATH);
  if (m) page = renderWork(shell.app, Number(m[1]), hstate);
  else if (loc.pathname === '/') page = renderBrowse(shell.app, loc, hstate, opts);
  else navigate('/', { replace: true });
}

const currentState = () => page?.getState?.() ?? emptyState();

// `作者:名字` resolves through the suggest endpoint; an unknown name falls back to title text.
async function findPerson(name) {
  try {
    const { people = [] } = await api.suggest(name);
    const lower = name.toLowerCase();
    return people.find((p) => [p.name, p.name_cn].some((n) => n?.toLowerCase() === lower)) || people[0] || null;
  } catch {
    return null;
  }
}

async function commit({ include, exclude, authors, text, person }) {
  let words = text;
  let who = person;
  for (const name of authors) {
    const found = await findPerson(name);
    if (found) who = found;
    else words = [words, name].filter(Boolean).join(' ');
  }
  if (who) rememberPerson(who.id, who.name);
  goBrowse(applyCommit(currentState(), { include, exclude, person: who, text: words }));
}

const TEXT_INPUT = /^(text|search|number|email|url|tel|password)$/;
function typing(el) {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') return true;
  return el.tagName === 'INPUT' && TEXT_INPUT.test(el.type);
}

function onKeyDown(e) {
  if (e.defaultPrevented || e.isComposing) return;
  if (typing(e.target) || document.querySelector('dialog[open]')) return; // an open dialog keeps its keys
  if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    shell.input.focus();
    shell.input.select();
    return;
  }
  page?.onKey?.(e);
}

initShell();
initSearchBox({
  input: shell.input,
  box: shell.suggest,
  list: shell.suggestList,
  foot: shell.suggestFoot,
  onCommit: commit,
  onOpenWork: (work) => navigate(`/work/${work.id}`),
  onRemoveLast: () => page?.removeLastCondition?.() ?? false,
  onArrowOut: () => page?.focusResults?.() ?? false,
});
for (const [input, key] of [[shell.read, 'read'], [shell.fav, 'fav']]) {
  input.addEventListener('change', () => {
    input.blur(); // hand arrow keys back to the results
    goBrowse({ ...currentState(), [key]: input.checked });
  });
}
document.addEventListener('keydown', onKeyDown);
onStatus((st) => shell.setStatus(st));
startPolling();
startRouter(render);
