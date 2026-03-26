import os
import asyncio
import datetime
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

from monitor import run_crawling_cycle
from crawler import (
    collect_ruliweb_recent_deals,
    collect_fmkorea_recent_deals,
    collect_arcalive_recent_deals,
)

# DB 임포트
from database import SessionLocal
from models import Keyword

# 환경 변수 로드
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ALERT_CHANNEL_ID = os.getenv("DISCORD_ALERT_CHANNEL_ID")

# 봇 권한 및 인텐트 설정
# 메시지 내용을 직접 안 읽고 슬래시(/) 명령어만 사용하므로 기본 설정만으로 충분합니다.
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
KST = datetime.timezone(datetime.timedelta(hours=9))
SEARCH_WINDOW_DAYS = 15
SEARCH_SEMAPHORE = asyncio.Semaphore(1)

# DB 세션 헬퍼 함수
def get_db_session():
    db = SessionLocal()
    try:
        return db
    except Exception as e:
        db.close()
        raise e

def _platform_name(link: str) -> str:
    if "fmkorea" in link:
        return "펨코"
    if "arca.live" in link:
        return "아카라이브"
    return "루리웹"

def _is_query_match(title: str, keyword: str) -> bool:
    title_no_space = title.replace(" ", "").lower()
    keyword_no_space = keyword.replace(" ", "").lower()
    if not keyword_no_space:
        return False
    return keyword_no_space in title_no_space

def _format_posted_at(posted_at) -> str:
    if not posted_at:
        return "작성시각 미상"
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=datetime.timezone.utc)
    return posted_at.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")

@bot.event
async def on_ready():
    print(f"로그인 성공: {bot.user.name} ({bot.user.id})")
    try:
        # 슬래시 명령어 동기화 (서버에 명령어 목록 업데이트)
        synced = await bot.tree.sync()
        print(f"동기화된 슬래시 명령어 개수: {len(synced)}개")
    except Exception as e:
        print(f"명령어 동기화 실패: {e}")

    # 백그라운드 크롤링 태스크 시작
    if not crawler_task.is_running():
        crawler_task.start()

# 5분마다 실행되는 백그라운드 스케줄러
@tasks.loop(minutes=5)
async def crawler_task():
    if not ALERT_CHANNEL_ID:
        print("❗ 경고: DISCORD_ALERT_CHANNEL_ID가 설정되지 않아 크롤링 알림을 쏠 수 없습니다.")
        return
    print("웹 크롤링(루리웹, 펨코, 아카라이브) 통합 탐색 사이클 시작...")
    await run_crawling_cycle(bot, ALERT_CHANNEL_ID)

# ==================== 슬래시 명령어 ====================

@bot.tree.command(name="알림등록", description="새로운 핫딜 모니터링 키워드를 등록합니다.")
@app_commands.describe(
    name="모니터링할 브랜드나 제품명 (예: 에어팟 프로)", 
    aliases="쉼표로 구분한 유의어/동의어 (예: 에어팟프로,에팟프)", 
    exclude="검색에서 제외할 단어 (예: 케이스,필름,중고)"
)
async def add_keyword(interaction: discord.Interaction, name: str, aliases: str = None, exclude: str = None):
    db = get_db_session()
    
    # 중복 체크
    existing = db.query(Keyword).filter(Keyword.name == name).first()
    if existing:
        await interaction.response.send_message(f"❌ '{name}' 키워드는 이미 모니터링 중입니다.", ephemeral=True)
        db.close()
        return

    # 새 키워드 DB 저장
    new_keyword = Keyword(
        name=name,
        aliases=aliases,
        exclude_words=exclude,
        is_active=True
    )
    db.add(new_keyword)
    db.commit()
    db.close()

    await interaction.response.send_message(
        f"✅ 성공적으로 등록되었습니다!\n"
        f"▶ **키워드:** {name}\n"
        f"▶ **유의어:** {aliases or '없음'}\n"
        f"▶ **제외어:** {exclude or '없음'}"
    )

@bot.tree.command(name="알림목록", description="현재 모니터링 중인 핫딜 키워드 목록을 보여줍니다.")
async def list_keywords(interaction: discord.Interaction):
    db = get_db_session()
    keywords = db.query(Keyword).all()
    db.close()

    if not keywords:
        await interaction.response.send_message("📭 현재 등록된 모니터링 키워드가 하나도 없습니다.")
        return

    msg = "**📊 [현재 모니터링 중인 키워드 목록]**\n"
    for k in keywords:
        status = "🟢 활성" if k.is_active else "🔴 비활성"
        msg += f"- **{k.name}** ({status}) | 유의어: {k.aliases or '없음'} | 제외어: {k.exclude_words or '없음'}\n"
    
    await interaction.response.send_message(msg)

