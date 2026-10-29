# The Daily Ledger

*The World. The Markets. Your Cities.* — a fully automated, bilingual (Italian/English) personal newspaper that is published every morning at 08:00 Europe/Luxembourg time. The International Edition and seven city editions (Venice, Luxembourg, Milan, Rome, London, Paris, New York), each in Italian and English, make 16 logical editions. Every story is generated from identified sources and checked by a code-level evidence gate.

Accuracy is mandatory. Completeness is optional. Fabrication is forbidden.

---

## How it works

```
 05:20  GitHub Actions ─► fetch 209 verified RSS/Atom feeds + official market data
        │                  (timeouts, retries, Retry-After, one failure never stops the run)
        ├─► normalise: canonical URLs, multilingual date parsing, ambiguous dates rejected,
        │              wire-agency credits detected, opinion pieces and low-value items dropped
        ├─► cluster reports about the same development (EN/IT/FR/DE, alias-aware)
        ├─► independence: same owner group / same wire copy / near-identical text = ONE origin
        ├─► rank (transparent 100-point model, weights in config/settings.yaml)
        ├─► plan 16 editions; each story is written ONCE and reused across editions
        ├─► evidence pack per story: official docs first, URLs checked live (404 ⇒ dropped),
        │   official pages fetched in full (robots.txt respected); paywalls never bypassed
        ├─► AI draft (both languages from the same claims) ─► PUBLICATION GATE ─► repair ─►
        │   per-language regeneration ─► else an attributed headline brief (cannot invent)
        ├─► Big Picture analyses, market ledger, Morning Brief, Day Ahead, trackers
        └─► stage edition in data/ (committed to git = archive + audit trail)
 08:00  publish ─► static site (GitHub Pages) + optional email ─► status page
```

### The publication gate (`ledger/validate.py`)

A draft is published only if every checkable atom traces to the evidence. These checks are deterministic code, not AI self-assessment:

| Check | Rule |
|---|---|
| Claims | Each claim cites real evidence ids and a support excerpt that occurs **verbatim** in that evidence |
| Numbers | Every figure in a paragraph occurs in the evidence its claims cite (IT/EN number formats, scales such as bn/miliardi, %, bp) |
| Dates | Calendar dates and weekdays must be in the evidence or be the source's own publication date |
| Quotations | Quoted text must be verbatim in the cited evidence |
| Names | Every proper name must occur in the cited evidence (alias-aware: BCE = ECB, Roma = Rome…) |
| Causation | Causal wording ("because", "a causa di"…) only where a source states the cause |
| Legal status | A proposal or draft can never be described as approved or in force |
| Crime | No guilt wording without a conviction in evidence; hedging is required where evidence describes arrests or charges |
| Attribution | Single-source stories must attribute |
| Access | If only headlines/snippets were readable, the story must stay short and is labelled |
| Conflicts | Conflicting figures must be reported together; an official figure must be included |
| URLs | The model may not write links; sources are attached from evidence |
| Parity | IT and EN must carry the same figures (multiset), dates, cited claims and key names |

On real drafts written from live evidence on 2026-10-28, the gate passed honest drafts. It also correctly rejected an Italian draft that implied a measure was "in force" when the sources did not say so, and a draft that used an unsupported title and exceeded the snippet-length cap.

**Limitation (stated plainly):** the gate makes invented *specifics* fail closed. It cannot prove that a sentence relates two true facts correctly. Every story therefore shows its evidence record and source links.

---

## Status: what is implemented, tested, configured, operational

