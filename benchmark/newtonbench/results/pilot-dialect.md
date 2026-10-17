# Amendment 6 PILOT: Compass with the transcendental dialect (descriptive only)

PILOT. Descriptive only: no hypothesis test, not pooled with the registered or the post-hoc analysis. N = 1 trial per task; the reference columns are N = 2 per task. Correctness is NewtonBench's own judgement. A certification is counted apart from correctness: it says only that the law agrees with the receipts within tolerance.

Runs: `/Users/norriswu/Documents/kongtou项目/RDS/newtonbench-eval/runs/pilot-dialect` (RDS commit 457db8cb69b7f08be1680b30ebf02553169310f2). Tokens counted against the cap: 17,960,385 of 54,937,698 (pilot 17,889,121, smoke 71,264).

| Stratum | Subset | Tasks | Run | Not run | Compass correct | Certified | Certified correct | False certified | Fallback correct | Registered Compass | Corrected Compass | code_assisted | vanilla | Corrected false certified | Tokens counted |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pooled | newly_expressible | 38 | 38 | 0 | 25/38 | 30 | 24 | 6 | 28/38 | 0/76 | 0/76 | 73/76 | 70/76 | 51/51 | 10,319,076 |
| pooled | still_not_expressible | 6 | 6 | 0 | 0/6 | 1 | 0 | 1 | 5/6 | 0/12 | 0/12 | 6/12 | 12/12 | 0/0 | 6,192,584 |
| pooled | regression | 8 | 8 | 0 | 7/8 | 7 | 6 | 1 | 7/8 | 6/16 | 14/16 | 16/16 | 16/16 | 0/14 | 1,377,461 |
| pooled | all | 52 | 52 | 0 | 32/52 | 38 | 30 | 8 | 40/52 | 6/104 | 14/104 | 95/104 | 98/104 | 51/65 | 17,889,121 |
| noise0 | newly_expressible | 19 | 19 | 0 | 14/19 | 16 | 14 | 2 | 17/19 | 0/38 | 0/38 | 38/38 | 36/38 | 18/18 | 4,873,312 |
| noise0 | still_not_expressible | 3 | 3 | 0 | 0/3 | 0 | 0 | 0 | 3/3 | 0/6 | 0/6 | 4/6 | 6/6 | 0/0 | 3,480,095 |
| noise0 | regression | 4 | 4 | 0 | 4/4 | 3 | 3 | 0 | 4/4 | 4/8 | 8/8 | 8/8 | 8/8 | 0/8 | 296,410 |
| noise0 | all | 26 | 26 | 0 | 18/26 | 19 | 17 | 2 | 24/26 | 4/52 | 8/52 | 50/52 | 50/52 | 18/26 | 8,649,817 |
| noise0.01 | newly_expressible | 19 | 19 | 0 | 11/19 | 14 | 10 | 4 | 11/19 | 0/38 | 0/38 | 35/38 | 34/38 | 33/33 | 5,445,764 |
| noise0.01 | still_not_expressible | 3 | 3 | 0 | 0/3 | 1 | 0 | 1 | 2/3 | 0/6 | 0/6 | 2/6 | 6/6 | 0/0 | 2,712,489 |
| noise0.01 | regression | 4 | 4 | 0 | 3/4 | 4 | 3 | 1 | 3/4 | 2/8 | 6/8 | 8/8 | 8/8 | 0/6 | 1,081,051 |
| noise0.01 | all | 26 | 26 | 0 | 14/26 | 19 | 13 | 6 | 16/26 | 2/52 | 6/52 | 45/52 | 48/52 | 33/39 | 9,239,304 |

## Per trial

