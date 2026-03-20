import cloudscraper
from bs4 import BeautifulSoup
scraper = cloudscraper.create_scraper()
html = scraper.get('https://arca.live/b/hotdeal').text
soup = BeautifulSoup(html, 'html.parser')
rows = soup.select('.vrow:not(.notice)')
for i, r in enumerate(rows[1:3]):
    print(f"--- ROW {i} ---")
    print(r)
