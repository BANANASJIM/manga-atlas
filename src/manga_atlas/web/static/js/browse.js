// Browse page (home = empty query): facets, condition chips, single-tag header, results, infinite scroll.

import * as api from './api.js';
import { pickReadable, updateBadge, workCard, workRow } from './cards.js';
import { extLink, h, icon, plainClick, swap } from './dom.js';
import { openOrganize } from './organize.js';
import { goBrowse, here, navigate, remember } from './router.js';
import {
  DEFAULT_TAG_GROUP,
  FACET_SKIP_GROUPS,
  FACET_VISIBLE,
  FLASH_MS,
  LINKS,
  PAGE_SIZE,
  RELAX_MAX,
  RELAX_SHOW,
  RESTORE_MAX,
  SCORE_STEPS,
  SCROLL_AHEAD_PX,
  SORTS,
  TAG_GROUPS,
  TEXT_SORT,
} from './settings.js';
import { shell } from './shell.js';
import {
  addTag,
  browseURL,
  cacheKey,
  clearConditions,
  conditions,
  cycleTag,
  defaultSort,
  effectiveSort,
  normYear,
  onlyTag,
  parseState,
  summarize,
} from './state.js';
import { getStatus, onStatus, peekStatus, refreshStatus } from './status.js';
import { getResults, putResults, rememberPerson, setReadable } from './store.js';
import { fmt, shortDate, W } from './words.js';

const expanded = new Set(); // facet groups opened with 更多, kept across searches

function normalize(d) {
  const th = d.tag_header;
  return {
    total: d.total ?? 0,
    items: d.items ?? [],
    facets: d.facets ?? {},
    tagHeader: th?.tag ? { ...th.tag, related: th.related ?? [], authors: th.people ?? [] } : null,
  };
}

