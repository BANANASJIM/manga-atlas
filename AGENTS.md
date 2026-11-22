# Rules for agents and humans

Set-up and checks are in [CONTRIBUTING.md](CONTRIBUTING.md); how the parts fit is in
[docs/architecture.md](docs/architecture.md). Code must not silently redefine documented behaviour;
report conflicts instead.

## Must

- Keep it simple: single user, local only, one process + SQLite. No extra services, accounts, or job queues.
- All tunables (ports, URLs, source IDs, thresholds, TTLs, tag lists) come from `config.py` (overridable by TOML).
  No magic values elsewhere: UI copy comes from `web/static/js/words.js`, colours and sizes from the CSS tokens.
- `suwayomi/` is the only code that talks to Suwayomi. Searching is `fetchSourceManga`; nothing adds to the library or writes
  categories except an explicit organize `apply`.
- Be polite to manga sources: never two concurrent requests to the same source; keep the spacing from `config.py`;
  live tests stay minimal and are marked `live`. Some sites ban IPs after bursts.
- Data goes under `Settings.data_dir`.
- Bind to 127.0.0.1 only; keep the Host check and the `X-Atlas` header check.
- Run `scripts/check.sh` before every commit; it must pass. UI changes also need `uv run pytest -m e2e tests/e2e`.
- Commit messages in English, imperative mood, and with no AI attribution or `Co-Authored-By` lines.
- Comments only where they carry weight. UI copy is terse Chinese (one short line per element).

## Must not

- Review your own work and call it an independent review.
