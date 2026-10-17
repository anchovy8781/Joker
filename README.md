# YouTube Exchange — 유튜브 채널 기반 가상 주식 거래소 (MVP)

유튜브 채널을 가상 종목으로 상장하고, 여러 사용자가 **하나의 영구 시장**에서 **현금 가치가 없는 게임 크레딧**으로 거래하는 웹 게임입니다.
실제 주문장·체결 엔진, 불변 원장, 혼합형 주가 엔진, YouTube Data API 연동, 악용 탐지, 관리자 대시보드가 동작합니다.

> 현금 입출금·환전·유료 결제·채널 소유권 거래 기능은 없습니다. 모든 가격은 게임 규칙에 따른 가상 가격이며 실제 채널의 가치나 미래 성과를 의미하지 않습니다.
> 사용한 외부 서비스는 모두 무료입니다(YouTube Data API 무료 쿼터, 선택 사항인 Cloudflare Turnstile 무료 플랜, Let's Encrypt).

---

## 1. 구성

```
frontend/  Next.js 15 · React 19 · TypeScript · Tailwind (다크 거래소 UI, /api 를 백엔드로 프록시)
backend/   FastAPI · Pydantic 2 · SQLAlchemy 2 · Alembic · psycopg 3
           app/services/orders.py       주문·체결 엔진
           app/services/ledger.py       불변 현금 원장
           app/services/pricing.py      혼합형 주가 엔진 (hybrid-v1)
           app/services/youtube.py      YouTube Data API v3 공급자 + 데모 공급자
           app/services/collector.py    데이터 수집·검증 이벤트 탐지
           app/services/risk.py         악용 탐지·위험 등급
           app/services/reconciliation.py  원장 대사
           app/services/jobs.py, app/worker.py  PostgreSQL 작업 큐 / 워커
           app/cli.py                   관리 명령 (관리자 생성, 데모 시드, 작업 실행)
docs/      SECURITY.md (보안·운영 체크리스트), DEPLOYMENT.md (HTTPS 배포)
docker-compose.yml  db · migrate · api · worker · web
```

```
브라우저 ──▶ web(Next.js :3000) ──/api──▶ api(FastAPI :8000) ──▶ PostgreSQL
                                          worker(작업 큐) ───────▶ PostgreSQL ──▶ YouTube Data API
```

## 1-1. 추가 기능 (2차)

| 기능 | 내용 |
| --- | --- |
| 거래 시간 (한국시간) | 월·목 17:00–23:10, **화·수·금 16:00–23:10 (추가 거래시간)**, 토·일 13:00–22:00. 장외에는 매수·매도 **예약 주문**(시장가·지정가)만 가능 — 현금·주식을 즉시 예약하고 장 시작 시 접수 순서대로 제출 |
| 지정가 자동 매수/매도 | "현재가가 X 이하(이상)가 되면 지정가/시장가로 N주" 조건 주문. 조건 충족 시 일반 주문 엔진으로 제출, 잔액 부족 시 실패 알림 |
| 기부함 | 사용자 간 게임 크레딧 기부(제로섬 원장 기록). 하루에 받을 수 있는 금액 최대 **500,000** (보내기도 동일), 1회 100~500,000. 다중 계정 악용 방지: 가입 72시간 + 체결 5회 이상, 같은 네트워크 계정 간 기부 차단 |
| 인기도 1~50 | 구독자 규모 25% · 조회 증가 20% · **좋아요율** 20% · **좋아요 수** 15% · 화제성(인기 급상승/신규 영상) 10% · 거래대금 10%. 좋아요는 최근 업로드 10개의 공개 좋아요 수(YouTube API `playlistItems`+`videos`) |
| 주가 엔진 v2 | 실적 점수에 좋아요율 변화·인기도 변화 추가, 수급은 시간 감쇠(반감기 6시간) + 유동성 신뢰도(얇은 시장은 덜 움직임), 심리에 인기도 수준 반영. 기존 ±8% 한도 유지 |
| 상장 승인제 | **모든 상장은 관리자 승인 필요**(CLI 자동 상장 제거). 사용자 상장 요청은 **매일 오후 7시부터**, **전체 합계 하루 25개**. 초과 시 "오늘의 상장 요청 최대한도에 도달했습니다" 표시 |
| 원격 승인 | 상장 요청이 오면 관리자에게 알림 → 폰 앱의 관리자 화면(`/admin?tab=listing`)에서 바로 승인/거절 |
| 할증 수수료 | 신규 영상 업로드 후 48시간, 유명인 신규 채널(승인 시 체크) 상장 후 30일 동안 수수료 **1%**. 이 종목을 **손실 매도하면 매도 수수료 면제**. 수수료율은 주문 시점에 고정 |
| 단체 채팅 · 알림 · 미션 · 순위 | 전체/종목별 채팅, 체결·정지·기부·미션 알림, 1회/일일 미션 보상, 거래대금·시가총액·거래량·인기도·초고수 PICK 순위 |
| 모바일 / APK | 하단 탭바 모바일 UI + PWA. Android 앱은 `mobile/`(Capacitor), GitHub Actions가 APK 빌드 |

### 공개 서버 실행 (무료, 내 컴퓨터에서)
```bash
./scripts/start-public.sh
```
Docker만 있으면 됩니다. 랜덤 비밀값으로 `.env`를 만들고, 운영 모드(HTTPS 쿠키·보안 검사)로 전체 스택을 띄운 뒤 Cloudflare 무료 임시 터널로 `https://xxxx.trycloudflare.com` 주소를 출력합니다(계정·도메인·결제 불필요). 이 주소를 앱 첫 화면에 입력하세요.
* 컴퓨터가 켜져 있는 동안만 서비스됩니다. 임시 터널 주소는 재시작마다 바뀝니다(앱의 "서버 주소 변경"으로 수정).
* 고정 주소가 필요하면 무료 Cloudflare 계정의 Named Tunnel 또는 `docs/DEPLOYMENT.md`의 서버 배포를 사용하세요.

### 컴퓨터를 꺼도 24시간 운영 — 남는 안드로이드 폰을 서버로
[`docs/PHONE_SERVER.md`](docs/PHONE_SERVER.md): Termux에서 명령어 한 줄로 설치(무료, 루팅 불필요). 서버가 공개 주소를
GitHub(`server-address` 브랜치)에 자동 게시하고, **앱은 주소 입력 없이 자동 연결**됩니다.

### Android APK
* GitHub 저장소의 **Actions → Android APK** 실행 결과 하단 **Artifacts**에서 `youtube-exchange-apk`를 내려받아 설치합니다(디버그 서명, "출처를 알 수 없는 앱 설치" 허용 필요).
* `mobile/`이 바뀌면 자동 빌드되고, Actions 화면의 "Run workflow"로 수동 실행도 됩니다.
* 앱은 입력한 거래 서버를 앱 안 WebView로 엽니다. 뒤로가기 버튼은 앱 안에서 이전 화면으로 이동합니다.

## 2. 설치 및 실행

### A. Docker (권장)

```bash
cp .env.example .env            # 값 확인 (비밀 값은 직접 생성)
docker compose up -d --build    # db → migrate → api, worker → web
docker compose exec api python -m app.cli create-admin admin # 관리자 생성 (비밀번호 입력)
docker compose exec api python -m app.cli seed-demo --approve-by admin  # [DEMO] 종목 8개 (관리자 승인으로 상장)
open http://localhost:3000
```

* API 문서(개발 모드): http://localhost:8000/api/docs
* 워커가 6시간마다 데이터 수집, 매일 01:00 UTC 가격 갱신, 02:00 원장 대사, 15분마다 이상 거래 탐지, 10분마다 유동성 호가 갱신을 실행합니다.
* 즉시 실행: `docker compose exec api python -m app.cli run collect_data` (`price_update`, `reconcile`, `risk_scan`, `mm_requote`)

### B. 로컬 개발

필요: Python 3.13, Node 22, PostgreSQL 16

```bash
# DB
createuser -P yex  # 비밀번호 예: yex_dev_password
createdb -O yex yex && createdb -O yex yex_test

# 백엔드
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export YEX_DATABASE_URL=postgresql+psycopg://yex:yex_dev_password@localhost:5432/yex YEX_COOKIE_SECURE=false
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli seed-demo
.venv/bin/python -m app.cli create-admin admin
.venv/bin/uvicorn app.main:app --port 8000          # 터미널 1
.venv/bin/python -m app.worker                      # 터미널 2

# 프론트엔드
cd ../frontend && npm ci && npm run dev             # 터미널 3 → http://localhost:3000
```

## 3. 테스트

```bash
# 백엔드 (실제 PostgreSQL 사용, yex_test DB를 매 실행마다 초기화)
cd backend && .venv/bin/pytest
#  실제 DB 서버 재시작까지 검증하려면:
YEX_TEST_DB_RESTART_CMD="sudo service postgresql restart" .venv/bin/pytest tests/test_persistence.py

# 프론트엔드 단위·컴포넌트 테스트
cd frontend && npm test

# E2E (스택이 실행 중일 때, 데모 시드 필요)
cd frontend && npx playwright test        # 데스크톱 + 모바일 뷰포트
```

### 최근 실행 결과

| 스위트 | 결과 |
| --- | --- |
| 백엔드 pytest | **84 passed** (실제 `service postgresql restart` 포함) |
| 프론트엔드 Vitest | **14 passed** |
| Playwright E2E | **6 passed** (3 시나리오 × 데스크톱/모바일) |
| Docker Compose | 빌드·마이그레이션·시드·가입·체결·DB 컨테이너 재시작 후 데이터 보존 확인 |

### 필수 테스트 항목 매핑

| # | 항목 | 테스트 |
| --- | --- | --- |
| 1 | 신규 자금 정확히 1회 지급 | `test_auth.py::test_01_*` |
| 2 | 중복 요청으로 이중 지급 없음 | `test_02_*` (동시 8스레드), `test_02b_*` (동시 가입 6건) |
| 3 | 잔액 초과 매수 거부 | `test_orders.py::test_03_*`, `test_03b_*` (예약금 재사용 불가) |
| 4 | 보유 초과 매도 거부 | `test_04_*` |
| 5 | 동시 주문 시 잔액 음수 없음 | `test_concurrency.py::test_05_*` (20스레드), `test_05b_*` (HTTP 12건) |
| 6 | 재전송 시 중복 체결 없음 | `test_06_*`, `test_06b_*` (동일 키 동시 10건) |
| 7 | 부분 체결 잔량 정확 | `test_07_*` |
| 8 | 취소 시 예약 자금·주식 반환 | `test_08_*`, `test_08b_*` |
| 9 | 수수료 정확 | `test_09_*`, `test_09b_*` (클라이언트 수수료/가격 필드 거부) |
| 10 | 원장 합계 = 잔액 | `test_ledger.py::test_10_*` 외 불변성·변조 탐지 |
| 11 | 변동 한도 준수 | `test_pricing.py::test_11*` (5,000회 무작위 + 실제 갱신) |
| 12 | 가격 갱신 재실행 멱등 | `test_12_*` |
| 13 | 오래된 데이터를 최신으로 표시 안 함 | `test_13_*`, `test_never_collected_real_channel_*`, 프론트 `ui.test.tsx` |
| 14 | 타인 계좌·주문 열람/수정 불가 | `test_security.py::test_14*` |
| 15 | 일반 사용자 관리자 API 불가 | `test_15_*` (관리자 API 15종) |
| 16 | 취소 남발·자전거래 탐지 | `test_risk.py::test_16*` |
| 17 | DB 재시작 후 거래 기록 보존 | `test_persistence.py::test_17_*` |
| 18 | 가격·시간 우선 | `test_18_*` (매수·매도 양방향) |
| 19 | 상대 없는 시장가 → 허위 체결 없음 | `test_19_*`, `test_19b_*` |
| 20 | 수집 실패 시 가격·상태 안전 | `test_20_*`, `test_deleted_channel_*`, `test_price_engine_failure_*` |

## 4. 핵심 설계

### 4.1 경제 규칙
* 금액은 모두 **정수 크레딧(BIGINT)**, 평균단가·실현손익만 NUMERIC. 부동소수점 미사용.
* 가입 시 1,000,000 크레딧 1회 지급: 계좌 행 잠금 + `ledger_entries(user_id) WHERE entry_type='signup_grant'` **부분 UNIQUE 인덱스**로 이중 방어.
* 수수료 0.2%(매수·매도 각각), 서버 계산, **1크레딧 단위 올림**(주문 분할로 회피 불가). 수수료는 경제에서 소멸.
* 크레딧은 가입 지급과 사유가 기록된 관리자 조정으로만 생성됩니다. 매수·매도 대금은 정확히 상쇄(제로섬) — 대사 작업이 매일 검증.
* 미실현 손익은 현금에 더하지 않으며, 총자산(평가)과 현금을 구분해 표시합니다.
* `ledger_entries`, `trades`, `audit_logs` 는 **DB 트리거로 UPDATE/DELETE 차단**(추가 전용). 정정은 반대 방향 새 항목으로만. 관리자 API에도 수정/삭제 경로 없음(테스트로 검증).

### 4.2 주문·체결 엔진
* 지정가/시장가, **가격 우선 → 시간 우선**, 부분 체결, 잔량은 주문장 유지.
* 매수는 현금, 매도는 주식을 **예약**. 매수 예약액 = `수량 × (가격 + ceil(가격×0.2%))` — 어떤 분할 체결에서도 수수료까지 덮는 것이 수학적으로 보장됩니다(`ceil(q·x) ≤ q·ceil(x)`).
* 잠금 순서: **종목 행 → 주문장 주문 → 참여 계좌(user_id 오름차순)** — 한 트랜잭션에서 현금·포지션·수수료·원장·체결을 함께 기록. 데드락/직렬화 오류는 자동 재시도.
* **Idempotency key**: `(user_id, idempotency_key)` UNIQUE + 요청 해시. 동일 키 재전송 → 기존 결과 반환, 다른 내용 → 409.
* 시장가: 기준가 ±5% 가격 보호, 범위 밖 잔량은 취소(IOC). 상대가 없으면 `rejected/NO_LIQUIDITY` — **허위 체결 없음**.
* 지정가 가격 밴드: 기준가 ±15%, **저유동성 종목은 ±7%**.
* 자기 거래 방지(STP), 미체결 주문은 가격에 영향 없음(가격은 체결로만 변경).

### 4.3 유동성 공급 (IPO 마켓메이커)
신규 시장에는 매도자가 없으므로, 상장 시 발행 주식 전량을 `market_maker` **시스템 계정**이 보유합니다. 이 계정은
* 기준가 ±0.5/1.5/3% 에 실제 지정가 주문을 냅니다(주문장에 보이고, 일반 주문과 같은 규칙으로 체결).
* 보유한 주식만 팔고, **판매로 번 현금으로만** 매수 호가를 냅니다 → 크레딧을 만들지 않습니다.
* 랭킹·수급 점수에서 제외됩니다(마켓메이커가 공격자(taker)인 체결은 수급 계산에서 제외).
* 결과: 시장 초기에는 매수 호가가 비어 있을 수 있습니다(마켓메이커 현금 0). 사용자 매수가 쌓이면 매수 호가가 생깁니다.

### 4.4 혼합형 주가 엔진 (`hybrid-v1`)
```
raw = 0.50·fundamental(±4%) + 0.30·flow(±3%) + 0.20·sentiment(±2%)
daily = clamp(raw, −8%, +8%)       next = max(100, round(base × (1 + daily)))
```
* **base = 전일 공식 기준가**(종가). 장중 체결가는 조작 가능성이 있어 수급 점수로만 반영합니다.
* **실적**: 구독자 7일 증가율 40% + 조회 수 7일 증가율 40% + 추세(7일 vs 28일 주평균) 20%. 시장 중앙값 대비 상대 성장(상장 3개 이상일 때)으로 정규화, ±50% 윈저라이즈, tanh 스케일. 7일 기록만 있으면 신뢰도 0.5, 없으면 0. 구독자 비공개 채널은 조회 수로 재가중하고 신뢰도 ×0.8.
* **수급**: `D = (매수 체결대금 − 매도 체결대금) / (합 + ε)`, 공격자 방향 기준, 이전 갱신 이후 **겹치지 않는 구간**만(중복 집계 없음), 미체결 주문 제외, 서로 다른 거래자 5명 미만이면 신뢰도 비례 축소(1인 펌핑 방지).
* **심리**: 공식 데이터에서 규칙으로만 생성한 이벤트 — YouTube 인기 급상승 차트 진입(+0.5, 3일), 구독자 성장 가속(+0.3, 7일), 구독자 감소(−0.3), 2주 연속 조회 증가 둔화(−0.4). `deduplication_key` 로 중복 반영 방지, 만료 적용. 사용자 게시물·소문은 반영하지 않습니다.
* **멱등성**: `price_updates(asset_id, effective_date)` UNIQUE + 작업 큐 dedup key. 사용 데이터·각 요소 값·공식 버전·구간을 `inputs_json`에 저장하고 감사 로그에 기록.
* **안전장치**: 데이터가 48시간 이상 오래됐거나 채널을 찾을 수 없으면 가격을 유지(`skipped`)하고 **자동 거래 정지**. 신선한 데이터가 들어오면 자동 정지만 자동 해제(운영자 정지는 유지). 계산 오류 시 기존 가격 유지(`failed` 기록).

### 4.5 악용 방지
| 위험 | 대응 |
| --- | --- |
| 다중 계정·지급금 악용 | IP별 가입 속도 제한, IP는 HMAC 해시로만 저장, 같은 네트워크 다수 가입은 `needs_verification` 표시(차단하지 않음), 선택적 Turnstile CAPTCHA |
| 자전거래 | 같은 두 계정 간 24시간 반복 체결(≥5: medium), 연관 계정(같은 가입/로그인 네트워크) 간 반복(≥3: high, ≥10: critical) |
| 스푸핑 | 큰 주문을 60초 안에 미체결 취소하는 패턴(1시간 3회) |
| 취소 남발 | 1시간 20건 이상 & 취소율 90% 이상 |
| 거래량 급증·집중 | 7일 평균 대비 10배 급증, 한 계정이 급증 거래의 70% 이상 참여 |
| 대응 단계 | 7일 미검토 점수 합 기준: 낮음(기록) → 중간(주문 속도 제한 10/분) → 높음(신규 주문 보류·검토 요청) → critical(계정 `restricted`, 이의 제기 가능). **자동 영구 정지 없음**, 모든 이벤트에 사유·근거(JSON) 저장, 사용자는 `/portfolio`에서 사유 확인 및 이의 제기 |

### 4.6 데이터베이스
요구 테이블 전부 + `sessions`, `price_updates`, `jobs`, `rate_limits`, `reconciliation_runs`, `announcements`, `reports`, `appeals`, `api_quota_usage`.
제약: UNIQUE(`normalized_username`, `youtube_channel_id`, `symbol`, `(user_id, idempotency_key)`, `trade_uid`), CHECK(현금·예약 ≥ 0, 예약 ≤ 잔액/보유, 수량 > 0, 잔량 ≤ 수량, 가격 > 0, 수수료 ≥ 0, buyer ≠ seller, 상태 enum), 모든 FK.
인덱스: 주문장 `(asset_id, side, status, limit_price, id)`, 체결 `(asset_id, executed_at)`/`(buyer_id|seller_id, executed_at)`, 원장 `(user_id, created_at)`/`(reference_type, reference_id)`, 지표 `(channel_id, metric_type, observed_at)`, 작업 `(status, run_at)` 등.

**데이터 보존 정책(권장)**: 원장·체결·감사 로그·가격 산정 기록은 영구 보존(삭제 금지). 주문은 영구 보존(체결 근거). 세션은 만료 후 30일, `rate_limits` 는 1일, 완료된 `jobs` 는 90일 후 정리 가능. 채널 지표는 YouTube API 정책에 따라 보관 기간을 관리(아래 참조). 정리 작업 자체는 아직 미구현입니다.

## 5. 실제 YouTube API 설정 (무료)

1. https://console.cloud.google.com 에서 프로젝트 생성 (결제 수단 등록 불필요).
2. *API 및 서비스 → 라이브러리* 에서 **YouTube Data API v3** 사용 설정.
3. *사용자 인증 정보 → API 키* 생성 → *키 제한*: API = YouTube Data API v3, 애플리케이션 = 서버 IP.
4. `.env` 에 `YEX_YOUTUBE_API_KEY=...` 설정 후 재시작.
5. 실제 채널 상장:
   ```bash
   docker compose exec api python -m app.cli add-channel UCxxxxxxxxxxxxxxxxxxxxxx "표시 이름"   # 심사 대기로 등록 + 데이터 수집
   docker compose exec api python -m app.cli approve <channel_id> SYMBOL 10000 1000000 --admin admin [--celebrity]
   ```
   또는 관리자 화면 → 상장 관리에서 등록 → (첫 수집 후) 승인. 첫 수집 전에는 거래 정지 상태로 상장됩니다.

* 사용 엔드포인트: `channels.list (part=snippet,statistics)` 1유닛/50채널, `videos.list (chart=mostPopular)` 1유닛/지역. 기본 쿼터 10,000유닛/일이면 충분하며, 사용량은 `api_quota_usage` 로 추적·차단합니다.
* 공식 API가 주는 공개 통계(구독자 수(반올림값), 총 조회 수, 동영상 수)만 사용합니다. 기간별 변화는 직접 수집한 일별 기록으로 계산합니다. 비공개 분석 데이터는 추정하지 않습니다.
* 채널 삭제/정지/접근 제한으로 응답에 없으면 `unavailable` → 자동 거래 정지.
* **약관**: YouTube API Services Terms 및 Developer Policies 는 API 데이터의 저장·갱신 기간을 제한합니다(통계 데이터 주기적 갱신·삭제 의무 등). 운영 전 최신 정책을 확인하고 보관 기간을 설정하세요. 이 서비스는 YouTube 와 제휴하지 않았습니다.

## 6. 데모 데이터 vs 실제 데이터
* 채널의 `data_source` 가 `demo` 또는 `youtube_api` 로 고정되며, 지표 행도 같은 `source` 로 저장됩니다. 두 공급자는 서로의 채널을 갱신하지 않습니다(테스트로 검증).
* 데모 채널 ID는 `DEMO…` 로 시작, 이름은 `[DEMO]` 접두사, UI 곳곳에 **DEMO 데이터** 배지와 필터가 표시됩니다.
* API 키가 없으면 실제 채널은 갱신되지 않고, 48시간 후 오래된 데이터로 표시·거래 정지됩니다(최신 데이터처럼 표시하지 않음).

## 7. API 요약
인증 `POST /api/auth/register|login|logout`, `GET /api/auth/me` ·
종목 `GET /api/assets`, `/api/assets/{id}`, `/history`, `/trades`, `/orderbook`, `/metrics`, `/events`, `/price-updates`, `GET /api/market/summary` ·
계좌 `GET /api/account`, `/account/positions`, `/account/ledger`, `/account/risk` ·
주문 `POST /api/orders`, `GET /api/orders`, `GET /api/orders/{id}`, `POST /api/orders/{id}/cancel` ·
체결 `GET /api/trades` · 랭킹 `GET /api/leaderboard` · 상태 `GET /api/system/status`, `GET /api/announcements` ·
신고/이의 `POST /api/reports`, `POST /api/appeals` ·
관리자 `GET /api/admin/risk-events|audit-logs|channels|jobs|price-updates|reconciliation|reports|appeals`, `POST /api/admin/assets/{id}/halt|resume`, `POST /api/admin/channels`, `/channels/{id}/approve|reject`, `/risk-events/{id}/review`, `/users/{id}/status`, `/adjustments`, `/announcements`, `/jobs/{type}/run`, `/reconciliation/run`, `/reports/{id}/resolve`, `/appeals/{id}/resolve`.
오류 형식: `{"error": {"code", "message", "details", "request_id"}}`.

## 8. 아직 구현되지 않은 기능 (정직한 목록)
* **이메일 인증**: 이메일 발송 서비스가 필요해 미구현. 계정 상태 플래그(`needs_verification`)만 존재.
* **CAPTCHA 프론트엔드 위젯**: 서버 측 Turnstile 검증은 구현, 가입 화면 위젯은 미연결(설정 시 위험 가입은 `CAPTCHA_REQUIRED` 로 거부됨).
* **실시간 푸시**: WebSocket 대신 폴링(3~15초).
* **카테고리별 정규화**: 실적 점수는 시장 전체 중앙값 대비로만 정규화.
* **데이터 보존 정리 작업**(세션·rate_limits·jobs 정리, YouTube 정책 기반 지표 만료) 미구현.
* **관리자 2FA**, 세션 회전, 관리자 권한 세분화(역할은 user/admin 두 단계).
* 랭킹 페이지네이션/캐싱, 대규모 트래픽용 Redis·작업 큐 분리(현재 PostgreSQL 기반으로 충분한 규모 가정).
* 일일 가격 갱신 시 차트는 체결/기준가 봉만 표시(상장 이전 가격 이력 없음).

## 9. 운영 환경 추가 보안 조치
[`docs/SECURITY.md`](docs/SECURITY.md), [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) 참고. 요약:
HTTPS 리버스 프록시(Caddy+Let's Encrypt 무료) · `YEX_ENV=production`(불안전 설정 시 기동 거부) · `YEX_COOKIE_SECURE=true` · `YEX_TRUSTED_PROXY_HOPS=1` · DB·API 포트 비공개 · 앱 DB 역할의 원장/체결/감사 테이블 UPDATE/DELETE/TRUNCATE 권한 회수 · 비밀 값 무작위 생성 및 비밀 관리 · API 키 IP/API 제한 · 백업·복구 리허설 · 실패 작업/대사 불일치/critical 위험 알림 · 관리자 2FA · 정기 의존성 감사.
