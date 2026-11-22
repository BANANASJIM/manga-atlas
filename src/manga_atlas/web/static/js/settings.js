// Frontend tunables and endpoint paths. Server-side settings live in config.py.

export const PAGE_SIZE = 48;
export const RESTORE_MAX = 480; // most items refetched to restore a deep scroll
export const CACHE_MAX = 8; // browse results kept for back/forward
export const CACHE_TTL_MS = 5 * 60_000;
export const SUGGEST_DELAY_MS = 90;
export const STATUS_POLL_MS = 30_000;
export const STATUS_WAIT_MS = 1500; // the work page renders without status after this
export const SCROLL_AHEAD_PX = 800; // start loading the next page this far before the end
export const FLASH_MS = 1500; // how long a one-off button message stays

export const SCORE_STEPS = [6, 7, 8];
export const FACET_VISIBLE = 8; // chips per facet group before 更多
export const USER_TAGS_VISIBLE = 8;
export const SOURCE_TAGS_VISIBLE = 8;
export const RELATION_VISIBLE = 6;
export const ALIAS_SEARCH_MAX = 4;
export const RELAX_MAX = 6; // conditions tried for the zero-result hint
export const RELAX_SHOW = 3;
export const PEOPLE_MAX = 300; // remembered author names
export const ORGANIZE_VISIBLE = 12; // 整理书架: entry titles per category before 更多

// Tag groups from the vocabulary, in panel order; region and status have their own facets.
export const DEFAULT_TAG_GROUP = 'tag'; // tags in no vocabulary group, and chips of unknown groups
// Tag sources beside Bangumi (labels in words.js), in work-page row order; the tags only a source
// gives form a facet group named after it.
export const TAG_SOURCES = ['jawiki', 'yurimiru'];
export const TAG_GROUPS = ['genre', 'audience', 'format', 'origin', DEFAULT_TAG_GROUP, ...TAG_SOURCES];
export const FACET_SKIP_GROUPS = ['region', 'status'];

export const SORTS = ['rank', 'heat', 'score', 'date', 'fav'];
export const TEXT_SORT = 'rel';
export const DEFAULT_SORT = 'rank';
export const LIST_SEP = ',';
export const TINTS = 6; // placeholder cover tints: --tint-0 … --tint-5 in app.css

export const WRITE_HEADERS = { 'X-Atlas': '1' };

export const API = {
  search: '/api/search',
  suggest: '/api/suggest',
  status: '/api/status',
  checkMany: '/api/check',
  organize: '/api/organize',
  work: (id) => `/api/work/${id}`,
  check: (id) => `/api/work/${id}/check`,
  favorite: (id) => `/api/favorites/${id}`,
  cover: (id) => `/cover/${id}`,
};

export const NEW_TAB = { target: '_blank', rel: 'noopener' }; // attributes of a link out of the app

export const LINKS = {
  bgmSubject: (id) => `https://bgm.tv/subject/${id}`,
  read: (id) => `/api/read/${id}`, // the server sends the reader on to the entry's Suwayomi page
  suwayomiSearch: (base, q) => `${base}/sources/all/search?query=${encodeURIComponent(q)}`,
  suwayomiCategory: (base, id) => `${base}/library?tab=${id}`, // that category's tab of the library
};
