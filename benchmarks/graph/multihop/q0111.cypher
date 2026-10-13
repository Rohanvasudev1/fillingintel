// q0111 (agent-drafted, dev, multi_hop): How do NVIDIA (fiscal 2026 10-K), AMD
// (fiscal 2025 10-K) and Intel (fiscal 2025 10-K) each describe their reliance on TSMC?
// Hops: TSMC (found by name or alias) -SUPPLIES-> filer; each evidence chunk -PART_OF->
// the 10-K the question names for that filer. Evidence from other filings is left out.
UNWIND [{ticker: 'NVDA', period: 'FY2026'},
        {ticker: 'AMD', period: 'FY2025'},
        {ticker: 'INTC', period: 'FY2025'}] AS asked
MATCH (co:Company {ticker: asked.ticker})-[:FILED]->(f:Filing {form_type: '10-K', fiscal_period: asked.period})
MATCH (tsmc:Organization)-[r:SUPPLIES]->(co)
WHERE 'TSMC' IN [tsmc.name] + coalesce(tsmc.aliases, [])
UNWIND range(0, size(r.chunk_ids) - 1) AS i
MATCH (:Chunk {chunk_id: r.chunk_ids[i]})-[:PART_OF]->(f)
RETURN co.ticker AS company, f.fiscal_period AS fiscal_period, tsmc.name AS supplier,
       collect(r.chunk_ids[i]) AS chunk_ids, collect(r.evidence_spans[i]) AS evidence_spans
ORDER BY company