export function renderBrowse(root, loc, hstate, { pop }) {
  const state = parseState(loc.search);
  const key = cacheKey(state);
  const ctrl = new AbortController();
  const els = {};
  let entry = null; // cached { data, selected }
  let loading = false; // the first results are on their way
  let focusWhenDrawn = false; // ↓ came from the search box meanwhile
  let loadingMore = false;
  let observer = null;
  let batch = null;
  let organizer = null; // the open 整理书架 dialog
  let status = peekStatus();

  shell.setQuery(state.q);
  shell.setSwitches(state);
  paintTitle();

  const offStatus = onStatus((s) => {
    status = s;
    paintStatus();
  });

  // Typing in the box means ↓ is no longer wanted; leaving the page drops it with the page.
  const dropFocus = () => {
    focusWhenDrawn = false;
  };
  shell.input.addEventListener('input', dropFocus);

  start();

  return {
    getState: () => ({ ...state, t: [...state.t], x: [...state.x] }),
    onKey,
    focusResults() {
      if (entry) return focusCard(entry.selected ?? 0);
      focusWhenDrawn = loading; // while results load the key is kept, not lost
      return loading;
    },
    removeLastCondition,
    cleanup() {
      ctrl.abort();
      batch?.ctrl.abort();
      organizer?.close();
      observer?.disconnect();
      offStatus();
      shell.input.removeEventListener('input', dropFocus);
    },
  };

  async function start() {
    const hit = getResults(key);
    if (hit) {
      entry = hit;
      draw();
      restore();
      return;
    }
    const want = Math.min(Math.max(PAGE_SIZE, hstate.loaded || 0), RESTORE_MAX);
    let data;
    loading = true;
    try {
      data = await fetchFirst(want);
    } catch {
      loading = false;
      focusWhenDrawn = false;
      if (!ctrl.signal.aborted) drawError();
      return;
    }
    loading = false;
    if (ctrl.signal.aborted) return;
    entry = putResults(key, { data: normalize(data), selected: null });
    draw();
    restore();
    if (focusWhenDrawn && document.activeElement === shell.input) focusCard(0);
    focusWhenDrawn = false;
  }

  // The first `want` results. A server may cap a page below what is asked, so ask again from where
  // it stopped until there are enough, it has no more, or a request fails (what came is shown).
  async function fetchFirst(want) {
    const data = await api.search(state, 0, want, ctrl.signal);
    const items = data.items ?? [];
    while (items.length < Math.min(want, data.total ?? 0)) {
      let page;
      try {
        page = await api.search(state, items.length, want - items.length, ctrl.signal);
      } catch {
        break;
      }
      if (!page.items?.length) break;
      items.push(...page.items);
    }
    return { ...data, items };
  }

  function restore() {
    window.scrollTo(0, hstate.scrollY ?? 0);
    if (!pop || entry.selected == null) return;
    const el = cards()[entry.selected];
    if (!el) return;
    el.focus({ preventScroll: true });
    const r = el.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) el.scrollIntoView({ block: 'nearest' });
  }

  function go(next) {
    if (!ctrl.signal.aborted) goBrowse(next); // once aborted, a newer page owns the URL
  }

  // ---- layout ----

  function paintTitle() {
    const summary = summarize(state);
    document.title = summary ? `${summary} · ${W.brand}` : W.brand;
  }

  function draw() {
    // Who the results are limited to is named with them, so a bookmarked ?a=… gets its chip text.
    for (const p of entry.data.facets.people || []) rememberPerson(p.id, p.name, p.role, p.creator);
    swap(
      root,
      h(
        'div.browse',
        null,
        facetPanel(),
        h('section.results', { 'aria-label': W.results }, conditionBar(), tagHeader(), resultsHead(), resultsBody()),
      ),
    );
    paintStatus();
    watchEnd();
    paintTitle();
  }

  function drawError() {
    const retry = h('button.btn', { type: 'button' }, W.retry);
    retry.addEventListener('click', () => {
      root.inert = true;
      start();
    });
    swap(root, h('div.empty', null, h('p.empty-title', null, W.failed), retry));
  }

  // ---- facet panel ----

  function facetPanel() {
    const { facets } = entry.data;
    els.catalog = h('p.facet-foot');
    els.panel = h(
      'aside.facets',
      { 'aria-label': W.filters },
      tagGroups(facets).map(([grp, tags]) =>
        facetGroup(`tag:${grp}`, W.groups[grp] || W.sources[grp]?.group || W.groupOther, tags.map(tagChip)),
      ),
      valueGroup('region', W.region, facets.region, state.region, 'region'),
      valueGroup('status', W.status, facets.status, state.st, 'st'),
      yearGroup(),
      scoreGroup(),
      els.catalog,
    );
    paintCatalog();
    return els.panel;
  }

  function redrawPanel(focusGroup) {
    const old = els.panel;
    const top = old.scrollTop;
    old.replaceWith(facetPanel());
    els.panel.scrollTop = top;
    els.panel.querySelector(`[data-group="${focusGroup}"] .more`)?.focus();
  }

  function tagGroups({ tags = [], included = [], excluded = [] }) {
    const seen = new Set();
    const byGroup = new Map();
    const put = (grp, chip) => {
      if (!byGroup.has(grp)) byGroup.set(grp, []);
      byGroup.get(grp).push(chip);
    };
    for (const t of tags) {
      if (!t?.name || seen.has(t.name) || FACET_SKIP_GROUPS.includes(t.grp)) continue;
      seen.add(t.name);
      put(t.grp || DEFAULT_TAG_GROUP, t);
    }
    // Selected tags are not listed but still need a chip to cycle. It goes in the tag's own group
    // (made when the list has none of it), or the default one when that group is unknown here;
    // an excluded tag shows how many works it removes. The server answers by the spelling the URL
    // gave, which it may fold into another name (gl, 戀愛), so that is what is matched.
    const chosen = new Map([...included, ...excluded].map((t) => [t.query, t]));
    for (const name of [...state.t, ...state.x]) {
      if (seen.has(name)) continue;
      const t = chosen.get(name);
      const own = TAG_GROUPS.includes(t?.grp) && !FACET_SKIP_GROUPS.includes(t.grp);
      put(own ? t.grp : DEFAULT_TAG_GROUP, { name, n: t?.n ?? null });
    }
    const order = [...TAG_GROUPS, ...[...byGroup.keys()].filter((g) => !TAG_GROUPS.includes(g)).sort()];
    const rank = (t) => (state.t.includes(t.name) ? 0 : state.x.includes(t.name) ? 1 : 2);
    return order.filter((g) => byGroup.has(g)).map((g) => [g, byGroup.get(g).sort((a, b) => rank(a) - rank(b))]);
  }

  function facetGroup(group, label, chips) {
    if (!chips.length) return null;
    const open = expanded.has(group);
    const toggle =
      chips.length > FACET_VISIBLE
        ? h(
            'button.more',
            {
              type: 'button',
              'aria-expanded': String(open),
              onclick: () => {
                if (open) expanded.delete(group);
                else expanded.add(group);
                redrawPanel(group);
              },
            },
            open ? W.less : W.more,
          )
        : null;
    return h(
      'section.facet',
      { 'aria-label': label, 'data-group': group },
      h('h3', null, label),
      h('div.chips', null, open ? chips : chips.slice(0, FACET_VISIBLE), toggle),
    );
  }

  function tagChip(t) {
    const st = state.t.includes(t.name) ? 'inc' : state.x.includes(t.name) ? 'exc' : 'off';
    let n = t.n != null ? fmt(t.n) : null;
    if (st === 'exc') n = t.n > 0 ? `${W.mark.exc}${fmt(t.n)}` : null; // works this exclusion removes
    return h(
      `button.chip${st === 'off' ? '' : `.${st}`}`,
      {
        type: 'button',
        'data-name': t.name,
        'aria-label': W.tagAria(t.name, st, st === 'exc' ? null : t.n),
        title: W.tagNext[st],
        onclick: () => go(cycleTag(state, t.name)),
      },
      st === 'off' ? null : h('span.mark', { 'aria-hidden': 'true' }, W.mark[st]),
      t.name,
      n ? h('span.n', null, n) : null,
    );
  }

  function valueGroup(group, label, values, current, param) {
    const list = (values || []).filter((v) => v?.name);
    if (current && !list.some((v) => v.name === current)) list.unshift({ name: current, n: null });
    list.sort((a, b) => (b.name === current) - (a.name === current));
    const chips = list.map((v) => {
      const on = v.name === current;
      return h(
        `button.chip${on ? '.inc' : ''}`,
        {
          type: 'button',
          'data-name': v.name,
          'aria-pressed': String(on),
          'aria-label': W.valueAria(v.name, v.n, on),
          onclick: () => go({ ...state, [param]: on ? '' : v.name }),
        },
        on ? h('span.mark', { 'aria-hidden': 'true' }, W.mark.inc) : null,
        v.name,
        v.n != null ? h('span.n', null, fmt(v.n)) : null,
      );
    });
    return facetGroup(group, label, chips);
  }

  function yearGroup() {
    const [from, to] = state.y ? state.y.split('-') : ['', ''];
    const field = (value, placeholder, label) =>
      h('input.year-in', {
        type: 'text',
        inputmode: 'numeric',
        maxlength: '4',
        placeholder,
        'aria-label': label,
        value,
      });
    const a = field(from, W.yearFrom, W.yearFromLabel);
    const b = field(to, W.yearTo, W.yearToLabel);
    const apply = () => {
      const y = normYear(`${a.value.trim()}-${b.value.trim()}`);
      if (y !== state.y) go({ ...state, y });
    };
    for (const el of [a, b]) {
      el.addEventListener('change', apply);
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') apply();
      });
    }
    return h(
      'section.facet',
      { 'aria-label': W.year, 'data-group': 'year' },
      h('h3', null, W.year),
      h('div.year', null, a, h('span.dash', { 'aria-hidden': 'true' }, W.dash), b),
    );
  }

  function scoreGroup() {
    const options = [['', W.any], ...SCORE_STEPS.map((n) => [String(n), W.scoreMin(n)])];
    return h(
      'section.facet',
      { 'aria-label': W.score, 'data-group': 'score' },
      h('h3', null, W.score),
      segmented(
        W.score,
        options.map(([value, label]) => ({
          label,
          on: state.min === value,
          data: { 'data-min': value },
          pick: () => go({ ...state, min: value }),
        })),
      ),
    );
  }

  function segmented(label, options) {
    return h(
      'div.seg',
      { role: 'group', 'aria-label': label },
      options.map((o) =>
        h(
          `button${o.cls || ''}`,
          {
            type: 'button',
            class: o.on ? 'on' : null,
            'aria-pressed': String(o.on),
            'aria-label': o.aria || null,
            title: o.aria || null,
            onclick: o.pick,
            ...o.data,
          },
          o.label,
        ),
      ),
    );
  }

  function paintCatalog() {
    const c = status?.catalog;
    if (els.catalog) els.catalog.textContent = c?.built_at && c.works != null ? W.data(shortDate(c.built_at), c.works) : '';
  }

  // ---- condition chips and the single-tag header ----

  function conditionBar() {
    const list = conditions(state);
    if (!list.length) return null;
    // 整理书架 files the library by the included tags only.
    els.organize = state.t.length
      ? h('button.btn.organize', { type: 'button', onclick: organize }, W.organize.title)
      : null;
    return h(
      'div.conds',
      { role: 'group', 'aria-label': W.conditions },
      h('span.conds-label', { 'aria-hidden': 'true' }, W.conditions),
      h('div.chips', null, list.map(condChip)),
      els.organize,
      h('button.link-btn.clear', { type: 'button', onclick: () => go(clearConditions(state)) }, W.clear),
    );
  }

  function organize() {
    organizer = openOrganize(root, [...state.t], els.organize);
  }

  function condChip(c) {
    const isTag = c.kind === 'inc' || c.kind === 'exc';
    const label = isTag
      ? h(
          'button.cond-label',
          { type: 'button', 'aria-label': W.condAria(c.name, c.kind), onclick: () => go(c.toggle()) },
          h('span.mark', { 'aria-hidden': 'true' }, W.mark[c.kind]),
          c.name,
        )
      : h('span.cond-label', null, c.label);
    return h(
      `span.cond.${c.kind}`,
      { 'data-name': c.name ?? c.label },
      label,
      h(
        'button.cond-x',
        { type: 'button', 'aria-label': W.remove(c.label), title: W.remove(c.label), onclick: () => go(c.remove()) },
        W.mark.remove,
      ),
    );
  }

  function tagHeader() {
    const th = entry.data.tagHeader;
    if (!th?.name) return null;
    const related = (th.related || []).filter((r) => r?.name);
    const authors = (th.authors || []).filter((a) => a?.id != null && a.name);
    for (const a of authors) rememberPerson(a.id, a.name);
    return h(
      'section.tag-head',
      { 'aria-label': th.name },
      h(
        'div.th-top',
        null,
        h('h1.th-name', null, th.name),
        h('span.th-counts', null, h('b', null, W.works(th.n ?? entry.data.total))),
      ),
      h(
        'div.th-cols',
        null,
        related.length
          ? h(
              'div.th-related',
              null,
              h('h2', null, W.related, h('small', null, W.relatedHint)),
              h('div.chips', null, related.map(relatedChip)),
            )
          : null,
        authors.length
          ? h('div.th-authors', null, h('h2', null, W.commonAuthors), h('div.chips', null, authors.map(authorChip)))
          : null,
      ),
    );
  }

  function relatedChip(r) {
    return h(
      'span.rchip',
      { 'data-name': r.name },
      h(
        'a.rchip-name',
        { href: browseURL(onlyTag(state, r.name)), 'data-link': '', title: W.only(r.name) },
        r.name,
        r.n != null ? h('span.n', null, fmt(r.n)) : null,
      ),
      h(
        'button.plus',
        { type: 'button', 'aria-label': W.add(r.name), title: W.add(r.name), onclick: () => go(addTag(state, r.name)) },
        W.mark.plus,
      ),
    );
  }

  function authorChip(a) {
    return h(
      'button.chip',
      { type: 'button', 'data-id': a.id, onclick: () => go({ ...state, a: String(a.id) }) },
      a.name,
      a.n != null ? h('span.n', null, fmt(a.n)) : null,
    );
  }

  // ---- results ----

  function resultsHead() {
    const { total } = entry.data;
    const summary = h('div.summary', { 'aria-live': 'polite' }, h('b', null, W.total(total)));
    els.check = total ? h('button.btn.check', { type: 'button', onclick: toggleBatch }, W.checkPage) : null;
    const current = effectiveSort(state);
    const sorts = state.q ? [TEXT_SORT, ...SORTS] : SORTS;
    const sortSeg = segmented(
      W.sortLabel,
      sorts.map((s) => ({
        label: W.sorts[s],
        on: s === current,
        data: { 'data-sort': s },
        pick: () => go({ ...state, sort: s === defaultSort(state) ? '' : s }),
      })),
    );
    const viewSeg = segmented(
      W.viewLabel,
      ['grid', 'list'].map((v) => ({
        label: icon(v),
        aria: W.views[v],
        cls: '.icon-btn',
        on: state.view === v,
        data: { 'data-view': v },
        pick: () => go({ ...state, view: v }),
      })),
    );
    return h('div.results-head', null, summary, els.check, h('span.grow'), total ? [sortSeg, viewSeg] : null);
  }

  function make(item, i) {
    return state.view === 'list' ? workRow(item, i) : workCard(item, i);
  }

  function cards() {
    return els.list ? [...els.list.querySelectorAll('a.card')] : [];
  }

  function resultsBody() {
    const { items, total } = entry.data;
    if (!total) return emptyView();
    els.list = h(`ul.${state.view === 'list' ? 'list' : 'grid'}`, { 'aria-label': W.results }, items.map(make));
    els.list.addEventListener('click', onCardClick);
    els.end = h('p.sentinel', null, items.length < total ? W.loading : W.end);
    return [els.list, els.end];
  }

  function onCardClick(e) {
    const a = e.target.closest('a.card');
    if (!a || !plainClick(e)) return;
    e.preventDefault();
    openWork(Number(a.dataset.index));
  }

  function openWork(index) {
    const item = entry.data.items[index];
    if (!item) return;
    entry.selected = index;
    remember({ loaded: entry.data.items.length });
    navigate(`/work/${item.id}`, { state: { from: here(), index } });
  }

  function emptyView() {
    const relax = h('div.relax');
    suggestRelax(relax);
    const url = status?.url;
    return h(
      'div.empty',
      null,
      h('p.empty-title', null, W.empty),
      relax,
      state.q && url ? extLink(LINKS.suwayomiSearch(url, state.q), 'ext-link', W.suwayomiSearch(state.q)) : null,
    );
  }

  // Zero results: how many works each single relaxation would bring back.
  async function suggestRelax(box) {
    const list = conditions(state, { all: true }).slice(-RELAX_MAX);
    const counted = await Promise.all(
      list.map((c) =>
        api.count(c.remove(), ctrl.signal).then(
          (n) => ({ c, n }),
          () => null,
        ),
      ),
    );
    if (ctrl.signal.aborted) return;
    const best = counted
      .filter((x) => x?.n > 0)
      .sort((a, b) => b.n - a.n)
      .slice(0, RELAX_SHOW);
    box.replaceChildren(
      ...best.map(({ c, n }) =>
        h(
          'button.chip.relax-chip',
          { type: 'button', 'data-name': c.name ?? c.label, onclick: () => go(c.remove()) },
          W.relax(W.relaxLabel(c.name ?? c.label), n),
        ),
      ),
    );
  }

  function watchEnd() {
    observer?.disconnect();
    if (!els.end || entry.data.items.length >= entry.data.total) return;
    observer = new IntersectionObserver(
      (list) => {
        if (list.some((e) => e.isIntersecting)) loadMore();
      },
      { rootMargin: `0px 0px ${SCROLL_AHEAD_PX}px 0px` },
    );
    observer.observe(els.end);
  }

  async function loadMore() {
    const { data } = entry;
    if (loadingMore || !els.list || data.items.length >= data.total) return;
    loadingMore = true;
    let more = null;
    try {
      more = await api.search(state, data.items.length, PAGE_SIZE, ctrl.signal);
    } catch {
      if (!ctrl.signal.aborted) {
        const retry = h('button.link-btn', { type: 'button', onclick: loadMore }, W.retry);
        els.end.replaceChildren(`${W.failed} `, retry);
      }
    }
    loadingMore = false;
    if (!more || ctrl.signal.aborted) return;
    const fresh = more.items || [];
    const start = data.items.length;
    data.items.push(...fresh);
    if (!fresh.length) data.total = data.items.length; // backend ran dry early; stop asking
    els.list.append(...fresh.map((item, k) => make(item, start + k)));
    els.end.textContent = data.items.length < data.total ? W.loading : W.end;
    remember({ loaded: data.items.length });
    if (data.items.length >= data.total) observer?.disconnect();
    else if (observer) {
      observer.unobserve(els.end); // re-observing fires again if the end is still in view
      observer.observe(els.end);
    }
  }

  // ---- keyboard ----

  function columns(list) {
    if (state.view === 'list' || list.length < 2) return 1;
    const top = list[0].getBoundingClientRect().top;
    let n = 1;
    while (n < list.length && Math.abs(list[n].getBoundingClientRect().top - top) < 2) n += 1;
    return n;
  }

  function focusCard(i) {
    const list = cards();
    const el = list[i];
    if (!el) return false;
    entry.selected = i;
    el.focus({ preventScroll: true });
    el.scrollIntoView({ block: 'nearest' });
    if (i >= list.length - columns(list) * 2) loadMore();
    return true;
  }

  function onKey(e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const list = cards();
    if (!list.length) return;
    const i = list.indexOf(document.activeElement);
    if (e.key === 'r' || e.key === 'R') {
      if (i < 0) return;
      e.preventDefault();
      readFrom(i);
      return;
    }
    const cols = columns(list);
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols }[e.key];
    if (!step) return;
    if (i < 0 && document.activeElement !== document.body) return; // focus is on some other control
    e.preventDefault();
    if (i < 0) focusCard(entry.selected ?? 0);
    else if (i + step < 0 && e.key === 'ArrowUp') shell.input.focus();
    else focusCard(Math.max(0, Math.min(list.length - 1, i + step)));
  }

  function removeLastCondition() {
    const last = conditions(state).at(-1);
    if (!last) return false;
    go(last.remove());
    return true;
  }

  // R on a card: open the first cached ✓/≈ source; unknown availability opens the work page to check.
  async function readFrom(i) {
    const item = entry.data.items[i];
    if (!item) return;
    try {
      const [detail, st] = await Promise.all([api.work(item.id, ctrl.signal), getStatus()]);
      const row = pickReadable(detail.availability);
      if (row && st.url && st.reachable !== false) {
        window.open(LINKS.read(row.manga_id), '_blank', 'noopener');
        return;
      }
    } catch {
      if (ctrl.signal.aborted) return;
    }
    openWork(i);
  }

  // ---- 检查本页 ----

  function paintStatus() {
    paintCatalog();
    const down = status?.reachable === false;
    const title = down ? W.suwayomiDown : '';
    if (els.organize) Object.assign(els.organize, { disabled: down, title });
    if (els.check) Object.assign(els.check, { disabled: down && !batch, title });
  }

  function toggleBatch() {
    if (batch) batch.ctrl.abort();
    else runBatch();
  }

  // Unchecked works from the first one in view, one page at most.
  function batchIds() {
    const list = cards();
    const top = document.querySelector('header.top')?.getBoundingClientRect().bottom ?? 0;
    const first = Math.max(
      0,
      list.findIndex((c) => c.getBoundingClientRect().bottom > top),
    );
    return entry.data.items
      .slice(first)
      .filter((it) => !it.readable || it.readable === 'unknown')
      .slice(0, PAGE_SIZE)
      .map((it) => it.id);
  }

  async function runBatch() {
    const ids = batchIds();
    if (!ids.length) {
      flash(els.check, W.checkedAll);
      return;
    }
    batch = { ctrl: new AbortController(), done: 0, total: ids.length };
    const { signal } = batch.ctrl;
    paintBatch();
    try {
      // At most the server's check_batch_max works per request; with no known limit, none.
      const size = (await getStatus()).checkBatch || (await refreshStatus()).checkBatch;
      if (!size) return;
      for (let start = 0; start < ids.length && !signal.aborted; start += size) {
        for await (const line of api.checkMany(ids.slice(start, start + size), signal)) {
          applyReadable(line.id, line.readable);
          batch.done = start + line.done;
          paintBatch();
        }
      }
    } catch {
      if (!signal.aborted) refreshStatus();
    } finally {
      batch = null;
      paintBatch();
      paintStatus();
    }
  }

  function applyReadable(id, readable) {
    const item = entry.data.items.find((it) => it.id === id);
    if (!item || readable === item.readable) return;
    setReadable(id, readable);
    const card = els.list?.querySelector(`a.card[data-id="${id}"]`);
    if (card) updateBadge(card, readable);
  }

  function paintBatch() {
    if (!els.check) return;
    els.check.textContent = batch ? W.checkingPage(batch.done, batch.total) : W.checkPage;
    els.check.classList.toggle('running', Boolean(batch));
    els.check.setAttribute('aria-busy', String(Boolean(batch)));
  }

  function flash(el, text) {
    el.textContent = text;
    setTimeout(() => {
      if (!batch) el.textContent = W.checkPage;
    }, FLASH_MS);
  }
}
