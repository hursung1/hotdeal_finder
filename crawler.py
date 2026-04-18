import os
import json
import base64
import asyncio
import datetime
import hashlib
import time
import threading
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from playwright.async_api import async_playwright
import cloudscraper
from zoneinfo import ZoneInfo

URL = "https://bbs.ruliweb.com/market/board/1020"
FMKOREA_URL = "https://www.fmkorea.com/hotdeal"
ARCALIVE_BOARD_URL = "https://arca.live/b/hotdeal"
PPOMPPU_BASE_URL = "https://www.ppomppu.co.kr/zboard/zboard.php"
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
PPOMPPU_BOARD_CONFIGS = [
    {"id": "ppomppu", "name": "뽐뿌게시판"},
    {"id": "ppomppu4", "name": "해외뽐뿌"},
    {"id": "ppomppu8", "name": "알리뽐뿌"},
    {"id": "pmarket", "name": "쇼핑뽐뿌"},
]
PPOMPPU_BOARD_NAME_BY_ID = {config["id"]: config["name"] for config in PPOMPPU_BOARD_CONFIGS}
PPOMPPU_OLLAMA_MODEL = os.getenv("OLLAMA_PPOMPPU_MODEL", "gemma4:latest")
PPOMPPU_OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://127.0.0.1:11434/api/generate")
PPOMPPU_OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_PPOMPPU_TIMEOUT_SECONDS", "180"))
PPOMPPU_OLLAMA_PARSE_CACHE_MAX = int(os.getenv("PPOMPPU_OLLAMA_PARSE_CACHE_MAX", "256"))
PPOMPPU_SHOPPING_HINTS = ("gmarket", "naver", "smartstore", "11st", "auction", "coupang", "linkprice")
PPOMPPU_RECENT_CACHE_LOCK = threading.Lock()
PPOMPPU_RECENT_CACHE = {
    "days": None,
    "items": [],
    "fetched_at": None,
    "last_refresh_started_at": None,
    "last_refresh_completed_at": None,
    "last_refresh_error": None,
    "last_refresh_reason": None,
    "refreshing": False,
    "refresh_count": 0,
    "latest_fingerprints": {},
    "cached_fingerprints": {},
    "pending_refresh": False,
    "last_latest_checked_at": None,
    "last_change_detected_at": None,
    "changed_boards": [],
}
PPOMPPU_OLLAMA_PARSE_CACHE_LOCK = threading.Lock()
PPOMPPU_OLLAMA_PARSE_CACHE = {}


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


def build_ppomppu_board_url(board_id, page=1):
    query = {"id": board_id}
    if page is not None:
        query["page"] = page
    return urlunsplit(("https", "www.ppomppu.co.kr", "/zboard/zboard.php", urlencode(query), ""))


def _ppomppu_response_ok(status_code, soup, board_config):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    board_id = board_config["id"]
    board_name = board_config["name"]
    return (
        status_code == 200
        and board_name in title
        and bool(soup.select(f'a.baseList-title[href*="view.php?id={board_id}"]'))
    )


def _extract_ppomppu_row_date_text(cells):
    for cell in cells:
        text = cell.get_text(" ", strip=True)
        if not text:
            continue
        if re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})", text):
            return text
        if re.fullmatch(r"(\d{2})/(\d{2})/(\d{2})", text):
            return text
    return None


def _clone_deal_items(items):
    return [dict(item) for item in items]


def _ppomppu_fingerprint_from_items(items):
    hasher = hashlib.sha256()
    link_count = 0

    for item in items:
        link = (item.get("link") or "").strip()
        if not link:
            continue
        hasher.update(link.encode("utf-8"))
        hasher.update(b"\n")
        link_count += 1

    if link_count == 0:
        return ""
    return hasher.hexdigest()


def _diff_ppomppu_fingerprints(base_map, target_map):
    changed_board_ids = []

    for board_id, board_name in PPOMPPU_BOARD_NAME_BY_ID.items():
        if (base_map or {}).get(board_id) != (target_map or {}).get(board_id):
            changed_board_ids.append(board_id)

    return changed_board_ids


