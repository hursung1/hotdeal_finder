# 핫딜 소스 구현 가이드

작성일: 2026-04-08 (KST)

문서 목적:
- 실제 코드 작성 시 바로 참고할 수 있도록, 사이트별 접근 방식과 성공 판정 기준, fallback, 파싱 포인트를 정리합니다.
- 이 문서는 "보기 좋은 조사 메모"보다 "구현 시 바로 옮겨 적을 수 있는 기준서"를 목표로 합니다.
- 아래 내용은 이 환경에서 `requests`, `cloudscraper`, 일부 RSS 엔드포인트를 직접 확인한 결과를 바탕으로 작성했습니다.

표기 규칙:
- `실측`: 이 환경에서 직접 확인한 방식
- `권장`: 직접 성공/실패 전부를 확인한 것은 아니지만, 구현상 fallback으로 적절한 방식

## 1. 루리웹

### 1. 사이트 이름
- 루리웹

### 2. 사이트 url
- `https://bbs.ruliweb.com/market/board/1020`

### 3. 해당 사이트에서 핫딜정보를 가져오는 방법 요약
- `requests`로 정적 HTML을 받아 파싱하면 됩니다.
- 현재 코드는 `requests`를 먼저 시도하고, 실패 시 `cloudscraper`로 fallback 합니다.

### 4. 사이트 접근 방법 (1차)
- `requests.Session().get(url, headers=HEADERS, timeout=20)` `실측`

### 4-(1). 정보획득 성공/실패 확인은 무엇을 통해 확인할 수 있는지
- 성공 확인:
  - HTTP 상태코드 `200`
  - 페이지 제목에 `핫딜예판 유저 핫딜 중고장터` 포함
  - `tr.table_body` 개수 `> 0`
  - `td.subject a.deco` 개수 `> 0`
- 실패 확인:
  - 비정상 상태코드
  - 핵심 선택자 개수 `0`

### 5. 1차 방법 실패 시(fallback) 다른 접근 방법
- `cloudscraper` `실구현`
- 브라우저 자동화는 우선순위가 낮습니다.

### 6. 정보 획득 시 이를 parsing하는 방법
- row:
  - `tr.table_body`
- 제외 대상:
  - row class에 `notice`
  - 필요 시 `best`
- 제목/링크:
  - `td.subject a.deco`
- 날짜:
  - `td.time`
- 페이지네이션:
  - `https://bbs.ruliweb.com/market/board/1020?page=N`
- 가격:
  - 제목 문자열에서 정규식 추출

### 7. 기타 사항
- 구조가 안정적인 편입니다.
- 현재 프로젝트에서는 최신 수집과 최근 검색 수집 모두 `requests 우선 -> cloudscraper fallback` 구조를 사용합니다.

## 2. 펨코

### 1. 사이트 이름
- 에펨코리아 핫딜

### 2. 사이트 url
- `https://www.fmkorea.com/hotdeal`

### 3. 해당 사이트에서 핫딜정보를 가져오는 방법 요약
- 평상시에는 `requests`로 목록 HTML을 받아 파싱할 수 있습니다.
- 다만 간헐적으로 보안 시스템 페이지가 뜨므로, 기본은 HTTP로 두고 실패 시 브라우저 fallback이 필요합니다.

### 4. 사이트 접근 방법 (1차)
- `requests.Session().get(url, headers=HEADERS, timeout=20)` `실측`

### 4-(1). 정보획득 성공/실패 확인은 무엇을 통해 확인할 수 있는지
- 성공 확인:
  - HTTP 상태코드 `200`
  - 페이지 제목이 `핫딜 - 에펨코리아`
  - `a.hotdeal_var8` 개수 `> 0`
- 실패 확인:
  - 비정상 상태코드
  - 페이지 제목에 `보안 시스템`
  - 첫 페이지에서 `a.hotdeal_var8` 개수 `0`

### 5. 1차 방법 실패 시(fallback) 다른 접근 방법
- Playwright headless `실측`
- 필요 시 `FMKOREA_COOKIE`를 추가로 주입 `실측`

### 6. 정보 획득 시 이를 parsing하는 방법
- 제목/링크:
  - `a.hotdeal_var8`
- 컨테이너:
  - `anchor.find_parent("li")`
  - 구형 구조 대응이 필요하면 `find_parent("tr")`도 같이 고려
- 날짜:
  - `span.regdate`
- 페이지네이션:
  - `https://www.fmkorea.com/hotdeal?page=N`
