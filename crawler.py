import os
import asyncio
import datetime
import time
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from playwright.async_api import async_playwright
import cloudscraper
from zoneinfo import ZoneInfo

URL = "https://bbs.ruliweb.com/market/board/1020"
FMKOREA_URL = "https://www.fmkorea.com/hotdeal"
ARCALIVE_BOARD_URL = "https://arca.live/b/hotdeal"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
UTC = datetime.timezone.utc
KST = ZoneInfo("Asia/Seoul")
FMKOREA_PLAYWRIGHT_SEMAPHORE = asyncio.Semaphore(1)
RULIWEB_BOARD_TITLE = "핫딜예판 유저 핫딜 중고장터"
ARCALIVE_BOARD_TITLE = "핫딜 채널"
RULIWEB_ROW_SELECTOR = "tr.table_body"
RULIWEB_TITLE_SELECTOR = "td.subject a.deco"
ARCALIVE_ROW_SELECTOR = ".vrow.hybrid, a.vrow.column:not(.notice)"
ARCALIVE_TITLE_SELECTOR = "a.title.hybrid-title, a.title"


def _fmkorea_is_blocked(status_code, soup):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return status_code != 200 or "보안 시스템" in title


def _ruliweb_response_ok(status_code, soup):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return (
        status_code == 200
        and RULIWEB_BOARD_TITLE in title
        and bool(soup.select(RULIWEB_ROW_SELECTOR))
        and bool(soup.select(RULIWEB_TITLE_SELECTOR))
    )


def _arcalive_is_blocked(status_code, soup, html_text):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return (
        status_code != 200
        or "Just a moment..." in title
        or "Enable JavaScript and cookies to continue" in html_text
    )


def _arcalive_response_ok(status_code, soup, html_text):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return (
        not _arcalive_is_blocked(status_code, soup, html_text)
        and ARCALIVE_BOARD_TITLE in title
        and bool(soup.select(ARCALIVE_ROW_SELECTOR))
        and bool(soup.select(ARCALIVE_TITLE_SELECTOR))
    )


def _parse_ruliweb_listing_items(soup, now_kst=None, cutoff_utc=None, exclude_best=False):
    items = []
    seen = set()
    page_has_recent_post = False

    for row in soup.select(RULIWEB_ROW_SELECTOR):
        row_classes = row.get("class", [])
        if "notice" in row_classes or (exclude_best and "best" in row_classes):
            continue

        title_tag = row.select_one(RULIWEB_TITLE_SELECTOR)
        if not title_tag:
            continue

        title = _normalize_title(title_tag.get_text(" ", strip=True))
        link = normalize_link(URL, title_tag.get("href"))
        if not title or not link or link in seen:
            continue

        payload = {
            "title": title,
            "link": link,
            "price": extract_price(title),
        }

        if cutoff_utc is not None:
            date_tag = row.select_one("td.time")
            if not date_tag:
                continue

            posted_at = parse_ruliweb_datetime(date_tag.get_text(" ", strip=True), now_kst)
            if posted_at is None:
                continue

            if posted_at >= cutoff_utc:
                page_has_recent_post = True
            else:
                continue

            payload["posted_at"] = _to_aware_utc(posted_at)

        seen.add(link)
        items.append(payload)

    return items, page_has_recent_post


def _parse_arcalive_listing_items(soup, cutoff_utc=None):
    items = []
    seen = set()
    page_has_recent_post = False

    for row in soup.select(ARCALIVE_ROW_SELECTOR):
        title_tag = row.select_one(ARCALIVE_TITLE_SELECTOR)
        if not title_tag:
            continue

        title = _normalize_title(title_tag.get_text(" ", strip=True))
        link = normalize_link("https://arca.live", title_tag.get("href") or row.get("href"))
        if not title or not link or link in seen:
            continue

        payload = {
            "title": title,
            "link": link,
        }

        price = None
        price_tag = row.select_one(".deal-price")
        if price_tag:
            price = extract_price(price_tag.get_text(" ", strip=True))
        if price is None:
            price = extract_price(title)
        payload["price"] = price

        if cutoff_utc is not None:
            time_tag = row.select_one("time[datetime]")
            if not time_tag:
                continue

            iso_dt = time_tag.get("datetime")
            if not iso_dt:
                continue

            try:
                posted_at = datetime.datetime.fromisoformat(iso_dt.replace("Z", "+00:00")).astimezone(UTC)
            except ValueError:
                continue

            if posted_at >= cutoff_utc:
                page_has_recent_post = True
            else:
                continue

            payload["posted_at"] = _to_aware_utc(posted_at)

        seen.add(link)
        items.append(payload)

    return items, page_has_recent_post


