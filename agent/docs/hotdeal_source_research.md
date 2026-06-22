# 핫딜 커뮤니티 수집 조사 메모

작성일: 2026-04-08 (KST)

목적:
- 현재 지원 중인 사이트와 추가 후보 사이트에서 목록 정보를 어떤 방식으로 받아올 수 있는지 기록합니다.
- 실제 이 환경에서 `requests` / `cloudscraper` 기준으로 확인한 결과만 적습니다.

테스트 환경:
- Python `requests`
- `cloudscraper`
- 기본 헤더는 프로젝트의 `crawler.py` `HEADERS` 기준

요약:

| 사이트 | 대표 URL | 1차 접근 방식 | 결과 | 비고 |
| --- | --- | --- | --- | --- |
| 루리웹 | `https://bbs.ruliweb.com/market/board/1020` | `requests` | 성공 | 정적 HTML 테이블 |
| 펨코 | `https://www.fmkorea.com/hotdeal` | `requests` | 성공(간헐 차단 가능) | HTTP 우선, 차단 시 fallback 필요 |
| 아카라이브 | `https://arca.live/b/hotdeal` | `cloudscraper` | 성공 | 일반 `requests`는 403 |
| 뽐뿌 | `https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu` | `requests` | 성공 | 구형 테이블 구조 |
| 딜바다 | `https://www.dealbada.com/bbs/board.php?bo_table=deal_domestic` | `requests` | 성공 | 정적 HTML 테이블 |
| 다모앙 | `https://damoang.net/economy` | `cloudscraper` | 성공(간헐 차단 가능) | 일반 `requests`는 403 |
| 쿨엔조이 | `https://coolenjoy.net/bbs/jirum` | `requests` | 성공 | canonical 도메인 사용 권장, RSS 있음 |
| 클리앙 | `https://www.clien.net/service/board/jirum` | `requests` | 성공 | SSR HTML 목록 확인 |

## 현재 지원 중인 사이트

### 1. 루리웹
- URL: `https://bbs.ruliweb.com/market/board/1020`
- 접근 방식: `requests`로 바로 접근 가능
- 상태:
  - `status=200`
  - 정적 HTML 목록 확인
- 파싱 포인트:
  - row: `tr.table_body`
  - 제목/링크: `td.subject a.deco`
  - 날짜: `td.time`
- 페이지네이션:
  - `https://bbs.ruliweb.com/market/board/1020?page=N`
- 메모:
  - 현재 프로젝트 구현과 동일한 방향으로 유지 가능합니다.

### 2. 펨코
- URL: `https://www.fmkorea.com/hotdeal`
- 접근 방식:
  - `requests`로 접근 가능
  - 다만 시점/세션/IP 상태에 따라 보안 페이지가 뜰 수 있으므로 fallback이 필요합니다.
- 상태:
  - 정상 시 `status=200`, 제목 `핫딜 - 에펨코리아`
  - 간헐적으로 보안 페이지 차단이 발생한 적이 있습니다.
- 파싱 포인트:
  - 제목/링크: `a.hotdeal_var8`
  - 아이템 컨테이너: `li` 상위 요소
  - 날짜: `span.regdate`
- 페이지네이션:
  - `https://www.fmkorea.com/hotdeal?page=N`
- 메모:
  - 현재 프로젝트는 `HTTP fetch -> 실패 시 Playwright fallback` 구조로 가는 것이 맞습니다.

### 3. 아카라이브
- 대표 URL:
  - `https://arca.live/b/hotdeal`
  - `https://arca.live/b/hotdeal?format=rss`
- 접근 방식:
  - 일반 `requests`는 `403`
  - `cloudscraper + 프로젝트 HEADERS` 조합은 성공
- 상태:
  - `status=200`
  - 제목 `핫딜 채널`
- 파싱 포인트:
  - row: `.vrow.hybrid`
  - 제목/링크: `a.title.hybrid-title`, `a.title`
  - 날짜: `time[datetime]`
- 페이지네이션:
  - `https://arca.live/b/hotdeal?p=N`
- 메모:
  - `?format=rss`는 이 환경에서 XML RSS가 아니라 HTML로 내려왔습니다.
  - 실제 구현은 board HTML 기준으로 두는 편이 안전합니다.

## 추가 후보 사이트

### 4. 뽐뿌
- URL: `https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu`
- 접근 방식: `requests`로 바로 접근 가능
- 상태:
  - `status=200`
  - 제목 `뽐뿌 - 뽐뿌게시판`
- 파싱 포인트:
  - row: `tr.baseList`
  - 제목/링크: `a.baseList-title`
  - 날짜: row 내부 4번째 `td.baseList-space` 텍스트 예: `26/04/07`
  - 글 링크 패턴: `view.php?id=ppomppu&page=...&divpage=...&no=...`
- 페이지네이션:
  - `https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu&page=N`
- 메모:
  - 구형 테이블 구조지만 서버 렌더링이라 파싱 난도는 낮습니다.