@bot.tree.command(name="알림삭제", description="모니터링 중인 키워드를 삭제합니다.")
@app_commands.describe(name="삭제할 키워드. 여러 개면 쉼표(,)로 구분 (띄어쓰기 주의)")
async def remove_keyword(interaction: discord.Interaction, name: str):
    db = get_db_session()
    try:
        raw_names = [x.strip() for x in name.split(",")]
        target_names = list(dict.fromkeys([x for x in raw_names if x]))

        if not target_names:
            await interaction.response.send_message("❌ 삭제할 키워드를 입력해주세요. 예: `에어팟 프로, 27인치 모니터`", ephemeral=True)
            return

        matched_keywords = db.query(Keyword).filter(Keyword.name.in_(target_names)).all()
        matched_name_set = {k.name for k in matched_keywords}
        missing_names = [n for n in target_names if n not in matched_name_set]

        if not matched_keywords:
            await interaction.response.send_message(
                f"❌ 입력한 키워드를 찾을 수 없습니다: {', '.join(target_names)}",
                ephemeral=True
            )
            return

        for keyword in matched_keywords:
            db.delete(keyword)
        db.commit()

        deleted_names = [n for n in target_names if n in matched_name_set]
        msg = f"🗑️ 삭제 완료: {', '.join(deleted_names)}"
        if missing_names:
            msg += f"\n⚠️ 미존재 키워드: {', '.join(missing_names)}"

        await interaction.response.send_message(msg)
    finally:
        db.close()

@bot.tree.command(name="핫딜검색", description="입력한 키워드로 최근 15일 핫딜 게시글을 검색합니다.")
@app_commands.describe(
    keyword="검색할 키워드 (예: 에어팟 프로)",
    limit="표시할 최대 게시글 수 (기본 5, 최대 10)"
)
async def search_hotdeal(interaction: discord.Interaction, keyword: str, limit: app_commands.Range[int, 1, 10] = 5):
    if not keyword.strip():
        await interaction.response.send_message("❌ 검색어를 입력해주세요.", ephemeral=True)
        return

    if SEARCH_SEMAPHORE.locked():
        await interaction.response.send_message("⏳ 다른 핫딜검색이 실행 중입니다. 잠시 후 다시 시도해주세요.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    async with SEARCH_SEMAPHORE:
        all_deals = []
        crawl_errors = []

        try:
            all_deals.extend(collect_ruliweb_recent_deals(days=SEARCH_WINDOW_DAYS))
        except Exception as e:
            crawl_errors.append(f"루리웹: {e}")

        try:
            fmkorea_deals, blocked = await collect_fmkorea_recent_deals(days=SEARCH_WINDOW_DAYS)
            all_deals.extend(fmkorea_deals)
            if blocked:
                crawl_errors.append("펨코: 보안 시스템으로 수집 차단됨 (FMKOREA_COOKIE 설정 필요)")
        except Exception as e:
            crawl_errors.append(f"펨코: {e}")

        try:
            all_deals.extend(collect_arcalive_recent_deals(days=SEARCH_WINDOW_DAYS))
        except Exception as e:
            crawl_errors.append(f"아카라이브: {e}")

        matched = []
        seen_links = set()
        for deal in all_deals:
            title = (deal.get("title") or "").strip()
            link = (deal.get("link") or "").strip()
            if not title or not link:
                continue
            if link in seen_links:
                continue
            if not _is_query_match(title, keyword):
                continue
            seen_links.add(link)
            matched.append({
                "title": title,
                "link": link,
                "price": deal.get("price"),
                "platform": _platform_name(link),
                "posted_at": deal.get("posted_at"),
            })

        matched.sort(
            key=lambda x: x["posted_at"] if x.get("posted_at") else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            reverse=True
        )

        if not matched:
            msg = f"🔎 최근 {SEARCH_WINDOW_DAYS}일 기준 **{keyword}** 검색 결과가 없습니다."
            msg += f"\n(스캔 게시글: {len(all_deals)}건)"
            if crawl_errors:
                msg += "\n⚠️ 일부 사이트 수집 실패: " + " | ".join(crawl_errors)
            await interaction.followup.send(msg)
            return

        shown = min(len(matched), limit)
        embeds = []
        for deal in matched[:shown]:
            price_text = f"{deal['price']:,}원" if deal["price"] else "가격 미상"
            embed = discord.Embed(
                title=deal["title"][:256],
                url=deal["link"],
                color=0x00AAFF
            )
            embed.add_field(name="플랫폼", value=deal["platform"], inline=True)
            embed.add_field(name="가격", value=price_text, inline=True)
            embed.add_field(name="작성시각", value=_format_posted_at(deal.get("posted_at")), inline=True)
            embed.add_field(name="페이지", value=deal["link"], inline=False)
            embeds.append(embed)

        msg = f"🔎 최근 {SEARCH_WINDOW_DAYS}일 기준 **{keyword}** 검색 결과 {len(matched)}건 중 {shown}건을 보여드립니다."
        msg += f"\n(스캔 게시글: {len(all_deals)}건)"
        if crawl_errors:
            msg += "\n⚠️ 일부 사이트 수집 실패: " + " | ".join(crawl_errors)
        await interaction.followup.send(msg, embeds=embeds)

# =======================================================

if __name__ == "__main__":
    if not TOKEN or TOKEN == "your_bot_token_here":
        print("❗ 에러: .env 파일에 DISCORD_BOT_TOKEN을 올바르게 설정해주세요.")
    else:
        bot.run(TOKEN)
