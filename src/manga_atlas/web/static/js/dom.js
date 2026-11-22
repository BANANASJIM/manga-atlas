// DOM helpers. Text always goes in as text nodes, never as HTML.

import { NEW_TAB } from './settings.js';
import { W } from './words.js';

const PROPS = new Set(['value', 'checked', 'disabled', 'hidden']);

// h('a.card.row', {href, onclick}, child, [children], 'text')
export function h(tag, attrs, ...children) {
  const [name, ...classes] = tag.split('.');
  const el = document.createElement(name || 'div');
  if (classes.length) el.className = classes.join(' ');
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === 'class') el.classList.add(...String(value).split(' ').filter(Boolean));
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
    else if (PROPS.has(key)) el[key] = value;
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  append(el, children);
  return el;
}

// A link out of the app: opens in a new tab, its label ends in ↗; `attrs` add to the link's own.
export function extLink(href, cls, text, attrs) {
  return h(`a.${cls}`, { href, ...NEW_TAB, ...attrs }, W.outbound(text));
}

// A click that is the page's own business: the main button, no modifier key.
export const plainClick = (e) => e.button === 0 && !(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey);

// Replaces a page's content and makes it interactive again (main.js marks it inert while loading).
export function swap(root, ...nodes) {
  fill(root, ...nodes);
  root.inert = false;
}

// Replaces el's children; null and false are skipped, as in h().
export function fill(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

// Static, trusted path data for the few icons the UI needs.
const ICONS = {
  search: '<circle cx="7" cy="7" r="4.6"/><path d="M10.5 10.5 14 14"/>',
  grid: '<rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/><rect x="9" y="9" width="4.5" height="4.5" rx="1"/>',
  list: '<path d="M2.5 4h11M2.5 8h11M2.5 12h11"/>',
  left: '<path d="M10 3.5 5.5 8l4.5 4.5"/>',
  right: '<path d="M6 3.5 10.5 8 6 12.5"/>',
  refresh: '<path d="M13 8a5 5 0 1 1-1.6-3.7"/><path d="M13 2.8v2.7h-2.7"/>',
};

export function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('class', `icon icon-${name}`);
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name];
  return svg;
}

// Shows the first `visible` children of `container`; a toggle reveals the rest.
export function collapse(container, visible, wrap) {
  const items = [...container.children];
  if (items.length <= visible) return container;
  items.slice(visible).forEach((el) => el.classList.add('extra'));
  container.classList.add('collapsed');
  const button = h('button.more', { type: 'button', 'aria-expanded': 'false' }, W.more);
  button.addEventListener('click', () => {
    const open = container.classList.toggle('collapsed') === false;
    button.textContent = open ? W.less : W.more;
    button.setAttribute('aria-expanded', String(open));
  });
  container.append(wrap ? h(wrap, { class: 'more-wrap' }, button) : button);
  return container;
}

export function interleave(nodes, separator) {
  return nodes.flatMap((node, i) => (i ? [separator(), node] : [node]));
}
