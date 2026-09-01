---
name: research-competitor
description: Research into an entity (competitor/product/company/technology) and adding it to a comparison table, including images. Use for researching a competitor, a product, a startup, or a technology domain, adding an entity from a URL to a comparison table, or an "analyze this and add to the list" request.
---

# מחקר → טבלת-השוואה

A research procedure in a fixed structure: **topic + constraint + table**, and almost always also **real images**.

## מתי מופעל
A request to research a competitor/product/company/domain, add an entity from a link to the comparison table, or "analyze this and add to the list."

## שתי צורות
- **From the link** — one link → one record.
- **Deep-dive round** — broad topic → **split into parallel sub-agents**, one for each sub-topic; do not grind sequentially.

## צעדים
1. **Collection:** Primary sources via web search. **Prefer verified sources over marketing statements** (public reports > home page). Video → subtitles plus frames to see how the mechanism actually works and what the real cycle rate is (versus an inflated marketing rate).
2. **Images (almost always):** Use the existing image-search tool. **Real images only** — never a fabricated illustration or a verbal description instead of an image. Every image is submitted with dimensions in the caption (or "no dimensions").
3. **Record:** Preserve the existing table structure; fill every field. **Add a confidence rating** to every record (verified / marketing statement / unverified).
4. **Closure:** Deduplicate by name **and by company** (do not duplicate); a finding that challenges an assumption — state it explicitly; if an open point is closed, update the map; commit plus report.

## כלל-האמינות
**Always** mark what is verified and what is not. Performance numbers on company websites are marketing — without independent confirmation, write "unverified." Do not hide the gap.

## פלט צפוי
Record(s) in the comparison table, confidence-rated, plus real images; cited sources; updated data-source registry.

## בעלים

Data gathering

---
*Record in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage is enforced by R-004.*
