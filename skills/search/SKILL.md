---
name: search
description: Locate relevant data sources, databases, and articles on the web — before scraping or contacting a provider. Use when you need to find where relevant data lives (databases, articles, patents, video channels, price books, academic papers) before deciding how to pull it — the discovery step, not the extraction step.
---

# Search (search)

## Purpose

Locate *where* relevant data already exists — a database, an article, a video
channel, a patent, a price book — before deciding how to pull it. Search is the
discovery step; the pull itself is `scraping` or a dedicated API call; reaching
out directly when no public source exists is `provider-outreach`.

## When Triggered

- A new question where it is unclear whether a data source already exists in the project.
- Standing directive: "actively hunt for data sources" for every domain being
  worked on (demolition, construction, tiling, pricing, safety...) — not a
  one-off task.
- Discovering a new player/competitor/technology not on the existing watch list.
- Finding a YouTube channel/feed of an entity you want to track over time.

## Steps

1. **Define the exact requirement before searching.** Not "demolition robot" —
   "sub-one-ton demolition robot, passes through a standard door, active market".
   Same principle as in `provider-outreach`: a vague search returns vague results.

2. **Pick the tool by source type, not one tool for everything:**
   - General web search: `WebSearch`/`WebFetch`.
   - Keyless video search: `yt-dlp "ytsearch10:<query>"` — title, channel,
     `channel_id`.
   - Long-term channel tracking: public RSS feed
     `https://www.youtube.com/feeds/videos.xml?channel_id=UC...` — the 15
     most recent videos with dates, no API, no bot blocking. The most reliable
     option for ongoing tracking. `watch/video_watch.py --add "<name>"` resolves
     the `channel_id` via search and stores it.
   - Built-in YouTube Data API: `canonical/google_apis.py yt "<query>"`.
   - Patents: Google Patents by assignee/keyword — kinematics, coupling,
     power, competitor FTO.
   - Academic: arXiv (cs.RO), Semantic Scholar, OpenAlex, Papers With
     Code — for non-commercial research solutions.

3. **Prefer a verified source over a marketing claim.** Public company? A stock
   filing is worth more than its home page. A press article beats "according to
   company sources".

4. **Search cheap before investing in depth.** `token-economy`: a short
   keyword query before fully browsing a page; do not pull entire content just
   to check whether it is relevant.

5. **Every new database/source you come across — record it in the same breath**,
   not at the end of the day. A row in the live data sources registry: what it
   is, how to access it, license status, whether commercial use is allowed,
   status (in use/ready/to check/lead). This is the project's standing directive
   — not a one-off task.

6. **Web search is a centralized permission.** When another agent needs a web
   search, it requests it through Data gathering and does not open its own
   parallel search channel — this keeps provenance uniform and cost measured in
   one place. Document who requested it and why.

## Expected Output

A list of sources/links ranked by relevance and reliability, with the access
type noted for each (API/RSS/scraping/direct request); every new database is
recorded in the source registry in the same round it is found.

## Owner

Data gathering

---
*Registered in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage enforced by R-004.*
