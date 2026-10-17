# 남는 안드로이드 폰으로 거래 서버 24시간 운영하기

컴퓨터를 꺼도 서비스가 계속되도록, 집에 남는 안드로이드 폰을 서버로 씁니다.
비용은 0원(전기요금 정도)이고 루팅·결제·도메인이 필요 없습니다.

```
[서버 폰] Termux ─ PostgreSQL(데이터베이스)
          └ Ubuntu ─ API · 워커(가격 갱신·예약 주문) · 웹 · Cloudflare 터널(공개 https 주소)
                     └ 주소가 바뀌면 GitHub에 자동 게시
[사용자 폰] 앱 실행 → GitHub에서 주소 확인 → 자동 접속 (주소 입력 필요 없음)
```

## 준비물
- 안드로이드 7 이상, **메모리 3GB 이상**, 저장공간 여유 6GB 이상인 폰 (최근 5년 내 폰이면 대부분 OK)
- 충전기와 집 Wi-Fi (서버 폰은 항상 충전기에 꽂아 두세요)

## 1. 앱 설치 (서버 폰)
**F-Droid**(https://f-droid.org)를 설치한 뒤, F-Droid에서 아래 두 앱을 설치하세요.
- **Termux**
- **Termux:Boot** (재부팅 후 자동 시작용). 설치 후 **한 번 열어주세요**.

> 플레이스토어의 Termux는 오래된 버전이라 동작하지 않습니다. 반드시 F-Droid 버전을 쓰세요.

## 2. 절전 해제 (중요)
안드로이드는 오래 켜진 앱을 꺼버립니다. 서버 폰에서:
- 설정 → 애플리케이션 → **Termux** → 배터리 → **제한 없음(최적화 안 함)**
- Termux:Boot도 같은 설정
- (삼성) 설정 → 배터리 → 백그라운드 사용 제한 → **절전 예외 앱**에 Termux 추가

## 3. 서버 설치 (명령어 한 줄)
Termux를 열고 아래를 붙여넣은 뒤 엔터:

```bash
curl -fsSL https://raw.githubusercontent.com/anchovy8781/Joker/HEAD/deploy/phone/termux-install.sh | bash
```

처음에는 **20~40분** 걸립니다(Ubuntu, Python, Node 설치와 웹 빌드). 중간에 화면이 꺼지지 않게 해 주세요.

## 4. 관리자 만들기
```bash
~/bin/ytx-admin create-admin 내아이디
~/bin/ytx-admin seed-demo --approve-by 내아이디     # (선택) 데모 종목 8개
```
YouTube API 키는 `proot-distro login ubuntu` 후 `/opt/ytx-data/env` 파일의 `YEX_YOUTUBE_API_KEY=` 에 넣으세요.

## 5. 앱 자동 연결 설정 (1회)
서버 폰의 공개 주소가 바뀌면 GitHub 저장소에 자동으로 올리고, 앱은 그 주소로 바로 접속합니다.
이를 위해 **이 저장소에만 쓸 수 있는 GitHub 토큰**을 한 번 만듭니다.

1. https://github.com/settings/personal-access-tokens/new 접속
2. Token name: `ytx-phone-server`, Expiration: 원하는 기간(최대 1년, 만료 전에 새로 발급)
3. **Repository access → Only select repositories → `anchovy8781/Joker`**
4. **Permissions → Repository permissions → Contents: Read and write** (다른 권한은 건드리지 마세요)
5. Generate token → 토큰 복사
6. 서버 폰 Termux에서:
   ```bash
   ~/bin/ytx-token
   ```
   붙여넣기(화면에 안 보여요) → 엔터

> 토큰은 서버 폰의 `/opt/ytx-data/env`에만 저장되고(권한 600) 저장소에는 올라가지 않습니다.
> 저장소에는 `server-address` 브랜치의 `server.json`(현재 주소)만 올라갑니다.

## 6. 서버 시작
```bash
~/bin/ytx-start
```
"API ● 정상 / 웹 ● 정상 / 공개 주소 https://…trycloudflare.com" 이 나오면 성공입니다.
이제 사용자 폰에서 **앱을 열기만 하면 자동으로 접속**됩니다.

| 명령 | 하는 일 |
| --- | --- |
| `~/bin/ytx-start` | 서버 시작 (재부팅 후에는 Termux:Boot가 자동 실행) |
| `~/bin/ytx-status` | 상태와 현재 공개 주소 확인 |
| `~/bin/ytx-stop` | 서버 중지 |
| `~/bin/ytx-update` | 최신 코드로 업데이트 (중지 → 업데이트 → `ytx-start`) |
| `~/bin/ytx-admin …` | 관리 명령 (관리자 생성, 상장 승인 등) |

각 프로그램이 죽으면 5초 뒤 자동으로 다시 켜집니다. 로그: `/opt/ytx-data/logs/` (Ubuntu 안).

## 고정 주소를 원할 때 (선택, 무료)
Cloudflare 임시 주소는 서버를 재시작할 때 바뀝니다(앱은 자동으로 새 주소를 찾아갑니다).
주소 자체를 고정하려면 **무료 ngrok 계정**(카드 불필요)의 고정 도메인을 쓰세요.
1. https://dashboard.ngrok.com 가입 → **Domains**에서 무료 도메인 1개 받기 (예: `abc.ngrok-free.app`)
2. **Your Authtoken** 복사
3. `/opt/ytx-data/env` 에 `NGROK_AUTHTOKEN=…`, `NGROK_DOMAIN=abc.ngrok-free.app` 입력
4. `~/bin/ytx-update` → `~/bin/ytx-start`

## 알아둘 점
- 서버 폰이 꺼지거나 Wi-Fi가 끊기면 서비스도 멈춥니다. 다시 켜지면 Termux:Boot가 자동으로 서버를 시작합니다.
- 데이터는 서버 폰에 저장됩니다. 가끔 백업하세요:
  `pg_dump yex > ~/storage/downloads/ytx-backup-$(date +%F).sql` (Termux에서, 처음 한 번 `termux-setup-storage` 필요)
- 동시 접속자가 많아지면(수백 명 이상) 폰 성능이 부족할 수 있습니다. 그때는 클라우드 서버로 옮기세요.
- 서버 폰은 발열이 생길 수 있으니 케이스를 벗기고 통풍되는 곳에 두세요.
