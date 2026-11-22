// Work cards, covers and readable badges shared by both pages.

import { h } from './dom.js';
import { API, FACET_SKIP_GROUPS, TINTS } from './settings.js';
import { W } from './words.js';

const RANK = { yes: 3, maybe: 2, no: 1 };

export const isReadable = (r) => r === 'yes' || r === 'maybe';

// Best of two availability states: yes > maybe > no > unknown.
export function bestOf(a, b) {
  return (RANK[b] || 0) > (RANK[a] || 0) ? b : a;
}

// First fresh ✓ row, else first fresh ≈ row, that can be opened in Suwayomi.
export function pickReadable(rows) {
  const ok = (rows || []).filter((r) => r.manga_id != null && r.fresh !== false);
  return ok.find((r) => r.state === 'yes') || ok.find((r) => r.state === 'maybe') || null;
}

export const scoreLabel = (v) => (v ? Number(v).toFixed(1) : '');

export function metaLine(item) {
  return [scoreLabel(item.score), item.year, item.status].filter(Boolean).join(` ${W.sep} `);
}

const glyph = (title) => (Array.from((title || '').trim())[0] || W.sep).toUpperCase();
const tint = (id) => `tint-${Math.abs(Number(id) || 0) % TINTS}`;

export function badge(readable) {
  if (!isReadable(readable)) return null;
  return h(
    `span.badge.${readable}`,
    { title: W.badgeTitle[readable] },
    h('i.dot', { 'aria-hidden': 'true' }),
    readable === 'maybe' ? `${W.mark.maybe} ${W.badge}` : W.badge,
  );
}

// Placeholder glyph underneath; the image covers it once loaded and is dropped on 404.
export function cover(item, size) {
  const img = h('img', { src: item.cover || API.cover(item.id), alt: '', loading: 'lazy', decoding: 'async' });
  img.addEventListener('error', () => img.remove(), { once: true });
  return h(
    `div.cover.${tint(item.id)}`,
    { class: size || null },
    h('span.ph', { 'aria-hidden': 'true' }, glyph(item.title)),
    img,
  );
}

export function updateBadge(card, readable) {
  const slot = card.querySelector('[data-badge]');
  slot.querySelector('.badge')?.remove();
  const b = badge(readable);
  if (b) slot.append(b);
}

const names = (list) => (list || []).map((v) => v?.name).filter(Boolean);

// A favorite's ★, before its title.
const favMark = (item) => (item.favorite ? [h('span.fav-mark', { title: W.fav.label.on }, W.fav.mark.on), ' '] : null);

function link(item, index, cls, ...children) {
  return h(
    `a.card${cls}`,
    { href: `/work/${item.id}`, 'data-id': item.id, 'data-index': index },
    ...children,
  );
}

export function workCard(item, index) {
  const c = cover(item);
  c.dataset.badge = '';
  const b = badge(item.readable);
  if (b) c.append(b);
  return h(
    'li',
    null,
    link(
      item,
      index,
      '',
      c,
      h('span.title', { title: item.title }, favMark(item), item.title),
      h('span.meta', null, metaLine(item)),
    ),
  );
}

export function workRow(item, index) {
  const tags = names((item.tags || []).filter((t) => !FACET_SKIP_GROUPS.includes(t?.grp)));
  const meta = [metaLine(item), W.authors(names(item.authors)), item.region].filter(Boolean);
  return h(
    'li',
    null,
    link(
      item,
      index,
      '.row',
      cover(item, 'small'),
      h(
        'span.body',
        null,
        h(
          'span.line1',
          null,
          h('span.title', null, favMark(item), item.title),
          item.name && item.name !== item.title ? h('span.orig', null, item.name) : null,
        ),
        h('span.meta', null, meta.join(` ${W.sep} `)),
        tags.length ? h('span.tags', null, tags.map((t) => h('span.tag', null, t))) : null,
        item.summary ? h('span.summary', null, item.summary) : null,
      ),
      h('span.badge-slot', { 'data-badge': '' }, badge(item.readable)),
    ),
  );
}
