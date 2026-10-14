// q0120 (agent-drafted, dev, multi_hop): In their latest annual reports (NVIDIA fiscal
// 2026; AMD and Intel fiscal 2025), how does each company name the other two as competitors?
// Hops: filer -COMPETES_WITH- another filer (stored one way, matched both ways); each
// evidence chunk -PART_OF-> the naming filer's own 10-K for the asked year.
UNWIND [{ticker: 'NVDA', period: 'FY2026'},
        {ticker: 'AMD', period: 'FY2025'},
        {ticker: 'INTC', period: 'FY2025'}] AS asked
MATCH (co:Company {ticker: asked.ticker})-[:FILED]->(f:Filing {form_type: '10-K', fiscal_period: asked.period})
MATCH (co)-[r:COMPETES_WITH]-(rival:Company)
UNWIND range(0, size(r.chunk_ids) - 1) AS i
MATCH (:Chunk {chunk_id: r.chunk_ids[i]})-[:PART_OF]->(f)
RETURN co.ticker AS company, f.fiscal_period AS fiscal_period, rival.ticker AS names_as_competitor,
       collect(r.chunk_ids[i]) AS chunk_ids, collect(r.evidence_spans[i]) AS evidence_spans
ORDER BY company, names_as_competitor