def _parse_ruliweb_latest_with_client(client):
    res = client.get(URL, headers=HEADERS, timeout=20)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    if not _ruliweb_response_ok(res.status_code, soup):
        raise ValueError("루리웹 응답 구조 확인 실패")
    items, _page_has_recent_post = _parse_ruliweb_listing_items(soup)
    return items


def _collect_ruliweb_recent_deals_with_client(client, days=30, max_pages=400):
    now_kst = datetime.datetime.now(KST)
    cutoff_utc = _recent_cutoff_utc(days=days)
    items = []
    pages_scanned = 0

    for page in range(1, max_pages + 1):
        target_url = f"{URL}?page={page}"
        res = client.get(target_url, headers=HEADERS, timeout=20)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        if page == 1 and not _ruliweb_response_ok(res.status_code, soup):
            raise ValueError("루리웹 응답 구조 확인 실패")

        rows = soup.select(RULIWEB_ROW_SELECTOR)
        if not rows:
            break
        pages_scanned += 1

        page_items, page_has_recent_post = _parse_ruliweb_listing_items(
            soup, now_kst=now_kst, cutoff_utc=cutoff_utc, exclude_best=True
        )
        items.extend(page_items)
        if not page_has_recent_post:
            break

    return items, pages_scanned


def _parse_arcalive_latest_with_client(client):
    res = client.get(ARCALIVE_BOARD_URL, headers=HEADERS, timeout=20)
    soup = BeautifulSoup(res.text, "html.parser")
    if not _arcalive_response_ok(res.status_code, soup, res.text):
        raise ValueError("아카라이브 응답 구조 확인 실패")
    items, _page_has_recent_post = _parse_arcalive_listing_items(soup)
    return items


def _parse_fmkorea_listing_items(soup, now_kst=None, cutoff_utc=None):
    items = []
    seen = set()
    page_has_recent_post = False
    anchors = soup.select("a.hotdeal_var8")

    for anchor in anchors:
        title = _normalize_title(anchor.get_text(" ", strip=True))
        link = normalize_link(FMKOREA_URL, anchor.get("href"))
        if not title or not link or link in seen:
            continue
        seen.add(link)

        payload = {
            "title": title,
            "link": link,
            "price": extract_price(title),
        }

        if cutoff_utc is not None:
            container = anchor.find_parent("li") or anchor.find_parent("tr")
            date_text = extract_fmkorea_row_date_text(container)
            posted_at = parse_fmkorea_datetime(date_text, now_kst)
            if posted_at is None:
                continue
            if posted_at >= cutoff_utc:
                page_has_recent_post = True
            else:
                continue
            payload["posted_at"] = _to_aware_utc(posted_at)

        items.append(payload)

    return items, page_has_recent_post, len(anchors)

def normalize_link(base_url, href):
    if not href:
        return None
    absolute = urljoin(base_url, href)
    parsed = urlsplit(absolute)
    # 게시글 식별과 무관한 query/fragment를 제거해 URL 중복 판정을 안정화합니다.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

def parse_ruliweb():
    try:
        with requests.Session() as session:
            items = _parse_ruliweb_latest_with_client(session)
        print("루리웹 최신 수집: requests fetch 성공")
        return items
    except (requests.RequestException, ValueError) as e:
        print(f"루리웹 최신 수집: requests fetch 실패 ({e}), cloudscraper fallback 시도")

    try:
        with cloudscraper.create_scraper() as scraper:
            items = _parse_ruliweb_latest_with_client(scraper)
        print("루리웹 최신 수집: cloudscraper fallback 성공")
        return items
    except Exception as e:
        print(f"루리웹 최신 수집: cloudscraper fallback 실패 ({e})")
        return []

def extract_price(title):
    # 정규표현식: 3자리마다 콤마가 있거나 없는 숫자 + '원' 이 붙어있는 형태를 주로 찾습니다.
    # 예: 299,000, 399000원, (59,000/무료)
    match = re.search(r'([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})원?', title)
    if match:
        num_str = match.group(1).replace(',', '')
        try:
            return int(num_str)
        except ValueError:
            return None
    return None

