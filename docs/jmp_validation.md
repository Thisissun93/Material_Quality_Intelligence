# JMP로 결과 검증하기

이 앱의 계산이 표준 통계 도구와 같은지 JMP로 직접 확인하는 절차입니다.

## 1. 데이터 내보내기

```bash
python scripts/export_for_jmp.py
```

`data/jmp/`에 세 파일이 생깁니다.
- `gage_rr.csv`: Gage R&R 원데이터 (part, operator, replicate, value)
- `product_lots.csv`: 제품 로트별 측정값
- `expected.md`: 이 앱이 계산한 값 (비교 기준)

## 2. Gage R&R

1. JMP에서 `gage_rr.csv` 열기
2. **분석 → 품질 및 공정 → 변동성/계수형 게이지 차트**
   (Analyze → Quality and Process → Variability / Attribute Gauge Chart)
3. Y, 반응 = `value` / X, 그룹화 = `operator`, `part` / 모형 유형 = **교차(Crossed)**
4. 빨간 삼각형 → **게이지 연구(Gauge Studies) → 게이지 R&R**
   - K = **6**
   - 공차 구간(Tolerance) = **2000** (점도 규격 2500–4500)
   - 분산 성분 추정 방법이 선택 가능하면 **EMS**
5. `expected.md`의 %StudyVar(%Gauge R&R), 반복성, 재현성, ndc와 비교

참고: 교호작용(Part×Operator) 분산 추정값이 음수이면 두 도구 모두 0으로 표시합니다.

## 3. 공정능력

1. `product_lots.csv` 열기
2. **분석 → 품질 및 공정 → 공정 능력** (Process Capability)
3. 열 = `viscosity` 등, 규격 한계 입력 (`config/specs.yaml` 값)
4. 군내 시그마 = **이동 범위 평균** (Moving Range, Average) — 이 앱의 단기 σ와 같은 정의
5. Cpk(군내), Ppk(전체) 값을 `expected.md`와 비교

## 결과 기록

| 항목 | 이 앱 | JMP | 차이 |
|---|---|---|---|
| %GRR (StudyVar) | | | |
| 반복성 %StudyVar | | | |
| 재현성 %StudyVar | | | |
| ndc | | | |
| 점도 Cpk | | | |
| 점도 Ppk | | | |

비교 화면을 `docs/img/jmp_*.png`로 저장해 README에 추가하면 됩니다.
