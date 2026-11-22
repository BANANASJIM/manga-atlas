// Work page: Bangumi details, relations, and 在哪读 streamed source by source.

import * as api from './api.js';
import { bestOf, cover, isReadable, scoreLabel } from './cards.js';
import { collapse, extLink, fill, h, icon, interleave, plainClick, swap } from './dom.js';
import { navigate } from './router.js';
import {
  ALIAS_SEARCH_MAX,
  LINKS,
  NEW_TAB,
  RELATION_VISIBLE,
  SOURCE_TAGS_VISIBLE,
  STATUS_WAIT_MS,
  TAG_SOURCES,
  USER_TAGS_VISIBLE,
} from './settings.js';
import { shell } from './shell.js';
import { browseURL, cacheKey, emptyState, parseState, summarize, uniq } from './state.js';
import { getStatus, onStatus, peekStatus, refreshStatus } from './status.js';
import { peekResults, rememberPerson, setFavorite, setReadable } from './store.js';
import { clock, fmt, shortDate, W } from './words.js';

const browse = (patch) => browseURL({ ...emptyState(), ...patch });
const sep = () => h('span.sep', { 'aria-hidden': 'true' }, W.sep);

function fromLine(line) {
  return {
    // An expired cached verdict waits for the check: never offered, never counted.
    state: line.fresh === false ? 'stale' : line.state,
    manga_id: line.manga_id ?? null,
    title: line.title || '',
    author_match: line.author_match ?? null, // false: the source names another author
    renamed: Boolean(line.renamed), // listed under a title the work is not known by
  };
}

// 同名不同作: a short title by another author (state no: listed for a look, never read).
const isOther = (r) => r.state === 'no' && r.author_match === false && Boolean(r.title);
const isMissing = (r) => r.state === 'no' && !isOther(r);

// Where it can be read first, then what deserves a look, then the rest (checking, waiting).
const ROW_ORDER = { yes: 0, maybe: 0, down: 2 };
const rowRank = (r) => (isOther(r) ? 1 : (ROW_ORDER[r.state] ?? 3));

// ✓ by author or by a long title; ≈ a short title only, or another translation its author vouches
// for. A title the work is not known by is shown.
function subText(r) {
  if (r.author_match && isReadable(r.state)) return W.work.authorMatch(r.renamed ? r.title : '');
  if (r.state === 'yes') return W.work.titleMatch(r.title);
  if (r.state === 'maybe') return W.work.titleOnly(r.title);
  if (isOther(r)) return W.work.otherWork(r.title);
  return W.work.state[r.state];
}

