import os
import discord
from discord.ext import commands
from discord import app_commands
from google import genai
from google.genai import errors, types

class AIChat(commands.Cog):
    """General AI Chat command."""

    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

    def get_system_instruction(self):
        prompt_file = "gemini_prompt.txt"
        if os.path.exists(prompt_file):
            try:
                with open(prompt_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception as e:
                print(f"Failed to read {prompt_file}: {e}")
        return None

    @commands.command(name="gemini", help="Ask Gemini a question.")
    async def gemini_prefix(self, ctx, *, prompt: str):
        if not self.client:
            await ctx.send("Gemini API key is not configured. Please set GEMINI_API_KEY in the environment.")
            return

        async with ctx.typing():
            try:
                sys_prompt = self.get_system_instruction()
                config = types.GenerateContentConfig(
                    system_instruction=sys_prompt if sys_prompt else None,
                    tools=[{"google_search": {}}]
                )
                
                response = await self.client.aio.models.generate_content(
                    model='gemini-3.1-flash',
                    contents=prompt,
                    config=config
                )
                
                text = response.text
                if not text:
                    await ctx.send("Received empty response from Gemini.")
                    return
                
                # Discord has a 2000 character limit per message
                chunks = [text[i:i+1990] for i in range(0, len(text), 1990)]
                for chunk in chunks:
                    await ctx.send(chunk)
            except errors.APIError as e:
                await ctx.send(f"API Error: {str(e)}")
            except Exception as e:
                await ctx.send(f"An unexpected error occurred: {str(e)}")

    @app_commands.command(name="gemini", description="Send a prompt to the Gemini API")
    @app_commands.describe(prompt="The prompt to send to Gemini")
    async def gemini_slash(self, interaction: discord.Interaction, prompt: str):
        if not self.client:
            await interaction.response.send_message("Gemini API key is not configured.", ephemeral=True)
            return

        # Defer the response since Gemini generation might take a few seconds
        await interaction.response.defer()
        
        try:
            sys_prompt = self.get_system_instruction()
            config = types.GenerateContentConfig(
                system_instruction=sys_prompt if sys_prompt else None,
                tools=[{"google_search": {}}]
            )
            
            response = await self.client.aio.models.generate_content(
                model='gemini-3.1-flash',
                contents=prompt,
                config=config
            )
            
            text = response.text
            if not text:
                await interaction.followup.send("Received empty response from Gemini.")
                return
            
            chunks = [text[i:i+1990] for i in range(0, len(text), 1990)]
            await interaction.followup.send(chunks[0])
            for chunk in chunks[1:]:
                # Using followup for additional chunks
                await interaction.followup.send(chunk)
                    
        except errors.APIError as e:
            await interaction.followup.send(f"API Error: {str(e)}")
        except Exception as e:
            await interaction.followup.send(f"An unexpected error occurred: {str(e)}")


async def setup(bot):
    await bot.add_cog(AIChat(bot))
