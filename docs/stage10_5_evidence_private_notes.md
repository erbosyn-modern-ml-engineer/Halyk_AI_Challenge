# Stage 10.5 private-set evidence invariants

Evidence selection must remain generalizable on unseen data:

- no scenario IDs, transaction IDs, public thresholds or public answer values in scored logic;
- authoritative treatment replay has priority over plain transaction absence;
- plain absence candidates come only from transactions that contributed to the resolved Stage 6 result;
- zero or multiple verdict-flipping candidates publish `null`;
- a candidate is never selected by amount magnitude, chronological position, cumulative threshold crossing or lexical similarity;
- LLM output may propose upstream semantic candidates but cannot bypass counterfactual verification.
