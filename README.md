# Nexus Alpha (MVP Blueprint)

## 1) MVP Roadmap (Python-first)

### Phase 0 — Scope, rules, and taxonomy (Week 1)
1. Define **in-scope opportunity classes**:
   - Incentivized testnets/mainnets
   - Retroactive airdrops
   - Staking/restaking points programs
   - Ambassador + node-operator programs
2. Define **hard reject classes** for ingestion/filtering:
   - Trading signals
   - Price predictions
   - Technical analysis
   - Referral spam / engagement bait
3. Create a canonical action schema:
   - `project_name`, `action_type`, `required_action`, `estimated_cost_usd`, `deadline_at`, `chain`, `source_url`
4. Establish confidence labels (`high`, `medium`, `low`) and explainability fields (`why_included`, `why_rejected`).

### Phase 1 — Ingestion engine (Week 2)
1. Build source connectors in Python:
   - X/Twitter API client
   - RSS collector for Substack + Mirror.xyz
   - Discord announcements fetcher (bot + whitelisted channels)
   - On-chain metrics puller (DefiLlama/Dune)
2. Normalize all raw payloads into a common `raw_events` envelope:
   - `source`, `author`, `published_at`, `raw_text`, `raw_json`, `url`, `ingested_at`
3. Add deduplication:
   - URL hash + semantic near-duplicate detection (MinHash/embedding cosine threshold)
4. Schedule jobs with APScheduler/Celery + Redis for retry and queueing.

### Phase 2 — AI filtering + extraction layer (Week 3)
1. Implement Gemini 1.5 Flash classification pipeline with two-pass logic:
   - Pass A: **Noise gate** (include/exclude)
   - Pass B: **Structured extraction** into strict JSON
2. Enforce deterministic output:
   - JSON schema validation (Pydantic)
   - Auto-retry with error-aware reprompt when invalid
3. Add adversarial defenses:
   - Blocklist patterns (“100x”, “gem”, “next moon”, etc.)
   - Account quality heuristics (fresh account, low credibility, repetitive hype)
4. Persist both accepted + rejected decisions for auditability.

### Phase 3 — Venture scoring engine (Week 4)
1. Build investor tier registry:
   - Tier-1: Paradigm, a16z crypto, Dragonfly, Polychain, etc.
   - Tier-2 / Tier-3 with configurable weights
2. Scoring formula (example):
   - `opportunity_score = investor_weight + action_feasibility + cost_efficiency + deadline_urgency + source_reliability`
3. Add interpretable score breakdown per card.
4. Backtest against known historical airdrop cohorts to tune weights.

### Phase 4 — Delivery layer (Week 5)
1. Implement Telegram bot (python-telegram-bot/aiogram):
   - `/feed` daily alpha cards
   - `/project <name>` detail view
   - `/done <task_id>` mark participation state
2. Alpha Card template:
   - Project
   - Required action
   - Cost of entry
   - VC backing
   - Deadline
   - Logic-to-Profit ratio
   - Confidence + source links
3. User-specific filtering:
   - Capital range, chains, risk profile, time availability.

### Phase 5 — Reliability + operations (Week 6)
1. Monitoring + alerting:
   - ingestion lag
   - filter rejection rate
   - schema-validation failure rate
2. Build admin review console for false positive/negative labeling.
3. Continuous prompt + heuristic tuning loop with weekly evaluation set.
4. Deploy with Docker Compose/Kubernetes (API + worker + DB + Redis).

---

## 2) Data Schema (PostgreSQL + optional NoSQL)

## 2.1 PostgreSQL (system of record)

### `projects`
- `id` (uuid, pk)
- `name` (text, unique)
- `category` (enum: testnet, airdrop, restaking, ambassador, node)
- `website_url` (text)
- `primary_chain` (text)
- `status` (enum: active, watchlist, ended)
- `created_at`, `updated_at`

### `opportunities`
- `id` (uuid, pk)
- `project_id` (fk -> projects.id)
- `title` (text)
- `required_action` (text)
- `action_type` (enum: swap, bridge, stake, run_node, social, form, other)
- `cost_of_entry_usd` (numeric)
- `reward_type` (enum: points, token, nft, allocation, unknown)
- `deadline_at` (timestamptz, nullable)
- `logic_to_profit_ratio` (numeric)
- `confidence` (enum: high, medium, low)
- `source_reliability_score` (numeric)
- `is_active` (bool)
- `created_at`, `updated_at`

### `project_funding_rounds`
- `id` (uuid, pk)
- `project_id` (fk)
- `round_type` (enum: pre_seed, seed, strategic, series_a, series_b, token_round, grant)
- `amount_usd` (numeric)
- `raised_at` (date)
- `valuation_usd` (numeric, nullable)
- `source_url` (text)
- `created_at`

### `investors`
- `id` (uuid, pk)
- `name` (text, unique)
- `tier` (smallint)  -- 1 is best
- `weight` (numeric)
- `is_active` (bool)

### `project_investors`
- `project_id` (fk)
- `investor_id` (fk)
- `lead_investor` (bool)
- `notes` (text)
- `PRIMARY KEY(project_id, investor_id)`

### `sources`
- `id` (uuid, pk)
- `platform` (enum: twitter, substack, mirror, discord, defillama, dune, web)
- `source_name` (text)
- `source_url` (text)
- `credibility_score` (numeric)
- `is_whitelisted` (bool)

