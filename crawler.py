import os
import datetime
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from playwright.async_api import async_playwright
import cloudscraper
from zoneinfo import ZoneInfo

URL = "https://bbs.ruliweb.com/market/board/1020"
FMKOREA_URL = "https://www.fmkorea.com/hotdeal"
ARCALIVE_URL = "https://arca.live/b/hotdeal?format=rss"
ARCALIVE_BOARD_URL = "https://arca.live/b/hotdeal"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
UTC = datetime.timezone.utc
KST = ZoneInfo("Asia/Seoul")

def normalize_link(base_url, href):
    if not href:
        return None
    absolute = urljoin(base_url, href)
    parsed = urlsplit(absolute)
    # 게시글 식별과 무관한 query/fragment를 제거해 URL 중복 판정을 안정화합니다.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

def parse_ruliweb():
    res = requests.get(URL, headers=HEADERS, timeout=20)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')
    
    items = []
    
    # 루리웹 게시판 리스트 구조 분석용
    rows = soup.select("tr.table_body")
    for row in rows:
        # 공지사항(notice)인지 확인
        if "notice" in row.get("class", []):
            continue
            
        # 제목 태그 추출 (현재 루리웹 구조는 td.subject > a.deco 로 되어 있음)
        subject_td = row.select_one("td.subject")
        if not subject_td:
            continue
            
        title_tag = subject_td.select_one("a.deco")
        if not title_tag:
            continue
            
        title = title_tag.text.strip()
        link = normalize_link(URL, title_tag.get("href"))
        if not link:
            continue
        
        # 제목 앞부분의 말머리를 떼기 위해 한 번 더 정제할 수 있지만 일단 원본 유지
        price = extract_price(title)
        
        items.append({
            "title": title,
            "link": link,
            "price": price
        })
        
    return items

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
    now_kst = datetime.datetime.now(KST)
    cutoff_utc = _recent_cutoff_utc(days=days)
    session = requests.Session()
    items = []

    for page in range(1, max_pages + 1):
        target_url = f"{URL}?page={page}"
        res = session.get(target_url, headers=HEADERS, timeout=20)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        rows = soup.select("tr.table_body")
        if not rows:
            break

        page_has_recent_post = False
        for row in rows:
            row_classes = row.get("class", [])
            if "notice" in row_classes or "best" in row_classes:
                continue

            title_tag = row.select_one("td.subject a.deco")
            date_tag = row.select_one("td.time")
            if not title_tag or not date_tag:
                continue

            posted_at = parse_ruliweb_datetime(date_tag.get_text(" ", strip=True), now_kst)
            if posted_at is None:
                continue

            if posted_at >= cutoff_utc:
                page_has_recent_post = True
            else:
                continue

            title = _normalize_title(title_tag.get_text(" ", strip=True))
            link = normalize_link(URL, title_tag.get("href"))
            if not title or not link:
                continue

            items.append(
                {
                    "title": title,
                    "link": link,
                    "price": extract_price(title),
                    "posted_at": _to_aware_utc(posted_at),
                }
            )

        if not page_has_recent_post:
            break

    return items

def collect_arcalive_recent_deals(days=30, max_pages=400):
    cutoff_utc = _recent_cutoff_utc(days=days)
    scraper = cloudscraper.create_scraper()
    items = []

    for page in range(1, max_pages + 1):
        target_url = f"{ARCALIVE_BOARD_URL}?p={page}"
        res = scraper.get(target_url, headers=HEADERS, timeout=20)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        rows = soup.select(".vrow.hybrid, a.vrow.column:not(.notice)")
        if not rows:
            break

        page_has_recent_post = False
        for row in rows:
            title_tag = row.select_one("a.title.hybrid-title, a.title")
            time_tag = row.select_one("time[datetime]")
            if not title_tag or not time_tag:
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

            title = _normalize_title(title_tag.get_text(" ", strip=True))
            link = normalize_link("https://arca.live", title_tag.get("href") or row.get("href"))
            if not title or not link:
                continue

            price = None
            price_tag = row.select_one(".deal-price")
            if price_tag:
                price = extract_price(price_tag.get_text(" ", strip=True))
            if price is None:
                price = extract_price(title)

            items.append(
                {
                    "title": title,
                    "link": link,
                    "price": price,
                    "posted_at": _to_aware_utc(posted_at),
                }
            )

        if not page_has_recent_post:
            break

    return items

