# 조사 패키지: UF-2611-001 (viscosity)

- 출하 판정: **hold**, 생산일 2026-11-03
- 데이터를 가져온 시스템 6곳: LIMS, MES, QMS, 공급사 CoA, 변경 이력, 설비 FDC
- 모은 기록 61건, 수집 시간 0.16초

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

## 고객 클레임 8D 초안 (클레임이 있는 로트만)

# 고객 클레임 CC-2611-001: Customer A (demo) · Slow dispense flow, incomplete fillet on large die

| 항목 | 내용 |
|---|---|
| 8D 번호 | 8D-CC-2611-001 |
| 제품 / 규격 | Capillary Underfill UF-300 · 점도 2500–4500 mPa·s |
| 유형 | 고객 클레임 (Customer A (demo), 접수 2026-11-07, 심각도 major) |
| 발행일 | 2026-11-18 |
| 개정 | Rev.0 |
| 챔피언 / 팀장 | 품질보증팀장 / SQE (공급사 품질) |

|  | D0 | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 |
|---|---|---|---|---|---|---|---|---|---|
| 상태 | 완료 | 완료 | 완료 | 진행 | 데이터 검증 완료 · 재현 시험 대기 | 계획 | 계획 | 계획 | 미결 |
| 완료/목표일 | 11-18 | 11-18 | 11-18 | 11-19 | 12-02 | 12-09 | 01-02 | 01-17 | 02-01 |

#### D0 증상 및 긴급 대응
- **증상**: 고객 표현: "Slow dispense flow, incomplete fillet on large die" (로트 UF-2611-001, 120 kg). 출하 시 점도 4,495 mPa·s (규격 내, 전체 로트 중 99 백분위). UF-2608-003부터 점도 평균이 3,431 → 3,840 mPa·s로 이동. 규격 이탈은 0건이지만 상한 10% 이내 근접 로트 6개. Ppk 0.89 (목표 1.33).
- **ERA**: 24시간 내: 고객 재고 동일 로트 사용 중지 요청 · 대체품 출하 · 보관 샘플 재측정
- 고객 클레임 → 8D 회신 의무 (D3 24시간, D4 2주)

#### D1 팀 구성
| 기능 | 역할 |
|---|---|
| 품질보증팀장 | 챔피언 · 자원 승인 |
| SQE (공급사 품질) | 팀장 · 공급사 대응 · 8D 작성 |
| 품질관리(QC) 시험실 | 재측정 · 측정시스템 검증 |
| 생산기술 | 공정 조건 검토 · 원료-제품 영향 분석 |
| 생산 | 재고·재공 격리 · 배치 순서 조정 |
| 구매 | 공급사 SCAR · 대체 공급 검토 |
| 연구소(배합) | 배합 허용 범위 검토 |

#### D2 문제 기술
**문제 정의**: UF-300 점도가 2026-08-05(UF-2608-003) 이후 평균 +409 mPa·s 이동, 공정능력이 Ppk 0.89로 저하됨 (영향 62로트).

| 5W2H | 내용 |
|---|---|
| Customer | Customer A (demo) · 로트 UF-2611-001 · 120 kg · Slow dispense flow, incomplete fillet on large die |
| What | 점도 평균 상승, 규격 상한 접근 |
| Where | 전 생산 라인 제품 로트 (출하검사 시험실 측정) |
| When | 2026-08-05 이후 지속, 최근 10로트 평균 4,328 |
| Who | 고객 Customer A (demo) 수입/공정에서 발견, 2026-11-07 접수 |
| How | 기준 대비 +409 mPa·s 단계적 상승, 3σ보다 4로트 먼저 감지 |
| How many | 영향 62로트 / 규격 이탈 0 / 상한 근접 6 |

**IS / IS NOT**

|  | IS | IS NOT | 시사점 |
|---|---|---|---|
| 어느 로트 | UF-2611-001: 4,495 | 기준 구간 평균 3,431 | 규격 내, 99 백분위 → 고객 공정 허용 범위가 출하 규격보다 좁을 가능성 |
| 무엇 | 점도 | 반응열, 필러 함량 | 특정 물성만 변화 → 경화 반응보다 유동성(필러) 쪽 요인 |
| 어디 (원료) | NanoSphere: 4,029 | SilicaWorks: 3,542 | NanoSphere 원료 사용 로트에서만 높음 |
| 언제 | 2026-08-05 이후 | 기준 구간 (처음 40로트) | 특정 시점부터 시작 → 변경점(4M) 추적 |
| 누가 (측정자) | 모든 측정자에서 상승 | 특정 측정자 편중 없음 | 측정자 범위 3,776–3,886 → 측정자 요인 제외 |