### 5. 딜바다
- URL: `https://www.dealbada.com/bbs/board.php?bo_table=deal_domestic`
- 접근 방식: `requests`로 바로 접근 가능
- 상태:
  - `status=200`
  - 제목 `국내핫딜 1 페이지 | 딜바다닷컴`
- 파싱 포인트:
  - row: `table.hoverTable tbody tr:not(.bo_notice)`
  - 제목/링크: `td.td_subject a`
  - 날짜: `td.td_date`
  - 글 링크 패턴: `board.php?bo_table=deal_domestic&wr_id=...`
- 페이지네이션:
  - `https://www.dealbada.com/bbs/board.php?bo_table=deal_domestic&page=N`
- 메모:
  - 구조가 안정적이고, 추가 후보 중 구현 난도가 낮은 편입니다.

### 6. 다모앙
- URL: `https://damoang.net/economy`
- 접근 방식:
  - 일반 `requests`는 `403`
  - `cloudscraper + 프로젝트 HEADERS`는 성공
- 상태:
  - 성공 시 `status=200`, 제목 `알뜰구매 | 다모앙 - 종합 커뮤니티`
  - 간헐적으로 Cloudflare 차단이 발생할 수 있습니다.
- 파싱 포인트:
  - row: `a.post-row[href^="/economy/"]`
  - 제목: `.post-title`
  - 댓글 수: `.comment-count`
  - 오늘 날짜 표시: `.date-today`
  - 글 링크 패턴: `/economy/<id>`
- 페이지네이션:
  - `https://damoang.net/economy?page=N`
- 메모:
  - 날짜는 행 텍스트 안에 섞여 있어 추가 정제가 필요합니다.
  - 접근은 되지만 안정성 면에서는 뽐뿌/딜바다보다 한 단계 더 까다롭습니다.

### 7. 쿨엔조이
- 대표 URL:
  - `https://coolenjoy.net/bbs/jirum`
  - `https://coolenjoy.net/bbs/board.php?bo_table=jirum`
- 접근 방식: `requests`로 바로 접근 가능
- 상태:
  - `status=200`
  - 제목 `지름/알뜰정보 페이지 | 쿨엔조이`
- 파싱 포인트:
  - row: `ul.na-table > li`
  - 제목/링크: `a.na-subject`
  - 날짜: row 내부 `i.fa-clock-o`가 들어 있는 `div` 텍스트 예: `등록일 04.01`
  - 글 링크 패턴: `https://coolenjoy.net/bbs/jirum/<id>`
- 페이지네이션:
  - `https://coolenjoy.net/bbs/jirum?page=N`
- 추가 수단:
  - RSS 링크 존재: `https://coolenjoy.net/bbs/rss.php?bo_table=jirum`
- 메모:
  - 처음 테스트한 `new.coolenjoy.net`은 인증서 mismatch가 있었고, canonical 도메인 `coolenjoy.net`은 정상 동작했습니다.

### 8. 클리앙
- URL: `https://www.clien.net/service/board/jirum`
- 접근 방식: `requests`로 바로 접근 가능
- 상태:
  - `status=200`
  - 제목 `클리앙 : 알뜰구매`
- 파싱 포인트:
  - row: `div.list_item.symph_row`
  - 제목/링크: `div.list_title a[href*="/service/board/jirum/"]`
  - 날짜: `span.timestamp`
- 페이지네이션:
  - `https://www.clien.net/service/board/jirum?po=N&od=T31&category=0&groupCd=`
- 메모:
  - HTML 자체는 잘 내려오므로 구현 가능성이 높습니다.
  - 글 row 내부에 부가 링크가 많아 제목 anchor를 좁게 잡는 것이 중요합니다.

## 구현 우선순위 제안

1. 뽐뿌
- 트래픽 가치가 크고 `requests`만으로 접근 가능합니다.
- 구조도 전형적인 게시판형이라 구현 난도가 높지 않습니다.

2. 딜바다
- `requests`로 바로 열리고 테이블 구조가 단순합니다.
- 빠르게 추가하기 좋은 후보입니다.

3. 클리앙
- HTML 접근은 되지만 row 내부 링크가 많아 selector 정제가 필요합니다.
- 그래도 차단 문제는 상대적으로 덜 보였습니다.

4. 쿨엔조이
- 접근은 쉽고 RSS도 있습니다.
- 다만 목록 구조가 현대식 카드형이라 파싱 로직을 조금 더 꼼꼼하게 잡아야 합니다.

5. 다모앙
- `cloudscraper` 필요
- 간헐 차단 가능성이 있어 모니터링/재시도 정책까지 같이 설계해야 합니다.

## 비고

- 이 문서는 "현재 이 환경에서 실제로 확인된 것"만 적었습니다.
- 차단 정책은 시점/세션/IP에 따라 변할 수 있으므로, 구현 전 최종 재검증은 다시 한 번 하는 것이 맞습니다.
