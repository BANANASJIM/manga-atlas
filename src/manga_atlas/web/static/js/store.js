// In-memory browse results (for back/forward) and remembered author names.

import { CACHE_MAX, CACHE_TTL_MS, PEOPLE_MAX } from './settings.js';

const results = new Map(); // cache key -> { data, selected, at, stale? }

// For showing a list: a stale entry (see setReadable) is fetched again.
export function getResults(key) {
  const entry = peekResults(key);
  return entry && !entry.stale ? entry : null;
}

// Stale entries too: the work page steps through the list it was opened from, as it was then.
export function peekResults(key) {
  const entry = results.get(key);
  if (!entry) return null;
  if (Date.now() - entry.at > CACHE_TTL_MS) {
    results.delete(key);
    return null;
  }
  results.delete(key);
  results.set(key, entry);
  return entry;
}

export function putResults(key, entry) {
  results.delete(key);
  results.set(key, { ...entry, at: Date.now() });
  while (results.size > CACHE_MAX) results.delete(results.keys().next().value);
  return results.get(key);
}

// Keeps cached items in step with a fresh availability result. A readable-only result may now
// gain or lose this work: it turns stale, fetched again when next shown, while prev/next keep
// stepping through it unchanged.
export function setReadable(id, readable) {
  update(id, 'readable', readable, (p) => p.get('read') === '1');
}

// The same for a 收藏 toggle: a result limited to or sorted by favorites may gain, lose or move it.
export function setFavorite(id, on) {
  update(id, 'favorite', on, (p) => p.get('fav') === '1' || p.get('sort') === 'fav');
}

function update(id, field, value, affected) {
  for (const [key, entry] of results) {
    if (affected(new URLSearchParams(key))) entry.stale = true;
    for (const item of entry.data.items) if (item.id === id) item[field] = value;
  }
}

const PEOPLE_KEY = 'manga-atlas.people';
const people = loadPeople(); // id -> { name, role?, creator? }

function loadPeople() {
  try {
    return new Map(JSON.parse(localStorage.getItem(PEOPLE_KEY) || '[]'));
  } catch {
    return new Map();
  }
}

// `role` and `creator` come from a search response (the role held in most works); a call without
// them keeps what is known.
export function rememberPerson(id, name, role = null, creator = null) {
  if (id == null || !name) return;
  const key = String(id);
  const prev = people.get(key) ?? {};
  const next = role ? { name, role, creator: Boolean(creator) } : { ...prev, name };
  if (prev.name === next.name && prev.role === next.role && prev.creator === next.creator) return;
  people.delete(key);
  people.set(key, next);
  while (people.size > PEOPLE_MAX) people.delete(people.keys().next().value);
  try {
    localStorage.setItem(PEOPLE_KEY, JSON.stringify([...people]));
  } catch {
    // Storage full or disabled: names still live for this page.
  }
}

export const personInfo = (id) => people.get(String(id)) ?? null;
