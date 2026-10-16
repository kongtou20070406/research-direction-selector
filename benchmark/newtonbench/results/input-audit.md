# Simulator input audit (amendment 5)

Noise-free vanilla_equation output over the 64 declared design points. "Ignored" = varying that input alone over its design levels, from any design point, never changes the output. An input the ground-truth law does not depend on is ignored under both harnesses.

| Cell | Inputs | Ignored, formal-run harness | Distinct outputs | Ignored, amended harness | Distinct outputs |
|---|---|---|---|---|---|
| `m0_gravity/easy/v0` | mass1, mass2, distance | - | 20 | - | 20 |
| `m0_gravity/medium/v0` | mass1, mass2, distance | - | 28 | - | 28 |
| `m0_gravity/hard/v1` | mass1, mass2, distance | mass2 | 12 | mass2 | 12 |
| `m1_coulomb_force/easy/v1` | q1, q2, distance | - | 23 | - | 23 |
| `m1_coulomb_force/medium/v0` | q1, q2, distance | - | 37 | - | 37 |
| `m1_coulomb_force/hard/v0` | q1, q2, distance | - | 40 | - | 40 |
| `m2_magnetic_force/easy/v0` | current1, current2, distance | - | 16 | - | 16 |
| `m2_magnetic_force/medium/v2` | current1, current2, distance | current1 | 16 | current1 | 16 |
| `m2_magnetic_force/hard/v0` | current1, current2, distance | - | 31 | - | 31 |
| `m3_fourier_law/easy/v2` | k, A, delta_T, d | - | 13 | - | 13 |
| `m3_fourier_law/medium/v1` | k, A, delta_T, d | - | 28 | - | 28 |
| `m3_fourier_law/hard/v1` | k, A, delta_T, d | - | 64 | - | 64 |
| `m4_snell_law/easy/v2` | n1, n2, angle1 | n1, n2, angle1 | 1 | - | 52 |
| `m4_snell_law/medium/v2` | n1, n2, angle1 | n1, n2, angle1 | 1 | - | 52 |
| `m4_snell_law/hard/v2` | n1, n2, angle1 | n1, n2, angle1 | 1 | - | 52 |
| `m5_radioactive_decay/easy/v2` | N0, lambda_constant, t | - | 17 | - | 17 |
| `m5_radioactive_decay/medium/v2` | N0, lambda_constant, t | - | 17 | - | 17 |
| `m5_radioactive_decay/hard/v2` | N0, lambda_constant, t | - | 13 | - | 13 |
| `m6_underdamped_harmonic/easy/v1` | k, m, b | k, m, b | 1 | - | 37 |
| `m6_underdamped_harmonic/medium/v0` | k, m, b | k, m, b | 1 | - | 31 |
| `m6_underdamped_harmonic/hard/v1` | k, m, b | k, m, b | 1 | - | 46 |
| `m7_malus_law/easy/v2` | I_0, theta | - | 64 | - | 64 |
| `m7_malus_law/medium/v0` | I_0, theta | - | 64 | - | 64 |
| `m7_malus_law/hard/v0` | I_0, theta | - | 64 | - | 64 |
| `m8_sound_speed/easy/v2` | gamma, T, M | gamma, T, M | 1 | gamma | 7 |
| `m8_sound_speed/medium/v0` | gamma, T, M | gamma, T, M | 1 | - | 26 |
| `m8_sound_speed/hard/v0` | gamma, T, M | gamma, T, M | 1 | - | 64 |
| `m9_hooke_law/easy/v2` | x | - | 64 | - | 64 |
| `m9_hooke_law/medium/v1` | x | - | 64 | - | 64 |
| `m9_hooke_law/hard/v0` | x | - | 64 | - | 64 |
| `m10_be_distribution/easy/v1` | omega, T | T | 2 | - | 17 |
| `m10_be_distribution/medium/v2` | omega, T | omega, T | 1 | - | 14 |
| `m10_be_distribution/hard/v0` | omega, T | omega, T | 1 | - | 27 |
| `m11_heat_transfer/easy/v1` | m, c, delta_T | - | 24 | - | 24 |
| `m11_heat_transfer/medium/v2` | m, c, delta_T | - | 28 | - | 28 |
| `m11_heat_transfer/hard/v1` | m, c, delta_T | - | 64 | - | 64 |
