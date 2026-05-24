import os
import asyncio
from aiohttp import web
import aiohttp_jinja2
import jinja2

from discord.ext import commands
import sentry_sdk

class WebServerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.app = web.Application()
        self.runner = None
        
        # Setup Jinja2 templates
        template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
        aiohttp_jinja2.setup(self.app, loader=jinja2.FileSystemLoader(template_dir), auto_reload=True)
        
        # Add routes
        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_post('/submit', self.handle_submit)
        self.app.router.add_get('/suggestions', self.handle_suggestions)
        
        # Start server task
        self.bot.loop.create_task(self.start_server())

    async def start_server(self):
        """Starts the aiohttp web server"""
        await self.bot.wait_until_ready()
        
        port = int(os.getenv("WEB_SERVER_PORT", "8080"))
        host = os.getenv("WEB_SERVER_HOST", "0.0.0.0")
        
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, host, port)
        
        try:
            await site.start()
            print(f"Web server started on http://{host}:{port}")
        except Exception as e:
            print(f"Failed to start web server: {e}")
            sentry_sdk.capture_exception(e)

    async def cog_unload(self):
        """Cleanup when cog is unloaded"""
        if self.runner:
            asyncio.create_task(self.runner.cleanup())

    @aiohttp_jinja2.template('index.html')
    async def handle_index(self, request):
        return {}

    @aiohttp_jinja2.template('index.html')
    async def handle_submit(self, request):
        data = await request.post()
        title = data.get('title', '').strip()
        author = data.get('author', '').strip()
        isbn = data.get('isbn', '').strip()
        reason = data.get('reason', '').strip()
        submitter = data.get('submitter', '').strip() or "Anonymous"
        
        if not title and not isbn:
            return {'error': 'Please provide a Title or an ISBN.'}

        try:
            # Delegate to SuggestedBooks cog to handle PocketBase insertion
            suggested_books_cog = self.bot.get_cog("SuggestedBooks")
            if not suggested_books_cog:
                raise Exception("SuggestedBooks cog not found. Cannot save suggestion.")
            
            # Since reason is not currently in the PocketBase schema, we can append it to the submitter string if present
            submitter_text = submitter
            if reason:
                submitter_text += f" (Reason: {reason})"

            display_name = await suggested_books_cog.add_suggestion(
                title=title,
                author=author,
                isbn=isbn,
                suggested_by=submitter_text,
                suggested_from="Web Form"
            )
            
            return {'success': f'Successfully suggested: {display_name}!'}
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return {'error': f'An error occurred while submitting your suggestion: {e}'}

    @aiohttp_jinja2.template('suggestions.html')
    async def handle_suggestions(self, request):
        suggested_books_cog = self.bot.get_cog("SuggestedBooks")
        if not suggested_books_cog:
            return {'error': 'Suggestions service is temporarily unavailable.'}
        
        try:
            books = await suggested_books_cog.get_raw_suggestions()
            return {'books': books}
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return {'error': 'Failed to load suggestions.'}

async def setup(bot):
    await bot.add_cog(WebServerCog(bot))
