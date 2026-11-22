// History-API routing. Scroll positions live in history.state so back/forward can restore them.

import { plainClick } from './dom.js';
import { browseURL } from './state.js';

let render = null;

export function startRouter(fn) {
  render = fn;
  history.scrollRestoration = 'manual';
  addEventListener('popstate', () => render(location, history.state || {}, { pop: true }));
  document.addEventListener('click', onClick);
  render(location, history.state || {}, { pop: false });
}

export function remember(extra = {}) {
  history.replaceState({ ...(history.state || {}), scrollY: window.scrollY, ...extra }, '');
}

export function navigate(url, { replace = false, state = {} } = {}) {
  if (replace) history.replaceState(state, '', url);
  else {
    remember();
    history.pushState(state, '', url);
  }
  render(location, state, { pop: false });
}

export const here = () => location.pathname + location.search;

// Goes to `url` unless the page is there already.
function visit(url) {
  if (url !== here()) navigate(url);
}

// The browse page for a browse state (state.js).
export const goBrowse = (state) => visit(browseURL(state));

function onClick(e) {
  if (e.defaultPrevented || !plainClick(e)) return;
  const a = e.target.closest('a[data-link]');
  if (!a || a.target) return;
  e.preventDefault();
  visit(a.getAttribute('href'));
}
