// Browse state <-> URL query. The page URL uses the API's parameter names, so a bookmark is a saved search.

import { DEFAULT_SORT, LIST_SEP, SORTS, TEXT_SORT } from './settings.js';
import { personInfo } from './store.js';
import { W } from './words.js';

export function emptyState() {
  return { q: '', t: [], x: [], a: '', y: '', st: '', region: '', min: '', read: false, fav: false, sort: '', view: 'grid' };
}

export const uniq = (list) => [...new Set(list)];
const splitList = (v) => uniq((v || '').split(LIST_SEP).map((s) => s.trim()).filter(Boolean));

export function parseState(search) {
  const p = new URLSearchParams(search);
  const s = emptyState();
  s.q = (p.get('q') || '').trim();
  s.t = splitList(p.get('t'));
  s.x = splitList(p.get('x')).filter((n) => !s.t.includes(n));
  s.a = (p.get('a') || '').trim();
  s.y = normYear(p.get('y'));
  s.st = (p.get('st') || '').trim();
  s.region = (p.get('region') || '').trim();
  s.min = /^\d+(\.\d+)?$/.test(p.get('min') || '') ? p.get('min') : '';
  s.read = p.get('read') === '1';
  s.fav = p.get('fav') === '1';
  const sort = p.get('sort') || '';
  s.sort = sort === TEXT_SORT || SORTS.includes(sort) ? sort : '';
  s.view = p.get('view') === 'list' ? 'list' : 'grid';
  return s;
}

// Lists keep literal commas so the address bar stays readable: ?t=百合,校园&x=恐怖
function pairs(s) {
  const enc = encodeURIComponent;
  const out = [];
  if (s.q) out.push(['q', enc(s.q)]);
  if (s.t.length) out.push(['t', s.t.map(enc).join(LIST_SEP)]);
  if (s.x.length) out.push(['x', s.x.map(enc).join(LIST_SEP)]);
  if (s.a) out.push(['a', enc(s.a)]);
  if (s.y) out.push(['y', s.y]);
  if (s.st) out.push(['st', enc(s.st)]);
  if (s.region) out.push(['region', enc(s.region)]);
  if (s.min) out.push(['min', s.min]);
  if (s.read) out.push(['read', '1']);
  if (s.fav) out.push(['fav', '1']);
  return out;
}

const join = (list) => list.map(([k, v]) => `${k}=${v}`).join('&');

export function toSearch(s) {
  const list = pairs(s);
  if (s.sort) list.push(['sort', s.sort]);
  if (s.view === 'list') list.push(['view', 'list']);
  return list.length ? `?${join(list)}` : '';
}

export const browseURL = (s) => `/${toSearch(s)}`;

// Same results regardless of how they are displayed.
export const cacheKey = (s) => toSearch({ ...s, view: 'grid' });

export const defaultSort = (s) => (s.q ? TEXT_SORT : DEFAULT_SORT);

export function effectiveSort(s) {
  if (s.sort === TEXT_SORT && !s.q) return DEFAULT_SORT;
  return s.sort || defaultSort(s);
}

export function apiQuery(s, offset, limit) {
  const list = pairs(s);
  list.push(['sort', effectiveSort(s)], ['offset', String(offset)], ['limit', String(limit)]);
  return join(list);
}

// '2010-2020', '2010-', '-2020'; a bare year means that year only.
export function normYear(raw) {
  const v = (raw || '').trim();
  if (/^\d{4}$/.test(v)) return `${v}-${v}`;
  const m = v.match(/^(\d{4})?-(\d{4})?$/);
  if (!m || (!m[1] && !m[2])) return '';
  let [from, to] = [m[1] || '', m[2] || ''];
  if (from && to && Number(from) > Number(to)) [from, to] = [to, from];
  return `${from}-${to}`;
}

export function yearLabel(y) {
  const [from, to] = y.split('-');
  return from && from === to ? from : `${from}${W.dash}${to}`;
}

const without = (list, name) => list.filter((n) => n !== name);

export function cycleTag(s, name) {
  if (s.t.includes(name)) return { ...s, t: without(s.t, name), x: [...s.x, name] };
  if (s.x.includes(name)) return { ...s, x: without(s.x, name) };
  return { ...s, t: [...s.t, name] };
}

export const addTag = (s, name) => ({ ...s, t: uniq([...s.t, name]), x: without(s.x, name) });
export const onlyTag = (s, name) => ({ ...emptyState(), t: [name], read: s.read, fav: s.fav, view: s.view });
export const dropText = (s) => ({ ...s, q: '', sort: s.sort === TEXT_SORT ? '' : s.sort });

export function clearConditions(s) {
  return { ...emptyState(), read: s.read, fav: s.fav, view: s.view, sort: s.sort === TEXT_SORT ? '' : s.sort };
}

// 作者 <name>; for a publisher, magazine or the like <role> <name>; a bare id until the name is known.
function personLabel(id) {
  const who = personInfo(id);
  if (!who?.name) return W.chip.author(W.personFallback(id));
  return who.creator === false && who.role ? W.chip.credit(who.role, who.name) : W.chip.author(who.name);
}

// Active conditions in chip order; `all` adds the text query, 只看能读 and 只看收藏 (zero-result hint,
// summaries).
export function conditions(s, { all = false } = {}) {
  const out = [];
  const tag = (name, kind) => ({
    kind,
    name,
    label: name,
    toggle: () =>
      kind === 'inc'
        ? { ...s, t: without(s.t, name), x: [...s.x, name] }
        : { ...s, x: without(s.x, name), t: [...s.t, name] },
    remove: () => ({ ...s, t: without(s.t, name), x: without(s.x, name) }),
  });
  if (all && s.q) out.push({ kind: 'text', name: s.q, label: W.chip.text(s.q), remove: () => dropText(s) });
  for (const name of s.t) out.push(tag(name, 'inc'));
  for (const name of s.x) out.push(tag(name, 'exc'));
  if (s.a) out.push({ kind: 'author', label: personLabel(s.a), remove: () => ({ ...s, a: '' }) });
  if (s.y) out.push({ kind: 'year', label: W.chip.year(yearLabel(s.y)), remove: () => ({ ...s, y: '' }) });
  if (s.st) out.push({ kind: 'status', label: s.st, remove: () => ({ ...s, st: '' }) });
  if (s.region) out.push({ kind: 'region', label: s.region, remove: () => ({ ...s, region: '' }) });
  if (s.min) out.push({ kind: 'score', label: W.chip.score(s.min), remove: () => ({ ...s, min: '' }) });
  if (all && s.read) out.push({ kind: 'read', label: W.readOnly, remove: () => ({ ...s, read: false }) });
  if (all && s.fav) out.push({ kind: 'fav', label: W.favOnly, remove: () => ({ ...s, fav: false }) });
  return out;
}

export function summarize(s) {
  return conditions(s, { all: true })
    .map((c) => (c.kind === 'exc' ? `${W.mark.exc}${c.name}` : c.label))
    .join(' ');
}

// Merges what was typed in the search box into the current state.
export function applyCommit(base, { include, exclude, person, text }) {
  const inc = uniq(include);
  const exc = uniq(exclude).filter((n) => !inc.includes(n));
  const next = {
    ...base,
    t: uniq([...base.t.filter((n) => !exc.includes(n)), ...inc]),
    x: uniq([...base.x.filter((n) => !inc.includes(n)), ...exc]),
    a: person ? String(person.id) : base.a,
    q: text.trim(),
  };
  if (!next.q && next.sort === TEXT_SORT) next.sort = '';
  return next;
}
