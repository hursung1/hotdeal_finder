import asyncio
import datetime
import os
import re
import time
from collections import Counter
from zoneinfo import ZoneInfo

import cloudscraper
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from crawler import HEADERS, normalize_link
from database import Base, SessionLocal, engine
from models import HotdealPriceRecord

RULIWEB_URL = "https://bbs.ruliweb.com/market/board/1020"
FMKOREA_URL = "https://www.fmkorea.com/hotdeal"
ARCALIVE_URL = "https://arca.live/b/hotdeal?format=rss"

KST = ZoneInfo("Asia/Seoul")
UTC = datetime.timezone.utc


def get_with_retry(client, url, headers, timeout=20, attempts=3, sleep_sec=1.2):
    last_err = None
    for idx in range(attempts):
        try:
            return client.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as err:
            last_err = err
            if idx < attempts - 1:
                time.sleep(sleep_sec)
    raise last_err


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


def normalize_deal_title(raw_text):
    text = " ".join((raw_text or "").split())
    # 게시글 댓글 수 등 가변 값 제거 (예: "(12)", "[34]")
    text = re.sub(r"\s*\(\d+\)\s*$", "", text)
    text = re.sub(r"\s*\[\d+\]\s*$", "", text)
    return text.strip()


def to_naive_utc(dt_obj):
    if dt_obj is None:
        return None
    if dt_obj.tzinfo is None:
        return dt_obj
    return dt_obj.astimezone(UTC).replace(tzinfo=None)


def extract_registered_price(raw_text):
    text = (raw_text or "").strip()
    if not text:
        return None

    # 우선순위 1: "12,345원" 또는 "12345원"
    won_match = re.search(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})\s*원", text)
    if won_match:
        return int(won_match.group(1).replace(",", ""))

    # 우선순위 2: 콤마가 포함된 가격 표기 (예: "(12,345/무료)", "12,345)")
    comma_match = re.search(r"([0-9]{1,3}(?:,[0-9]{3})+)\s*(?:/|\)|\\])", text)
    if comma_match:
        return int(comma_match.group(1).replace(",", ""))

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


def collect_ruliweb_prices(cutoff_utc, max_pages=400):
    now_kst = datetime.datetime.now(KST)
    session = requests.Session()
    items = []

    for page in range(1, max_pages + 1):
        target_url = f"{RULIWEB_URL}?page={page}"
        res = get_with_retry(session, target_url, headers=HEADERS, timeout=20)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        rows = soup.select("tr.table_body")
        if not rows:
            break

        page_has_recent_post = False
        page_saved = 0

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

            title = normalize_deal_title(title_tag.get_text(" ", strip=True))
            link = normalize_link(RULIWEB_URL, title_tag.get("href"))
            if not title or not link:
                continue

            listed_price = extract_registered_price(title)
            if listed_price is None:
                continue

            items.append(
                {
                    "platform": "루리웹",
                    "title": title,
                    "url": link,
                    "listed_price": listed_price,
                    "posted_at": to_naive_utc(posted_at),
                    "crawled_page": page,
                }
            )
            page_saved += 1

        print(f"[루리웹] page={page} saved={page_saved}")
        if not page_has_recent_post:
            break

    return items


def collect_arcalive_prices(cutoff_utc, max_pages=400):
    scraper = cloudscraper.create_scraper()
    items = []

    for page in range(1, max_pages + 1):
        target_url = f"{ARCALIVE_URL}&p={page}"
        res = get_with_retry(scraper, target_url, headers=HEADERS, timeout=20)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        rows = soup.select(".vrow.hybrid")
        if not rows:
            break

        page_has_recent_post = False
        page_saved = 0

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

            title = normalize_deal_title(title_tag.get_text(" ", strip=True))
            link = normalize_link("https://arca.live", title_tag.get("href") or row.get("href"))
            if not title or not link:
                continue

            listed_price = None
            price_tag = row.select_one(".deal-price")
            if price_tag:
                listed_price = extract_registered_price(price_tag.get_text(" ", strip=True))
            if listed_price is None:
                listed_price = extract_registered_price(title)
            if listed_price is None:
                continue

            items.append(
                {
                    "platform": "아카라이브",
                    "title": title,
                    "url": link,
                    "listed_price": listed_price,
                    "posted_at": to_naive_utc(posted_at),
                    "crawled_page": page,
                }
            )
            page_saved += 1

        print(f"[아카라이브] page={page} saved={page_saved}")
        if not page_has_recent_post:
            break

    return items


