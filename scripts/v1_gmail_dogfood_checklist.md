# v1 Schema Dogfood — Triage Checklist

Run `scripts/v1_gmail_dogfood.py` after Gmail OAuth, then read the generated
`dogfood_report.md` against this checklist. Sections map 1:1 with the report.
Each item is a question to ask, with red flags and a clear stop-vs-defer call.

---

## 1. Counts & timing

- [ ] `docs synced > 0` and matches roughly what you'd expect for the window
- [ ] `chunks stored ≥ docs synced` (every doc produces ≥1 chunk)
- [ ] `ingest ms / doc` is in the **single-digit** range
  - 🚩 `> 50 ms/doc` → chunker is doing too much, or sqlite is fsyncing per row
- [ ] `chunks/doc max < 50` for a 500-doc sample
  - 🚩 If one doc produced hundreds of chunks, that doc is a marketing email or
    auto-generated digest; spot-check it

**Stop-vs-defer:** these are infrastructure smoke tests. If any are red,
investigate before going further.

---

## 2. v1 field coverage

These are the fields the new schema introduced. Coverage tells you whether the
connector + pipeline actually populated them on real data.

- [ ] `source_id non-empty == 100.0%` — anything less means Gmail is producing
  rows without source_id and the UNIQUE constraint isn't protecting them
- [ ] `thread_id namespaced (gmail:*) ≈ 100%` — anything below 95% means
  pipeline namespacing didn't fire on some path; investigate
- [ ] `content_hash non-empty == 100.0%` — pipeline always populates this; <100%
  is a real bug
- [ ] `participants_raw populated > 95%` — the missing 5% are mailing-list /
  no-From-header weirdness; tolerable
- [ ] `channel populated ∈ [80%, 95%]` — archived mail with no system label is
  legitimately `channel=NULL`; outside this band is suspicious
- [ ] `last_synced > 0 == 100.0%` — pipeline always sets this

**Stop-vs-defer:** *anything red here is a stop.* The whole PR was about making
these fields work, so failing to populate them means the schema isn't doing
what we agreed.

---

## 3. Chunking quality

This is the section most likely to surface real-data failure modes that the
unit tests can't catch.

### HTML markers in chunk

- `< 5%` — `text/plain` extraction is working. Move on.
- `5–20%` (yellow) — some marketing email is leaking HTML. The chunker is
  embedding `<div>...</div>` as content. **Defer to retrieval PR**, but flag
  in that PR's scope so vector search isn't trained on raw markup.
- `> 20%` (red) — chunker is regularly ingesting HTML. **Fix before retrieval
  PR.** Either (a) Gmail connector strips HTML before yielding `Document`, or
  (b) chunker detects HTML and skips/strips. Decide which layer owns it.

### Quote markers in chunk

- `< 10%` — replies aren't drowning in quoted prior messages.
- `10–30%` (yellow) — typical for any Gmail corpus with active conversations.
  Tolerable for v1.
- `> 30%` (red) — a quote-strip pass is needed before vector search. The same
  paragraph appearing in 5 reply messages will dominate cosine retrieval and
  drown out the actual new content in each reply.

### Length distribution

- median chunk: `200–800` chars — healthy
- p99 chunk: `> 4000` chars — chunker isn't splitting aggressively enough; the
  `max_tokens` cap may need lowering, or your data has multi-page docs that
  the splitter can't break (e.g. forwarded PDFs with no paragraph breaks)
- many chunks `< 50` chars — signature/footer fragments. Consider a min-chunk
  filter; too many tiny chunks bloat the FTS index for no recall benefit.

### top 5 longest / shortest chunks

Eyeball them. You're looking for:

- Longest: is it actually meaningful content, or a 5KB unsubscribe footer?
- Shortest: are they "Sounds good", "Thanks!", and `--`-style sigs? That's
  fine. If they're empty strings or single characters, the chunker has a bug.

**Stop-vs-defer:**
- Length distribution wildly off → **stop**, the chunker is broken on real data
- HTML/quote bloat → **defer to retrieval PR**, but document the % so you know
  what to fix before turning vectors on

---

## 4. Dedup signal

