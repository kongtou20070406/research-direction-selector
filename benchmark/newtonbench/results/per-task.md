# Per-task results

Each cell: one mark per trial (✓ = NewtonBench judge says equivalent, ✗ otherwise), then the per-task median RMSLE (inf = no finite score). E = expressible in the Compass dialect (a priori).

## Stratum noise0

| Task | E | vanilla | code_assisted | compass | compass_fallback | Compass path (end state) per trial |
|---|---|---|---|---|---|---|
| `m0_gravity/easy/v0` | E | ✓✓ (0) | ✓✓ (0) | ✓✓ (2.5e-17) | ✓✓ (2.5e-17) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m0_gravity/hard/v1` |  | ✓✓ (2.32e-18) | ✓✓ (0) | ✗✗ (inf) | ✓✓ (0) | NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED), NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED) |
| `m0_gravity/medium/v0` | E | ✓✓ (2.52e-16) | ✓✓ (2.52e-16) | ✓✓ (2.87e-16) | ✓✓ (2.87e-16) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m10_be_distribution/easy/v1` |  | ✓✓ (0) | ✓✓ (4.95e-05) | ✗✗ (5.76) | ✗✗ (5.76) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m10_be_distribution/hard/v0` |  | ✓✓ (4.18e-17) | ✓✓ (4.18e-17) | ✗✗ (0.0465) | ✗✗ (0.0465) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m10_be_distribution/medium/v2` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (10.9) | ✗✗ (10.9) | LIVE_UNCERTIFIED (JUDGMENT_REQUIRED), LIVE_UNCERTIFIED (JUDGMENT_REQUIRED) |
| `m11_heat_transfer/easy/v1` | E | ✓✓ (0) | ✓✓ (0) | ✓✓ (3e-16) | ✓✓ (3e-16) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m11_heat_transfer/hard/v1` |  | ✓✓ (1.31e-16) | ✓✓ (1.31e-16) | ✗✗ (0.0196) | ✗✗ (0.0196) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m11_heat_transfer/medium/v2` | E | ✓✓ (1.89e-16) | ✓✓ (1.95e-16) | ✓✓ (2.09e-16) | ✓✓ (2.09e-16) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m1_coulomb_force/easy/v1` | E | ✓✓ (1.17e-16) | ✓✓ (2.33e-16) | ✓✓ (2.33e-16) | ✓✓ (2.33e-16) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m1_coulomb_force/hard/v0` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.0404) | ✗✗ (0.0404) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m1_coulomb_force/medium/v0` | E | ✓✓ (0) | ✓✓ (0) | ✓✓ (0) | ✓✓ (0) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m2_magnetic_force/easy/v0` | E | ✓✓ (0) | ✓✓ (0) | ✓✓ (0) | ✓✓ (0) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m2_magnetic_force/hard/v0` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (inf) | ✓✓ (0) | NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED), NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED) |
| `m2_magnetic_force/medium/v2` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (inf) | ✗✓ (0.103) | CERTIFIED (GOAL_IDENTIFIED), NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED) |
| `m3_fourier_law/easy/v2` | E | ✓✓ (0) | ✓✓ (0) | ✓✓ (0) | ✓✓ (0) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m3_fourier_law/hard/v1` |  | ✓✓ (0) | ✓✓ (1.39e-16) | ✗✗ (inf) | ✗✓ (0.0214) | CERTIFIED (GOAL_IDENTIFIED), NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED) |
| `m3_fourier_law/medium/v1` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.0286) | ✗✗ (0.0286) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m4_snell_law/easy/v2` |  | ✓✓ (0) | ✓✓ (1.35e-16) | ✗✗ (3.36) | ✗✗ (3.36) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m4_snell_law/hard/v2` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (3.67) | ✗✗ (3.67) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m4_snell_law/medium/v2` |  | ✓✓ (1.21e-16) | ✗✗ (1.7e-16) | ✗✗ (3.67) | ✗✗ (3.67) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m5_radioactive_decay/easy/v2` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (inf) | ✓✓ (0) | NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED), NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED) |
| `m5_radioactive_decay/hard/v2` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.012) | ✗✗ (0.012) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m5_radioactive_decay/medium/v2` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (inf) | ✓✓ (0) | NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED), NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED) |
| `m6_underdamped_harmonic/easy/v1` | E | ✓✓ (0) | ✓✓ (0) | ✗✗ (9.83) | ✗✗ (9.83) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m6_underdamped_harmonic/hard/v1` | E | ✓✓ (2.37e-16) | ✓✓ (3.26e-17) | ✗✗ (11) | ✗✗ (11) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m6_underdamped_harmonic/medium/v0` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (2.3) | ✗✗ (2.3) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m7_malus_law/easy/v2` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (2.92e-13) | ✗✗ (2.92e-13) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m7_malus_law/hard/v0` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (inf) | ✓✓ (0) | NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED), NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED) |
| `m7_malus_law/medium/v0` |  | ✓✓ (1.26e-16) | ✓✓ (0) | ✗✗ (inf) | ✓✓ (0) | NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED), NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED) |
| `m8_sound_speed/easy/v2` | E | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.943) | ✗✗ (0.943) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m8_sound_speed/hard/v0` |  | ✗✗ (inf) | ✓✓ (2.2e-16) | ✗✗ (1.66) | ✗✗ (1.66) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m8_sound_speed/medium/v0` | E | ✓✓ (0) | ✓✓ (0) | ✗✗ (1.71) | ✗✗ (1.71) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m9_hooke_law/easy/v2` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (inf) | ✓✓ (0) | NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED), NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED) |
| `m9_hooke_law/hard/v0` | E | ✓✓ (0) | ✓✓ (7.89e-17) | ✓✓ (0) | ✓✓ (0) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m9_hooke_law/medium/v1` | E | ✓✓ (1.78e-17) | ✓✓ (1.78e-17) | ✓✓ (1.78e-17) | ✓✓ (1.78e-17) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |

## Stratum noise0.01

| Task | E | vanilla | code_assisted | compass | compass_fallback | Compass path (end state) per trial |
|---|---|---|---|---|---|---|
| `m0_gravity/easy/v0/noise0.01` | E | ✓✓ (0.0001) | ✓✓ (8.74e-05) | ✓✓ (0.000891) | ✓✓ (0.000891) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m0_gravity/hard/v1/noise0.01` |  | ✓✓ (8.93e-06) | ✓✓ (8.93e-06) | ✗✗ (inf) | ✓✗ (6.11e-05) | NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED), CERTIFIED (GOAL_IDENTIFIED) |
| `m0_gravity/medium/v0/noise0.01` | E | ✓✓ (2.52e-16) | ✓✓ (2.52e-16) | ✓✓ (0.000422) | ✓✓ (0.000422) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m10_be_distribution/easy/v1/noise0.01` |  | ✗✓ (0.000129) | ✓✓ (0.000214) | ✗✗ (5.76) | ✗✗ (5.76) | LIVE_UNCERTIFIED (JUDGMENT_REQUIRED), LIVE_UNCERTIFIED (JUDGMENT_REQUIRED) |
| `m10_be_distribution/hard/v0/noise0.01` |  | ✓✓ (1.07e-07) | ✓✓ (9.85e-08) | ✗✗ (0.309) | ✗✗ (0.309) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m10_be_distribution/medium/v2/noise0.01` |  | ✗✓ (9.51e-05) | ✓✓ (0.000143) | ✗✗ (15.5) | ✗✗ (15.5) | LIVE_UNCERTIFIED (JUDGMENT_REQUIRED), LIVE_UNCERTIFIED (JUDGMENT_REQUIRED) |
| `m11_heat_transfer/easy/v1/noise0.01` | E | ✓✓ (0) | ✓✓ (0) | ✓✓ (0.00392) | ✓✓ (0.00392) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m11_heat_transfer/hard/v1/noise0.01` |  | ✓✓ (6.34e-16) | ✓✓ (1.31e-16) | ✗✗ (0.00535) | ✗✗ (0.00535) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m11_heat_transfer/medium/v2/noise0.01` | E | ✓✓ (1.96e-16) | ✓✓ (1.96e-16) | ✓✓ (0.00116) | ✓✓ (0.00116) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m1_coulomb_force/easy/v1/noise0.01` | E | ✓✓ (1.17e-16) | ✓✓ (2.33e-16) | ✓✓ (2.33e-16) | ✓✓ (2.33e-16) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m1_coulomb_force/hard/v0/noise0.01` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.0109) | ✗✗ (0.0109) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m1_coulomb_force/medium/v0/noise0.01` | E | ✓✓ (0) | ✓✓ (0) | ✓✓ (0) | ✓✓ (0) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m2_magnetic_force/easy/v0/noise0.01` | E | ✓✓ (0.00124) | ✓✓ (0.00155) | ✓✓ (0.00233) | ✓✓ (0.00233) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m2_magnetic_force/hard/v0/noise0.01` |  | ✓✓ (0) | ✓✓ (0.00159) | ✗✗ (0.00507) | ✗✗ (0.00507) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m2_magnetic_force/medium/v2/noise0.01` |  | ✓✓ (0.00102) | ✓✓ (0.000819) | ✗✗ (0.708) | ✗✗ (0.708) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m3_fourier_law/easy/v2/noise0.01` | E | ✓✓ (0) | ✓✓ (0) | ✓✓ (0.00523) | ✓✓ (0.00523) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m3_fourier_law/hard/v1/noise0.01` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.032) | ✗✗ (0.032) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m3_fourier_law/medium/v1/noise0.01` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.028) | ✗✗ (0.028) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m4_snell_law/easy/v2/noise0.01` |  | ✓✓ (1.35e-16) | ✓✓ (1.35e-16) | ✗✗ (3.36) | ✗✗ (3.36) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m4_snell_law/hard/v2/noise0.01` |  | ✓✓ (0) | ✗✗ (1.8e-16) | ✗✗ (3.67) | ✗✗ (3.67) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m4_snell_law/medium/v2/noise0.01` |  | ✓✓ (1.21e-16) | ✗✗ (1.7e-16) | ✗✗ (3.67) | ✗✗ (3.67) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m5_radioactive_decay/easy/v2/noise0.01` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.0132) | ✗✗ (0.0132) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m5_radioactive_decay/hard/v2/noise0.01` |  | ✓✓ (0) | ✓✗ (4.14e-08) | ✗✗ (0.0282) | ✗✗ (0.0282) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m5_radioactive_decay/medium/v2/noise0.01` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.0202) | ✗✗ (0.0202) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m6_underdamped_harmonic/easy/v1/noise0.01` | E | ✓✓ (2.81e-17) | ✓✓ (0) | ✗✗ (9.84) | ✗✗ (9.84) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m6_underdamped_harmonic/hard/v1/noise0.01` | E | ✓✓ (0) | ✓✓ (6.52e-17) | ✗✗ (11) | ✗✗ (11) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m6_underdamped_harmonic/medium/v0/noise0.01` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (2.29) | ✗✗ (2.29) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m7_malus_law/easy/v2/noise0.01` |  | ✓✓ (3.95e-16) | ✓✓ (3.95e-16) | ✗✗ (2.1e-05) | ✗✗ (2.1e-05) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m7_malus_law/hard/v0/noise0.01` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.0155) | ✗✗ (0.0155) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m7_malus_law/medium/v0/noise0.01` |  | ✓✓ (0) | ✓✓ (0) | ✗✗ (0.00708) | ✗✗ (0.00708) | CERTIFIED (GOAL_IDENTIFIED), LIVE_UNCERTIFIED (JUDGMENT_REQUIRED) |
| `m8_sound_speed/easy/v2/noise0.01` | E | ✓✓ (0.000994) | ✓✓ (0.0013) | ✗✗ (0.943) | ✗✗ (0.943) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m8_sound_speed/hard/v0/noise0.01` |  | ✗✗ (inf) | ✗✗ (5.9) | ✗✗ (1.66) | ✗✗ (1.66) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m8_sound_speed/medium/v0/noise0.01` | E | ✓✓ (0.00181) | ✓✓ (0.00102) | ✗✗ (1.71) | ✗✗ (1.71) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m9_hooke_law/easy/v2/noise0.01` |  | ✓✓ (0.000267) | ✓✓ (0.000451) | ✗✗ (0.00192) | ✗✗ (0.00192) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
| `m9_hooke_law/hard/v0/noise0.01` | E | ✓✓ (0.00412) | ✓✓ (0.00331) | ✗✗ (0.81) | ✗✗ (0.81) | LIVE_UNCERTIFIED (JUDGMENT_REQUIRED), LIVE_UNCERTIFIED (JUDGMENT_REQUIRED) |
| `m9_hooke_law/medium/v1/noise0.01` | E | ✓✓ (0.00203) | ✓✓ (0.00166) | ✓✓ (0.0123) | ✓✓ (0.0123) | CERTIFIED (GOAL_IDENTIFIED), CERTIFIED (GOAL_IDENTIFIED) |
