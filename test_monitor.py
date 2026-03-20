import asyncio
from monitor import run_crawling_cycle
from unittest.mock import MagicMock

async def test():
    bot_mock = MagicMock()
    channel_mock = MagicMock()
    
    async def mock_send(*args, **kwargs):
        print("MOCK SEND:", args, kwargs)
        
    channel_mock.send = mock_send
    bot_mock.get_channel.return_value = channel_mock
    
    print("running run_crawling_cycle")
    await run_crawling_cycle(bot_mock, "1234")
    print("done")

asyncio.run(test())
