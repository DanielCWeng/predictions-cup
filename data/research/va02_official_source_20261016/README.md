# VA-02 official-source provenance pilot — one historical election (2026-01-06 Virginia Special)

**Verdict: SOURCE_ACCEPTABLE_FOR_SCHEMA_ONLY.** This is **not** a certified 2026-11-03 VA-02 count feed, an event clock, an official-to-SIG matched episode, or executable alpha. Report 2026-10-16; #204.

## Source, permitted scope, and historical boundaries
Virginia Department of Elections states election data is available in JSON plus a downloadable change log and distinguishes election-night unofficial from subsequently certified results: https://www.elections.virginia.gov/results/ . Historical event displayed under the official Virginia ENR hostname: https://enr.elections.virginia.gov/results/public/virginia/elections/2026-January-6-Special . This sample is an **already-held** Jan 6, 2026 special election (state Senate 15 and House of Delegates 77), not VA-02 U.S. House, and the currently served results indicate `isOfficialResults=true` as of the later January publication. Official state results page states the respective electoral boards certify results.

Official Department privacy policy (https://www.elections.virginia.gov/privacy-policy/) explicitly acknowledges ordinary **browsing/downloading** and logging visits; it does **not** specify a licensed or rate-limited automated election-night ingestion contract, historical first-publication SLA, vendor API authentication, or derivative/commercial rights. **Therefore no recurring feed polling/republication or production use is authorised by this pilot.** We made bounded public archive reads only, no SIG requests.

## Originals preserved losslessly
Five files here are **byte-for-byte originals compressed with deterministic gzip `mtime=0`** and stored as Git binary objects. To reproduce a raw SHA256: `gzip -dc <artifact>.gz | sha256sum`. SHA256 below is for the **uncompressed original response body**, not reserialized JSON, parsed rows, or Git blob SHA. The source server may later replace content at the same URL: **committed Git blobs are the immutable archive**.

| Archived original / Git filename (+.gz) | Original bytes | Original SHA-256 |
|---|---:|---|
| `2026jan6_meta_original.json` | 8,747 | `641bdba6bebd0a8d819dd7d0a36c322007e8c5d6e4d5ea74771a1591cf2200bc` |
| `2026jan6_jurisdiction_original.json` | 44,167 | `5b78f0b2a37f547701d258b7cd3eb8e7ea8de263422981be3632d33596c66b66` |
| `2026jan6_data_original.json` | 89,316 | `d4be6cc7d21f32ab700908ac1ef1c6467b087905d459453c58cda46580b9a1ee` |
| `2026jan6_media_json_original.json` | 115,566 | `c040d3673c272e1eb479f3ab33dc88f9406f1a0914ee559d87c82e3626fa4765` |
| `2026jan6_change_log_original.csv` | 332,545 | `6491df6f9aa568c8ab8b7b0e7597969632de756cb5f33bd574323863f4387e69` |

Original URLs (all Virginia official ENR domain):
- Metadata: https://enr.elections.virginia.gov/results/public/api/elections/virginia/2026-January-6-Special
- Jurisdiction / export locator: https://enr.elections.virginia.gov/results/public/api/jurisdictions/virginia/2026-January-6-Special
- Contests and public candidate/party UUIDs: https://enr.elections.virginia.gov/results/public/api/elections/virginia/2026-January-6-Special/data
- Actual media JSON path **taken from retrieved jurisdiction's `mediaExportPath`**, not guessed: https://enr.elections.virginia.gov/cdn/results/virginia/export-2026-January-6-Special.json
- Change-log `blobName` **taken from the retrieved election's `publicReportCategories`**, not guessed: https://enr.elections.virginia.gov/cdn/results/d2c804ee-4ec2-46bb-91d7-5b41526eab03/Election%20Change%20Log_142aa685-af8c-4634-9be1-ad2ddc57eaa7.csv

**Retrieval clock (research machine, NOT original source/event receipt):** 2026-10-16 17:36:48Z metadata and jurisdiction; approx 17:36:49Z data; JSON 17:37:30.294–17:37:30.935Z; CSV 17:37:30.940–17:37:31.739Z. HTTP Date of those requests is 2026-10-16, proving only our later retrieval.

**Distinguish publisher clocks:** metadata `asOf=2026-01-14T22:31:44.4592723Z`, `lastUpdated=2026-01-12T20:59:25.3414495Z`; final **export build** `createdAt=2026-01-14T22:31:46.7394973Z`; CDN JSON `Last-Modified: Wed, 14 Jan 2026 22:31:46 GMT`, ETag `0x8DE53BCB3C41394`; log CDN `Last-Modified: Wed, 14 Jan 2026 22:31:48 GMT`, ETag `0x8DE53BCB4C7EDAB`. **None are source-first-public-availability for an individual tally revision.** No public-publish-time proof or local 2026-January contemporaneous receipt was recovered.

## Concrete ballot identities — observed, not inferred from names
Archive's current JSON export uses compact `ballotItems[].id` / `ballotOptions[].id` (e.g. Senate-15 `cc5`, Mike Jones `cs12`, John Thomas `cs13`). Separate official public `/data` provides ballot-option UUID, `nativeId`, candidate name, and `party.id`:

- Senate-15 contest UUID `01000000-2327-40a7-ee41-08de3e528c43`: Mike J. Jones D, option `01000000-2327-40a7-ef94-08de3e528c43`, native `cs12`, party `01000000-2327-40a7-e24c-08de3e528c43`; John R. Thomas R, option `01000000-2327-40a7-f051-08de3e528c43`, native `cs13`, party `01000000-2327-40a7-e614-08de3e528c43`; write-in option native `wi-cc5`.
- House-77 contest UUID `01000000-2327-40a7-6a06-08de3e528c46`: Charlie H. Schmidt D, option `01000000-2327-40a7-6a46-08de3e528c46`, native `cs10`, same Democratic party UUID; Richard S. Stonage Jr R, option `01000000-2327-40a7-6a63-08de3e528c46`, native `cs11`, same Republican party UUID; write-in option native `wi-cc4`.
- These are **public ENR publication identifiers**, not proof of election-authority's canonical ballot-file ID for 2026-Nov VA-02 or any SIG settlement identity. Explicit write-in rows mean existing #211 strict exactly-two-choices nowcaster **must abstain** on unadapted historical source. Do not silently discard write-in to force binary model input.

## Actual publisher change-log and correction behavior
CSV has 1,546 data rows, columns `Timestamp, Username, Election, Source, Snapshot, Locality, Precinct, Contest, Choice, VotingMethod, FieldName, PreviousValue, NewValue, ChangeReason, Comments`. Its `Timestamp` values (e.g. `01/09/2026 17:24:38`) have **no offset or timezone field**. They represent recorded report/edit events, **not independently witnessed public HTTP availability**; sequence/snapshot ID and published-at cannot be recovered from current log alone.

- `Canvass Correction` (5 rows): Jan 9 `17:24:38` Richmond City, precinct `402 - FOUR HUNDRED TWO`, House-77 `Post-Election` Charlie Schmidt `Total Vote Count` **54→55** (also ballots cast 58→59). At `17:27:56` locality House-77 post-election Schmidt **295→296**, ballots **324→325**. Reason comment says `needed too add handcount`.
- `File Import` **downward adjustment**: Jan 7 `14:43:04`, Chesterfield, precinct `101 - BELLWOOD`, Senate-15 Mike Jones early vote count **41→17**; also precinct 103 John Thomas 55→51. A decreasing count need not signal a real-world vote reversal; reason is file import. Do not multiply edit rows into independent positive events.
- Change reasons observed: File Import 733, Election Night Initial Entry 358, Manual Entry 280, Canvass Initial Entry 132, Other 38, Canvass Correction 5. Source column labels all 1,546 rows `Manual Entry`, even for `File Import` reasons: this is why a downstream parser must **not** infer source type or external publication time from labels without owner documentation.
- Earliest CSV edit stamp Jan 6 `19:12:06`; latest Jan 12 `15:59:17`. This does not equal the Jan 14 final media-export creation clock, nor our October retrieval.

## Verdict, scope and the single next proof
**SOURCE_ACCEPTABLE_FOR_SCHEMA_ONLY**: authenticated government-hosted archive with actual JSON + immutable change log, machine-readable contest/choice/party identities, and reproducible correction behavior. **SOURCE BLOCKED for point-in-time signal and LIVE**: no original per-revision public publication time, independently timestamped contemporaneous receipt or proved automated reuse permissions; no Nov-3 VA-02 mapping and no matched SIG source/event series. Real official-result→SIG accepted episodes = **0**, measured economic advantage = **NOT ESTIMABLE**, no model built or LIVE operations.

**Exactly one prerequisite to elevate beyond schema study:** acquire a **publisher-confirmed Virginia ENR event-source access-and-clock contract** for the 2026 general election: it must explicitly authorize the proposed bounded automated use and identify which authenticated timestamp/revision field represents the time each public revision first became available (including timezone and corrections). If unavailable, remain SOURCE BLOCKED for causal trading research. The separate SIG VA-02 939/1032 resolution review belongs exclusively to former Alpha 2 and does not change this source verdict.

No new SIG GETs, recurring official polling, services, maker/risk/runtime changes, trading orders or deployment. This branch is inert research evidence only.
