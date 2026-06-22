import unittest

import bot


class BotMessageLimitTests(unittest.TestCase):
    def test_format_crawl_error_uses_short_first_line(self):
        error = RuntimeError(
            "BrowserType.launch: Target page, context or browser has been closed\n"
            "Browser logs:\n"
            + ("x" * 5000)
        )

        formatted = bot._format_crawl_error("fmkorea", error)

        self.assertLessEqual(
            len(formatted),
            len("fmkorea: ") + bot.CRAWL_ERROR_DETAIL_LIMIT,
        )
        self.assertIn("BrowserType.launch", formatted)
        self.assertNotIn("Browser logs", formatted)

    def test_append_crawl_errors_respects_discord_content_limit(self):
        base_message = "search result summary"
        crawl_errors = [
            "fmkorea: " + ("x" * 5000),
            "ruliweb: " + ("y" * 5000),
        ]

        message = bot._append_crawl_errors(base_message, crawl_errors)

        self.assertLessEqual(len(message), bot.DISCORD_CONTENT_LIMIT)
        self.assertIn(base_message, message)
        self.assertIn("fmkorea", message)


if __name__ == "__main__":
    unittest.main()
