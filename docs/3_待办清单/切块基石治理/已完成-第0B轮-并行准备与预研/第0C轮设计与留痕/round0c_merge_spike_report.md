# Round 0C Merge Spike Report

- generated_at: `2026-07-15T01:33:32.560455+00:00`
- overall before count/median/lt200: 366 / 164.5 / 59.02%
- overall after count/median/lt200: 276 / 197.0 / 50.36%

## Per document

- **StampServer用户手册_Rocky9 .docx**: lt200 53.04% → 36.00% (count 181 → 125)
- **StampTools用户手册.docx**: lt200 56.18% → 47.37% (count 89 → 57)
- **StampWebRTC用户手册.docx**: lt200 72.92% → 71.28% (count 96 → 94)

## Gold fact-window checks

- `mq-002` best_hits=1/7 all_in_one=False
- `mq-003` best_hits=1/7 all_in_one=False
- `mq-005` best_hits=3/3 all_in_one=True