### `raw_events`
- `id` (uuid, pk)
- `source_id` (fk)
- `external_id` (text)
- `published_at` (timestamptz)
- `author` (text)
- `url` (text)
- `raw_text` (text)
- `raw_json` (jsonb)
- `content_hash` (text)
- `ingested_at` (timestamptz)
- Indexes: (`content_hash`), (`published_at` desc)

### `filter_decisions`
- `id` (uuid, pk)
- `raw_event_id` (fk)
- `decision` (enum: include, reject)
- `decision_reason` (text)
- `model_name` (text)
- `model_confidence` (numeric)
- `prompt_version` (text)
- `created_at`

### `extracted_actions`
- `id` (uuid, pk)
- `raw_event_id` (fk)
- `project_name` (text)
- `required_action` (text)
- `cost_of_entry_usd` (numeric)
- `vc_backing_summary` (text)
- `deadline_at` (timestamptz, nullable)
- `structured_payload` (jsonb)
- `validation_status` (enum: valid, invalid, corrected)
- `created_at`

### `user_profiles`
- `id` (uuid, pk)
- `telegram_user_id` (text, unique)
- `username` (text)
- `capital_band` (enum: low, medium, high)
- `risk_profile` (enum: conservative, balanced, aggressive)
- `preferred_chains` (text[])
- `created_at`, `updated_at`

### `user_participation`
- `id` (uuid, pk)
- `user_id` (fk)
- `opportunity_id` (fk)
- `status` (enum: discovered, planned, in_progress, completed, skipped)
- `wallet_address` (text, nullable)
- `tx_hash` (text, nullable)
- `notes` (text)
- `last_action_at` (timestamptz)
- `UNIQUE(user_id, opportunity_id)`

### `opportunity_deadline_reminders`
- `id` (uuid, pk)
- `opportunity_id` (fk)
- `remind_at` (timestamptz)
- `channel` (enum: telegram)
- `sent_at` (timestamptz, nullable)

## 2.2 Optional NoSQL (fast retrieval + analytics)
Use MongoDB/Elasticsearch for:
- Full-text search over noisy source content
- Semantic similarity lookup
- Rapid feed rendering with denormalized “Alpha Card” documents

Suggested document collections:
- `alpha_cards_cache`
- `source_content_embeddings`
- `trend_clusters`

---

## 3) Gemini Prompt Engineering (99% noise-reduction target)

### 3.1 System instruction (hardened)

```text
You are the Nexus Alpha Filtering Engine.

Mission:
Extract only action-based Web3 opportunities that can produce deterministic or quasi-deterministic rewards through protocol participation.

Strictly INCLUDE content only when at least one concrete required action is present (e.g., bridge, swap, stake, run node, complete quest, provide liquidity, register for testnet).

Strictly EXCLUDE any content that primarily discusses:
- price predictions
- market sentiment
- technical analysis
- leverage/futures signals
- meme shilling / hype language
- generic news without an actionable participation path

Output format:
Return valid JSON only with this schema:
{
  "decision": "include" | "reject",
  "project_name": string | null,
  "required_action": string | null,
  "cost_of_entry": {
    "amount_usd": number | null,
    "confidence": "high" | "medium" | "low"
  },
  "vc_backing": [string],
  "deadline": string (ISO8601) | null,
  "evidence": [string],
  "reason": string,
  "noise_flags": [string]
}

Decision policy:
- If required_action is missing or vague -> reject.
- If content has both hype and real action, include only if action steps are explicit and verifiable.
- Never infer VC backing without textual evidence.
- Never output markdown.
```

### 3.2 Two-stage filtering pattern
1. **Stage A: Binary gate**
   - Prompt optimized for precision-first reject/include.
2. **Stage B: Structured extractor**
   - Run only for Stage A includes.
3. **Stage C: Rule validator (non-LLM)**
   - Reject if mandatory fields empty.
   - Reject if banned-keyword ratio exceeds threshold.

### 3.3 Noise suppression tactics
- Maintain blocklist lexicon:
  - “100x”, “moon”, “gem”, “bullish breakout”, “entry/exit”, “resistance/support”, “signal”, “pump”, “target price”.
- Apply source trust weighting:
  - Penalize low-trust accounts and repeated engagement bait.
- Use few-shot contrastive examples:
  - 10 reject examples (TA/shill)
  - 10 include examples (real task + cost + timeline)
- Add confidence thresholding:
  - Auto-reject if model confidence < 0.70.

### 3.4 Evaluation loop for 99% noise reduction
1. Build a gold dataset (at least 1,000 labeled posts).
2. Metrics:
   - Noise rejection precision (target >= 0.99)
   - Opportunity recall (target >= 0.90)
   - JSON validity rate (target >= 0.995)
3. Weekly drift checks:
   - New slang/shill term discovery
   - Source manipulation detection
4. Human-in-the-loop review:
   - Sample false negatives + false positives
   - Patch prompts + rules, version prompts (`v1.0`, `v1.1`, etc.)

---

## 4) Suggested Python Stack
- **Ingestion:** `httpx`, `feedparser`, `tweepy`, `discord.py`
- **Pipelines:** `pydantic`, `tenacity`, `celery`, `redis`
- **Storage:** `sqlalchemy` + `alembic`, PostgreSQL, optional MongoDB/Elasticsearch
- **LLM integration:** Gemini API SDK + strict JSON schema validation
- **Delivery:** `aiogram` or `python-telegram-bot`
- **Ops:** Docker, Prometheus/Grafana, Sentry
