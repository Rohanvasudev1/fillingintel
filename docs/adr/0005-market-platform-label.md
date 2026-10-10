# Market platforms get their own label; Segment means reportable segment only

Extraction run 1 made Segment nodes for both of NVIDIA's reportable segments (Compute & Networking, Graphics) and its four market platforms (Data Center, Gaming, Professional Visualization, Automotive). One label for both would make "the Gaming segment" ambiguous and would collide AMD's reportable segments with NVIDIA's platforms by name. Ontology version 3 adds a MarketPlatform label and keeps Segment for the segments a filer's segment note reports. The extract prompt becomes `extract/v2`, and NVDA FY2026 is extracted again under it.

Two further changes ride on the same version. Segment and MarketPlatform keys include the filer's CIK (`{label}:{cik}:{norm(name)}`), because a segment always belongs to one company and AMD and Intel could both report a "Gaming"; a name-only key would merge them before entity resolution runs. Cross-filing extracted nodes gain an `aliases` list holding the names entity resolution merged into them, so question linking in Step 11 can find a node by any name a filing used. MarketPlatform gets `HAS_MARKET_PLATFORM` from Company and may be an endpoint of `MEASURES`, `AFFECTS` and the start of `OFFERS`.

Decided on 2026-10-10 in the Step 9 grilling.

## Considered options
- **Widen Segment to cover market platforms.** No ontology change, but one label holds two kinds of thing, and questions about segments get the wrong nodes.
- **Leave run 1 as it is and document the ambiguity.** Entity resolution would then merge or split segments on a definition the data does not follow.
