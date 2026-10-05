# Question filters come from the question text, never from eval labels

Every eval record carries `tickers` and `fiscal_periods` labels, and the RUNBOOK says the vector arm filters by company and period. If retrieval used those labels, every arm would see gold information, and the period-confusion hypothesis H3 would mean nothing. So one deterministic parser, shared by all arms, reads the filters from the question text. When it is unsure, it applies no filter rather than guessing. Each run reports the parser's accuracy against the labels as a separate number. Decided on 2026-10-04 in the Step 5 grilling.

## How the filter behaves
- It is a hard filter. A fiscal year means that year's 10-K and its 10-Qs. A quarter means that quarter's 10-Q. Several companies or periods mean their union. If the parser finds nothing, there is no filter.
- Only explicit period phrases count. "Fiscal 2025", "FY2025" and "Nth quarter of fiscal 2025" apply to every company. Calendar quarters such as "Q3 2025" apply only to AMD and Intel, whose fiscal years follow the calendar. A bare year sets nothing, because it often dates an event.
- "10-K" or "10-Q" restricts the form. "Latest" or "most recent" next to a form name means that company's newest filing of that form in the corpus.
- On dev, each run reports exact match against the labels and a filter-excluded-gold rate. The second must be 0 before the baseline run.

## Refinements (2026-10-05, Step 5 ticket 04)
Each follows "no filter when unsure". The user approved them after the ticket 04 review.
- A period with no filing in the corpus (fiscal 2021, NVIDIA fiscal 2024) is ignored. Its figures appear as comparatives in the next filing, which the filter still selects when the question names it.
- A fourth quarter, which has no 10-Q, leaves its company with no period filter.
- A list sharing one year or quarter word ("fiscal 2024 and 2025", "Q1 and Q2 of fiscal 2025") sets no period, because reading one item would cut out the others. Lists of complete phrases ("Q3 2024 and Q3 2025") still filter.
- A filter that selects no filing is no filter.