def _normalize_title(raw_text):
    text = " ".join((raw_text or "").split())
    # 게시글 댓글 수 등 가변 값 제거 (예: "(12)", "[34]")
    text = re.sub(r"\s*\(\d+\)\s*$", "", text)
    text = re.sub(r"\s*\[\d+\]\s*$", "", text)
    return text.strip()

def _recent_cutoff_utc(days=30):
    return datetime.datetime.now(UTC) - datetime.timedelta(days=days)

def _to_aware_utc(dt_obj):
    if dt_obj is None:
        return None
    if dt_obj.tzinfo is None:
        return dt_obj.replace(tzinfo=UTC)
    return dt_obj.astimezone(UTC)


def _format_recent_search_stats(items_count, pages_scanned, elapsed_seconds, blocked_status="아님", fallback_status="미사용"):
    return (
        f"{items_count}건, {pages_scanned}페이지, {elapsed_seconds:.2f}초, "
        f"차단={blocked_status}, fallback={fallback_status}"
    )

def parse_ruliweb_datetime(raw_text, now_kst):
    text = (raw_text or "").strip()
    if not text:
        return None

    hm_match = re.fullmatch(r"(\d{2}):(\d{2})", text)
    if hm_match:
        hour, minute = int(hm_match.group(1)), int(hm_match.group(2))
        local_dt = now_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return local_dt.astimezone(UTC)

    ymd_match = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", text)
    if ymd_match:
        year, month, day = map(int, ymd_match.groups())
        local_dt = datetime.datetime(year, month, day, 23, 59, 59, tzinfo=KST)
        return local_dt.astimezone(UTC)

    return None

def parse_fmkorea_datetime(raw_text, now_kst):
    text = (raw_text or "").strip()
    if not text:
        return None

    hm_match = re.fullmatch(r"(\d{2}):(\d{2})", text)
    if hm_match:
        hour, minute = int(hm_match.group(1)), int(hm_match.group(2))
        local_dt = now_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return local_dt.astimezone(UTC)

    ymd_dot_match = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", text)
    if ymd_dot_match:
        year, month, day = map(int, ymd_dot_match.groups())
        local_dt = datetime.datetime(year, month, day, 23, 59, 59, tzinfo=KST)
        return local_dt.astimezone(UTC)

    ymd_dash_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if ymd_dash_match:
        year, month, day = map(int, ymd_dash_match.groups())
        local_dt = datetime.datetime(year, month, day, 23, 59, 59, tzinfo=KST)
        return local_dt.astimezone(UTC)

    md_match = re.fullmatch(r"(\d{2})\.(\d{2})", text)
    if md_match:
        month, day = map(int, md_match.groups())
        year = now_kst.year
        local_dt = datetime.datetime(year, month, day, 23, 59, 59, tzinfo=KST)
        if local_dt > now_kst:
            local_dt = local_dt.replace(year=year - 1)
        return local_dt.astimezone(UTC)

    return None

def extract_fmkorea_row_date_text(row):
    if row is None:
        return None

    selector_candidates = [
        "td.time",
        "td.date",
        "td.regdate",
        "span.time",
        "span.date",
        "span.regdate",
    ]
    for selector in selector_candidates:
        tag = row.select_one(selector)
        if tag:
            text = tag.get_text(" ", strip=True)
            if text:
                return text

    for cell in row.select("td, span"):
        text = cell.get_text(" ", strip=True)
        if not text:
            continue
        if re.fullmatch(r"(\d{2}):(\d{2})", text):
            return text
        if re.fullmatch(r"(\d{4})[.-](\d{2})[.-](\d{2})", text):
            return text
        if re.fullmatch(r"(\d{2})\.(\d{2})", text):
            return text

    return None

def collect_ruliweb_recent_deals(days=30, max_pages=400):
    started_at = time.perf_counter()
    try:
        with requests.Session() as session:
            items, pages_scanned = _collect_ruliweb_recent_deals_with_client(session, days=days, max_pages=max_pages)
        elapsed = time.perf_counter() - started_at
        print(
            f"루리웹 최근 검색 수집: requests fetch 성공 "
            f"({_format_recent_search_stats(len(items), pages_scanned, elapsed, blocked_status='아님', fallback_status='미사용')})"
        )
        return items
    except (requests.RequestException, ValueError) as e:
        print(f"루리웹 최근 검색 수집: requests fetch 실패 ({e}), cloudscraper fallback 시도")

    started_at = time.perf_counter()
    try:
        with cloudscraper.create_scraper() as scraper:
            items, pages_scanned = _collect_ruliweb_recent_deals_with_client(scraper, days=days, max_pages=max_pages)
        elapsed = time.perf_counter() - started_at
        print(
            f"루리웹 최근 검색 수집: cloudscraper fallback 성공 "
            f"({_format_recent_search_stats(len(items), pages_scanned, elapsed, blocked_status='아님', fallback_status='cloudscraper')})"
        )
        return items
    except Exception as e:
        print(f"루리웹 최근 검색 수집: cloudscraper fallback 실패 ({e})")
        return []

