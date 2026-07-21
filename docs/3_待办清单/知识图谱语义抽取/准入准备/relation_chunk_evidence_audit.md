# Relation Chunk Evidence Audit

- generated_at: `2026-07-21T07:24:50.543140+00:00`
- db_path: `E:\申浩霖实习文件夹\rag_cy\rag\data\rag_relational.db`
- approved relations: **2737**
- missing source_chunk_id: **50**
- backbone seed missing: **40**
- non-backbone missing: **10**

## Policy (3A)

- backbone: seed:product_backbone may use document/seed-level evidence without source_chunk_id; default backbone_seed_policy (management boundary only, not answer-fusion candidate until allowlist confirms).
- non-backbone: Approved relations without source_chunk_id must be backfilled or excluded from retrieval candidates.

## By created_by

| created_by | count |
|---|---:|
| seed:product_backbone | 40 |
| rule:profile_sync | 8 |
| rule:special_relations | 2 |

## By relation_type

| relation_type | count |
|---|---:|
| belongs_to | 39 |
| different_from | 5 |
| requires | 4 |
| has_field | 1 |
| has_table | 1 |

