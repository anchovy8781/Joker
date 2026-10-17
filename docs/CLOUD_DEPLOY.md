# 컴퓨터·휴대폰 없이 24시간 운영하기 (Supabase + Render, 무료)

```
[앱/브라우저] ──https──▶ Render 무료 웹 서비스 (웹 + API, 고정 주소 https://youtube-exchange.onrender.com)
                               ▲                 │
          매 1분 예약 작업 호출 │                 ▼
                   Supabase pg_cron ──── Supabase PostgreSQL (데이터베이스)
```

- **Supabase**: 데이터베이스 + 1분마다 예약 작업 실행(pg_cron). 무료.
- **Render**: 웹사이트와 거래 API 실행. 무료. Supabase가 1분마다 호출하므로 잠들지 않습니다.
- 둘 다 GitHub 계정으로 가입할 수 있고, 결제 정보 없이 무료 플랜을 쓸 수 있습니다(가입 시 카드를 요구하면 진행하지 말고 알려주세요).
- 앱은 이 고정 주소로 **자동 접속**합니다. 첫 화면에서 주소를 입력할 필요가 없습니다.

소요 시간: 약 20분

---

## 1단계. Supabase 데이터베이스 만들기

1. https://supabase.com → **Start your project** → GitHub로 로그인
2. **New project**
   - Name: `youtube-exchange`
   - Database Password: **Generate a password** → 복사해서 안전한 곳에 보관
   - Region: **Northeast Asia (Seoul)**
   - Plan: Free → **Create new project** (1~2분 대기)
3. 상단 **Connect** 버튼 → **Connection String** 탭 → **Session pooler** 항목의 주소 복사
   ```
   postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres
   ```
4. 이 주소를 아래처럼 고칩니다 (메모장에서):
   - 맨 앞 `postgresql://` → **`postgresql+psycopg://`**
   - `[YOUR-PASSWORD]` → 2번에서 만든 비밀번호
   ```
   postgresql+psycopg://postgres.abcdefgh:비밀번호@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres
   ```
   > "Direct connection"이 아니라 **Session pooler** 주소여야 합니다 (Render는 IPv6 직접 연결을 못 씁니다).

## 2단계. Render에 서버 올리기 (버튼 한 번)

1. https://render.com → **Get Started** → GitHub로 로그인
2. 이 링크를 엽니다: **https://render.com/deploy?repo=https://github.com/anchovy8781/Joker**
   (GitHub 저장소 접근 허용을 물어보면 허용)
3. 입력 칸을 채웁니다:
   | 항목 | 값 |
   | --- | --- |
   | `YEX_DATABASE_URL` | 1단계 4번에서 고친 주소 |
   | `YEX_BOOTSTRAP_ADMIN_USERNAME` | 관리자 아이디 (영문/숫자 3~20자) |
   | `YEX_BOOTSTRAP_ADMIN_PASSWORD` | 관리자 비밀번호 (10자 이상) |
   | `YEX_YOUTUBE_API_KEY` | 유튜브 API 키 (없으면 비워두기) |
   나머지(`YEX_TICK_SECRET` 등)는 자동으로 만들어집니다.
4. **Apply** → 첫 빌드는 5~10분 걸립니다. 서비스 화면에 **Live** 가 뜨면 완료.
5. 서비스 화면 위쪽의 주소를 확인합니다: `https://youtube-exchange.onrender.com`
   - 이 이름이 이미 사용 중이라 **다른 주소**(예: `youtube-exchange-ab12.onrender.com`)가 나왔다면, 그 주소를 Claude에게 알려주세요. 앱이 그 주소로 자동 접속하도록 등록해 드립니다.
6. 브라우저로 그 주소에 접속 → 관리자 아이디로 로그인되면 성공. (데모 종목 8개가 자동 상장됩니다)

## 3단계. 1분 예약 작업 연결 (Supabase)

1. Render 서비스 → **Environment** → `YEX_TICK_SECRET` 값의 눈 아이콘 → 값 복사
2. Supabase → 왼쪽 메뉴 **SQL Editor** → **New query**
3. 저장소의 [`deploy/supabase/setup.sql`](../deploy/supabase/setup.sql) 내용을 붙여넣고 두 곳을 바꿉니다:
   - `<APP_URL>` → `https://youtube-exchange.onrender.com` (2단계의 주소, 끝에 / 없이)
   - `<TICK_SECRET>` → 1번에서 복사한 값
4. **Run** → 끝.
5. 3분 뒤 확인 (같은 SQL Editor에서):
   ```sql
   select status_code, created from net._http_response order by created desc limit 5;
   ```
   `200` 이 보이면 정상입니다. 이제 가격 갱신, 장 시작 시 예약 주문 처리, 자동 매매, 원장 대사가 자동으로 돌아갑니다.

## 4단계. 앱

[Android APK](https://github.com/anchovy8781/Joker/actions/workflows/android-apk.yml)를 설치하고 열면 **바로 접속**됩니다. 주소 입력 화면은 나오지 않습니다.

---

## 무료 플랜 한도와 주의점
| 항목 | 무료 한도 | 이 구성에서 |
| --- | --- | --- |
| Render 실행 시간 | 월 750시간 | 24시간×31일 = 744시간 → **서비스 1개는 딱 맞음** (다른 무료 서비스를 같이 돌리면 부족) |
| Render 메모리 | 512MB | 실제 사용 약 120MB (테스트 결과) |
| Render CPU | 0.1 CPU | 사용자가 많아지면 느려질 수 있음 |
| Supabase DB 용량 | 500MB | 거래 수십만 건 수준까지 충분 |
| Supabase 일시정지 | 7일간 활동 없으면 정지 | 1분마다 작업이 돌아서 정지되지 않음 |

- 코드가 GitHub에 올라가면 Render가 **자동으로 다시 배포**합니다(재배포 중 1~3분 접속 불가).
- 백업: Supabase → Database → Backups (무료 플랜은 매일 자동 백업이 제한적이므로, 중요하면 가끔 `pg_dump` 로 받아두세요).
- 보안: 데이터베이스의 거래 테이블은 Supabase 공개 API에서 접근할 수 없도록 잠겨 있습니다(자동 적용). Supabase의 `anon key`/`service_role key`는 이 앱에서 쓰지 않으니 공유하지 마세요.

## 문제 해결
| 증상 | 확인할 것 |
| --- | --- |
| Render 로그에 `Refusing to start in production` | 표시된 환경변수를 고치기 (예: DB 주소에 `+psycopg` 누락) |
| Render 로그에 `password authentication failed` | DB 비밀번호 / Session pooler 주소 확인 |
| `net._http_response` 에 404 | `<TICK_SECRET>` 값이 Render와 다름 |
| `net._http_response` 가 비어 있음 | `<APP_URL>` 오타, 또는 Render 서비스가 아직 배포 중 |