async def collect_fmkorea_prices(cutoff_utc, max_pages=400):
    now_kst = datetime.datetime.now(KST)
    items = []

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
                    print("[펨코] 차단됨: 보안 시스템 페이지가 표시되어 수집을 중단합니다.")
                    break

                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                anchors = soup.select("a.hotdeal_var8, td.title > a, a.title")
                if not anchors:
                    print(f"[펨코] page={page_num} 게시글 선택자 결과가 없어 중단합니다.")
                    break

                page_has_recent_post = False
                page_saved = 0
                page_seen_urls = set()

                for anchor in anchors:
                    title = normalize_deal_title(anchor.get_text(" ", strip=True))
                    link = normalize_link(FMKOREA_URL, anchor.get("href"))
                    if not title or not link or link in page_seen_urls:
                        continue
                    page_seen_urls.add(link)

                    listed_price = extract_registered_price(title)
                    if listed_price is None:
                        continue

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
                            "platform": "펨코",
                            "title": title,
                            "url": link,
                            "listed_price": listed_price,
                            "posted_at": to_naive_utc(posted_at),
                            "crawled_page": page_num,
                        }
                    )
                    page_saved += 1

                print(f"[펨코] page={page_num} saved={page_saved}")
                if not page_has_recent_post:
                    break
        finally:
            await browser.close()

    return items


def save_records(records):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing_rows = db.query(HotdealPriceRecord).all()
        existing_map = {row.url: row for row in existing_rows}
        inserted = 0
        updated = 0
        skipped = 0

        seen_batch = set()
        for record in records:
            url = record["url"]
            if url in seen_batch:
                skipped += 1
                continue

            existing = existing_map.get(url)
            if existing:
                if (
                    existing.listed_price != record["listed_price"]
                    or existing.title != record["title"]
                    or existing.posted_at != record["posted_at"]
                    or existing.platform != record["platform"]
                    or existing.crawled_page != record["crawled_page"]
                ):
                    existing.platform = record["platform"]
                    existing.title = record["title"]
                    existing.listed_price = record["listed_price"]
                    existing.posted_at = record["posted_at"]
                    existing.crawled_page = record["crawled_page"]
                    updated += 1
                else:
                    skipped += 1
                seen_batch.add(url)
                continue

            db.add(HotdealPriceRecord(**record))
            seen_batch.add(url)
            inserted += 1

        db.commit()
        return inserted, updated, skipped
    finally:
        db.close()


async def main():
    now_utc = datetime.datetime.now(UTC)
    cutoff_utc = now_utc - datetime.timedelta(days=30)
    print(f"최근 1개월 기준 시각(UTC): {cutoff_utc.isoformat()}")

    all_records = []

    try:
        ruli_items = collect_ruliweb_prices(cutoff_utc=cutoff_utc)
        all_records.extend(ruli_items)
    except Exception as err:
        print(f"[루리웹] 수집 실패: {err}")

    try:
        arca_items = collect_arcalive_prices(cutoff_utc=cutoff_utc)
        all_records.extend(arca_items)
    except Exception as err:
        print(f"[아카라이브] 수집 실패: {err}")

    try:
        fmkorea_items = await collect_fmkorea_prices(cutoff_utc=cutoff_utc)
        all_records.extend(fmkorea_items)
    except Exception as err:
        print(f"[펨코] 수집 실패: {err}")

    inserted, updated, skipped = save_records(all_records)
    platform_counts = Counter([x["platform"] for x in all_records])

    print("=== 백필 요약 ===")
    print(f"탐지 레코드: {len(all_records)}")
    for platform in ["루리웹", "아카라이브", "펨코"]:
        print(f"- {platform}: {platform_counts.get(platform, 0)}건")
    print(f"DB 신규 저장: {inserted}건")
    print(f"DB 업데이트: {updated}건")
    print(f"DB 중복 스킵: {skipped}건")


if __name__ == "__main__":
    asyncio.run(main())