def _mark_ppomppu_latest_snapshot(latest_items_by_board):
    if not latest_items_by_board:
        return []

    latest_fingerprints = {
        board_id: _ppomppu_fingerprint_from_items(items)
        for board_id, items in latest_items_by_board.items()
    }
    checked_at = datetime.datetime.now(UTC)

    with PPOMPPU_RECENT_CACHE_LOCK:
        previous_latest = dict(PPOMPPU_RECENT_CACHE["latest_fingerprints"])

        changed_board_ids = []
        for board_id, fingerprint in latest_fingerprints.items():
            if previous_latest.get(board_id) != fingerprint:
                changed_board_ids.append(board_id)
            PPOMPPU_RECENT_CACHE["latest_fingerprints"][board_id] = fingerprint

        PPOMPPU_RECENT_CACHE["last_latest_checked_at"] = checked_at

        cache_missing = PPOMPPU_RECENT_CACHE["fetched_at"] is None
        cache_changed = any(
            PPOMPPU_RECENT_CACHE["cached_fingerprints"].get(board_id) != fingerprint
            for board_id, fingerprint in PPOMPPU_RECENT_CACHE["latest_fingerprints"].items()
        )
        PPOMPPU_RECENT_CACHE["pending_refresh"] = cache_missing or cache_changed

        if changed_board_ids:
            PPOMPPU_RECENT_CACHE["last_change_detected_at"] = checked_at
            PPOMPPU_RECENT_CACHE["changed_boards"] = [
                PPOMPPU_BOARD_NAME_BY_ID[board_id] for board_id in changed_board_ids
            ]

    return changed_board_ids


def get_ppomppu_recent_cache_status():
    with PPOMPPU_RECENT_CACHE_LOCK:
        return {
            "days": PPOMPPU_RECENT_CACHE["days"],
            "items_count": len(PPOMPPU_RECENT_CACHE["items"]),
            "fetched_at": PPOMPPU_RECENT_CACHE["fetched_at"],
            "last_refresh_started_at": PPOMPPU_RECENT_CACHE["last_refresh_started_at"],
            "last_refresh_completed_at": PPOMPPU_RECENT_CACHE["last_refresh_completed_at"],
            "last_refresh_error": PPOMPPU_RECENT_CACHE["last_refresh_error"],
            "last_refresh_reason": PPOMPPU_RECENT_CACHE["last_refresh_reason"],
            "refreshing": PPOMPPU_RECENT_CACHE["refreshing"],
            "refresh_count": PPOMPPU_RECENT_CACHE["refresh_count"],
            "latest_fingerprints": dict(PPOMPPU_RECENT_CACHE["latest_fingerprints"]),
            "cached_fingerprints": dict(PPOMPPU_RECENT_CACHE["cached_fingerprints"]),
            "pending_refresh": PPOMPPU_RECENT_CACHE["pending_refresh"],
            "last_latest_checked_at": PPOMPPU_RECENT_CACHE["last_latest_checked_at"],
            "last_change_detected_at": PPOMPPU_RECENT_CACHE["last_change_detected_at"],
            "changed_boards": list(PPOMPPU_RECENT_CACHE["changed_boards"]),
        }


def ppomppu_recent_cache_needs_refresh(days=30):
    with PPOMPPU_RECENT_CACHE_LOCK:
        if PPOMPPU_RECENT_CACHE["refreshing"]:
            return False

        cache_days_mismatch = PPOMPPU_RECENT_CACHE["days"] != days
        cache_missing = PPOMPPU_RECENT_CACHE["fetched_at"] is None
        latest_snapshot_ready = bool(PPOMPPU_RECENT_CACHE["latest_fingerprints"])

        if PPOMPPU_RECENT_CACHE["pending_refresh"]:
            return True
        if cache_days_mismatch:
            return latest_snapshot_ready or cache_missing
        if cache_missing:
            return latest_snapshot_ready
        return False


def _parse_ppomppu_listing_items(soup, board_config, now_kst=None, cutoff_utc=None):
    items = []
    seen = set()
    page_has_recent_post = False
    board_id = board_config["id"]
    board_url = build_ppomppu_board_url(board_id)

    for row in soup.select("tr"):
        cells = row.select("td")
        if not cells:
            continue

        row_number = cells[0].get_text(" ", strip=True)
        if not row_number.isdigit():
            continue

        title_tag = row.select_one(f'a.baseList-title[href*="view.php?id={board_id}"]')
        if not title_tag:
            continue

        link = normalize_ppomppu_link(board_url, title_tag.get("href"))
        if not link or link in seen:
            continue

        query = parse_qs(urlsplit(link).query)
        if (query.get("id") or [""])[0] != board_id:
            continue

        title = _normalize_title(title_tag.get_text(" ", strip=True))
        if not title:
            continue

        payload = {
            "title": title,
            "link": link,
            "price": extract_price(title),
            "platform": board_config["name"],
        }

        if cutoff_utc is not None:
            date_text = _extract_ppomppu_row_date_text(cells)
            posted_at = parse_ppomppu_datetime(date_text, now_kst)
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


