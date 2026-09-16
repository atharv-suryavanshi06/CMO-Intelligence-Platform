# RAG Evaluation Results

Ground truth: `D:/CMO Intelligence Platform/evaluation/datasets/cmo_intelligence_ground_truth.json`

Questions evaluated: **30**

## Retrieval configuration

The evaluator measures the production **hybrid retriever** only: semantic embedding retrieval fused with keyword/BM25 retrieval and the configured reranking weights. Separate keyword-only and semantic-only scores are not reported.

Top-K: **5**

## Retriever accuracy

Retriever accuracy is question-level: a question is successful when at least one expected chunk appears in the top-K results.

| Metric | Score |
| --- | ---: |
| Hit@5 (retriever accuracy) | 0.8333 |
| MRR | 0.4800 |
| Average first relevant rank | 1.56 |

## Chunk accuracy

Chunk accuracy compares retrieved chunk IDs with the manually verified expected chunk IDs.

| Metric | Score |
| --- | ---: |
| PRECISION@5 | 0.1667 |
| RECALL@5 | 0.8167 |
| F1@5 | 0.2762 |

## Answer quality

| Metric | Score |
| --- | ---: |
| Faithfulness | 1.0000 |
| Answer Relevancy | 0.9900 |
| Answer Correctness | 0.9467 |

Answer-quality judge coverage: 30/30.
Faithfulness, answer relevancy, and answer correctness are Gemini judge scores from 0 to 1.

## Per-question results

| ID | Question | Hit@5 | First relevant rank | Chunk precision@5 | Chunk recall@5 | Chunk F1@5 | Faithfulness | Answer relevancy | Answer correctness | Total latency (ms) | Status |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | What was the scope of the Bain and Meta Conversational Commerce Survey in India? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 5034.9463 | completed |
| 2 | What was the sample and response rate for The CMO Survey 2025? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 4581.6858 | completed |
| 3 | According to the APAC Privacy Playbook, how can businesses unlock more first-party data? | 1.0000 | 1 | 0.2000 | 0.5000 | 0.2857 | 1.0000 | 1.0000 | 1.0000 | 2671.3896 | completed |
| 4 | What must technology enable in an effective Marketing Operating Model (MOM)? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 5184.7754 | completed |
| 5 | Why is accurate media measurement critical for CMOs according to the media effectiveness guide? | 1.0000 | 3 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 2369.4124 | completed |
| 6 | How does McKinsey define modern marketing? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 6846.2909 | completed |
| 7 | What did the Q1 2025 Programmatic Transparency Benchmark find about effective impressions and the optimization gap? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 5263.4662 | completed |
| 8 | What operating model does the State of Marketing Europe report recommend for marketing ROI (MROI)? | 0.0000 | N/A | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 5194.2122 | completed |
| 9 | How has AI use in marketing changed from 2024 to 2026, and what is projected for the next three years? | 1.0000 | 2 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 3453.7538 | completed |
| 10 | What was the scope of the 2022 cross-media research on the short- and long-term impact of advertising? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 4229.4409 | completed |
| 11 | What does IAB State of Data 2025 say about AI scaling across media campaigns? | 0.0000 | N/A | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.9000 | 0.8000 | 5960.1929 | completed |
| 12 | What ad-spend growth does the IAB 2025 Outlook Study project, and which channels lead digital growth? | 1.0000 | 5 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 5423.0438 | completed |
| 13 | What is conversational commerce and which businesses are expected to drive much of it in India? | 1.0000 | 2 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 2001.0617 | completed |
| 14 | Why will service commerce accelerate user adoption of conversational commerce? | 1.0000 | 2 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 3830.3346 | completed |
| 15 | When was The CMO Survey 2025 fielded? | 1.0000 | 5 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 3927.8234 | completed |
| 16 | What types of companies and seniority levels were represented in The CMO Survey 2025? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 3254.1591 | completed |
| 17 | How does the APAC Privacy Playbook say first-party data can improve conversion measurement? | 0.0000 | N/A | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.9000 | 0.8000 | 6638.0699 | completed |
| 18 | What privacy change is creating measurement challenges for advertisers according to the APAC Privacy Playbook? | 0.0000 | N/A | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 3267.2566 | completed |
| 19 | What financial impact did the Marketing Operating Model report describe for faster delivery of IT-dependent marketing functions? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 4407.5575 | completed |
| 20 | Why are privacy-preserving measurement solutions increasingly important for CMOs? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 3338.6857 | completed |
| 21 | What growth and cost outcomes does McKinsey estimate modern marketing can unlock? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 2875.8915 | completed |
| 22 | How does modern marketing change the way a marketing department operates? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 7552.3215 | completed |
| 23 | What makes an ad impression effective in the Q1 2025 Programmatic Transparency Benchmark? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 2390.6922 | completed |
| 24 | What improvement drove the increase in effective programmatic impressions from 2023 to Q1 2025? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 3441.4587 | completed |
| 25 | In the recommended MROI operating model, what do owners, doers, and users do? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 2850.8620 | completed |
| 26 | How is economic pessimism reshaping marketing priorities in The CMO Survey 2026? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 6505.3364 | completed |
| 27 | Which marketing AI applications had the strongest adoption in The CMO Survey 2026? | 1.0000 | 2 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 3523.1249 | completed |
| 28 | Which organizations conducted the 2022 cross-media advertising research and what did it measure? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 1.0000 | 1.0000 | 3048.7760 | completed |
| 29 | Who leads AI adoption in the IAB State of Data 2025 and what benefits do they report? | 0.0000 | N/A | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 5156.3322 | completed |
| 30 | What are buyers prioritizing in the IAB 2025 Outlook amid fragmentation and measurement challenges? | 1.0000 | 1 | 0.2000 | 1.0000 | 0.3333 | 1.0000 | 0.9000 | 0.8000 | 2188.0744 | completed |