- duplicate ratio (`total rows / unique content_hash`):
  - `1.00–1.05` → essentially no dupes (expected for a fresh dogfood with no
    re-sync)
  - `1.05–1.20` → mild repetition, mostly signatures and short replies
  - `> 1.2` → significant repetition; likely many copies of legal disclaimers,
    "Sent from my iPhone", reply chains where the same paragraph re-appears

- top 5 most-repeated content_hashes — eyeball them
  - 🟢 If they're "Sent from my iPhone", "Confidentiality notice…",
    "—\nName\nTitle\nCompany" → those are boilerplate. Filter candidate but
    not a correctness bug.
  - 🚩 If a real paragraph (a multi-sentence chunk of actual content) appears
    `> 5×`, you have a quote-bloat problem. Fix at chunking time, not query
    time — embedding the same content many times is wasteful and biases
    retrieval.

**Stop-vs-defer:** dedup is informative, not a stop signal. The schema captures
the hash; whether and how to dedupe at query time is a retrieval-PR decision.

---

## 5. Channel distribution

Sanity check: do the channel buckets match how you actually use Gmail?

- [ ] `INBOX` is the largest bucket (most personal Gmail accounts)
- [ ] `SENT` is `15–30%`
- [ ] `(none)` is `5–25%` — archived mail with no system label
- 🚩 If `(none) > 50%` → either we're not extracting `labelIds` correctly, or
  this account uses non-standard labels (e.g. "Skip Inbox" filters that strip
  INBOX). Spot-check by looking at one of the no-channel rows in raw DB.

**Stop-vs-defer:** anomalies here are usually account-specific, not bugs.
Defer unless the distribution is obviously broken.

---

## 6. People leaderboard

The point: do the top 20 normalized addresses look like the people you
actually email? This is the litmus test for participant normalization.

- [ ] Top entries are people, not `noreply@*` / `donotreply@*` / `*@github.com`
  notification senders
- [ ] Names look right — `jon.saad-falcon@stanford.edu`, not
  `Jon Saad-Falcon <jon.saad-falcon@stanford.edu>` (raw shouldn't leak through)
- [ ] No obvious dupes from alias drift (e.g. `jon@stanford.edu` and
  `jon.saad-falcon@stanford.edu` listed separately and *both in your top 20*)
  - 🟢 If they're separate but both real addresses (work + personal) → fine,
    that's accurate
  - 🚩 If the same person shows up twice with addresses you know are the same
    → this is the alias-map use case we deferred; not a bug, just confirmation
    that the alias map will be useful

🚩 If `noreply@*` is the top sender, mail-list noise dominates your corpus.
Worth filtering automated senders before the next ingest, but that's a
**retrieval-PR scope** decision, not a schema bug.

---

## 7. Canned retrieval queries

For each of q1–q6:

- [ ] **q1** (lexical, no filter): top result's content visibly contains the
  query terms
- [ ] **q2** (source filter): every result has `source=gmail` (always true here
  since this is a Gmail-only corpus, but verifies the filter is wired)
- [ ] **q3** (time range): no result is older than the cutoff
- [ ] **q4** (longest thread): every chunk has the same `gmail:`-prefixed
  `thread_id` — this is the schema invariant we just shipped
- [ ] **q5** (`channel=SENT` raw SQL): every result is something you sent
- [ ] **q6** (dedup verification): both rows share the same `content_hash`,
  and looking at their `source_id` confirms they're different messages with
  identical content (signatures or quoted reply bodies)

🚩 If any of these return weird/wrong results, the schema isn't doing what we
think — **stop and investigate**.

---

## When to stop and fix vs. keep going

| signal | action |
|---|---|
| v1 field not populated as expected | **stop**, fix Gmail/pipeline |
| canned queries return wrong results | **stop**, schema isn't doing its job |
| chunker max length out of bounds | **stop**, chunker is broken on real data |
| HTML > 20% of chunks | **defer to retrieval PR**, but track |
| HTML 5–20% / quote markers > 30% | **defer to retrieval PR** |
| dedup repetition is mostly boilerplate | **defer**, decide at retrieval time |
| people leaderboard has alias dupes | **defer**, validates alias-map need |
| `noreply@*` dominates top contacts | **defer**, sender filter is retrieval-PR scope |

**The headline question this dogfood answers:** does the schema capture enough,
correctly, on real data, to support C1/C2/E2 retrieval *once the retrieval
layer is wired up*? If yes, the next PR builds on solid ground. If no, fix
ingestion first.
