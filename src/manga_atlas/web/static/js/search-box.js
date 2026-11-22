// Search box: suggest dropdown, the three-prefix syntax and its keyboard flow.
// Typing only refreshes the dropdown; Enter / Tab / Shift+Enter commit the whole box.

import * as api from './api.js';
import { cover } from './cards.js';
import { h, icon } from './dom.js';
import { classify, parseQuery, tokenAt } from './query.js';
import { SUGGEST_DELAY_MS } from './settings.js';
import { W } from './words.js';

const EMPTY = { tags: [], works: [], people: [] };

export function initSearchBox({ input, box, list, foot, onCommit, onOpenWork, onRemoveLast, onArrowOut }) {
  let rows = [];
  let active = -1;
  let rowsFor = null; // the input value `rows` were built for
  let timer = 0;
  let ctrl = null;
  let cached = { q: null, data: EMPTY };
  let pending = null;

  foot.replaceChildren(...footer());
  input.addEventListener('focus', () => {
    if (!input.value) show([], '');
  });
  input.addEventListener('input', (e) => {
    if (!e.isComposing) schedule();
  });
  input.addEventListener('compositionend', schedule);
  input.addEventListener('keydown', onKeyDown);
  input.addEventListener('blur', close);
  box.addEventListener('mousedown', (e) => e.preventDefault()); // keep focus in the box
  list.addEventListener('click', onClick);

  const isOpen = () => !box.hidden && rows.length > 0;

  function context() {
    const v = input.value;
    const tok = tokenAt(v, input.selectionStart ?? v.length);
    const { kind, value } = classify(tok.text);
    return { v, tok, kind, q: value.trim(), plain: parseQuery(v).text };
  }

  function load(q) {
    if (!q) return Promise.resolve(EMPTY);
    if (cached.q === q) return Promise.resolve(cached.data);
    if (pending?.q === q) return pending.promise;
    ctrl?.abort();
    ctrl = new AbortController();
    const promise = api
      .suggest(q, ctrl.signal)
      .then(
        (raw) => {
          const data = { tags: raw.tags || [], works: raw.works || [], people: raw.people || [] };
          cached = { q, data };
          return data;
        },
        (err) => {
          if (err.name === 'AbortError') throw err;
          return EMPTY;
        },
      )
      .finally(() => {
        if (pending?.promise === promise) pending = null;
      });
    pending = { q, promise };
    return promise;
  }

  function build(ctx, data) {
    const out = [];
    if (ctx.kind === 'text' && ctx.plain) out.push({ type: 'text', text: ctx.plain });
    if (ctx.kind !== 'author') for (const tag of data.tags) out.push({ type: 'tag', tag, exclude: ctx.kind === 'exclude' });
    if (ctx.kind === 'text') for (const work of data.works) out.push({ type: 'work', work });
    if (ctx.kind === 'text' || ctx.kind === 'author') for (const person of data.people) out.push({ type: 'person', person });
    return out;
  }

  // A tag that exactly matches the text wins; prefixes pick their group; else the text search row.
  function preferred(ctx, list_) {
    const q = ctx.q.toLowerCase();
    const exact = list_.findIndex((r) => r.type === 'tag' && r.tag.name.toLowerCase() === q);
    if (q && exact >= 0) return exact;
    const want = { include: 'tag', exclude: 'tag', author: 'person' }[ctx.kind];
    if (want) return list_.findIndex((r) => r.type === want);
    return list_.length ? 0 : -1;
  }

  async function fresh() {
    const ctx = context();
    if (rowsFor === ctx.v) return { ctx, rows, active };
    const data = await load(ctx.q);
    const built = build(ctx, data);
    return { ctx, rows: built, active: preferred(ctx, built) };
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(refresh, SUGGEST_DELAY_MS);
  }

  async function refresh() {
    if (document.activeElement !== input) return;
    const v = input.value;
    let next;
    try {
      next = await fresh();
    } catch {
      return; // superseded by a newer keystroke
    }
    if (input.value !== v || document.activeElement !== input) return;
    show(next.rows, v, next.active);
  }

  function show(nextRows, forValue, nextActive = -1) {
    rows = nextRows;
    rowsFor = forValue;
    active = nextActive;
    render();
  }

  function render() {
    const nodes = [];
    let group = null;
    rows.forEach((row, i) => {
      const el = rowView(row, i);
      if (row.type === 'text') {
        nodes.push(el);
        group = null;
        return;
      }
      if (group?.dataset.type !== row.type) {
        const label = W.suggest.groups[row.type];
        group = h(
          'div.sg-group',
          { role: 'group', 'aria-label': label, 'data-type': row.type },
          h('div.sg-head', { 'aria-hidden': 'true' }, label),
        );
        nodes.push(group);
      }
      group.append(el);
    });
    list.replaceChildren(...nodes);
    list.hidden = !rows.length;
    box.hidden = false;
    input.setAttribute('aria-expanded', String(rows.length > 0));
    if (active >= 0) {
      input.setAttribute('aria-activedescendant', `sg-${active}`);
      list.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
    } else input.removeAttribute('aria-activedescendant');
  }

  function rowView(row, i) {
    const on = i === active;
    const attrs = { id: `sg-${i}`, role: 'option', 'aria-selected': String(on), 'data-i': i, class: on ? 'active' : null };
    if (row.type === 'text') {
      return h(
        'div.sg-row.sg-text',
        attrs,
        icon('search'),
        h('span.sg-main', null, W.suggest.search(row.text)),
        h('span.sg-hint', null, W.suggest.textHint),
        h('kbd', null, W.keys.enter),
      );
    }
    if (row.type === 'tag') {
      const t = row.tag;
      return h(
        'div.sg-row.sg-tag',
        { ...attrs, 'data-name': t.name },
        h('span.hash', { 'aria-hidden': 'true' }, row.exclude ? W.prefix.exclude : W.prefix.include),
        h('b.sg-main', null, t.name),
        t.n != null ? h('span.sg-count', null, W.works(t.n)) : null,
        on ? tagKeys(row) : null,
      );
    }
    if (row.type === 'work') {
      const w = row.work;
      return h(
        'div.sg-row.sg-work',
        attrs,
        cover(w, 'mini'),
        h(
          'span.sg-two',
          null,
          h('span.sg-main', null, w.title),
          w.name && w.name !== w.title ? h('span.sg-sub', null, w.name) : null,
        ),
        h('span.sg-count', null, w.year),
      );
    }
    const p = row.person;
    return h(
      'div.sg-row.sg-person',
      attrs,
      h(
        'span.sg-two',
        null,
        h('span.sg-main', null, p.name),
        p.name_cn && p.name_cn !== p.name ? h('span.sg-sub', null, p.name_cn) : null,
      ),
      p.works != null ? h('span.sg-count', null, W.works(p.works)) : null,
    );
  }

  function tagKeys(row) {
    const key = (label, act, text) =>
      h('button.sg-key', { type: 'button', tabindex: '-1', 'data-act': act }, h('kbd', null, label), text);
    return h(
      'span.sg-keys',
      null,
      row.exclude
        ? key(W.keys.enter, 'exclude', W.suggest.exclude)
        : [key(W.keys.tab, 'include', W.suggest.include), key(W.keys.shiftEnter, 'exclude', W.suggest.exclude)],
    );
  }

  function footer() {
    return [
      h(
        'span.sg-syntax',
        null,
        Object.entries(W.prefix).map(([kind, prefix]) => h(`span.syn.${kind}`, null, h('code', null, prefix), W.syntaxHint[kind])),
      ),
      h('span.grow'),
      h('span.sg-help', null, h('kbd', null, W.keys.updown), W.suggest.select, h('kbd', null, W.keys.enter), W.suggest.confirm),
    ];
  }

  function close() {
    clearTimeout(timer);
    box.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }

  function move(delta) {
    if (!rows.length) return;
    if (active < 0) active = delta > 0 ? 0 : rows.length - 1;
    else active = (active + delta + rows.length) % rows.length;
    render();
  }

  function onKeyDown(e) {
    if (e.isComposing || e.keyCode === 229) return; // IME is composing; Enter confirms the candidate
    switch (e.key) {
      case 'ArrowDown':
      case 'ArrowUp':
        if (isOpen()) {
          e.preventDefault();
          move(e.key === 'ArrowDown' ? 1 : -1);
        } else if (e.key === 'ArrowDown' && onArrowOut()) e.preventDefault();
        break;
      case 'Enter':
        e.preventDefault();
        submit(e.shiftKey ? 'exclude' : 'enter');
        break;
      case 'Tab':
        if (!e.shiftKey && isOpen() && rowsFor === input.value && rows.some((r) => r.type === 'tag')) {
          e.preventDefault();
          submit('include');
        }
        break;
      case 'Escape':
        e.preventDefault();
        if (box.hidden) input.blur();
        else close();
        break;
      case 'Backspace':
        if (!input.value && onRemoveLast()) e.preventDefault();
        break;
      default:
    }
  }

  // Waits for suggestions that match what is in the box, so fast typing commits the right thing.
  async function submit(kind) {
    const v = input.value;
    let now;
    try {
      now = await fresh();
    } catch {
      return;
    }
    if (input.value !== v) return;
    const row = now.rows[now.active];
    if (kind === 'enter') {
      choose(row || { type: 'text' });
      return;
    }
    const tagRow = row?.type === 'tag' ? row : now.rows.find((r) => r.type === 'tag');
    if (tagRow) choose(tagRow, { exclude: kind === 'exclude' ? true : tagRow.exclude });
  }

  function onClick(e) {
    const el = e.target.closest('[data-i]');
    const row = el && rows[Number(el.dataset.i)];
    if (!row) return;
    const act = e.target.closest('[data-act]')?.dataset.act;
    choose(row, { exclude: act ? act === 'exclude' : e.shiftKey || undefined });
  }

  function choose(row, { exclude } = {}) {
    const ctx = context();
    close();
    if (row.type === 'work') {
      input.value = '';
      onOpenWork(row.work);
      return;
    }
    const consumes = row.type === 'tag' || row.type === 'person';
    const parsed = parseQuery(consumes ? ctx.v.slice(0, ctx.tok.start) + ctx.v.slice(ctx.tok.end) : ctx.v);
    if (row.type === 'tag') ((exclude ?? row.exclude) ? parsed.exclude : parsed.include).push(row.tag.name);
    onCommit({ ...parsed, person: row.type === 'person' ? row.person : null });
  }
}