def _parse_ppomppu_latest_with_client(client, board_config):
    target_url = build_ppomppu_board_url(board_config["id"])
    res = client.get(target_url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    if not _ppomppu_response_ok(res.status_code, soup, board_config):
        raise ValueError(f"{board_config['name']} 응답 구조 확인 실패")
    items, _page_has_recent_post = _parse_ppomppu_listing_items(soup, board_config)
    return items


def _collect_ppomppu_recent_deals_with_client(client, board_config, days=30, max_pages=400):
    now_kst = datetime.datetime.now(KST)
    cutoff_utc = _recent_cutoff_utc(days=days)
    items = []
    pages_scanned = 0
    board_id = board_config["id"]

    for page in range(1, max_pages + 1):
        target_url = build_ppomppu_board_url(board_id, page=page)
        res = client.get(target_url, headers=HEADERS, timeout=20)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        if page == 1 and not _ppomppu_response_ok(res.status_code, soup, board_config):
            raise ValueError(f"{board_config['name']} 응답 구조 확인 실패")

        if not soup.select(f'a.baseList-title[href*="view.php?id={board_id}"]'):
            break
        pages_scanned += 1

        page_items, page_has_recent_post = _parse_ppomppu_listing_items(
            soup, board_config, now_kst=now_kst, cutoff_utc=cutoff_utc
        )
        items.extend(page_items)
        if not page_has_recent_post:
            break

    return items, pages_scanned


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


def normalize_ppomppu_link(base_url, href):
    if not href:
        return None

    absolute = urljoin(base_url, href)
    parsed = urlsplit(absolute)
    query = parse_qs(parsed.query)

    board_id = (query.get("id") or [None])[0]
    post_no = (query.get("no") or [None])[0]
    if not board_id or not post_no:
        return None

    # 뽐뿌 게시글 식별자는 path가 아니라 querystring의 id/no 조합입니다.
    canonical_query = urlencode({"id": board_id, "no": post_no})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, canonical_query, ""))

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
    text = title or ""
    patterns = [
        # 가장 신뢰도가 높은 케이스: 명시적인 원 표기
        r'([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,7})\s*원',
        # (13900/무배), 16,900/무배 같은 슬래시 가격 표기
        r'([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{3,7})(?=\s*/)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        num_str = match.group(1).replace(',', '')
        try:
            return int(num_str)
        except ValueError:
            continue
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


def _is_ppomppu_shop_link(href, text=""):
    href_lower = (href or "").lower()
    text_lower = (text or "").lower()
    return any(hint in href_lower for hint in PPOMPPU_SHOPPING_HINTS) or any(
        hint in text_lower for hint in PPOMPPU_SHOPPING_HINTS
    )


def _extract_first_url(text):
    match = re.search(r"https?://[^\s<>'\")]+", text or "")
    if not match:
        return None
    return match.group(0).rstrip(".,)")


def _decode_ppomppu_redirect_target(href):
    if not href:
        return None

    try:
        query = parse_qs(urlsplit(href).query)
        encoded_target = (query.get("target") or [None])[0]
        if not encoded_target:
            return None
        padding = "=" * (-len(encoded_target) % 4)
        decoded = base64.b64decode(encoded_target + padding).decode("utf-8", errors="ignore")
    except Exception:
        return None

    return _extract_first_url(decoded)


def _extract_json_payload(raw_text):
    if not raw_text:
        return None

    candidates = [raw_text.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", raw_text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())

    object_match = re.search(r"(\{.*\})", raw_text, re.DOTALL)
    if object_match:
        candidates.append(object_match.group(1).strip())

    array_match = re.search(r"(\[.*\])", raw_text, re.DOTALL)
    if array_match:
        candidates.append(array_match.group(1).strip())

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _clean_ppomppu_llm_text(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = " ".join(value.split()).strip()
    if not value or value.lower() == "null":
        return None
    return value


def _parse_foreign_price_amount(number_text):
    if not number_text:
        return None
    normalized = number_text.replace(",", "").strip()
    if not normalized:
        return None
    try:
        if "." in normalized:
            return float(normalized)
        return int(normalized)
    except ValueError:
        return None


def _parse_llm_price_metadata(price_text):
    text = _clean_ppomppu_llm_text(price_text)
    result = {
        "raw_text": text,
        "krw_value": None,
        "currency": None,
        "amount": None,
    }
    if not text:
        return result

    won_match = re.search(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,8})\s*원", text)
    if won_match:
        try:
            value = int(won_match.group(1).replace(",", ""))
        except ValueError:
            return result
        result["krw_value"] = value
        result["currency"] = "KRW"
        result["amount"] = value
        return result

    man_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*만", text)
    if man_match:
        try:
            value = int(round(float(man_match.group(1)) * 10000))
        except ValueError:
            return result
        result["krw_value"] = value
        result["currency"] = "KRW"
        result["amount"] = value
        return result

    foreign_patterns = [
        ("USD", r"(?:US\$|\$)\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"),
        ("EUR", r"(?:EUR|€)\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"),
        ("JPY", r"(?:JPY|¥)\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"),
        ("CNY", r"(?:CNY|RMB|위안|元|CN¥|￥)\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"),
        ("GBP", r"(?:GBP|£)\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"),
    ]
    for currency, pattern in foreign_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        amount = _parse_foreign_price_amount(match.group(1))
        if amount is None:
            continue
        result["currency"] = currency
        result["amount"] = amount
        return result

    trailing_foreign_patterns = [
        ("USD", r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(?:USD|달러)"),
        ("EUR", r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(?:EUR|유로)"),
        ("JPY", r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(?:JPY|엔)"),
        ("CNY", r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(?:CNY|RMB|위안|元)"),
        ("GBP", r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(?:GBP|파운드)"),
    ]
    for currency, pattern in trailing_foreign_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        amount = _parse_foreign_price_amount(match.group(1))
        if amount is None:
            continue
        result["currency"] = currency
        result["amount"] = amount
        return result

    bare_number_match = re.search(r"([0-9]{1,3}(?:,[0-9]{3})+)", text)
    if bare_number_match:
        try:
            value = int(bare_number_match.group(1).replace(",", ""))
        except ValueError:
            return result
        result["krw_value"] = value
        result["currency"] = "KRW"
        result["amount"] = value

    return result


def _parse_llm_price_text(price_text):
    return _parse_llm_price_metadata(price_text)["krw_value"]


def _store_ppomppu_ollama_cache(cache_key, payload):
    with PPOMPPU_OLLAMA_PARSE_CACHE_LOCK:
        PPOMPPU_OLLAMA_PARSE_CACHE[cache_key] = dict(payload)
        while len(PPOMPPU_OLLAMA_PARSE_CACHE) > PPOMPPU_OLLAMA_PARSE_CACHE_MAX:
            oldest_key = next(iter(PPOMPPU_OLLAMA_PARSE_CACHE))
            PPOMPPU_OLLAMA_PARSE_CACHE.pop(oldest_key, None)


def _pick_ppomppu_post_body_table(soup):
    best_table = None
    best_score = -1

    for table in soup.select("table"):
        text = table.get_text(" ", strip=True)
        if len(text) < 50:
            continue

        image_count = sum(
            1 for img in table.select("img[src]")
            if "ppomppu.co.kr/zboard/data3" in (img.get("src") or "")
        )
        external_link_count = sum(
            1 for a in table.select("a[href]")
            if _is_ppomppu_shop_link(a.get("href") or "", a.get_text(" ", strip=True))
        )

        score = image_count * 10 + external_link_count * 6 + min(len(text), 3000) / 500
        if "등록일" in text:
            score += 2
        if score > best_score:
            best_table = table
            best_score = score

    return best_table


def _build_ppomppu_post_sequence(root):
    sequence = []

    for paragraph in root.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        if text and text != "\xa0":
            sequence.append({"kind": "text", "value": text})

        for image in paragraph.find_all("img", src=True):
            src = image.get("src") or ""
            if "ppomppu.co.kr/zboard/data3" not in src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            sequence.append({"kind": "image", "value": src})

        for anchor in paragraph.find_all("a", href=True):
            href = anchor.get("href") or ""
            text = anchor.get_text(" ", strip=True)
            if href.startswith("//"):
                href = "https:" + href
            if not _is_ppomppu_shop_link(href, text):
                continue
            sequence.append({"kind": "link", "value": href, "text": text})

    return sequence


def _collect_ppomppu_global_links(scope):
    links = []
    seen = set()

    for anchor in scope.select("a[href]"):
        href = anchor.get("href") or ""
        text = anchor.get_text(" ", strip=True)
        if href.startswith("//"):
            href = "https:" + href
        if not _is_ppomppu_shop_link(href, text):
            continue

        canonical_value = _extract_first_url(text) or _decode_ppomppu_redirect_target(href) or href
        key = (text, canonical_value)
        if key in seen:
            continue
        seen.add(key)
        links.append({"kind": "link", "value": canonical_value, "text": text})

    return links


def _select_ppomppu_keyword_block(sequence, global_links, keyword):
    positions = [
        idx for idx, item in enumerate(sequence)
        if item["kind"] == "text" and keyword in item["value"]
    ]
    pos = positions[0] if positions else None

    if pos is None:
        return {
            "text_items": [item for item in sequence if item["kind"] == "text"][:6],
            "link_items": global_links[:1],
            "image_items": [],
        }

    text_items = [
        item for item in sequence[max(0, pos - 3):min(len(sequence), pos + 4)]
        if item["kind"] == "text"
    ]
    nearby_links = [
        item for item in sequence[max(0, pos - 4):min(len(sequence), pos + 4)]
        if item["kind"] == "link"
    ]
    if not nearby_links:
        previous_links = [item for item in sequence[:pos] if item["kind"] == "link"]
        if previous_links:
            nearby_links = [previous_links[-1]]
        elif len(global_links) == 1:
            nearby_links = global_links[:1]

    previous_images = []
    idx = pos - 1
    while idx >= 0 and sequence[idx]["kind"] == "image":
        previous_images.append(sequence[idx])
        idx -= 1
    previous_images = list(reversed(previous_images))

    next_images = []
    idx = pos + 1
    while idx < len(sequence) and sequence[idx]["kind"] == "image":
        next_images.append(sequence[idx])
        idx += 1

    image_items = []
    if previous_images:
        image_items = previous_images[:2]
    elif next_images:
        image_items = next_images[:2]
    else:
        nearby_previous_images = [
            item for item in sequence[max(0, pos - 5):pos]
            if item["kind"] == "image"
        ]
        nearby_next_images = [
            item for item in sequence[pos + 1:min(len(sequence), pos + 6)]
            if item["kind"] == "image"
        ]
        image_items = (nearby_previous_images + nearby_next_images)[:2]

    return {
        "text_items": text_items,
        "link_items": nearby_links[:1],
        "image_items": image_items,
    }


def _prepare_ppomppu_ollama_payload(post_url, keyword):
    response = requests.get(post_url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    response.encoding = "euc-kr"
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body_table = _pick_ppomppu_post_body_table(soup)
    if body_table is None:
        raise ValueError("뽐뿌 본문 블록을 찾지 못했습니다.")

    sequence = _build_ppomppu_post_sequence(body_table)
    global_links = _collect_ppomppu_global_links(soup)
    block = _select_ppomppu_keyword_block(sequence, global_links, keyword)

    lines = [f"제목: {title}", "", "검색어 주변 본문 블록:"]
    for item in block["text_items"]:
        lines.append(f"- {item['value']}")
    lines.append("")
    lines.append("검색어 주변 후보 링크:")
    if block["link_items"]:
        for item in block["link_items"]:
            lines.append(f"- {item.get('text', '')} => {item['value']}")
    else:
        lines.append("- (없음)")

    image_payloads = []
    for item in block["image_items"]:
        image_url = item["value"]
        image_response = requests.get(image_url, headers=HEADERS, timeout=20)
        image_response.raise_for_status()
        image_payloads.append(base64.b64encode(image_response.content).decode())

    candidate_url = None
    if block["link_items"]:
        candidate_url = block["link_items"][0]["value"]

    prompt = (
        "너는 핫딜 게시글에서 사용자의 검색어에 해당하는 상품 정보를 추출하는 모델이다.\n\n"
        "입력 데이터:\n"
        "- 사용자의 검색어\n"
        "- 게시글 내용\n"
        "- 검색어와 가까운 본문 블록에 붙은 상품 이미지들\n\n"
        "목표:\n"
        "사용자의 검색어에 해당하는 상품 1개에 대해 아래 3개 필드만 추출하라.\n"
        "- product_name\n"
        "- product_price\n"
        "- product_url\n\n"
        "추출 규칙:\n"
        "1. 검색어와 관련된 상품만 대상으로 한다.\n"
        "2. 각 필드는 게시글 내용에 명시되어 있으면 그것을 우선 사용한다.\n"
        "3. 게시글 내용에 해당 필드가 없을 때만 이미지를 보고 보완한다.\n"
        "4. 게시글 제목도 게시글 내용의 일부로 간주한다.\n"
        "5. 게시글 내용에 URL이 여러 개면, 검색어 대상 상품 설명과 가장 가깝거나 직접 연결되는 URL을 선택한다.\n"
        "6. 가격은 원문에 보이는 값만 사용한다. 추정하지 마라.\n"
        "7. 이미지에 가격이 여러 개 있으면 결제할인가, 최종혜택가, 최종가처럼 실제 구매자 결제 금액에 가장 가까운 값을 우선한다.\n"
        "8. 검색어와 무관한 다른 상품, 사은품, 주변기기, 부가 설명은 무시한다.\n"
        "9. 어떤 필드도 확인할 수 없으면 null로 둔다.\n"
        "10. 반드시 strict JSON만 반환한다. 다른 설명은 금지한다.\n\n"
        f"사용자 검색어:\n{keyword}\n\n"
        f"게시글 URL:\n{post_url}\n\n"
        f"게시글 내용:\n{chr(10).join(lines)}\n"
    )

    return {
        "prompt": prompt,
        "images": image_payloads,
        "candidate_url": candidate_url,
    }


def extract_ppomppu_product_with_ollama(post_url, keyword):
    normalized_post_url = normalize_ppomppu_link(post_url, post_url) or post_url
    normalized_keyword = (keyword or "").replace(" ", "").lower().strip()
    if not normalized_keyword:
        return None

    cache_key = f"{normalized_post_url}|{normalized_keyword}"
    with PPOMPPU_OLLAMA_PARSE_CACHE_LOCK:
        cached = PPOMPPU_OLLAMA_PARSE_CACHE.get(cache_key)
        if cached is not None:
            return dict(cached)

    payload = _prepare_ppomppu_ollama_payload(normalized_post_url, keyword)
    response = requests.post(
        PPOMPPU_OLLAMA_API_URL,
        json={
            "model": PPOMPPU_OLLAMA_MODEL,
            "prompt": payload["prompt"],
            "images": payload["images"],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        },
        timeout=PPOMPPU_OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    raw_response = response.json().get("response")
    parsed_payload = _extract_json_payload(raw_response)

    if isinstance(parsed_payload, list):
        parsed_payload = parsed_payload[0] if parsed_payload else {}
    if not isinstance(parsed_payload, dict):
        parsed_payload = {}

    product_name = _clean_ppomppu_llm_text(parsed_payload.get("product_name"))
    product_price_text = _clean_ppomppu_llm_text(parsed_payload.get("product_price"))
    product_url = _extract_first_url(_clean_ppomppu_llm_text(parsed_payload.get("product_url")) or "")

    if not product_url and payload["candidate_url"] and product_name:
        product_url = payload["candidate_url"]

    price_metadata = _parse_llm_price_metadata(product_price_text)

    result = {
        "product_name": product_name,
        "product_price_text": price_metadata["raw_text"],
        "product_price": price_metadata["krw_value"],
        "product_price_currency": price_metadata["currency"],
        "product_price_amount": price_metadata["amount"],
        "product_url": product_url,
        "source_post_url": normalized_post_url,
    }
    _store_ppomppu_ollama_cache(cache_key, result)
    return dict(result)

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


def parse_ppomppu_datetime(raw_text, now_kst):
    text = (raw_text or "").strip()
    if not text:
        return None

    hms_match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})", text)
    if hms_match:
        hour, minute, second = map(int, hms_match.groups())
        local_dt = now_kst.replace(hour=hour, minute=minute, second=second, microsecond=0)
        return local_dt.astimezone(UTC)

    ymd_match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{2})", text)
    if ymd_match:
        year, month, day = map(int, ymd_match.groups())
        local_dt = datetime.datetime(2000 + year, month, day, 23, 59, 59, tzinfo=KST)
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


def _parse_ppomppu_board(board_config):
    board_name = board_config["name"]

    try:
        with requests.Session() as session:
            items = _parse_ppomppu_latest_with_client(session, board_config)
        print(f"뽐뿌 최신 수집[{board_name}]: requests fetch 성공 ({len(items)}건)")
        return items, True
    except (requests.RequestException, ValueError) as e:
        print(f"뽐뿌 최신 수집[{board_name}]: requests fetch 실패 ({e}), cloudscraper fallback 시도")

    try:
        with cloudscraper.create_scraper() as scraper:
            items = _parse_ppomppu_latest_with_client(scraper, board_config)
        print(f"뽐뿌 최신 수집[{board_name}]: cloudscraper fallback 성공 ({len(items)}건)")
        return items, True
    except Exception as e:
        print(f"뽐뿌 최신 수집[{board_name}]: cloudscraper fallback 실패 ({e})")
        return [], False


def parse_ppomppu():
    items = []
    latest_items_by_board = {}

    for board_config in PPOMPPU_BOARD_CONFIGS:
        board_items, fetch_ok = _parse_ppomppu_board(board_config)
        items.extend(board_items)
        if fetch_ok:
            latest_items_by_board[board_config["id"]] = board_items

    changed_board_ids = _mark_ppomppu_latest_snapshot(latest_items_by_board)
    if changed_board_ids:
        changed_board_names = [PPOMPPU_BOARD_NAME_BY_ID[board_id] for board_id in changed_board_ids]
        print(f"뽐뿌 최근 검색 캐시: 최신 1페이지 변경 감지 ({', '.join(changed_board_names)})")

    print(f"뽐뿌 최신 수집 전체: {len(items)}건")
    return items


def collect_ppomppu_recent_deals(days=30, max_pages=400):
    all_items = []

    for board_config in PPOMPPU_BOARD_CONFIGS:
        board_name = board_config["name"]
        started_at = time.perf_counter()

        try:
            with requests.Session() as session:
                items, pages_scanned = _collect_ppomppu_recent_deals_with_client(
                    session, board_config, days=days, max_pages=max_pages
                )
            elapsed = time.perf_counter() - started_at
            print(
                f"뽐뿌 최근 검색 수집[{board_name}]: requests fetch 성공 "
                f"({_format_recent_search_stats(len(items), pages_scanned, elapsed, blocked_status='아님', fallback_status='미사용')})"
            )
            all_items.extend(items)
            continue
        except (requests.RequestException, ValueError) as e:
            print(f"뽐뿌 최근 검색 수집[{board_name}]: requests fetch 실패 ({e}), cloudscraper fallback 시도")

        started_at = time.perf_counter()
        try:
            with cloudscraper.create_scraper() as scraper:
                items, pages_scanned = _collect_ppomppu_recent_deals_with_client(
                    scraper, board_config, days=days, max_pages=max_pages
                )
            elapsed = time.perf_counter() - started_at
            print(
                f"뽐뿌 최근 검색 수집[{board_name}]: cloudscraper fallback 성공 "
                f"({_format_recent_search_stats(len(items), pages_scanned, elapsed, blocked_status='아님', fallback_status='cloudscraper')})"
            )
            all_items.extend(items)
        except Exception as e:
            print(f"뽐뿌 최근 검색 수집[{board_name}]: cloudscraper fallback 실패 ({e})")

    deduped_items = []
    seen_links = set()
    for item in all_items:
        link = item.get("link")
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        deduped_items.append(item)

    print(f"뽐뿌 최근 검색 수집 전체: {len(deduped_items)}건")
    return deduped_items


def refresh_ppomppu_recent_cache_if_needed(days=30, force=False):
    with PPOMPPU_RECENT_CACHE_LOCK:
        if PPOMPPU_RECENT_CACHE["refreshing"]:
            print("뽐뿌 최근 검색 캐시: 이미 갱신 중이라 추가 갱신을 건너뜁니다.")
            return False

        cache_missing = PPOMPPU_RECENT_CACHE["fetched_at"] is None
        cache_days_mismatch = PPOMPPU_RECENT_CACHE["days"] != days
        latest_snapshot_ready = bool(PPOMPPU_RECENT_CACHE["latest_fingerprints"])
        needs_refresh = force or PPOMPPU_RECENT_CACHE["pending_refresh"] or (cache_missing and latest_snapshot_ready) or cache_days_mismatch

        if not needs_refresh:
            print("뽐뿌 최근 검색 캐시: 갱신 불필요")
            return False

        if not force and cache_missing and not latest_snapshot_ready:
            print("뽐뿌 최근 검색 캐시: 최신 1페이지 fingerprint가 없어 백그라운드 갱신을 건너뜁니다.")
            return False

        if force:
            refresh_reason = "force"
        elif cache_days_mismatch:
            refresh_reason = f"검색일수 변경({PPOMPPU_RECENT_CACHE['days']}->{days})"
        elif cache_missing:
            refresh_reason = "초기 캐시 생성"
        elif PPOMPPU_RECENT_CACHE["changed_boards"]:
            refresh_reason = "최신 1페이지 변경(" + ", ".join(PPOMPPU_RECENT_CACHE["changed_boards"]) + ")"
        else:
            refresh_reason = "변경 감지"

        latest_fingerprints_snapshot = dict(PPOMPPU_RECENT_CACHE["latest_fingerprints"])
        PPOMPPU_RECENT_CACHE["refreshing"] = True
        PPOMPPU_RECENT_CACHE["last_refresh_started_at"] = datetime.datetime.now(UTC)
        PPOMPPU_RECENT_CACHE["last_refresh_error"] = None
        PPOMPPU_RECENT_CACHE["last_refresh_reason"] = refresh_reason

    print(f"뽐뿌 최근 검색 캐시: 갱신 시작 ({refresh_reason})")

    try:
        items = collect_ppomppu_recent_deals(days=days)
    except Exception as e:
        with PPOMPPU_RECENT_CACHE_LOCK:
            PPOMPPU_RECENT_CACHE["refreshing"] = False
            PPOMPPU_RECENT_CACHE["last_refresh_error"] = str(e)
        print(f"뽐뿌 최근 검색 캐시: 갱신 실패 ({e})")
        raise

    completed_at = datetime.datetime.now(UTC)
    with PPOMPPU_RECENT_CACHE_LOCK:
        PPOMPPU_RECENT_CACHE["items"] = _clone_deal_items(items)
        PPOMPPU_RECENT_CACHE["days"] = days
        PPOMPPU_RECENT_CACHE["fetched_at"] = completed_at
        PPOMPPU_RECENT_CACHE["last_refresh_completed_at"] = completed_at
        PPOMPPU_RECENT_CACHE["refreshing"] = False
        PPOMPPU_RECENT_CACHE["refresh_count"] += 1
        PPOMPPU_RECENT_CACHE["cached_fingerprints"] = latest_fingerprints_snapshot
        changed_after_refresh = _diff_ppomppu_fingerprints(
            PPOMPPU_RECENT_CACHE["cached_fingerprints"],
            PPOMPPU_RECENT_CACHE["latest_fingerprints"],
        )
        PPOMPPU_RECENT_CACHE["pending_refresh"] = bool(changed_after_refresh)
        PPOMPPU_RECENT_CACHE["changed_boards"] = [
            PPOMPPU_BOARD_NAME_BY_ID[board_id] for board_id in changed_after_refresh
        ]

    print(f"뽐뿌 최근 검색 캐시: 갱신 완료 ({len(items)}건)")
    return True


def get_ppomppu_recent_deals_cached(days=30):
    with PPOMPPU_RECENT_CACHE_LOCK:
        has_cache = (
            PPOMPPU_RECENT_CACHE["fetched_at"] is not None
            and PPOMPPU_RECENT_CACHE["days"] == days
        )
        if has_cache:
            cached_items = _clone_deal_items(PPOMPPU_RECENT_CACHE["items"])
            pending_refresh = PPOMPPU_RECENT_CACHE["pending_refresh"]
            refreshing = PPOMPPU_RECENT_CACHE["refreshing"]
            changed_boards = list(PPOMPPU_RECENT_CACHE["changed_boards"])
        else:
            cached_items = None
            pending_refresh = False
            refreshing = False
            changed_boards = []

    if cached_items is not None:
        if pending_refresh:
            print(
                "뽐뿌 최근 검색 캐시: HIT "
                f"({len(cached_items)}건, 최신 1페이지 변경 감지됨: {', '.join(changed_boards) or '미상'}, 갱신 {'진행 중' if refreshing else '대기'})"
            )
        else:
            print(f"뽐뿌 최근 검색 캐시: HIT ({len(cached_items)}건)")
        return cached_items

    print("뽐뿌 최근 검색 캐시: MISS, 동기 생성 시작")
    refresh_ppomppu_recent_cache_if_needed(days=days, force=True)

    with PPOMPPU_RECENT_CACHE_LOCK:
        if PPOMPPU_RECENT_CACHE["fetched_at"] is None or PPOMPPU_RECENT_CACHE["days"] != days:
            return []
        return _clone_deal_items(PPOMPPU_RECENT_CACHE["items"])

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
