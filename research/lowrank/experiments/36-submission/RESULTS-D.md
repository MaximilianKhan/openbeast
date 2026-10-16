# E36-D results — Qwen3.5-0.8B on b10865 (40 chunks × 512 per corpus)

Raw rows: `results-d.txt`; every paired table: `paired-d.txt` (E35 robust set);
logs: `logs-d/`. Rules: PLAN.md §D and the 18:10 amendment. Bytes: Q2_K
family 436,408,832; adapter 86,921,376 (crown 523,330,208); IQ2_M 392,015,872;
IQ3_XXS 412,082,176; Q3_K_M 480,304,128; CONTROL-A 517,487,616;
Q4_K_M 543,378,944.

| config | wiki KLD | fw KLD | code KLD | wiki PPL (40 ch) |
|---|---|---|---|---|
| Q2_K | 0.4900 | 0.5347 | 0.3926 | 28.46 |
| IQ2_M | 0.5477 | 0.5391 | 0.4881 | 28.35 |
| IQ3_XXS | 0.2577 | 0.2577 | 0.2225 | 20.93 |
| Q3_K_M | 0.1677 | 0.1678 | 0.1209 | 20.68 |
| Q4_K_M | 0.0397 | 0.0380 | 0.0279 | 17.56 |
| Q2_K + RR (wiki) | 0.3268 | 0.4255 | 0.5433 | 24.08 |
| Q2_K + RR (2:1 wiki:code) | 0.3690 | 0.4660 | 0.2591 | 25.28 |
| Q2_K + fc (wiki) | 0.1676 | 0.2250 | 0.2039 | 20.17 |
| crown: alt×1 + fc (wiki) | 0.1474 | 0.1966 | 0.1805 | 19.87 |
| CONTROL-A (pure quant, 517 MB) | 0.0489 | 0.0470 | 0.0338 | 17.58 |

## Verdicts

- **D0 gate: PASS.** RR-wiki − Q2_K = −0.1632 KLD, t = −28.4, 40/40 (paper: −34%; here −33%).
- **T1.6: the control dominates.** CONTROL-A − crown = −0.0986 (t = −26.7) wiki, −0.1495 (−32.0) fw, −0.1467 (−21.0) code; the control is 1.1% smaller. The 27B reading replicates at 0.8B and is far larger: at this size the corrected crown sits well below the stock-type interpolation line.
- **T1.15: calibration-sampling floor.** RR − Q2_K over three disjoint wiki.train thirds: wiki −0.1632/−0.1640/−0.1672 (range 0.004), fw −0.1092/−0.0950/−0.1125 (range 0.018), code +0.1507/+0.1550/+0.1480 (range 0.007). Third A reproduces the D0 capture exactly (determinism check).
- **T1.1: re-rounding transfers only to its calibration domain; fc transfers everywhere.** RR calibrated on wiki or FineWeb harms code (t = +13.6, +8.9); RR on code harms wiki and fw (t = +20.3, +21.1); RR on either code-containing mix improves all three corpora (2:1: t = −21.4, −8.2, −14.6; 1:1:1: −20.6, −10.0, −14.8). The fc correction improves all three corpora under all five calibrations (|t| ≥ 19).
- **T1.13: equalization harms at every strength; no interior optimum; not double-counting.** α = 0.25/0.5/0.75/1 vs bare: +0.022/+0.068/+0.075/+0.198 wiki (all t ≥ +5); same ordering on fw and code. Without an imatrix α = 1 still harms (+0.305, t = +16.5), so the harm does not come from counting importance twice.
- **Brackets.** RR-wiki beats IQ2_M (44 MB smaller) on wiki and fw (t = −25.9, −15.7) but not on code (+4.1); IQ3_XXS (24 MB smaller) beats RR-wiki on all three (t = +11.7, +22.2, +16.2).
- **T1.6 secondary (CONTROL-B, byte-matched at 522,992,640 B, −0.065%; designed after registration).** KLD 0.0432 / 0.0426 / 0.0285; CONTROL-B − crown = −0.1043 (t = −28.4) wiki, −0.1540 (−32.2) fw, −0.1521 (−21.3) code. Same verdict as the registered recipe.