**변경점(4M) 검토 · 발생 전 45일 ~ 후 10일**

| No. | 일자 | 4M | 내용 | 원료 보정 후 단차 | 판정 |
|---|---|---|---|---|---|
| CN-26-044 | 2026-07-08 | Machine | Mixer M2: Planetary blade replaced after overhaul | +40.29 (p=0.20) | 제외 |

#### D3 봉쇄 조치
| 위치 | 수량 | 조치 | 담당 | 목표일 |
|---|---|---|---|---|
| 고객 재고 (Customer A (demo)) | 120 kg (UF-2611-001) | 사용 중지 · 회수 또는 선별 · 대체품 긴급 출하 | SQE (공급사 품질) | 2026-11-19 |
| 원료 재고 | 3 로트 (SF-NA-020, SF-NA-022, SF-NA-024) | 격리 · 사용 전 D50 재측정 · 위험도 낮은 조합 우선 사용 | 품질관리(QC) 시험실 | 2026-11-19 |
| 재공 / 창고 (최근 4주) | 19 로트 | 출하 보류 · 보관 샘플 재측정 · 상한 근접 로트 별도 판정 | 생산 | 2026-11-19 |
| 출하분 (4주 이전) | 43 로트 | 출하 이력 추적 · 상한 근접 로트 고객 통보 여부 판단 | SQE (공급사 품질) | 2026-11-20 |
| 고객사 | 6 로트 (상한 10% 이내) | 필요 시 고객 사전 통보 및 사용 이력 확인 | 품질보증팀장 | 2026-11-20 |

- **유효성 확인**: 격리 이후 출하 로트 전수 재측정, 결과 규격 내 확인 시 해제
- **해제 기준**: D6 유효성 검증 완료 시 (연속 25로트 Ppk ≥ 1.33) 봉쇄 해제

#### D4 근본 원인
|  | 기술적 원인 (TRC) | 시스템 원인 (MRC) |
|---|---|---|
| 발생 (Occurrence) | 원료 D50 입도 변화 3.05 → 2.84 µm (NanoSphere 로트) → 예측 영향 +353 mPa·s | 원료 규격(2.4–3.6)이 제품 영향 기준으로 설정되지 않음: 규격 안에서의 이동도 제품을 움직임 · NanoSphere의 공정 변경이 4M 변경 이력에 없음 (사전 통보 없음) |
| 미검출 (Non-detection) | 수입검사가 규격 합/부만 판정 · 규격 내 추세(공급사별 이동)를 보지 않음 · NanoSphere CoA가 실제보다 높게 기재되어 이동을 가림 (SF-NA-016부터 차이 확대) | CoA 신뢰도 정기 검증과 원료 SPC가 수입검사 기준서에 없음 |

**5-Why · 발생**

1. 점도가 올라감
2. 필러 D50 입도가 작아져 비표면적·입자 간 마찰 증가 (모델 영향 1순위, R²=0.90)
3. NanoSphere 로트의 D50 입도가 단계적으로 감소
4. 공급사 분급(classification) 공정 변경 추정 → SCAR로 확인 필요
5. 원료 규격이 제품 영향 기준이 아니어서 규격 내 변화가 허용됨

**5-Why · 미검출**

1. 수입검사에서 합격 처리됨
2. 측정값이 원료 규격(2.4–3.6) 안에 있었음
3. 공급사별 추세 관리(SPC) 없음 · CoA 수치 의존
4. CoA-자사 측정 상관성 정기 검증 절차 없음

**6M 요인 검토**

| 6M | 가설 | 근거 | 판정 |
|---|---|---|---|
| Material | 필러 D50 입도 감소 (NanoSphere) | 모델 영향 +353, 공급사 비중 변화 | 확인 |
| Measurement | 점도계 %GRR 12.9%, 장비 교체 영향 | 장비 편차 +48.10 mPa·s (p=0.066) | 제외 (이동 폭 대비 작음) |
| Man | 측정자·작업자 차이 | 모든 측정자에서 동일 경향 | 제외 |
| Method (검사) | 수입검사 합/부 판정만 수행 | 규격 내 이동 미관리 | 확인 (유출 원인) |
| Machine | 믹서(M1/M2)·탈포 진공도·온도 | MES 공정 기록 vs 원료로 설명 안 되는 편차: 탈포 온도 p=0.19 · 믹서 p=0.61 · 탈포 진공도 p=0.92 | 제외 |
| Method (공정) | 믹싱 시간·배치 크기 | MES 공정 기록: 배치 크기 p=0.03 · 믹싱 시간 p=0.70 | 제외 |
| Environment | 시험실 온도(25℃) 관리 | 측정 온도 25.0℃ 기록 일정 | 제외 |