- 가격:
  - 제목 문자열에서 정규식 추출

### 7. 기타 사항
- 간헐 차단이 실제로 관찰됐습니다.
- 같은 코드라도 시점/세션/IP 상태에 따라 성공과 차단이 바뀔 수 있습니다.
- 따라서 구현은 `HTTP 우선 -> 차단 감지 시 fallback` 구조가 맞습니다.
- 짧은 간격 반복 요청 시 차단 확률이 올라갈 수 있습니다.

## 3. 아카라이브

### 1. 사이트 이름
- 아카라이브 핫딜 채널

### 2. 사이트 url
- `https://arca.live/b/hotdeal`
- `https://arca.live/b/hotdeal?format=rss`

### 3. 해당 사이트에서 핫딜정보를 가져오는 방법 요약
- 최신 수집은 board HTML(`https://arca.live/b/hotdeal`)을 `cloudscraper + 프로젝트 HEADERS`로 바로 받아옵니다.
- 최근 검색 수집은 board HTML을 `cloudscraper`로 바로 순회합니다.

### 4. 사이트 접근 방법 (1차)
- `cloudscraper.create_scraper().get("https://arca.live/b/hotdeal", headers=HEADERS, timeout=20)` `실구현`

### 4-(1). 정보획득 성공/실패 확인은 무엇을 통해 확인할 수 있는지
- 성공 확인:
  - HTTP 상태코드 `200`
  - 페이지 제목이 `핫딜 채널`
  - `.vrow.hybrid` 개수 `> 0`
- 실패 확인:
  - `403`
  - 응답 제목 `Just a moment...`
  - 본문에 `Enable JavaScript and cookies to continue`

### 5. 1차 방법 실패 시(fallback) 다른 접근 방법
- 현재 코드에서는 별도 fallback을 두지 않고 `cloudscraper` 단일 경로로 처리합니다.
- 필요 시 추가 fallback 후보는 Playwright 또는 `?format=rss` 실험 경로입니다.

### 6. 정보 획득 시 이를 parsing하는 방법
- row:
  - `.vrow.hybrid`
  - 보조 후보: `a.vrow.column:not(.notice)`
- 제목/링크:
  - `a.title.hybrid-title`
  - 보조 후보: `a.title`
- 날짜:
  - `time[datetime]`
- 페이지네이션:
  - `https://arca.live/b/hotdeal?p=N`
- 가격:
  - `.deal-price` 우선
  - 없으면 제목 문자열에서 정규식 추출

### 7. 기타 사항
- `?format=rss`는 이 환경에서 XML RSS가 아니라 HTML을 반환했습니다.
- 현재 구현은 최신 수집과 최근 검색 수집 모두 board HTML 기준입니다.
- 현재 구현은 최신 수집과 최근 검색 수집 모두 `cloudscraper`를 직접 사용합니다.

## 4. 뽐뿌

### 1. 사이트 이름
- 뽐뿌게시판

### 2. 사이트 url
- `https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu`

### 3. 해당 사이트에서 핫딜정보를 가져오는 방법 요약
- `requests`로 정적 HTML을 바로 받을 수 있습니다.
- 구형 게시판 구조지만 파싱은 비교적 단순합니다.

### 4. 사이트 접근 방법 (1차)
- `requests.Session().get(url, headers=HEADERS, timeout=20)` `실측`

### 4-(1). 정보획득 성공/실패 확인은 무엇을 통해 확인할 수 있는지
- 성공 확인:
  - HTTP 상태코드 `200`
  - 페이지 제목 `뽐뿌 - 뽐뿌게시판`
  - `tr.baseList` 개수 `> 0`
  - `a.baseList-title[href*="view.php?id=ppomppu"]` 개수 `> 0`
- 실패 확인:
  - 비정상 상태코드
  - 목록 row 또는 제목 링크가 `0`

### 5. 1차 방법 실패 시(fallback) 다른 접근 방법
- `cloudscraper` `권장`
- 브라우저 자동화는 우선순위가 낮습니다.

### 6. 정보 획득 시 이를 parsing하는 방법
- row:
  - `tr.baseList`
- 제목/링크:
  - `a.baseList-title`
- 날짜:
  - row 내부 `td` 중 4번째 `td.baseList-space` 텍스트
  - 예: `26/04/07`
- 작성자:
  - row 내부 3번째 `td.baseList-space`
- 글 링크 패턴:
  - `view.php?id=ppomppu&page=...&divpage=...&no=...`
- 페이지네이션:
  - `https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu&page=N`