async def collect_fmkorea_recent_deals(days=30, max_pages=400):
    now_kst = datetime.datetime.now(KST)
    cutoff_utc = _recent_cutoff_utc(days=days)
    items = []
    blocked = False

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

                page_title = await page.title()
                if "보안 시스템" in page_title:
                    blocked = True
                    break

                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                anchors = soup.select("a.hotdeal_var8, td.title > a, a.title")
                if not anchors:
                    break

                page_has_recent_post = False
                page_seen_urls = set()

                for anchor in anchors:
                    title = _normalize_title(anchor.get_text(" ", strip=True))
                    link = normalize_link(FMKOREA_URL, anchor.get("href"))
                    if not title or not link or link in page_seen_urls:
                        continue
                    page_seen_urls.add(link)

                    row = anchor.find_parent("tr")
                    date_text = extract_fmkorea_row_date_text(row)
                    posted_at = parse_fmkorea_datetime(date_text, now_kst)
                    if posted_at is None:
                        continue

                    if posted_at >= cutoff_utc:
                        page_has_recent_post = True
                    else:
                        continue

                    items.append(
                        {
                            "title": title,
                            "link": link,
                            "price": extract_price(title),
                            "posted_at": _to_aware_utc(posted_at),
                        }
                    )

                if not page_has_recent_post:
                    break
        finally:
            await browser.close()

    return items, blocked

async def parse_fmkorea():
    items = []
    # Playwright를 이용해 동적으로 페이지 접근
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            cookie_header = os.getenv("FMKOREA_COOKIE")
            if cookie_header:
                await page.set_extra_http_headers({"Cookie": cookie_header})

            # 펨코 핫딜 게시판으로 이동
            await page.goto(FMKOREA_URL, timeout=20000)
            await page.wait_for_timeout(2000)

            page_title = await page.title()
            if "보안 시스템" in page_title:
                print("펨코 크롤링 차단됨: 보안 시스템 페이지가 표시되어 게시글을 수집할 수 없습니다. 필요하면 FMKOREA_COOKIE 환경 변수를 설정해 주세요.")
                return []
            
            # 게시물 리스트 추출
            # 펨코 프론트 구조가 바뀔 수 있어 선택자를 순차적으로 시도합니다.
            selectors = ["a.hotdeal_var8", ".hotdeal_var8", "a.title"]
            elements = []
            for selector in selectors:
                elements = await page.query_selector_all(selector)
                if elements:
                    break
            
            seen = set()
            for elem in elements:
                title = await elem.inner_text()
                href = await elem.get_attribute("href")
                
                link = normalize_link(FMKOREA_URL, href)
                if not link or link in seen:
                    continue
                seen.add(link)
                    
                price = extract_price(title)
                
                if title and link:
                    items.append({
                        "title": title.strip(),
                        "link": link,
                        "price": price
                    })
        except Exception as e:
            print(f"펨코 크롤링 실패: {e}")
        finally:
            await browser.close()
            
    return items

def parse_arcalive():
    items = []
    scraper = cloudscraper.create_scraper()
    try:
        html = scraper.get(ARCALIVE_URL, headers=HEADERS, timeout=20).text
        soup = BeautifulSoup(html, 'html.parser')

        # 최신 DOM은 ".vrow.hybrid" 구조이므로 제목 링크를 기준으로 수집합니다.
        for vrow in soup.select('.vrow.hybrid, a.vrow.column:not(.notice)'):
            title_tag = vrow.select_one('a.title.hybrid-title, a.title')
            if not title_tag:
                continue

            raw_title = title_tag.get_text(" ", strip=True)
            title = " ".join(raw_title.split())
            if not title:
                continue

            link = normalize_link("https://arca.live", title_tag.get('href') or vrow.get('href'))
            if not link:
                continue

            price = None
            price_tag = vrow.select_one(".deal-price")
            if price_tag:
                price = extract_price(price_tag.get_text(" ", strip=True))
            if price is None:
                price = extract_price(title)
            
            items.append({
                "title": title,
                "link": link,
                "price": price
            })
    except Exception as e:
        print(f"아카라이브 크롤링 실패: {e}")
        
    return items

if __name__ == "__main__":
    print("루리웹 핫딜 크롤링 테스트 시작...")
    results = parse_ruliweb()
    
    print(f"총 {len(results)}건의 핫딜 게시글 수집 완료.")
    for idx, item in enumerate(results[:10]):
        print(f"[{idx+1}] 제목: {item['title']} | 추출 가격: {item['price']} | 링크: {item['link']}")
