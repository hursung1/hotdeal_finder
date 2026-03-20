import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

from monitor import run_crawling_cycle

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

# DB 세션 헬퍼 함수
def get_db_session():
    db = SessionLocal()
    try:
        return db
    except Exception as e:
        db.close()
        raise e

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
@app_commands.describe(name="삭제할 키워드 (띄어쓰기 주의)")
async def remove_keyword(interaction: discord.Interaction, name: str):
    db = get_db_session()
    keyword = db.query(Keyword).filter(Keyword.name == name).first()
    
    if not keyword:
        await interaction.response.send_message(f"❌ '{name}' 키워드를 찾을 수 없습니다.", ephemeral=True)
        db.close()
        return

    db.delete(keyword)
    db.commit()
    db.close()
    
    await interaction.response.send_message(f"🗑️ '{name}' 키워드가 모니터링 목록에서 삭제되었습니다.")

# =======================================================

if __name__ == "__main__":
    if not TOKEN or TOKEN == "your_bot_token_here":
        print("❗ 에러: .env 파일에 DISCORD_BOT_TOKEN을 올바르게 설정해주세요.")
    else:
        bot.run(TOKEN)