### 7. 기타 사항
- row 내부 클래스가 세부 필드별로 충분히 구분되지 않아, 일부 필드는 `td` 위치 기반 파싱이 더 현실적입니다.
- 공지/규칙 글이 섞일 수 있으므로 제목 링크와 번호 셀을 같이 확인하는 편이 안전합니다.

## 5. 딜바다

### 1. 사이트 이름
- 딜바다 국내핫딜

### 2. 사이트 url
- `https://www.dealbada.com/bbs/board.php?bo_table=deal_domestic`

### 3. 해당 사이트에서 핫딜정보를 가져오는 방법 요약
- `requests`로 바로 접근 가능하고, 테이블 구조가 명확합니다.
- 추가 후보 중 구현 난도가 가장 낮은 편입니다.

### 4. 사이트 접근 방법 (1차)
- `requests.Session().get(url, headers=HEADERS, timeout=20)` `실측`

### 4-(1). 정보획득 성공/실패 확인은 무엇을 통해 확인할 수 있는지
- 성공 확인:
  - HTTP 상태코드 `200`
  - 페이지 제목에 `국내핫딜`
  - `table.hoverTable tbody tr:not(.bo_notice)` 개수 `> 0`
  - `td.td_subject a` 개수 `> 0`
- 실패 확인:
  - 비정상 상태코드
  - 글 row 선택자 `0`

### 5. 1차 방법 실패 시(fallback) 다른 접근 방법
- `cloudscraper` `권장`
- 현재 기준으로는 필요성이 낮아 보입니다.

### 6. 정보 획득 시 이를 parsing하는 방법
- row:
  - `table.hoverTable tbody tr:not(.bo_notice)`
- 제목/링크:
  - `td.td_subject a`
- 날짜:
  - `td.td_date`
- 글 링크 패턴:
  - `board.php?bo_table=deal_domestic&wr_id=<id>`
- 페이지네이션:
  - `https://www.dealbada.com/bbs/board.php?bo_table=deal_domestic&page=N`

### 7. 기타 사항
- 공지 row는 `tr.bo_notice` 이므로 반드시 제외하는 편이 좋습니다.
- 링크가 protocol-relative (`//www.dealbada.com/...`) 로 내려오므로 절대 URL 정규화가 필요합니다.

## 6. 다모앙

### 1. 사이트 이름
- 다모앙 알뜰구매

### 2. 사이트 url
- `https://damoang.net/economy`

### 3. 해당 사이트에서 핫딜정보를 가져오는 방법 요약
- 일반 `requests`는 Cloudflare에 막힙니다.
- `cloudscraper + 프로젝트 HEADERS` 조합으로는 접근 성공을 확인했습니다.

### 4. 사이트 접근 방법 (1차)
- `cloudscraper.create_scraper().get(url, headers=HEADERS, timeout=20)` `실측`

### 4-(1). 정보획득 성공/실패 확인은 무엇을 통해 확인할 수 있는지
- 성공 확인:
  - HTTP 상태코드 `200`
  - 페이지 제목 `알뜰구매 | 다모앙 - 종합 커뮤니티`
  - `a.post-row[href^="/economy/"]` 개수 `> 0`
- 실패 확인:
  - 일반 `requests`에서 `403`
  - 제목 `Attention Required! | Cloudflare`
  - 본문에 차단 안내 문구

### 5. 1차 방법 실패 시(fallback) 다른 접근 방법
- Playwright `권장`
- 재시도/backoff도 같이 넣는 편이 좋습니다.

### 6. 정보 획득 시 이를 parsing하는 방법
- row:
  - `a.post-row[href^="/economy/"]`
- 제목:
  - `.post-title`
- 댓글 수:
  - `.comment-count`
- 날짜:
  - 오늘 글은 `.date-today`
  - 그 외 날짜는 row 전체 메타 텍스트에서 별도 추출 로직이 필요합니다.
- 글 링크 패턴:
  - `/economy/<id>`
- 페이지네이션:
  - `https://damoang.net/economy?page=N`

### 7. 기타 사항
- 날짜 구조가 다른 사이트보다 덜 명확합니다.
- "오늘/어제/절대 날짜"를 통합 처리할 별도 정규화 로직이 필요합니다.
- 접근은 성공했지만, 간헐 차단 가능성을 염두에 둬야 합니다.

## 7. 쿨엔조이

### 1. 사이트 이름
- 쿨엔조이 지름/알뜰정보

