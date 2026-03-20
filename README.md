# Hotdeal Finder

키워드 기반으로 핫딜 게시판을 주기적으로 수집하고, 조건에 맞는 글을 디스코드 채널로 알리는 봇 프로젝트입니다.

현재 지원 수집 대상:
- 루리웹 핫딜 게시판
- FMKorea 핫딜
- 아카라이브 핫딜

## 1. 주요 기능

- 디스코드 슬래시 명령어로 모니터링 키워드 등록/조회/삭제
- 5분 주기 자동 크롤링
- 키워드 + 유의어(aliases) 매칭
- 제외어(exclude words) 필터링
- 제목 내 가격 추출 후 목표 가격(target price) 기준 필터링
- URL 기준 중복 알림 차단
- 키워드별 역대 최저가 캐싱 및 최저가 갱신 알림 강조

## 2. 프로젝트 구조

```text
hotdeal_finder/
├── bot.py         # 디스코드 봇 엔트리포인트, 슬래시 명령어, 스케줄 태스크
├── monitor.py     # 크롤링 결과 필터링, 중복 체크, DB 저장, 알림 전송
├── crawler.py     # 사이트별 크롤러(루리웹/펨코/아카라이브), 가격 추출
├── models.py      # SQLAlchemy 모델(Keyword, DealHistory)
├── database.py    # DB 연결/세션 설정
├── init_db.py     # 테이블 초기화 스크립트
├── requirements.txt
└── hotdeal.db     # 기본 SQLite DB 파일
```

## 3. 동작 개념

1. 사용자가 디스코드에서 키워드를 등록합니다.
2. 봇이 5분마다 3개 커뮤니티 핫딜 게시판을 수집합니다.
3. 각 게시글에 대해 다음 순서로 필터링합니다.
- 제외어 포함 여부
- 키워드/유의어 매칭 여부
- 목표 가격 초과 여부
- 이미 알림 보낸 URL인지 여부
4. 통과한 항목은 `deal_histories`에 저장하고 디스코드 임베드 알림을 전송합니다.
5. 기존 최저가보다 낮으면 최저가 갱신으로 표시합니다.

## 4. 빠른 시작

### 4.1 환경 준비

- Python 3.11+ 권장
- `uv` 설치 필요
- 디스코드 봇 토큰 및 알림 채널 ID 필요

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

`playwright`를 처음 쓰는 환경이라면 브라우저 설치가 필요합니다.

```bash
uv run playwright install chromium
```

### 4.2 환경 변수 설정

`.env.example`를 참고해 `.env` 파일을 생성합니다.

```env
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_ALERT_CHANNEL_ID=your_channel_id_here
```

선택:
- `DATABASE_URL` (미설정 시 `sqlite:///./hotdeal.db` 사용)

### 4.3 DB 초기화

```bash
uv run python init_db.py
```

### 4.4 봇 실행

```bash
uv run python bot.py
```

정상 실행 시:
- 로그인 성공 로그 출력
- 슬래시 명령어 동기화
- 5분 주기 크롤링 사이클 시작

## 5. 디스코드 명령어

- `/알림등록`
- `name`: 모니터링 키워드
- `aliases`: 쉼표 구분 유의어(선택)
- `exclude`: 쉼표 구분 제외어(선택)

- `/알림목록`
- 현재 등록된 키워드 목록 확인

- `/알림삭제`
- 등록된 키워드 제거

## 6. 데이터 모델 요약

### `keywords`
- `id`, `name`
- `aliases`, `exclude_words`, `target_price`
- `is_active`, `created_at`
- `current_lowest_price`, `lowest_price_url`

### `deal_histories`
- `id`, `keyword_id`
- `platform`, `title`, `url`, `extracted_price`
- `is_alert_sent`, `collected_at`

## 7. 테스트/디버깅 파일

- `test_monitor.py`: `run_crawling_cycle`를 목 객체로 실행해 알림 동작 점검
- `test_arca.py`: 아카라이브 HTML 파싱 구조 확인용 스크립트

## 8. 현재 한계와 개선 포인트

- 사이트 HTML 구조가 바뀌면 크롤러가 깨질 수 있음
- 사이트별 에러/타임아웃 재시도 정책이 단순함
- 키워드 정규화(형태소/오타/영문 변형) 기능 없음
- 가격 추출이 제목 기반 정규식이라 정확도 한계가 있음
- 운영 환경에서는 로그/모니터링/알림 실패 재처리 체계 보강 권장

## 9. 라이선스

별도 라이선스 파일이 없으므로, 필요 시 `LICENSE`를 추가해 명시적으로 정의하세요.