def collect_arcalive_recent_deals(days=30, max_pages=400):
    cutoff_utc = _recent_cutoff_utc(days=days)
    items = []
    pages_scanned = 0
    started_at = time.perf_counter()

    try:
        with cloudscraper.create_scraper() as scraper:
            for page in range(1, max_pages + 1):
                target_url = f"{ARCALIVE_BOARD_URL}?p={page}"
                res = scraper.get(target_url, headers=HEADERS, timeout=20)
                res.raise_for_status()
                soup = BeautifulSoup(res.text, "html.parser")
                rows = soup.select(ARCALIVE_ROW_SELECTOR)
                if not rows:
                    break
                pages_scanned += 1

                page_items, page_has_recent_post = _parse_arcalive_listing_items(soup, cutoff_utc=cutoff_utc)
                items.extend(page_items)

                if not page_has_recent_post:
                    break

        elapsed = time.perf_counter() - started_at
        print(
            f"아카라이브 최근 검색 수집: cloudscraper fetch 성공 "
            f"({_format_recent_search_stats(len(items), pages_scanned, elapsed, blocked_status='아님', fallback_status='미사용')})"
        )
        return items
    except Exception as e:
        print(f"아카라이브 최근 검색 수집: cloudscraper fetch 실패 ({e})")
        return []