**원인 검증**

- 원료→제품 모델: 교차검증 R² 0.90, D50 입도 단독으로 이동량 +353 설명 (실제 이동 +409)
- 필러 공급사 비중 (기준→이동 구간): NanoSphere 50%→61%, SilicaWorks 50%→39%
- NanoSphere CoA vs 자사 D50 입도: 평균 차이 +0.116, 차이 확대 시작 SF-NA-016
- 재현 시험(On/Off): 기존 D50 로트와 저D50 로트로 동일 배합 비교 → 수행 예정

#### D5 영구 시정조치 선정
| 대상 | 조치 | 검증 방법 | 담당 | 목표일 |
|---|---|---|---|---|
| 발생 | NanoSphere SCAR: 분급 조건 원복 및 출하 기준 강화 | 공급사 3로트 연속 자사 측정 신규 한계 이상 | 구매 | 2026-12-09 |
| 발생 | 배치 전 원료 조합 위험도 확인 후 투입 (앱 '재고 원료 조합 위험도') | 예측 이탈확률 5% 초과 조합 투입 0건 | 생산 | 2026-11-25 |
| 미검출 | 수입검사 원료 한계를 제품 영향 기준으로 재설정: D50 입도 하한 2.58 µm (현행 2.4) | 모델 예측 제품 이탈확률 < 0.3% 유지 | SQE (공급사 품질) | 2026-12-02 |
| 미검출 | 공급사별 원료 EWMA 관리도 + 월 1회 CoA 신뢰도 검증, 신뢰 불가 시 Skip-lot 중지 | 규격 내 이동 3로트 이내 경보 | 품질관리(QC) 시험실 | 2026-12-02 |

#### D6 실행 및 유효성 확인
| 대상 | 조치 | 담당 | 목표일 | 상태 |
|---|---|---|---|---|
| 발생 | NanoSphere SCAR: 분급 조건 원복 및 출하 기준 강화 | 구매 | 2026-12-09 | 미등록 |
| 발생 | 배치 전 원료 조합 위험도 확인 후 투입 (앱 '재고 원료 조합 위험도') | 생산 | 2026-11-25 | 미등록 |
| 미검출 | 수입검사 원료 한계를 제품 영향 기준으로 재설정: D50 입도 하한 2.58 µm (현행 2.4) | SQE (공급사 품질) | 2026-12-02 | 미등록 |
| 미검출 | 공급사별 원료 EWMA 관리도 + 월 1회 CoA 신뢰도 검증, 신뢰 불가 시 Skip-lot 중지 | 품질관리(QC) 시험실 | 2026-12-02 | 미등록 |

**유효성 판정 기준**

- 조치 후 연속 25로트 점도 Ppk ≥ 1.33
- EWMA·CUSUM 경보 없음
- CoA-자사 차이 ±(규격 반폭의 10%) 이내

#### D7 재발 방지
| 고장 모드 | 원인 | 관리 방법 | O | D |
|---|---|---|---|---|
| 점도 규격 상한 이탈 | 필러 D50 입도 감소 (공급사 공정 변경) | 원료 SPC + 제품 영향 기준 한계 | 5 → 3 | 7 → 3 |

| 관리 항목 | 측정 방법 | 주기 | 이상 시 조치 |
|---|---|---|---|
| Spherical silica filler D50 입도 | Laser diffraction | 매 로트 | 신규 하한 2.58 미만 불합격 · EWMA 경보 시 공급사 통보 |
| CoA | 자사 측정 대조 | 월 1회 | 신뢰 불가 시 Skip-lot 중지 |

**표준 개정**

- 수입검사 기준서 (원료 한계·SPC·CoA 검증 추가)
- 공급사 품질 협약서 (변경 사전 통보 조항)
- 4M 변경관리 절차 (측정 장비 포함)

**수평 전개**

- 같은 필러를 쓰는 다른 제품 점검
- 전 원료 공급사 CoA 신뢰도 일괄 점검: NanoSphere
- 다른 원료 규격도 제품 영향 기준으로 재검토 (모델 기반)

**교훈**: 규격 안의 원료 변화도 제품을 움직인다. 원료 규격은 제품 영향으로 정하고, CoA는 정기적으로 검증한다.

#### D8 종결
- [ ] D6 유효성 검증 완료 (25로트)
- [ ] D7 문서 개정·교육 완료
- [ ] 고객 통보 건 종결 확인
- [ ] 봉쇄 해제 승인
- [ ] 팀 성과 공유 및 8D 종결 승인 (챔피언)

_D0–D4는 데이터로 작성, D5–D8은 제안 (담당은 기능 단위, 일자는 목표일)._