| Task | Subset | Status | Path | Origin | Certified | Correct | False cert. | RMSLE | Fallback correct | Queries | Wall s | Tokens counted | Unreported calls | Agent cap status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m0_gravity/hard/v1 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 6.09e-18 | 1 | 40 | 3076 | 68001 | 0 | AVAILABLE |
| m0_gravity/hard/v1/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 2.68e-05 | 1 | 40 | 3130 | 90075 | 0 | AVAILABLE |
| m10_be_distribution/easy/v1 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 0 | 1 | 0 | 0 | 40 | 6900 | 233857 | 0 | AVAILABLE |
| m10_be_distribution/easy/v1/noise0.01 | newly_expressible | COMPLETED | LIVE_UNCERTIFIED | JUMP_AGENT | 0 | 0 | 0 | 0.00125 | 0 | 40 | 5203 | 391518 | 0 | AVAILABLE |
| m10_be_distribution/hard/v0 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 4.18e-17 | 1 | 40 | 261 | 107805 | 0 | AVAILABLE |
| m10_be_distribution/hard/v0/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 0 | 1 | 0.351 | 0 | 40 | 2997 | 135767 | 0 | AVAILABLE |
| m10_be_distribution/medium/v2 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 0 | 1 | 0 | 0 | 40 | 4434 | 872522 | 0 | AVAILABLE |
| m10_be_distribution/medium/v2/noise0.01 | newly_expressible | COMPLETED | LIVE_UNCERTIFIED | JUMP_AGENT | 0 | 0 | 0 | 0.00347 | 0 | 40 | 5446 | 854709 | 0 | AVAILABLE |
| m11_heat_transfer/hard/v1 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 2.66e-16 | 1 | 40 | 3250 | 98077 | 0 | AVAILABLE |
| m11_heat_transfer/hard/v1/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 0 | 1 | 0.00647 | 0 | 40 | 6102 | 454350 | 0 | AVAILABLE |
| m1_coulomb_force/hard/v0 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 2.88e-16 | 1 | 40 | 3474 | 119805 | 0 | AVAILABLE |
| m1_coulomb_force/hard/v0/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 0 | 1 | 0.0139 | 0 | 40 | 5997 | 116634 | 0 | AVAILABLE |
| m2_magnetic_force/hard/v0 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 3004 | 67783 | 0 | AVAILABLE |
| m2_magnetic_force/hard/v0/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0.00374 | 1 | 40 | 5839 | 89303 | 0 | AVAILABLE |
| m2_magnetic_force/medium/v2 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 3387 | 80413 | 0 | AVAILABLE |
| m2_magnetic_force/medium/v2/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0.00293 | 1 | 40 | 3170 | 126863 | 0 | AVAILABLE |
| m3_fourier_law/hard/v1 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 1.16e-15 | 1 | 40 | 3321 | 114996 | 0 | AVAILABLE |
| m3_fourier_law/hard/v1/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0.00738 | 1 | 40 | 3467 | 156356 | 0 | AVAILABLE |
| m3_fourier_law/medium/v1 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 1.21e-15 | 1 | 40 | 3443 | 73189 | 0 | AVAILABLE |
| m3_fourier_law/medium/v1/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 1.21e-15 | 1 | 40 | 3493 | 88198 | 0 | AVAILABLE |
| m5_radioactive_decay/easy/v2 | newly_expressible | COMPLETED | NO_LIVE_RULEBOOK | - | 0 | 0 | 0 | - | 1 | 13 | 1560 | 666556 | 0 | AVAILABLE |
| m5_radioactive_decay/easy/v2/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 0 | 1 | 0.0118 | 0 | 40 | 4960 | 420286 | 0 | AVAILABLE |
| m5_radioactive_decay/hard/v2 | newly_expressible | COMPLETED | NO_LIVE_RULEBOOK | - | 0 | 0 | 0 | - | 1 | 40 | 5948 | 648720 | 0 | AVAILABLE |
| m5_radioactive_decay/hard/v2/noise0.01 | newly_expressible | COMPLETED | NO_LIVE_RULEBOOK | - | 0 | 0 | 0 | - | 0 | 17 | 2451 | 921135 | 0 | AVAILABLE |
| m5_radioactive_decay/medium/v2 | newly_expressible | COMPLETED | NO_LIVE_RULEBOOK | - | 0 | 0 | 0 | - | 1 | 13 | 1594 | 840558 | 0 | AVAILABLE |
| m5_radioactive_decay/medium/v2/noise0.01 | newly_expressible | COMPLETED | NO_LIVE_RULEBOOK | - | 0 | 0 | 0 | - | 0 | 13 | 2327 | 867927 | 0 | AVAILABLE |
| m6_underdamped_harmonic/medium/v0 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 3154 | 82436 | 0 | AVAILABLE |
| m6_underdamped_harmonic/medium/v0/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 4.88e-06 | 1 | 40 | 2782 | 85681 | 0 | AVAILABLE |
| m7_malus_law/easy/v2 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 7840 | 71926 | 0 | AVAILABLE |
| m7_malus_law/easy/v2/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 8346 | 116178 | 0 | AVAILABLE |
| m7_malus_law/hard/v0 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 8997 | 349864 | 0 | AVAILABLE |
| m7_malus_law/hard/v0/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 3.9e-16 | 1 | 40 | 6666 | 237855 | 0 | AVAILABLE |
| m7_malus_law/medium/v0 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 8262 | 196793 | 0 | AVAILABLE |
| m7_malus_law/medium/v0/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 8059 | 90858 | 0 | AVAILABLE |
| m8_sound_speed/hard/v0 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 4.39e-16 | 1 | 40 | 3056 | 83846 | 0 | AVAILABLE |
| m8_sound_speed/hard/v0/noise0.01 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0.000854 | 1 | 40 | 5868 | 92282 | 0 | AVAILABLE |
| m9_hooke_law/easy/v2 | newly_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 6703 | 96165 | 0 | AVAILABLE |
| m9_hooke_law/easy/v2/noise0.01 | newly_expressible | COMPLETED | LIVE_UNCERTIFIED | JUMP_AGENT | 0 | 1 | 0 | 0.00145 | 1 | 35 | 9221 | 109789 | 0 | AVAILABLE |
| m4_snell_law/easy/v2 | still_not_expressible | COMPLETED | NO_LIVE_RULEBOOK | - | 0 | 0 | 0 | - | 1 | 13 | 1788 | 1080354 | 0 | EXHAUSTED |
| m4_snell_law/easy/v2/noise0.01 | still_not_expressible | COMPLETED | NO_LIVE_RULEBOOK | - | 0 | 0 | 0 | - | 1 | 16 | 2670 | 1172746 | 0 | EXHAUSTED |
| m4_snell_law/hard/v2 | still_not_expressible | COMPLETED | NO_LIVE_RULEBOOK | - | 0 | 0 | 0 | - | 1 | 13 | 2289 | 1308072 | 0 | EXHAUSTED |
| m4_snell_law/hard/v2/noise0.01 | still_not_expressible | COMPLETED | NO_LIVE_RULEBOOK | - | 0 | 0 | 0 | - | 1 | 17 | 3060 | 1185811 | 0 | EXHAUSTED |
| m4_snell_law/medium/v2 | still_not_expressible | COMPLETED | NO_LIVE_RULEBOOK | - | 0 | 0 | 0 | - | 1 | 13 | 2374 | 1091669 | 0 | EXHAUSTED |
| m4_snell_law/medium/v2/noise0.01 | still_not_expressible | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 0 | 1 | 0.000704 | 0 | 40 | 5850 | 353932 | 0 | AVAILABLE |
| m11_heat_transfer/easy/v1 | regression | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 3242 | 66339 | 0 | AVAILABLE |
| m11_heat_transfer/easy/v1/noise0.01 | regression | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0.00179 | 1 | 40 | 3338 | 86899 | 0 | AVAILABLE |
| m6_underdamped_harmonic/easy/v1 | regression | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 3247 | 68800 | 0 | AVAILABLE |
| m6_underdamped_harmonic/easy/v1/noise0.01 | regression | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 6359 | 510310 | 0 | AVAILABLE |
| m8_sound_speed/medium/v0 | regression | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0 | 1 | 40 | 3030 | 68060 | 0 | AVAILABLE |
| m8_sound_speed/medium/v0/noise0.01 | regression | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 1 | 0 | 0.00228 | 1 | 40 | 2892 | 84563 | 0 | AVAILABLE |
| m9_hooke_law/hard/v0 | regression | COMPLETED | LIVE_UNCERTIFIED | JUMP_AGENT | 0 | 1 | 0 | 0 | 1 | 35 | 9195 | 93211 | 0 | AVAILABLE |
| m9_hooke_law/hard/v0/noise0.01 | regression | COMPLETED | CERTIFIED | JUMP_AGENT | 1 | 0 | 1 | 0.861 | 0 | 40 | 7288 | 399279 | 0 | AVAILABLE |
