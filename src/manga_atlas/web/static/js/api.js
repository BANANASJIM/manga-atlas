// HTTP client for the MangaAtlas API.

import { API, WRITE_HEADERS } from './settings.js';
import { apiQuery } from './state.js';

export class HttpError extends Error {
  constructor(status, url, detail = '') {
    super(`${status} ${url}`);
    this.status = status;
    this.detail = detail; // the API's own {"error": …}, when it sent one
  }
}

let busy = 0;
function track(promise) {
  busy += 1;
  document.documentElement.classList.add('busy');
  return promise.finally(() => {
    busy -= 1;
    if (!busy) document.documentElement.classList.remove('busy');
  });
}

const errorOf = (res) => res.json().then((d) => d?.error || '', () => '');

async function request(url, init) {
  const res = await fetch(url, init);
  if (!res.ok) throw new HttpError(res.status, url, await errorOf(res));
  return res;
}

const post = (url, body, accept, signal) =>
  request(url, {
    method: 'POST',
    signal,
    headers: { ...WRITE_HEADERS, 'Content-Type': 'application/json', Accept: accept },
    body: JSON.stringify(body),
  });

function getJSON(url, signal, quiet = false) {
  const p = request(url, { signal, headers: { Accept: 'application/json' } }).then((r) => r.json());
  return quiet ? p : track(p);
}

export const search = (state, offset, limit, signal) =>
  getJSON(`${API.search}?${apiQuery(state, offset, limit)}`, signal);

export const count = (state, signal) =>
  getJSON(`${API.search}?${apiQuery(state, 0, 1)}`, signal, true).then((d) => d.total ?? 0);

export const suggest = (q, signal) => getJSON(`${API.suggest}?q=${encodeURIComponent(q)}`, signal, true);

export const work = (id, signal) => getJSON(API.work(id), signal);

export const status = () => getJSON(API.status, undefined, true);

// 收藏: on adds the work to the favorites, off takes it out.
export const favorite = (id, on) =>
  request(API.favorite(id), { method: on ? 'PUT' : 'DELETE', headers: WRITE_HEADERS });

// 整理书架: apply false previews the categories, true writes them to Suwayomi.
export const organize = (tags, apply, signal) =>
  track(post(API.organize, { tags, apply }, 'application/json', signal).then((r) => r.json()));

async function* ndjson(body) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = '';
  const parse = (line) => {
    try {
      return JSON.parse(line);
    } catch {
      console.warn('bad NDJSON line', line);
      return null;
    }
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let nl = buf.indexOf('\n');
      while (nl >= 0) {
        const row = buf.slice(0, nl).trim() && parse(buf.slice(0, nl));
        buf = buf.slice(nl + 1);
        if (row) yield row;
        nl = buf.indexOf('\n');
      }
    }
    const last = buf.trim() && parse(buf);
    if (last) yield last;
  } finally {
    reader.cancel().catch(() => {});
  }
}

async function* postStream(url, body, signal) {
  const res = await post(url, body, 'application/x-ndjson', signal);
  yield* ndjson(res.body);
}

// One line per source: {source, state: yes|maybe|no|down, manga_id?, title?, author_match?}
export const checkWork = (id, force, signal) => postStream(API.check(id), force ? { force: true } : {}, signal);

// Progress lines for 检查本页: {id, readable, done, total}.
export const checkMany = (ids, signal) => postStream(API.checkMany, { ids }, signal);
