// 整理书架: a dialog that previews the Suwayomi categories for the included tags; only its 应用
// button writes them.

import * as api from './api.js';
import { collapse, extLink, fill, h } from './dom.js';
import { LINKS, ORGANIZE_VISIBLE } from './settings.js';
import { peekStatus } from './status.js';
import { W } from './words.js';

// Opens the dialog in `host` for `tags`; focus goes back to `opener` when it closes.
export function openOrganize(host, tags, opener) {
  const ctrl = new AbortController(); // for the preview: an apply, once sent, is never cut off
  const body = h('div.org-body');
  const msg = h('p.org-msg', { role: 'status' }, W.loading);
  const cancel = h('button.btn', { type: 'button', onclick: () => dialog.close() }, W.organize.cancel);
  const apply = h('button.btn.primary', { type: 'button', hidden: true, onclick: run }, W.organize.apply);
  const dialog = h(
    'dialog.org-dialog',
    { 'aria-labelledby': 'org-title', 'aria-describedby': 'org-hint' },
    h('h2.org-title', { id: 'org-title' }, W.organize.title),
    h('p.org-hint', { id: 'org-hint' }, W.organize.hint),
    body,
    h('div.org-foot', null, msg, h('span.grow'), cancel, apply),
  );
  dialog.addEventListener('cancel', (e) => {
    if (cancel.disabled) e.preventDefault(); // Esc waits for the write's answer
  });
  dialog.addEventListener('close', () => {
    ctrl.abort();
    dialog.remove();
    opener.focus();
  });
  host.append(dialog);
  dialog.showModal();
  preview();
  return { close: () => dialog.close() };

  async function preview() {
    let data;
    try {
      data = await api.organize(tags, false, ctrl.signal);
    } catch (err) {
      if (!ctrl.signal.aborted) fail(err);
      return;
    }
    if (ctrl.signal.aborted) return;
    const groups = data.categories;
    const unmapped = data.unmapped.length;
    const changes = groups.some((g) => g.mangas.some((m) => !m.assigned));
    fill(body, groups.map(group), unmapped ? h('p.org-unmapped', null, W.organize.unmapped(unmapped)) : null);
    msg.textContent = changes ? '' : W.organize.nothing;
    apply.hidden = !changes;
    if (!changes) cancel.textContent = W.organize.close;
  }

  // The only request that writes: one per dialog, never retried, and the dialog stays until it
  // answers, so its outcome is seen and a second one cannot overlap it.
  async function run() {
    apply.disabled = true;
    cancel.disabled = true;
    apply.textContent = W.organize.applying;
    let data = null;
    let error = null;
    try {
      data = await api.organize(tags, true);
    } catch (err) {
      error = err;
    }
    cancel.disabled = false;
    if (!dialog.isConnected) return; // the page was left meanwhile
    if (error) fail(error);
    else done(data);
  }

  function done(data) {
    const url = peekStatus()?.url;
    const filed = data.categories.filter((g) => g.mangas.length);
    fill(
      body,
      filed.map((g) =>
        h(
          'div.org-cat',
          null,
          badge(g),
          url ? extLink(LINKS.suwayomiCategory(url, g.category_id), 'ext-link', W.organize.open(g.name)) : g.name,
        ),
      ),
    );
    msg.textContent = W.organize.done(data.created.length, data.added);
    apply.hidden = true;
    cancel.textContent = W.organize.close;
    (body.querySelector('a') || cancel).focus();
  }

  function fail(err) {
    msg.textContent = W.organize.failed(err?.detail);
    msg.classList.add('error');
    apply.hidden = true;
    cancel.textContent = W.organize.close;
    cancel.focus();
  }
}

function badge(g) {
  return h(`span.org-state${g.exists ? '' : '.new'}`, null, g.exists ? W.organize.existing : W.organize.fresh);
}

// One category: what it gains first, then what it holds already.
function group(g) {
  const adds = g.mangas.filter((m) => !m.assigned);
  const kept = g.mangas.filter((m) => m.assigned);
  const head = h(
    'div.org-cat',
    null,
    h('h3', null, g.name),
    g.mangas.length ? badge(g) : null,
    h('span.org-count', null, count(adds.length, kept.length)),
  );
  if (!g.mangas.length) return h('section.org-group', null, head);
  const items = [
    ...adds.map((m) => h('li.org-item', { title: m.title }, m.title)),
    ...kept.map((m) =>
      h(
        'li.org-item.in',
        { title: W.organize.inCategory(m.title), 'aria-label': W.organize.inCategory(m.title) },
        h('span.mark', { 'aria-hidden': 'true' }, W.mark.inc),
        m.title,
      ),
    ),
  ];
  return h('section.org-group', null, head, collapse(h('ul.chips.org-list', null, items), ORGANIZE_VISIBLE, 'li'));
}

function count(adds, kept) {
  if (!adds && !kept) return W.organize.none;
  if (!adds) return W.organize.allIn;
  return kept ? `${W.organize.adds(adds)} ${W.sep} ${W.organize.kept(kept)}` : W.organize.adds(adds);
}