### 2. 사이트 url
- `https://coolenjoy.net/bbs/jirum`
- `https://coolenjoy.net/bbs/board.php?bo_table=jirum`

### 3. 해당 사이트에서 핫딜정보를 가져오는 방법 요약
- canonical 도메인 `coolenjoy.net` 으로는 `requests` 접근이 됩니다.
- 목록 HTML도 받을 수 있고, RSS도 제공됩니다.

### 4. 사이트 접근 방법 (1차)
- `requests.Session().get("https://coolenjoy.net/bbs/jirum", headers=HEADERS, timeout=20)` `실측`

### 4-(1). 정보획득 성공/실패 확인은 무엇을 통해 확인할 수 있는지
- 성공 확인:
  - HTTP 상태코드 `200`
  - 페이지 제목에 `지름/알뜰정보`
  - `a.na-subject` 개수 `> 0`
- 실패 확인:
  - 비정상 상태코드
  - 제목 링크 `0`
- 주의:
  - `new.coolenjoy.net` 은 별도 테스트에서 인증서 mismatch가 있었습니다.

### 5. 1차 방법 실패 시(fallback) 다른 접근 방법
- RSS 사용 `실측`
  - `https://coolenjoy.net/bbs/rss.php?bo_table=jirum`
- 필요 시 `cloudscraper` `권장`

### 6. 정보 획득 시 이를 parsing하는 방법
- row:
  - `ul.na-table > li`
- 제목/링크:
  - `a.na-subject`
- 날짜:
  - row 내부 `i.fa-clock-o` 를 포함한 `div` 텍스트
  - 예: `등록일 04.01`
- 글 링크 패턴:
  - `https://coolenjoy.net/bbs/jirum/<id>`
- 페이지네이션:
  - `https://coolenjoy.net/bbs/jirum?page=N`

### 7. 기타 사항
- `new.coolenjoy.net` 대신 `coolenjoy.net` 을 기준으로 구현하는 것이 맞습니다.
- RSS가 실제로 XML로 제공되므로, HTML 구조가 바뀌면 RSS를 대체 수단으로 쓰기 좋습니다.

## 8. 클리앙

### 1. 사이트 이름
- 클리앙 알뜰구매

### 2. 사이트 url
- `https://www.clien.net/service/board/jirum`

### 3. 해당 사이트에서 핫딜정보를 가져오는 방법 요약
- 이 환경에서는 `requests`만으로 SSR 목록 HTML을 받을 수 있습니다.
- 별도 anti-bot 우회 없이도 현재는 접근 가능합니다.

### 4. 사이트 접근 방법 (1차)
- `requests.Session().get(url, headers=HEADERS, timeout=20)` `실측`

### 4-(1). 정보획득 성공/실패 확인은 무엇을 통해 확인할 수 있는지
- 성공 확인:
  - HTTP 상태코드 `200`
  - 페이지 제목 `클리앙 : 알뜰구매`
  - `div.list_item.symph_row` 개수 `> 0`
  - `div.list_title a[href*="/service/board/jirum/"]` 개수 `> 0`
- 실패 확인:
  - 비정상 상태코드
  - row 또는 제목 링크가 `0`

### 5. 1차 방법 실패 시(fallback) 다른 접근 방법
- `cloudscraper` `권장`
- 브라우저 자동화는 2차 fallback 정도로만 고려

### 6. 정보 획득 시 이를 parsing하는 방법
- row:
  - `div.list_item.symph_row`
- 제목/링크:
  - `div.list_title a[href*="/service/board/jirum/"]`
- 날짜:
  - `span.timestamp`
- 페이지네이션:
  - `https://www.clien.net/service/board/jirum?po=N&od=T31&category=0&groupCd=`

### 7. 기타 사항
- row 내부에 잡다한 링크가 많으므로, 제목 anchor는 반드시 `div.list_title` 하위로 좁혀야 합니다.
- `jirum.rss` 는 현재 테스트 기준 `404` 였습니다.

## 구현 우선순위 메모

1. 뽐뿌
- 접근/파싱 난도가 낮고 트래픽 가치가 큽니다.

2. 딜바다
- 정적 테이블 구조라 빠르게 붙일 수 있습니다.

3. 클리앙
- SSR 접근이 가능하고 row 구조도 비교적 안정적입니다.

4. 쿨엔조이
- HTML도 되지만 RSS 대안이 있어 운영 유연성이 있습니다.

5. 다모앙
- 가치가 있지만 anti-bot과 날짜 정규화 때문에 난도가 더 높습니다.
