# Question filters come from the question text, never from eval labels

Every eval record carries `tickers` and `fiscal_periods` labels, and the RUNBOOK says the vector arm filters by company and period. If retrieval used those labels, every arm would see gold information, and the period-confusion hypothesis H3 would mean nothing. So one deterministic parser, shared by all arms, reads the filters from the question text. When it is unsure, it applies no filter rather than guessing. Each run reports the parser's accuracy against the labels as a separate number. Decided on 2026-10-04 in the Step 5 grilling.

## How the filter behaves
- It is a hard filter. A fiscal year means that year's 10-K and its 10-Qs. A quarter means that quarter's 10-Q. Several companies or periods mean their union. If the parser finds nothing, there is no filter.
- Only explicit period phrases count. "Fiscal 2025", "FY2025" and "Nth quarter of fiscal 2025" apply to every company. Calendar quarters such as "Q3 2025" apply only to AMD and Intel, whose fiscal years follow the calendar. A bare year sets nothing, because it often dates an event.
- "10-K" or "10-Q" restricts the form. "Latest" or "most recent" next to a form name means that company's newest filing of that form in the corpus.
- On dev, each run reports exact match against the labels and a filter-excluded-gold rate. The second must be 0 before the baseline run.
