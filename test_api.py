import asyncio
import aiohttp

async def main():
    async with aiohttp.ClientSession() as session:
        # First login to get a token, or we can just look at the api.py source
        pass

asyncio.run(main())