def _collect_fmkorea_recent_deals_http(days=30, max_pages=400):
    now_kst = datetime.datetime.now(KST)
    cutoff_utc = _recent_cutoff_utc(days=days)
    items = []
    pages_scanned = 0

    with requests.Session() as session:
        for page_num in range(1, max_pages + 1):
            target_url = f"{FMKOREA_URL}?page={page_num}"
            res = session.get(target_url, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(res.text, "html.parser")
            pages_scanned += 1

            if _fmkorea_is_blocked(res.status_code, soup):
                return items, True, False, pages_scanned

            page_items, page_has_recent_post, anchor_count = _parse_fmkorea_listing_items(
                soup, now_kst=now_kst, cutoff_utc=cutoff_utc
            )
            if page_num == 1 and anchor_count == 0:
                return items, False, True, pages_scanned
            if anchor_count == 0:
                break

            items.extend(page_items)
            if not page_has_recent_post:
                break

    return items, False, False, pages_scanned


async def _collect_fmkorea_recent_deals_playwright(days=30, max_pages=400):
    now_kst = datetime.datetime.now(KST)
    cutoff_utc = _recent_cutoff_utc(days=days)
    items = []
    blocked = False
    pages_scanned = 0

    async with FMKOREA_PLAYWRIGHT_SEMAPHORE:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            cookie_header = os.getenv("FMKOREA_COOKIE")
            if cookie_header:
                await page.set_extra_http_headers({"Cookie": cookie_header})

            try:
                for page_num in range(1, max_pages + 1):
                    target_url = f"{FMKOREA_URL}?page={page_num}"
                    await page.goto(target_url, timeout=25000)
                    await page.wait_for_timeout(1500)
                    pages_scanned += 1

                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    if _fmkorea_is_blocked(200, soup):
                        blocked = True
                        break

                    page_items, page_has_recent_post, anchor_count = _parse_fmkorea_listing_items(
                        soup, now_kst=now_kst, cutoff_utc=cutoff_utc
                    )
                    if anchor_count == 0:
                        break

                    items.extend(page_items)
                    if not page_has_recent_post:
                        break
            finally:
                await browser.close()

    return items, blocked, pages_scanned


async def collect_fmkorea_recent_deals(days=30, max_pages=400):
    started_at = time.perf_counter()
    try:
        items, blocked, structure_failed, pages_scanned = _collect_fmkorea_recent_deals_http(days=days, max_pages=max_pages)
        if not blocked and not structure_failed:
            elapsed = time.perf_counter() - started_at
            print(
                f"펨코 최근 검색 수집: HTTP fetch 성공 "
                f"({_format_recent_search_stats(len(items), pages_scanned, elapsed, blocked_status='아님', fallback_status='미사용')})"
            )
            return items, False
        if blocked:
            elapsed = time.perf_counter() - started_at
            print(
                f"펨코 최근 검색 수집: HTTP fetch 차단, Playwright fallback 시도 "
                f"({_format_recent_search_stats(len(items), pages_scanned, elapsed, blocked_status='HTTP', fallback_status='Playwright 예정')})"
            )
        else:
            elapsed = time.perf_counter() - started_at
            print(
                f"펨코 최근 검색 수집: HTTP 파싱 실패, Playwright fallback 시도 "
                f"({_format_recent_search_stats(len(items), pages_scanned, elapsed, blocked_status='아님', fallback_status='Playwright 예정')})"
            )
    except requests.RequestException as e:
        print(f"펨코 최근 검색 수집: HTTP fetch 실패 ({e}), Playwright fallback 시도")

    started_at = time.perf_counter()
    items, blocked, pages_scanned = await _collect_fmkorea_recent_deals_playwright(days=days, max_pages=max_pages)
    elapsed = time.perf_counter() - started_at
    if blocked:
        print(
            f"펨코 최근 검색 수집: Playwright도 차단됨 "
            f"({_format_recent_search_stats(len(items), pages_scanned, elapsed, blocked_status='HTTP+Playwright', fallback_status='Playwright')})"
        )
    else:
        print(
            f"펨코 최근 검색 수집: Playwright fallback 성공 "
            f"({_format_recent_search_stats(len(items), pages_scanned, elapsed, blocked_status='HTTP 차단/실패 후 복구', fallback_status='Playwright')})"
        )
    return items, blocked

def _parse_fmkorea_http_latest():
    with requests.Session() as session:
        res = session.get(FMKOREA_URL, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(res.text, "html.parser")
        if _fmkorea_is_blocked(res.status_code, soup):
            return [], True, False

        items, _page_has_recent_post, anchor_count = _parse_fmkorea_listing_items(soup)
        if anchor_count == 0:
            return [], False, True
        return items, False, False


async def _parse_fmkorea_playwright_latest():
    items = []
    async with FMKOREA_PLAYWRIGHT_SEMAPHORE:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            try:
                cookie_header = os.getenv("FMKOREA_COOKIE")
                if cookie_header:
                    await page.set_extra_http_headers({"Cookie": cookie_header})

                await page.goto(FMKOREA_URL, timeout=20000)
                await page.wait_for_timeout(2000)

                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                if _fmkorea_is_blocked(200, soup):
                    print("펨코 크롤링 차단됨: 보안 시스템 페이지가 표시되어 게시글을 수집할 수 없습니다.")
                    return [], True

                items, _page_has_recent_post, _anchor_count = _parse_fmkorea_listing_items(soup)
            except Exception as e:
                print(f"펨코 크롤링 실패: {e}")
                return [], False
            finally:
                await browser.close()

    return items, False


async def parse_fmkorea():
    try:
        items, blocked, structure_failed = _parse_fmkorea_http_latest()
        if not blocked and not structure_failed:
            print("펨코 최신 수집: HTTP fetch 성공")
            return items
        if blocked:
            print("펨코 최신 수집: HTTP fetch 차단, Playwright fallback 시도")
        else:
            print("펨코 최신 수집: HTTP 파싱 실패, Playwright fallback 시도")
    except requests.RequestException as e:
        print(f"펨코 최신 수집: HTTP fetch 실패 ({e}), Playwright fallback 시도")

    items, blocked = await _parse_fmkorea_playwright_latest()
    if blocked:
        print("펨코 최신 수집: Playwright도 차단됨")
    return items

def parse_arcalive():
    try:
        with cloudscraper.create_scraper() as scraper:
            items = _parse_arcalive_latest_with_client(scraper)
        print("아카라이브 최신 수집: cloudscraper fetch 성공")
        return items
    except Exception as e:
        print(f"아카라이브 최신 수집: cloudscraper fetch 실패 ({e})")
        return []

if __name__ == "__main__":
    print("루리웹 핫딜 크롤링 테스트 시작...")
    results = parse_ruliweb()
    
    print(f"총 {len(results)}건의 핫딜 게시글 수집 완료.")
    for idx, item in enumerate(results[:10]):
        print(f"[{idx+1}] 제목: {item['title']} | 추출 가격: {item['price']} | 링크: {item['link']}")
