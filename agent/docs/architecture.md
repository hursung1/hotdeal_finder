# Architecture

## 개요
- 이 프로젝트는 디스코드 봇 프로세스 하나가 크롤링, 키워드 관리, 알림 전송, 검색 요청 처리를 함께 수행하는 단일 프로세스 구조다.
- 별도 웹 서버나 API 서버는 없으며, 사용자 인터페이스는 디스코드 슬래시 명령어가 담당한다.
- 데이터 저장소는 SQLAlchemy 기반 DB 계층을 사용하며, 기본값은 로컬 SQLite `hotdeal.db`다.
- 수집 대상은 뽐뿌 게시판군, 루리웹 핫딜, FMKorea 핫딜, 아카라이브 핫딜이다.

## 구성요소
- `bot.py`
  - 디스코드 봇 엔트리포인트다.
  - `.env`에서 토큰과 알림 채널을 읽는다.
  - 슬래시 명령어(`/알림등록`, `/알림목록`, `/알림삭제`, `/핫딜검색`)를 등록한다.
  - 10분 주기 백그라운드 크롤링 태스크를 실행한다.
- `monitor.py`
  - 정기 크롤링 사이클의 애플리케이션 서비스 계층이다.
  - 크롤러 결과를 모으고, 활성 키워드 기준으로 제외어/포함어/목표가/중복 여부를 필터링한다.
  - 디스코드 전송 성공 후에만 `DealHistory`를 저장해 전송 실패 항목이 영구 스킵되지 않도록 한다.
  - 최저가 갱신 여부를 계산하고 `Keyword.current_lowest_price`를 갱신한다.
- `crawler.py`
  - 사이트별 수집, 파싱, URL 정규화, 가격 추출을 담당한다.
  - `requests`, `cloudscraper`, `playwright`를 상황에 따라 사용한다.
  - 최신 글 수집용 함수와 최근 N일 검색용 함수가 함께 들어 있다.
  - 뽐뿌 최근 검색 결과는 메모리 캐시와 fingerprint 기반 변경 감지로 관리한다.
  - 뽐뿌 상세 상품명/가격/상품 URL 보강은 OpenClaw gateway로 Codex OAuth 기반 GPT 모델을 호출한다.
- `models.py`
  - SQLAlchemy 모델 정의 파일이다.
  - `Keyword`, `DealHistory`, `HotdealPriceRecord` 테이블을 정의한다.
- `database.py`
  - DB engine, session factory, declarative base를 정의한다.
  - `DATABASE_URL`이 없으면 `sqlite:///./hotdeal.db`를 사용한다.
- `backfill_monthly_prices.py`
  - 봇 실행 흐름과 독립적인 배치 스크립트다.
  - 최근 1개월 가격 기록을 수집해 `HotdealPriceRecord`에 저장한다.

## 정기 알림 흐름
```text
Discord bot ready
  -> crawler_task (10분 주기)
  -> monitor.run_crawling_cycle
  -> crawler.parse_ppomppu / parse_ruliweb / parse_fmkorea / parse_arcalive
  -> active Keyword 조회
  -> 제외어 필터
  -> 키워드/유의어 매칭
  -> 목표가 필터
  -> DealHistory URL 중복 확인
  -> Discord embed 전송
  -> DealHistory 저장
  -> Keyword 최저가 캐시 갱신
```

## 각 명령어 기능
- `/핫딜검색`
  - 사용자로부터 검색하고자 하는 상품 쿼리를 받는다.
  - 해당 쿼리를 이용하여 핫딜게시판 게시글 중 일치하는 것을 찾는다.
    - 범위는 최근 15일 이내의 게시글이다.
    - 일치 여부는 공백 제거 후 포함 여부로 비교한다.
    - 뽐뿌의 경우 OpenClaw gateway로 서빙되는 GPT 기반 파싱으로 상품명, 가격, 상품 url을 추출하여 판단한다.
  - 가격이 저렴한 순으로, 5가지 상품을 답변으로 제공한다.
  - 이 때, (1) 상품명, (2) 상품의 가격, (3) 해당 게시글의 url, (4) 해당 상품 판매처의 url을 제공해야 한다.
- `/알림등록`
  - 사용자로부터 다음 정보를 받는다:
    - 핫딜 알림을 받고 싶은 상품명
    - 해당 상품의 동의어(유의어)
    - 검색 시 제외할 단어
  - 입력받은 정보를 검색어 DB에 추가한다.
  - 등록된 검색어는 bot의 주기적인 핫딜게시판 확인을 통해 새로운 핫딜이 떴을 때 사용자에게 알림이 제공되어야 한다.
