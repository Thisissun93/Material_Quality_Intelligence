# 로드맵

현장에서 실제로 운용하는 품질 시스템을 목표로, 세 단계로 진행합니다.

| 단계 | 내용 | 상태 |
|---|---|---|
| 1. 서비스화 | PostgreSQL, REST API, Docker Compose, 로그인·역할, 두 사람 전자서명 출하 승인, 해시 체인 감사 추적 | 완료 |
| 2. 타 시스템 연동 | OPC UA 설비 수집기(store-and-forward), 서버 측 FDC 판정, MES·LIMS용 REST API | 완료 (SECS/GEM 예정) |
| 3. AI 기능 | 사내 LLM 품질 어시스턴트(RAG·Tool Calling), 근거 검사, 34문항 평가 | 완료 (모델 확대 예정) |
| 측정과 검증 | 업무 시나리오 측정, 통계 기능 독립 계산 검증표 | 완료 (JMP 화면 대조 예정) |

## 다음 단계

### 측정과 검증
- [ ] JMP 결과 화면과 앱 값 대조 후 [docs/stats_validation.md](docs/stats_validation.md)에 추가
- [ ] 실제 현장에서 '로트 1건 조사' 시나리오를 수작업으로 재서 [docs/benchmark_investigation.md](docs/benchmark_investigation.md)의 기준값을 계측값으로 교체

### 운용
- [ ] 조사 패키지(`mqi/investigation.py`)를 앱 화면과 REST API(`GET /api/v1/lots/{lot}/investigation`)로 제공
- [ ] 사내 계정 연동(LDAP·SSO), 비밀번호 정책, 로그인 실패 시 계정 잠금
- [ ] 저장소 설정에서 Dependabot 보안 알림 켜기 (버전 업데이트 PR은 Codex 리뷰 사용량을 고려해 보류)
- [ ] 21 CFR Part 11 대응 검토: 시스템 밸리데이션 문서(IQ/OQ/PQ), 감사 추적 검토 절차

### 연동
- [ ] SECS/GEM(반도체 장비 표준) 수집 추가
- [ ] MES·LIMS 실제 API 응답 형식에 맞춘 어댑터

### AI 어시스턴트
- [ ] 복합 질문(로트 상태 + 대응 절차) 개선
- [ ] GPU에서 7B 이상 모델로 같은 34문항 평가 재측정
- [ ] 5-Why·근본원인 분류를 구조화해 8D D4 초안에 연결

## 하지 않기로 한 것
- AI가 출하 승인·OOS 종결 같은 결정을 직접 내리는 기능. 판단 근거를 모으고 정리하는 데까지만 쓰고, 결정과 서명은 사람이 합니다.