| Component | Implemented | Tested here | Operational |
|---|---|---|---|
| Source registry (209 feeds) | ✅ | ✅ live: 206/209 fetched on 2026-10-28 | Runs in GitHub Actions once deployed |
| Ingestion, dedupe, clustering, ranking | ✅ | ✅ live (≈7,000 items → ≈2,300 developments, ~1–3 min) | ″ |
| Evidence records, triangulation, conflicts | ✅ | ✅ unit + live | ″ |
| Publication gate + IT/EN parity | ✅ | ✅ 20 required scenarios + live drafts | ″ |
| AI drafting (Groq → Gemini → Cerebras, free tiers) | ✅ | ✅ provider chain, key rejection, quota fallthrough (mocked). The three endpoints were reached live and answered as expected without a key | After adding `GROQ_API_KEY` (and optionally `GEMINI_API_KEY`) |
| Fallback when AI fails (briefs, circuit breaker, `redraft`) | ✅ | ✅ (also hit live: a bogus endpoint opened the breaker) | ″ |
| Market panel (ECB, Bundesbank, US Treasury, BoE) | ✅ | ✅ live | ″ |
| Market panel (FRED: Brent, Henry Hub, Fed range) | ✅ | ⚠️ endpoint reachable; needs your free key | After adding `FRED_API_KEY` |
| Website: 16 editions, sections, Big Picture, story pages, archive + search, trackers, settings, status, dark/compact mode, saved stories, print CSS | ✅ | ✅ rendered and inspected (desktop + mobile) | After GitHub Pages is enabled |
| Corrections and withdrawals with version history | ✅ | ✅ | ″ |
| Email edition (SMTP) | ✅ | ✅ with simulated SMTP (success/failure) | After adding SMTP secrets |
| 08:00 schedule (DST-aware) | ✅ workflow written | ✅ decision logic and DST unit-tested | **Not active until you push the repo** |
| Deployment | — | — | **Not deployed** (no repository yet; see below) |

---

## Set-up (about 15 minutes)

1. **Create a public GitHub repository** (for example `daily-ledger`) and push this folder to it. GitHub Pages and unlimited Actions minutes are free only for public repositories. Your settings contain no secrets; secrets live in GitHub.
   ```bash
   cd daily-ledger
   git init && git add . && git commit -m "The Daily Ledger"
   git branch -M main
   git remote add origin https://github.com/<you>/daily-ledger.git
   git push -u origin main
   ```
2. **Enable Pages:** go to the repository's Settings → Pages → Build and deployment → Source: **GitHub Actions**.
3. **Allow the workflow to write:** go to Settings → Actions → General → Workflow permissions → **Read and write**.
4. **Add secrets** under Settings → Secrets and variables → Actions:
   - `GROQ_API_KEY` — **required for written articles**. Free, no card: console.groq.com → API Keys.
   - `GEMINI_API_KEY` — recommended backup. Free with a Google account: aistudio.google.com → Get API key.
   - `CEREBRAS_API_KEY` — optional second backup (cloud.cerebras.ai).
   - `FRED_API_KEY` — free from fred.stlouisfed.org (Brent, Henry Hub, Fed target range).
   - `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` — for the email edition. With Gmail, `SMTP_PASSWORD` is an *app password*, which requires 2-step verification. Then set `email.enabled: true` in `config/settings.yaml`.
   - Variable `SITE_URL` = `https://<you>.github.io/daily-ledger` (used for links in the email).
