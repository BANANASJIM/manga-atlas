// The page frame from index.html: header, search box, banner. Copy comes from words.js.

import { icon } from './dom.js';
import { W } from './words.js';

const $ = (id) => document.getElementById(id);

export const shell = {
  app: $('app'),
  input: $('q'),
  suggest: $('suggest'),
  suggestList: $('suggest-list'),
  suggestFoot: $('suggest-foot'),
  read: $('read'),
  readWrap: $('read-wrap'),
  fav: $('fav'),
  favWrap: $('fav-wrap'),
  suwa: $('suwa'),
  banner: $('banner'),

  setQuery(value) {
    this.input.value = value;
  },

  // The 只看能读 and 只看收藏 switches follow the browse state `s`; null (the work page) hides them.
  setSwitches(s) {
    this.read.checked = Boolean(s?.read);
    this.fav.checked = Boolean(s?.fav);
    this.readWrap.hidden = !s;
    this.favWrap.hidden = !s;
  },

  setStatus(st) {
    this.suwa.hidden = !st.url;
    if (st.url) this.suwa.href = st.url;
    this.suwa.dataset.state = st.reachable === true ? 'ok' : st.reachable === false ? 'down' : 'unknown';
    this.suwa.title = st.reachable === true ? W.suwayomiOk : st.reachable === false ? W.suwayomiDown : '';
    this.banner.hidden = st.reachable !== false;
  },
};

export function initShell() {
  const brand = $('brand');
  brand.setAttribute('aria-label', W.home);
  brand.querySelector('.logo').textContent = W.brand[0];
  brand.querySelector('.brand-name').textContent = W.brand;
  $('search').prepend(icon('search'));
  $('slash').textContent = W.keys.slash;
  shell.input.placeholder = W.searchPlaceholder;
  shell.input.setAttribute('aria-label', W.searchLabel);
  shell.suggestList.setAttribute('aria-label', W.searchLabel);
  shell.readWrap.querySelector('.switch-label').textContent = W.readOnly;
  shell.favWrap.querySelector('.switch-label').textContent = W.favOnly;
  shell.readWrap.title = W.readOnly; // narrow windows hide the labels
  shell.favWrap.title = W.favOnly;
  shell.suwa.querySelector('.suwa-name').textContent = W.outbound(W.suwayomi);
  shell.banner.textContent = W.banner;
}
