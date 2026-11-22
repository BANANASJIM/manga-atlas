// Suwayomi reachability, the enabled sources and catalogue info from GET /api/status, polled.

import * as api from './api.js';
import { STATUS_POLL_MS } from './settings.js';

const subscribers = new Set();
let current = null;
let inflight = null;

function normalize(raw) {
  const r = raw || {};
  return {
    url: String(r.suwayomi_url || '').replace(/\/+$/, ''),
    reachable: typeof r.reachable === 'boolean' ? r.reachable : null,
    sources: (r.sources || []).map((s) => ({ id: String(s.id), name: s.name || String(s.id) })),
    checkBatch: Number(r.check_batch_max) || null, // works per 检查本页 request
    catalog: r.catalog || null,
  };
}

export function refreshStatus() {
  inflight ??= api
    .status()
    .then(normalize, () => current || normalize(null))
    .then((s) => {
      current = s;
      inflight = null;
      for (const fn of subscribers) fn(s);
      return s;
    });
  return inflight;
}

export const getStatus = () => (current ? Promise.resolve(current) : refreshStatus());
export const peekStatus = () => current;

// Calls fn now (if known) and on every change; returns the unsubscribe function.
export function onStatus(fn) {
  subscribers.add(fn);
  if (current) fn(current);
  return () => subscribers.delete(fn);
}

export function startPolling() {
  refreshStatus();
  setInterval(() => {
    if (!document.hidden) refreshStatus();
  }, STATUS_POLL_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshStatus();
  });
}
