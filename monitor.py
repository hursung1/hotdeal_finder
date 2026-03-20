import discord
from database import SessionLocal
from models import Keyword, DealHistory
from crawler import parse_ruliweb, parse_fmkorea, parse_arcalive

def _truncate(text, limit=1000):
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"

def _candidate_urls_for_dedupe(url):
    candidates = {url}
    # 과거 데이터와의 호환: 기존 이력에는 query가 포함된 URL이 저장되어 있을 수 있습니다.
    if "arca.live" in url:
        candidates.add(f"{url}?p=1")
    if "ruliweb.com" in url:
        candidates.add(f"{url}?")
    return list(candidates)

async def run_crawling_cycle(bot, channel_id):
    db = SessionLocal()
    try:
        deals = []
        try:
            ruli_deals = parse_ruliweb()
            deals.extend(ruli_deals)
        except Exception as e:
            print(f"루리웹 크롤링 모듈 에러: {e}")

        try:
            fm_deals = await parse_fmkorea()
            deals.extend(fm_deals)
        except Exception as e:
            print(f"펨코 크롤링 모듈 에러: {e}")
            
        try:
            arca_deals = parse_arcalive()
            deals.extend(arca_deals)
        except Exception as e:
            print(f"아카라이브 크롤링 모듈 에러: {e}")
            
        keywords = db.query(Keyword).filter(Keyword.is_active == True).all()
        
        channel = bot.get_channel(int(channel_id))
        if not channel:
            print("에러: 설정된 알림 채널(DISCORD_ALERT_CHANNEL_ID)을 찾을 수 없습니다.")
            return

        sent_count = 0
        for deal in deals:
            for k in keywords:
                try:
                    # 1. 제외어(Exclude words) 체크
                    # 공백을 포함/미포함하여 변형된 단어들도 모두 차단하기 위해 제목/제외어 모두 공백을 제거하고 비교합니다.
                    deal_title_no_space = deal['title'].replace(" ", "").lower()
                    
                    if k.exclude_words:
                        excludes = [x.strip().replace(" ", "").lower() for x in k.exclude_words.split(',')]
                        # 제목에 제외어가 하나라도 포함되어 있으면 건너뜀
                        if any(ex in deal_title_no_space for ex in excludes if ex):
                            continue
                    
                    # 2. 포함어(Include words = name + aliases) 체크
                    # 띄어쓰기가 있는 키워드는 AND 검색으로 처리합니다 (예: "27인치 모니터" -> "27인치" AND "모니터")
                    includes = [k.name]
                    if k.aliases:
                        includes.extend([x.strip() for x in k.aliases.split(',')])
                    
                    matched = False
                    for inc in includes:
                        if not inc:
                            continue
                        # 키워드를 공백 기준으로 쪼갠 후, 공백이 제거된 제목(deal_title_no_space)에 모두 들어있는지(AND) 확인
                        sub_keywords = inc.lower().split()
                        if all(sub in deal_title_no_space for sub in sub_keywords):
                            matched = True
                            break
                            
                    if not matched:
                        continue
                        
                    # 3. 목표 가격(Target price) 필터링
                    if k.target_price and deal['price'] and deal['price'] > k.target_price:
                        continue

                    # 4. 중복 수집 체크 (이미 알림을 보낸 링크인지 url로 확인)
                    dedupe_urls = _candidate_urls_for_dedupe(deal['link'])
                    existing = db.query(DealHistory).filter(
                        DealHistory.url.in_(dedupe_urls),
                        DealHistory.is_alert_sent == True
                    ).first()
                    if existing:
                        continue
                        
                    # 5. 역대 최저가 판별 로직
                    is_lowest = False
                    if deal['price']:
                        if k.current_lowest_price is None or deal['price'] < k.current_lowest_price:
                            is_lowest = True

                    # 6. 디스코드 알림 메시지 포맷팅 및 전송
                    # 최저가 갱신 시 빨간색(FF0000), 일반 핫딜은 파란색(0055FF)
                    color = 0xFF0000 if is_lowest else 0x0055FF
                    embed = discord.Embed(title="🚨 새로운 핫딜이 떴습니다!", color=color)
                    embed.add_field(name="키워드", value=k.name, inline=True)
                    embed.add_field(name="추출 가격", value=f"{deal['price']:,}원" if deal['price'] else "가격 미상", inline=True)
                    
                    if is_lowest:
                        embed.add_field(name="🔥 역대 최저가 갱신!", value="이전에 수집된 모든 내역보다 저렴합니다.", inline=False)
                        
                    embed.add_field(name="게시글 제목", value=_truncate(deal['title']), inline=False)
                    embed.add_field(name="바로가기", value=_truncate(deal['link']), inline=False)

                    await channel.send(embed=embed)
                    
                    # 7. 디스코드 전송 성공 후에만 DB에 저장하여 "전송 실패 후 영구 스킵"을 방지합니다.
                    if "fmkorea" in deal['link']:
                        platform_name = "펨코"
                    elif "arca.live" in deal['link']:
                        platform_name = "아카라이브"
                    else:
                        platform_name = "루리웹"
                        
                    history = DealHistory(
                        keyword_id=k.id,
                        platform=platform_name,
                        title=deal['title'],
                        url=deal['link'],
                        extracted_price=deal['price'] or 0,
                        is_alert_sent=True
                    )
                    db.add(history)

                    if is_lowest:
                        k.current_lowest_price = deal['price']
                        k.lowest_price_url = deal['link']

                    db.commit()
                    sent_count += 1
                except Exception as deal_err:
                    db.rollback()
                    print(f"알림 처리 실패 (키워드={k.name}, 링크={deal.get('link')}): {deal_err}")

        print(f"크롤링 사이클 완료: 수집 {len(deals)}건, 전송 {sent_count}건")
                
    except Exception as e:
        print(f"크롤링 사이클 실행 중 에러 발생: {e}")
    finally:
        db.close()
