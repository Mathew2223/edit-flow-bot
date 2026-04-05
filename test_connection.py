import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def test():
    token = os.getenv("BOT_TOKEN")
    proxy = os.getenv("PROXY_URL", "").strip()
    
    url = f"https://api.telegram.org/bot{token}/getMe"
    
    if proxy:
        from aiohttp_socks import ProxyConnector
        import aiohttp
        connector = ProxyConnector.from_url(proxy)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, timeout=15) as resp:
                print(f"✅ Статус: {resp.status}")
                print(await resp.json())
    else:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                print(f"✅ Статус: {resp.status}")
                print(await resp.json())

try:
    asyncio.run(test())
except Exception as e:
    print(f"❌ Ошибка: {e}")