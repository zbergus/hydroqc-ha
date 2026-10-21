# Stability fork notes (`zbergus/hydroqc-ha`)

Personal fork of [hydroqc/hydroqc-ha](https://github.com/hydroqc/hydroqc-ha) focused on
keeping the Home Assistant integration usable while Hydro-Québec’s customer portal
is flaky. Upstream release `v0.7.0` (Feb 2026) is still the official baseline.

## Symptom this fork targets

After a few hours (often around evening portal maintenance), Home Assistant logs
flood with:

```text
HTTP error fetching Hydro-Québec data: Error Fetching
https://services-cl.solutions.hydroquebec.com/lsw/portail/prive/maj-session/ - 403
```

OAuth login still succeeds; creating the portal web session (`maj-session`) fails.
`ConfigEntryNotReady` then retries aggressively (hundreds of logins/hour), which
makes rate-limiting / WAF blocks worse. Matches open upstream issue
[#120](https://github.com/hydroqc/hydroqc-ha/issues/120).

## Merged from open PRs / forks

| Source | Decision | Why |
| --- | --- | --- |
| [#145](https://github.com/hydroqc/hydroqc-ha/pull/145) JWT base64url | **Merged** | Fixes real login crash for accented names; better than the hydroqc.ca workaround of renaming the account holder |
| [#146](https://github.com/hydroqc/hydroqc-ha/pull/146) brand assets | **Merged** | Harmless HA 2026.3+ branding from Frenck |
| [#143](https://github.com/hydroqc/hydroqc-ha/pull/143) reconfigure + new-contract 400 | **Merged** | Soft-fails HTTP 400 for ~10 days after a new contract (same stability pattern); reconfigure avoids delete/re-add |
| [#141](https://github.com/hydroqc/hydroqc-ha/pull/141) preheat recipe docs | **Merged** | Docs only |
| `fpoisson2` statistics `unit_class` | **Merged** | Removes invalid `unit_class` from external statistics metadata |
| [#131](https://github.com/hydroqc/hydroqc-ha/pull/131) Force IPv4 | **Skipped** | Only helps non-Canadian IPv6 geo-blocks; needs GitLab MR !475 for `WebUser(session=...)`. Login already works here |
| Dependabot PRs | **Skipped** | No runtime stability value for this fork |

## Local fixes (not upstream yet)

1. **Library patch** (`hydroqc_patches.py`): keep upstream web-session semantics
   (clear cookies → refresh access token → `maj-session`), then **one** full
   re-login retry on HTTP 403.
2. **Coordinator**: on portal HTTP 403, **60-minute cooldown** and soft-fail so
   setup stays loaded instead of `ConfigEntryNotReady` storms. Aligns with
   [hydroqc.ca troubleshooting](https://hydroqc.ca/en/docs/troubleshooting/)
   (portal maintenance is expected) and hydroqc2mqtt’s default 60‑minute sync.

## Why we did **not** fork Hydro-Quebec-API-Wrapper

Evaluated GitLab [`hydroqc/hydroqc`](https://gitlab.com/hydroqc/hydroqc) (PyPI
`Hydro-Quebec-API-Wrapper==4.2.6`) and open MRs:

| Library MR | Relevance |
| --- | --- |
| !455 force web session refresh on contract change | Multi-contract correctness, not 403 |
| !475 optional injected `ClientSession` | Enables Force IPv4; not a 403 fix |
| !434 / !402 / !299 | Missing-data / timezone / raw props — unrelated |

Reasons not to vendor or reverse-engineer the library for `maj-session`:

- Official design intentionally separates OAuth tokens from the portal web
  session (clear cookies, then Bearer → `maj-session`). Inventing a different
  cookie strategy fights that design.
- Read the Docs TODO is to **avoid `/portail/` URLs** entirely. Periods and
  hourly consumption still require them; there is no documented `wsapi`
  replacement for `maj-session` + portrait endpoints.
- Reverse-engineering a new session protocol needs live browser captures against
  a working HQ account and ongoing maintenance of Azure B2C quirks — high cost,
  low confidence of a durable fix.
- Docs already treat intermittent portal HTTP errors as expected; the HA damage
  mode is the retry storm, which this fork addresses.

If upstream ships a library release that replaces `/portail/` session creation,
drop the monkeypatch and pin that version in `manifest.json`.

## Install via HACS (this fork)

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/zbergus/hydroqc-ha` (category: Integration)
3. Download / update to the latest release of this fork
4. Restart Home Assistant
5. Remove the official `hydroqc/hydroqc-ha` custom repo if both are present to
   avoid version clashes
