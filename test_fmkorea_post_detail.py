import datetime
import unittest

from bs4 import BeautifulSoup

import crawler


FMKOREA_DETAIL_HTML = """
<html>
  <head>
    <title>[지마켓] 소니 무선 노이즈 캔슬링 헤드폰 WH-1000XM5 (298,680원) (무료배송) - 핫딜 - 에펨코리아</title>
  </head>
  <body>
    <div class="board clear">
      <div class="rd_hd">
        <span class="date">2026.04.08 18:06</span>
        <h1><span>[지마켓] 소니 무선 노이즈 캔슬링 헤드폰 WH-1000XM5 (298,680원) (무료배송)</span></h1>
      </div>
      <table class="hotdeal_table">
        <tr>
          <th>링크</th>
          <td><a class="hotdeal_url" href="https://m.gmarket.co.kr/vi/product/4661533867">https://m.gmarket.co.kr/vi/product/4661533867</a></td>
        </tr>
        <tr>
          <th>쇼핑몰</th>
          <td>지마켓 [포텐 터짐 우대 쇼핑몰, 제휴 링크]</td>
        </tr>
        <tr>
          <th>상품명</th>
          <td>소니 무선 노이즈 캔슬링 헤드폰 WH-1000XM5</td>
        </tr>
        <tr>
          <th>가격</th>
          <td>298,680원</td>
        </tr>
        <tr>
          <th>배송</th>
          <td>무료배송</td>
        </tr>
      </table>
    </div>
  </body>
</html>
"""


class FmkoreaPostDetailTests(unittest.TestCase):
    def test_parse_fmkorea_post_detail(self):
        soup = BeautifulSoup(FMKOREA_DETAIL_HTML, "html.parser")
        detail = crawler.parse_fmkorea_post_detail(
            soup,
            post_url="https://www.fmkorea.com/9685375013?cpage=1",
            now_kst=datetime.datetime(2026, 4, 10, 15, 0, tzinfo=crawler.KST),
        )

        self.assertEqual(
            detail["title"],
            "[지마켓] 소니 무선 노이즈 캔슬링 헤드폰 WH-1000XM5 (298,680원) (무료배송)",
        )
        self.assertEqual(detail["link"], "https://www.fmkorea.com/9685375013")
        self.assertEqual(detail["product_url"], "https://m.gmarket.co.kr/vi/product/4661533867")
        self.assertEqual(detail["seller"], "지마켓")
        self.assertEqual(detail["product_name"], "소니 무선 노이즈 캔슬링 헤드폰 WH-1000XM5")
        self.assertEqual(detail["product_price_text"], "298,680원")
        self.assertEqual(detail["product_price"], 298680)
        self.assertEqual(detail["price"], 298680)
        self.assertEqual(detail["shipping"], "무료배송")
        self.assertEqual(
            detail["posted_at"],
            datetime.datetime(2026, 4, 8, 9, 6, tzinfo=datetime.timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