- `/알림목록`
  - 사용자에게 현재 등록되어 있는 알림 대상 상품이 어떤것이 있는지 답변으로 제공한다.
- `/알림삭제`
  - 사용자로부터 더 이상 알림을 받고 싶지 않은 상품명을 받는다.
  - 해당 상품명을 갖는 entry를 검색어 DB에서 삭제한다.


## 데이터 모델
- `Keyword`
  - 사용자가 등록한 모니터링 조건이다.
  - `name`, `aliases`, `exclude_words`, `target_price`, `is_active`를 가진다.
  - `current_lowest_price`, `lowest_price_url`로 키워드별 최저가 상태를 캐싱한다.
- `DealHistory`
  - 실제 알림 전송이 완료된 게시글 이력이다.
  - URL은 unique이며, 정기 알림 중복 방지 기준으로 사용된다.
  - `Keyword`와 1:N 관계다.
- `HotdealPriceRecord`
  - 월간 가격 백필용 가격 기록이다.
  - 정기 알림 이력과 분리되어 있으며, 플랫폼/제목/URL/등록가/작성시각/수집 페이지를 저장한다.

## 외부 의존성
- Discord API
  - 봇 로그인, 슬래시 명령어, 알림 embed 전송에 사용한다.
- 핫딜 커뮤니티 웹 페이지
  - 뽐뿌, 루리웹, FMKorea, 아카라이브의 HTML 구조에 의존한다.
- Playwright Chromium
  - FMKorea처럼 정적 HTTP 수집이 실패할 수 있는 사이트의 브라우저 기반 fallback에 사용한다.
- OpenClaw gateway
  - 뽐뿌 검색 결과의 상품 정보 보강에 사용한다.
  - 기본 호출 방식은 `openclaw infer model run --gateway --json`이다.
  - OpenClaw gateway는 기본적으로 `127.0.0.1:18789`에서 동작하며, Codex OAuth 기반 `openai-codex` provider를 사용한다.
  - 기본 사용 모델은 `openai-codex/gpt-5.5`이다.
    - 만약 사용자가 특정 모델을 사용할 것을 요구하면 그 모델을 사용한다.
    - 사용자가 사용할 것을 요구한 모델이 없으면 기본 모델을 사용한다.

## 설정값
- `DISCORD_BOT_TOKEN`: 디스코드 봇 토큰
- `DISCORD_ALERT_CHANNEL_ID`: 정기 알림을 보낼 채널 ID
- `DATABASE_URL`: DB 연결 문자열, 미설정 시 SQLite 사용
- `FMKOREA_COOKIE`: FMKorea 브라우저 수집 시 선택적으로 주입하는 쿠키
- `OPENCLAW_COMMAND`: OpenClaw CLI 실행 파일 경로
- `OPENCLAW_PPOMPPU_MODEL`: 뽐뿌 파싱에 사용할 OpenClaw 모델명
- `OPENCLAW_PPOMPPU_TIMEOUT_SECONDS`: OpenClaw 요청 timeout
- `PPOMPPU_OPENCLAW_PARSE_BUDGET`: `/핫딜검색`에서 OpenClaw로 보강할 뽐뿌 후보 수 기준

## 동시성 및 상태 관리
- 디스코드 봇은 asyncio 이벤트 루프 위에서 동작한다.
- 정기 크롤링은 `discord.ext.tasks.loop`로 실행된다.
- `/핫딜검색`은 `SEARCH_SEMAPHORE`로 동시 실행을 1개로 제한한다.
- FMKorea Playwright 수집은 `FMKOREA_PLAYWRIGHT_SEMAPHORE`로 브라우저 동시 실행을 제한한다.
- 뽐뿌 최근 검색 캐시는 프로세스 메모리에 저장되며, `threading.Lock`으로 보호한다.
- 뽐뿌 캐시 갱신과 OpenClaw 파싱 일부는 `asyncio.to_thread()`로 이벤트 루프 블로킹을 줄인다.

## 장애 처리
- 정기 크롤링은 사이트별 try/except로 일부 사이트 실패가 전체 사이클 실패로 번지지 않게 한다.
- 알림 처리 중 개별 게시글/키워드 조합에서 예외가 나면 DB rollback 후 다음 항목으로 진행한다.
- 디스코드 전송 성공 이후 DB에 저장하므로, 전송 실패한 게시글은 다음 사이클에서 다시 시도될 수 있다.
- URL 정규화와 호환용 dedupe 후보를 사용해 과거 저장 URL 형식과 현재 canonical URL 차이를 흡수한다.