export function renderWork(root, id, hstate) {
  const ctrl = new AbortController();
  const rows = new Map(); // source id -> row, in source order
  const els = {};
  const ctx = typeof hstate.from === 'string' && Number.isInteger(hstate.index) ? hstate : null;
  let work = null;
  let status = null;
  let checkCtrl = null;

  shell.setQuery('');
  shell.setSwitches(null);

  const offStatus = onStatus((s) => {
    status = s;
    if (!work) return;
    addSources();
    paintRows();
    paintFallback();
  });

  // Started before the details load so ←/→ work at once.
  const near = ctx ? findNeighbors() : Promise.resolve(null);
  let markReady;
  const ready = new Promise((resolve) => {
    markReady = resolve;
  });
  load().finally(markReady);

  return {
    onKey,
    cleanup() {
      ctrl.abort();
      checkCtrl?.abort();
      offStatus();
    },
  };

  async function load() {
    // A slow status call (Suwayomi hanging) must not hold up the page; names and links fill in later.
    const late = new Promise((resolve) => setTimeout(() => resolve(peekStatus()), STATUS_WAIT_MS));
    try {
      [work, status] = await Promise.all([api.work(id, ctrl.signal), Promise.race([getStatus(), late])]);
    } catch (err) {
      if (!ctrl.signal.aborted) drawError(err);
      return;
    }
    if (ctrl.signal.aborted) return;
    document.title = `${work.title} · ${W.brand}`;
    for (const p of work.people || []) rememberPerson(p.id, p.name);
    initRows();
    swap(root, view());
    window.scrollTo(0, hstate.scrollY ?? 0);
    paintRows();
    paintFallback();
    clampSummary();
    near.then(paintNav);
    runCheck(false);
  }

  function drawError(err) {
    const missing = err?.status === 404;
    swap(
      root,
      h(
        'div.empty',
        null,
        h('p.empty-title', null, missing ? W.work.notFound : W.failed),
        h('a.btn', { href: '/', 'data-link': '' }, W.work.toResults),
      ),
    );
  }

  // ---- 在哪读 ----

  function initRows() {
    rows.clear();
    addSources();
    // Cached results (enabled sources only) show at once; the check stream then replaces them.
    for (const line of work.availability || []) addLine(line);
  }

  // Source names and order come from /api/status, which may arrive after the rows.
  function addSources() {
    const running = Boolean(els.where?.classList.contains('running'));
    for (const s of status?.sources || []) {
      const row = rows.get(s.id);
      if (row) row.name = s.name;
      else rows.set(s.id, { source: s.id, name: s.name, state: running || !els.where ? 'checking' : 'pending' });
    }
  }

  function addLine(line) {
    const key = String(line.source);
    const row = rows.get(key) || { source: key, name: line.name || key };
    rows.set(key, { ...row, ...fromLine(line) });
  }

  async function runCheck(force) {
    checkCtrl?.abort();
    const c = new AbortController();
    checkCtrl = c;
    if (force) {
      for (const r of rows.values()) Object.assign(r, { state: 'checking', manga_id: null, title: '', author_match: null });
    }
    els.where.classList.add('running');
    els.recheck.disabled = true;
    paintRows();
    let failed = false;
    try {
      for await (const line of api.checkWork(id, force, c.signal)) {
        addLine(line);
        paintRows();
      }
    } catch {
      if (c.signal.aborted) return;
      failed = true;
    }
    if (c.signal.aborted) return;
    for (const r of rows.values()) if (r.state === 'checking') r.state = failed ? 'down' : 'pending';
    if (failed) refreshStatus();
    els.where.classList.remove('running');
    els.recheck.disabled = false;
    const now = new Date();
    els.time.textContent = clock(now);
    els.time.dateTime = now.toISOString();
    paintRows();
    const best = [...rows.values()].reduce((acc, r) => bestOf(acc, r.state), undefined);
    if (best) setReadable(id, best);
  }

  function readTarget() {
    return els.rows?.querySelector('li.yes a.read') || els.rows?.querySelector('li.maybe a.read') || null;
  }

  function paintRows() {
    if (!els.rows) return;
    const focused = els.rows.contains(document.activeElement) ? document.activeElement.closest('li')?.dataset.source : null;
    const list = [...rows.values()];
    const online = Boolean(status?.url) && status.reachable !== false; // cached ✓ stay listed when down
    const can = (r) => isReadable(r.state) && r.manga_id != null && online;
    const target = list.find((r) => r.state === 'yes' && can(r)) || list.find((r) => r.state === 'maybe' && can(r));
    const missing = list.filter(isMissing);
    const fold = missing.length > 1; // all misses in one line
    const shown = (fold ? list.filter((r) => !isMissing(r)) : list).sort((a, b) => rowRank(a) - rowRank(b));
    els.rows.replaceChildren(
      ...shown.map((r) => rowView(r, can(r), r === target)),
      ...(fold ? [missingView(missing, !shown.length)] : []),
    );
    const n = list.filter((r) => isReadable(r.state)).length;
    els.sum.textContent = n ? W.work.readableSources(n) : '';
    if (focused) els.rows.querySelector(`li[data-source="${focused}"] a.read`)?.focus();
  }

  function rowView(r, canRead, isTarget) {
    return h(
      `li.src.${r.state}`,
      { 'data-source': r.source },
      h('span.mark', { 'aria-hidden': 'true' }, r.state === 'checking' ? h('span.spin') : W.mark[r.state]),
      h('span.src-body', null, h('b.src-name', null, r.name), h('small.src-sub', null, subText(r))),
      isTarget ? h('kbd.r-hint', { title: W.work.rHint }, W.keys.r) : null,
      canRead
        ? extLink(LINKS.read(r.manga_id), `read${r.state === 'yes' ? '.primary' : ''}`, W.work.read, {
            'aria-label': W.work.readLabel(r.name),
          })
        : null,
    );
  }

  function missingView(list, all) {
    const names = W.work.sourceList(list.map((r) => r.name));
    return h(
      'li.src.no.missing',
      null,
      h('span.mark', { 'aria-hidden': 'true' }, W.mark.no),
      h(
        'span.src-body',
        null,
        h('b.src-name', null, W.work.missing(list.length, all)),
        h('small.src-sub', { title: names }, names),
      ),
    );
  }

  function paintFallback() {
    if (!els.fallback) return;
    if (!status?.url) {
      els.fallback.replaceChildren();
      return;
    }
    const alts = altNames().slice(0, ALIAS_SEARCH_MAX);
    fill(
      els.fallback,
      extLink(LINKS.suwayomiSearch(status.url, work.title), 'global', W.work.global(work.title)),
      alts.length
        ? h(
            'div.alias-search',
            null,
            h('span.kind', null, W.work.aliasSearch),
            h(
              'div.chips',
              null,
              alts.map((a) => extLink(LINKS.suwayomiSearch(status.url, a), 'chip', a)),
            ),
          )
        : null,
    );
  }

  // ---- prev / next within the results this work was opened from ----

  async function findNeighbors() {
    const from = parseState(new URL(ctx.from, location.origin).search);
    const cached = peekResults(cacheKey(from));
    let items;
    let total;
    let base = 0;
    if (cached && (cached.data.items.length > ctx.index + 1 || cached.data.items.length >= cached.data.total)) {
      ({ items, total } = cached.data);
    } else {
      base = Math.max(0, ctx.index - 1);
      try {
        const d = await api.search(from, base, 3, ctrl.signal);
        items = d.items || [];
        total = d.total ?? 0;
      } catch {
        return null;
      }
    }
    const at = (k) => (k >= 0 ? items[k - base] || null : null);
    return { prev: at(ctx.index - 1), next: at(ctx.index + 1), total };
  }

  function paintNav(n) {
    if (!n || !els.pos) return;
    els.pos.textContent = W.work.pos(ctx.index + 1, n.total);
    els.prev.disabled = !n.prev;
    els.next.disabled = !n.next;
  }

  async function step(delta) {
    const n = await near;
    const target = delta < 0 ? n?.prev : n?.next;
    if (!target || ctrl.signal.aborted) return;
    const index = ctx.index + delta;
    const cached = peekResults(cacheKey(parseState(new URL(ctx.from, location.origin).search)));
    if (cached) cached.selected = index;
    navigate(`/work/${target.id}`, { replace: true, state: { from: ctx.from, index } });
  }

  function onKey(e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && ctx) {
      e.preventDefault();
      step(e.key === 'ArrowLeft' ? -1 : 1);
    } else if (e.key === 'r' || e.key === 'R') {
      // A key pressed while the page is still loading acts once cached rows are shown;
      // the keydown's user activation still covers opening the tab.
      e.preventDefault();
      ready.then(() => {
        if (!ctrl.signal.aborted) readTarget()?.click();
      });
    } else if (e.key === 'f' || e.key === 'F') {
      e.preventDefault();
      ready.then(() => {
        if (!ctrl.signal.aborted && els.fav) toggleFavorite();
      });
    }
  }

  // ---- 收藏 ----

  function paintFavorite() {
    const on = Boolean(work.favorite);
    const s = on ? 'on' : 'off';
    els.fav.classList.toggle('on', on);
    els.fav.title = W.fav.hint[s];
    fill(els.fav, h('span.mark', { 'aria-hidden': 'true' }, W.fav.mark[s]), W.fav.label[s]);
  }

  // Shown at once, taken back if the server refuses.
  async function toggleFavorite() {
    const show = (on) => {
      work.favorite = on;
      setFavorite(id, on);
      paintFavorite();
    };
    const on = !work.favorite;
    show(on);
    try {
      await api.favorite(id, on);
    } catch {
      show(!on);
    }
  }

  // ---- layout ----

  function altNames() {
    return uniq([work.name, ...(work.aliases || [])].filter((a) => a && a !== work.title));
  }

  function view() {
    return h(
      'article.work',
      null,
      navBar(),
      h(
        'div.work-main',
        null,
        h(
          'div.work-side',
          null,
          cover(work, 'big'),
          extLink(LINKS.bgmSubject(work.id), 'ext-link', W.work.bgm),
        ),
        h('div.work-info', null, titleBlock(), metaLine(), publishLine(), scoreBlock(), tagsBlock(), summaryBlock()),
        wherePanel(),
      ),
      relationsBlock(),
      footer(),
    );
  }

  function navBar() {
    if (!ctx) return null;
    const summary = summarize(parseState(new URL(ctx.from, location.origin).search));
    const back = h('a.back', { href: ctx.from }, icon('left'), W.work.back);
    // The results page is the previous history entry: going back restores its scroll and selection.
    back.addEventListener('click', (e) => {
      if (!plainClick(e)) return;
      e.preventDefault();
      history.back();
    });
    els.pos = h('span.pos');
    els.prev = h(
      'button.icon-btn',
      { type: 'button', disabled: true, 'aria-label': W.work.prev, title: W.work.prev, onclick: () => step(-1) },
      icon('left'),
    );
    els.next = h(
      'button.icon-btn',
      { type: 'button', disabled: true, 'aria-label': W.work.next, title: W.work.next, onclick: () => step(1) },
      icon('right'),
    );
    return h(
      'nav.work-nav',
      { 'aria-label': W.work.back },
      back,
      summary ? h('span.ctx', null, summary) : null,
      els.pos,
      h('span.grow'),
      els.prev,
      els.next,
    );
  }

  function titleBlock() {
    const alts = altNames();
    els.fav = h('button.btn.fav-toggle', { type: 'button', onclick: toggleFavorite });
    paintFavorite();
    return [
      h('div.work-head', null, h('h1.work-title', null, work.title), els.fav),
      alts.length ? h('p.aliases', { title: alts.join('\n') }, alts.join(` ${W.sep} `)) : null,
    ];
  }

  // A credit leads to that name's works when it has an id; the role is shown when asked.
  function credit(p, withRole) {
    return h(
      p.id != null ? 'a.plink' : 'span',
      p.id != null ? { href: browse({ a: String(p.id) }), 'data-link': '' } : null,
      p.name,
      withRole && p.role ? h('span.role', null, p.role) : null,
    );
  }

  function metaLine() {
    const creators = (work.people || []).filter((p) => p.creator);
    const showRoles = new Set(creators.map((p) => p.role)).size > 1;
    const parts = creators.map((p) => credit(p, showRoles));
    if (work.year) parts.push(h('a.plink', { href: browse({ y: `${work.year}-${work.year}` }), 'data-link': '' }, String(work.year)));
    if (work.status) parts.push(h('a.plink', { href: browse({ st: work.status }), 'data-link': '' }, work.status));
    if (work.vols) parts.push(h('span', null, W.work.vols(work.vols)));
    if (work.region) {
      parts.push(
        h(
          'a.plink',
          { href: browse({ region: work.region }), 'data-link': '' },
          work.region,
          work.region_inferred ? h('span.inferred', null, W.work.inferred) : null,
        ),
      );
    }
    return h('p.work-meta', null, interleave(parts, sep));
  }

  // Publisher and magazine: who put it out, not who made it. Always labelled with the role.
  function publishLine() {
    const others = (work.people || []).filter((p) => !p.creator);
    return others.length ? h('p.work-pub', null, interleave(others.map((p) => credit(p, true)), sep)) : null;
  }

  function scoreBlock() {
    return h(
      'div.score-block',
      null,
      h('span.score-big', null, scoreLabel(work.score) || W.dash),
      h(
        'span.score-sub',
        null,
        h('span', null, work.votes ? W.work.votes(work.votes) : W.work.noScore),
        work.rank ? h('span', null, W.work.rank(work.rank)) : null,
      ),
    );
  }

  function tagsBlock() {
    const tags = (work.tags || []).filter((t) => t?.name);
    const official = tags.filter((t) => t.official);
    const user = tags.filter((t) => !t.official && t.votes > 0).sort((a, b) => b.votes - a.votes);
    // Bangumi's rows mark official tags and count user votes; other sources' rows have neither.
    const chip = (bangumi) => (t) =>
      h(
        `a.chip${bangumi && t.official ? '.official' : ''}`,
        { href: browse({ t: [t.name] }), 'data-link': '', 'data-name': t.name },
        t.name,
        bangumi && !t.official && t.votes ? h('span.n', null, fmt(t.votes)) : null,
      );
    const chips = (list, limit, bangumi) => collapse(h('div.chips', null, list.map(chip(bangumi))), limit);
    const row = (kind, list, limit) =>
      list.length ? h('div.tag-row', null, h('span.kind', null, kind), chips(list, limit, true)) : null;
    // A source's tags (merged ones by their catalogue name) and a link to the page they come from.
    const sourceRow = (key) => {
      const words = W.sources[key];
      const list = tags.filter((t) => t.sources?.includes(key));
      const page = (work.pages || []).find((p) => p.source === key);
      if (!words || (!list.length && !page)) return null;
      const line = chips(list, SOURCE_TAGS_VISIBLE, false);
      if (page) line.append(extLink(page.url, 'page-link', words.link, { title: words.title(page.title) }));
      return h('div.tag-row', { 'data-source': key }, h('span.kind', null, words.label), line);
    };
    const rows = [
      row(W.officialTag, official, Infinity),
      row(W.userTag, user, USER_TAGS_VISIBLE),
      ...TAG_SOURCES.map(sourceRow),
    ];
    return rows.some(Boolean) ? h('div.tag-rows', { 'aria-label': W.work.tags }, rows) : null;
  }

  function summaryBlock() {
    if (!work.summary) return null;
    els.summary = h('p.summary.clamped', null, work.summary);
    els.expand = h('button.link-btn.expand', { type: 'button', hidden: true }, W.work.expand);
    els.expand.addEventListener('click', () => {
      const open = els.summary.classList.toggle('clamped') === false;
      els.expand.textContent = open ? W.work.collapse : W.work.expand;
    });
    return h('div.summary-wrap', null, els.summary, els.expand);
  }

  function clampSummary() {
    if (els.summary) els.expand.hidden = els.summary.scrollHeight <= els.summary.clientHeight + 1;
  }

  function wherePanel() {
    els.rows = h('ul.where-rows', { 'aria-live': 'polite' });
    els.sum = h('span.where-sum');
    els.time = h('time.where-time');
    els.recheck = h('button.link-btn.recheck', { type: 'button', onclick: () => runCheck(true) }, icon('refresh'), W.work.recheck);
    els.fallback = h('div.where-fallback');
    els.where = h(
      'aside.where',
      { 'aria-label': W.work.where },
      h('header.where-head', null, h('h2', null, W.work.where), els.sum, h('span.grow'), els.time, els.recheck),
      els.rows,
      els.fallback,
    );
    return els.where;
  }

  function relationsBlock() {
    const rels = (work.relations || []).filter((r) => r?.id != null);
    if (!rels.length) return null;
    const groups = new Map();
    for (const r of rels) {
      if (!groups.has(r.relation)) groups.set(r.relation, []);
      groups.get(r.relation).push(r);
    }
    return h(
      'section.relations',
      { 'aria-label': W.work.relations },
      h('h2', null, W.work.relations),
      h(
        'div.rel-groups',
        null,
        [...groups].map(([rel, list]) =>
          h(
            'section.rel-group',
            { 'aria-label': rel },
            h('h3', null, rel, h('span.n', null, list.length)),
            collapse(h('ul.rel-list', null, list.map(relItem)), RELATION_VISIBLE, 'li'),
          ),
        ),
      ),
    );
  }

  function relItem(r) {
    const meta = [r.type, r.year].filter(Boolean).join(` ${W.sep} `);
    const body = (...extra) =>
      h('span.rel-body', null, h('span.rel-title', null, r.title), meta ? h('span.rel-meta', null, meta) : null, ...extra);
    if (r.in_catalog === false) {
      return h(
        'li',
        null,
        h(
          'a.rel.ext',
          { href: LINKS.bgmSubject(r.id), ...NEW_TAB },
          h('span.rel-ph', { 'aria-hidden': 'true' }, r.type || W.work.bgm),
          body(h('span.rel-ext', null, W.outbound(W.work.bgm))),
        ),
      );
    }
    return h('li', null, h('a.rel', { href: `/work/${r.id}`, 'data-link': '' }, cover(r, 'tiny'), body()));
  }

  function footer() {
    const built = status?.catalog?.built_at;
    const credits = (work.pages || []).map((p) => W.sources[p.source]?.credit);
    const parts = [W.work.source, ...credits, built ? W.work.updated(shortDate(built)) : null];
    return h('footer.work-foot', null, parts.filter(Boolean).join(` ${W.sep} `));
  }
}