5. **First run:** go to Actions → *Daily edition* → *Run workflow* with command `build`, then run it again with `publish`. Check `data/status.json` (or the site's status page) for `llm_status: ok` and open the Pages URL. If the drafting model is unavailable, the edition still publishes as attributed headlines, and `redraft` can be run later.

### Activating the 08:00 publication

Nothing else is needed. Once the repository is on GitHub, the three triggers in `.github/workflows/daily.yml` run at 05:20, 07:50 and 08:25 Europe/Luxembourg. They use GitHub's IANA `timezone` field, so DST is automatic. `python -m ledger auto` decides from the clock and the stored state:

- before 08:00 it builds and stages;
- at 07:50 it waits until 08:00 and publishes;
- if everything ran late, it publishes immediately and flags the edition as late;
- a published edition is never rebuilt or re-sent.

GitHub does not guarantee start times; delays of a few minutes are normal. Scheduled workflows are paused after 60 days without repository activity, but the daily data commit counts as activity.

### Running locally (alternative to Actions)

```bash
pip install -r requirements.txt
export GROQ_API_KEY=...   # and/or GEMINI_API_KEY, CEREBRAS_API_KEY; or LLM_ENDPOINT/LLM_MODELS/LLM_API_KEY for Ollama or any OpenAI-compatible server
python -m ledger build && python -m ledger publish          # site in _site/
python -m pytest -q                                          # 29 tests
```

For a local schedule, use cron with `CRON_TZ=Europe/Luxembourg` and `20 5,7 * * * … python -m ledger auto`. A laptop that is asleep at 05:20 will publish late, so Actions is the reliable option.

### Everyday commands

| Command | What it does |
|---|---|
| `python -m ledger auto` | What the scheduler runs |
| `python -m ledger redraft` | Retry AI drafting for today's briefs from stored evidence (no re-fetch; spends quota on the most important stories first) |
| `python -m ledger correct DATE STORY --draft fixed.json --note-en … --note-it …` | Publish a correction. The corrected text must pass the gate; the old version stays visible |
| `python -m ledger withdraw DATE STORY --note-en … --note-it …` | Withdraw a story with a visible notice |
| `python -m ledger sources --check` | Re-test every feed |
| `python -m ledger market` | Print today's market panel |

---

## Costs (verified 2026-10-28)

| Item | Service | Cost |
|---|---|---|
| News retrieval | Public RSS/Atom feeds and official sites | €0 |
| AI drafting | Free tiers of Groq (each model has its own daily token quota: roughly 100k–500k tokens/day per model in 2026), Gemini and Cerebras, chained in that order. GitHub Models, the original plan, was retired by GitHub on 30 July 2026 | €0 (rate-limited; budget capped at 140 calls per run) |
| Market data | ECB, Bundesbank, US Treasury, Bank of England (free, official); FRED (free key) | €0 |
| Hosting | GitHub Pages (public repository) | €0 |
| Scheduler and compute | GitHub Actions, public repository | €0 |
| Database and storage | JSON in the git repository (≈150 KB compressed per day) | €0 |
| Email | Gmail SMTP with an app password (your existing account) | €0 |
| Domain | Optional | ~€10–15/year, not required |

**Mandatory recurring fees: €0**, provided the repository is public. A private repository loses free Pages and has a limited Actions quota.

Future costs, only if you choose them: a paid model for longer and richer articles; licensed index data (FTSE MIB, EURO STOXX, FTSE 100, gold); a custom domain.

---

## Known limitations and mitigations

- **Most publisher feeds expose only headline and summary.** Stories built on them are short and labelled "from headlines and summaries". Official documents are fetched in full where robots.txt allows.
- **Free AI quota:** with Groq alone, roughly 40–80 stories per day can be fully written (each story uses about 6–9k tokens; limits are per model and change without notice). Adding a Gemini key raises this. The rest appear as attributed headline briefs, never as invented text. `redraft` can complete them later the same day.
- **Clustering recall:** the same event reported in different languages, with few shared names, can stay split (for example, Luxembourg's tax reform in RTL Today, the Luxembourg Times and Le Quotidien). The result is a single-source label where corroboration existed, never a fabrication.
- **Equity indices** (FTSE MIB, EURO STOXX 50, STOXX 600, FTSE 100), gold and TTF gas have no free licensed source. They are shown as "unavailable". US index levels via FRED are licensed for display, so they are off by default because the site is public (`show_licensed_index_levels`).
- **Day Ahead** lists only official calendars that can be read (currently ONS) and milestones stated in verified stories. ECB, ISTAT and Fed calendars are not wired in.
- **Several publications in the brief block automated readers or have no feed.** See `docs/SOURCES.md`.
- **The public site** is readable by anyone who finds the URL. Pages has no access control on the free plan.

---

## Files

```
config/            settings.yaml · sources.yaml (209 verified feeds) · editions.yaml (sections, cities, trackers) · lexicon.yaml
ledger/            ingest · classify · cluster · evidence · rank · prompts · llm · writer · validate · market
                   pipeline · store · corrections · mailer · site · __main__ · templates/ · static/
tests/             test_scenarios.py (the 20 required scenarios) · test_integration.py · conftest.py
docs/SOURCES.md    feed verification record, including what was tested and rejected
.github/workflows/ daily.yml (schedule, build, publish, deploy) · tests.yml (CI + weekly feed check)
```
