# 조사 패키지: UF-2611-001 (viscosity)

- 출하 판정: **hold**
- 데이터를 가져온 시스템 6곳: LIMS, MES, QMS, 공급사 CoA, 변경 이력, 설비 FDC
- 모은 기록 61건, 수집 시간 0.22초

## 출하 판정과 사유 (2건)

| ko | en |
|---|---|
| 점도 규격 경계 4,495 (폭의 5% 이내) | viscosity within 5% of a limit (4,495) |
| 이 로트에 진행 중인 고객 클레임 | Open customer complaint on this lot |

## 검사 이력 (최초·재시험) · LIMS (5건)

| id | lot_id | property | value | unit | instrument | operator | measured_at | source_file | attempt | reason | seq | invalidated | oos | oos_no |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 145 | UF-2611-001 | peak_temp | 158.7 | °C | CaloriX DSC · table export (Kelvin) | Kim | 2026-11-03T00:00:00 | CaloriX_2026-11-02.csv | initial |  | 1 | False | False |  |
| 149 | UF-2611-001 | enthalpy | 221.3 | J/g | CaloriX DSC · table export (Kelvin) | Kim | 2026-11-03T00:00:00 | CaloriX_2026-11-02.csv | initial |  | 1 | False | False |  |
| 153 | UF-2611-001 | tg | 145.9 | °C | CaloriX DSC · table export (Kelvin) | Kim | 2026-11-03T00:00:00 | CaloriX_2026-11-02.csv | initial |  | 1 | False | False |  |
| 762 | UF-2611-001 | filler_pct | 59.73 | wt% | ThermoScale TGA · residue export | Park | 2026-11-03T00:00:00 | TGA_2026-11-02.csv | initial |  | 1 | False | False |  |
| 552 | UF-2611-001 | viscosity | 4495 | mPa·s | RotaVisc DV-2 · firmware 3.x | Park | 2026-11-03T09:31:00 | DV2_2026-11-02.csv | initial |  | 1 | False | False |  |

## OOS 조사 · QMS (0건)

해당 없음

## 투입 원료 로트 · MES (3건)

| material | raw_lot | supplier | made_at |
|---|---|---|---|
| epoxy_resin | ER-RE-020 | Resinova | 2026-10-22 |
| hardener | HD-AT-012 | AmineTech | 2026-10-27 |
| silica_filler | SF-NA-024 | NanoSphere | 2026-10-22 |

## 원료 수입검사·CoA 값 · LIMS, 공급사 CoA (10건)

| lot_id | material | property | value | unit | source | measured_at |
|---|---|---|---|---|---|---|
| ER-RE-020 | epoxy_resin | eew | 188.7 | g/eq | coa |  |
| ER-RE-020 | epoxy_resin | eew | 188 | g/eq | inhouse | 2026-10-24T00:00:00 |
| ER-RE-020 | epoxy_resin | resin_visc | 1.194e+04 | mPa·s | coa |  |
| ER-RE-020 | epoxy_resin | resin_visc | 1.165e+04 | mPa·s | inhouse | 2026-10-24T10:00:00 |
| HD-AT-012 | hardener | amine_value | 539.8 | mgKOH/g | coa |  |
| HD-AT-012 | hardener | amine_value | 542.2 | mgKOH/g | inhouse | 2026-10-29T00:00:00 |
| SF-NA-024 | silica_filler | d50 | 2.92 | µm | coa |  |
| SF-NA-024 | silica_filler | d50 | 2.515 | µm | inhouse | 2026-10-24T00:00:00 |
| SF-NA-024 | silica_filler | moisture | 0.0405 | % | coa |  |
| SF-NA-024 | silica_filler | moisture | 0.0385 | % | inhouse | 2026-10-24T00:00:00 |

## 해당 공급사 CoA 신뢰도 · 공급사 CoA (5건)

| supplier | property | n | bias | bias_pct_of_halfspec | p_value | corr | loa | drift_since | trust |
|---|---|---|---|---|---|---|---|---|---|
| AmineTech | amine_value | 12 | -0.8333 | 4.167 | 0.2466 | 0.8667 | 4.623 |  | trusted |
| NanoSphere | d50 | 12 | 0.1161 | 19.35 | 0.03841 | 0.9504 | 0.3352 | SF-NA-016 | untrusted |
| NanoSphere | moisture | 12 | 0.001558 | 3.117 | 0.1101 | 0.8125 | 0.006089 |  | trusted |
| Resinova | eew | 12 | -0.425 | 5.667 | 0.08881 | 0.9091 | 1.546 |  | trusted |
| Resinova | resin_visc | 12 | 67.56 | 2.702 | 0.2723 | 0.9427 | 396.9 |  | trusted |

## 배치 공정 기록 · MES (5건)

| param | value | text |
|---|---|---|
| batch_kg | 200 |  |
| degas_temp | 60 |  |
| mix_time | 47 |  |
| mixer |  | M2 |
| vacuum | 5.1 |  |

## 설비 런·FDC 알람 · 설비 FDC (1건)

| id | equipment | started | ended | status | alarms |
|---|---|---|---|---|---|
| 14 | M2 | 2026-11-03T06:00:00 | 2026-11-03T07:32:00 | ok |  |

## 생산일 전 60일 4M 변경점 · 변경 이력 (1건)

| change_no | date | category | item | description | notified | days_from_start |
|---|---|---|---|---|---|---|
| CN-26-063 | 2026-10-06 00:00:00 | Method | Incoming inspection | Sampling plan for resin lots updated | Y | -28 |

## 같은 원료를 쓴 다른 제품 로트 · MES (30건)

| product_lot | material | raw_lot |
|---|---|---|
| UF-2610-016 | epoxy_resin | ER-RE-020 |
| UF-2610-016 | silica_filler | SF-NA-024 |
| UF-2610-017 | epoxy_resin | ER-RE-020 |
| UF-2610-017 | silica_filler | SF-NA-024 |
| UF-2610-018 | epoxy_resin | ER-RE-020 |
| UF-2610-018 | silica_filler | SF-NA-024 |
| UF-2610-019 | epoxy_resin | ER-RE-020 |
| UF-2610-019 | hardener | HD-AT-012 |
| UF-2610-019 | silica_filler | SF-NA-024 |
| UF-2610-020 | epoxy_resin | ER-RE-020 |
| UF-2610-020 | hardener | HD-AT-012 |
| UF-2610-020 | silica_filler | SF-NA-024 |

… 외 18건

## 고객 클레임 · QMS (1건)

| id | no | received | customer | lot_id | property | symptom | qty | unit | severity | status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | CC-2611-001 | 2026-11-07 | Customer A (demo) | UF-2611-001 | viscosity | Slow dispense flow, incomplete fillet on large die | 120 | kg | major | Open |
