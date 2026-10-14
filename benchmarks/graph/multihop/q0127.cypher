// q0127 (agent-drafted, dev, multi_hop): In the risk factor listing what could cause
// NVIDIA to misestimate demand, how did the customer-investment factor change between
// the fiscal 2025 and fiscal 2026 10-Ks?
// Hops: NVIDIA -FILED-> FY2025 10-K -DISCLOSES-> the risk factor whose title names a
// failure to estimate customer demand -PERSISTS_AS-> its successors disclosed in the
// FY2026 10-K. Returns each end's EVIDENCED_BY chunks and the link's own evidence.
MATCH (nvda:Company {ticker: 'NVDA'})-[:FILED]->(:Filing {form_type: '10-K', fiscal_period: 'FY2025'})
      -[:DISCLOSES]->(earlier:RiskFactor)
WHERE toLower(earlier.title) CONTAINS 'estimate customer demand'
MATCH (earlier)-[p:PERSISTS_AS]->(later:RiskFactor)
      <-[:DISCLOSES]-(:Filing {form_type: '10-K', fiscal_period: 'FY2026'})<-[:FILED]-(nvda)
RETURN earlier.title AS fy2025_title, later.title AS fy2026_title,
       p.extract_prompts[0] AS link_method,
       [(earlier)-[:EVIDENCED_BY]->(c:Chunk) | c.chunk_id]
         + [(later)-[:EVIDENCED_BY]->(c:Chunk) | c.chunk_id] + p.chunk_ids AS chunk_ids
ORDER BY fy2025_title, fy2026_title
