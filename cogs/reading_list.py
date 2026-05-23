import base64
import json
import os
from datetime import datetime

import discord
from discord import app_commands
import sentry_sdk
from discord.ext import commands
from github import Github, GithubException


class ReadingList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.github_token = os.getenv("GITHUB_TOKEN")
        self.repo_name = os.getenv("GITHUB_REPO")
        self.gh = Github(self.github_token)

    @app_commands.command(name="addbook", description="Adds a book to the reading list on GitHub.")
    @app_commands.describe(
        title="Title of the book",
        author="Author of the book",
        publish_date="Publish date",
        isbn="ISBN of the book",
        status="Status of the book",
        start_date="Start reading date (YYYY-MM-DD)",
        end_date="Finished reading date (YYYY-MM-DD)"
    )
    @app_commands.choices(status=[
        app_commands.Choice(name="Planned", value="planned"),
        app_commands.Choice(name="Reading", value="reading"),
        app_commands.Choice(name="Read", value="read"),
        app_commands.Choice(name="Dropped", value="dropped"),
    ])
    async def add_book(
        self,
        interaction: discord.Interaction,
        title: str,
        author: str,
        publish_date: str,
        isbn: str,
        status: app_commands.Choice[str],
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        await interaction.response.defer()
        status_val = status.value
        today = datetime.now().strftime("%Y-%m-%d")

        final_start_date = (
            start_date
            if start_date
            else (today if status_val in ["read", "reading"] else "")
        )
        final_end_date = end_date if end_date else (today if status_val == "read" else "")

        if not self.repo_name or not self.github_token:
            await interaction.followup.send(
                "Error: GitHub configuration missing in environment variables."
            )
            return

        try:
            try:
                repo = self.gh.get_repo(self.repo_name)
            except GithubException as e:
                if e.status == 404:
                    await interaction.followup.send(f"Error: Repository '{self.repo_name}' not found.")
                    return
                raise e

            file_path = "src/data/reading.json"
            try:
                # Use executor to avoid blocking the event loop on GitHub API calls
                def get_contents():
                    return repo.get_contents(file_path)
                contents = await self.bot.loop.run_in_executor(None, get_contents)
                
                if isinstance(contents, list):
                    await interaction.followup.send(f"Error: '{file_path}' is a directory.")
                    return

                if contents.content is None:
                    await interaction.followup.send(f"Error: '{file_path}' has no content.")
                    return

                current_data = json.loads(
                    base64.b64decode(contents.content).decode("utf-8")
                )
                sha = contents.sha
                if sha is None:
                    await interaction.followup.send(f"Error: Could not retrieve SHA for '{file_path}'.")
                    return
            except GithubException as e:
                if e.status == 404:
                    # File doesn't exist, initialise with empty list
                    current_data = []
                    sha = None
                else:
                    raise e

            new_book = {
                "title": title,
                "author": author,
                "status": status_val,
                "startDate": final_start_date,
                "endDate": final_end_date,
                "publishDate": publish_date,
                "isbn": isbn,
            }

            current_data.insert(0, new_book)
            updated_content = json.dumps(current_data, indent=2)

            def update_or_create_file():
                if sha:
                    repo.update_file(
                        path=file_path,
                        message=f"Add book: {title} by {author}",
                        content=updated_content,
                        sha=sha,
                    )
                else:
                    repo.create_file(
                        path=file_path,
                        message=f"Initialise {file_path} and add book: {title} by {author}",
                        content=updated_content,
                    )
            await self.bot.loop.run_in_executor(None, update_or_create_file)

            await interaction.followup.send(
                f"Successfully added **{title}** by {author} to the reading list!"
            )

        except GithubException as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(
                f"An error occurred while interacting with GitHub: {e.data.get('message', str(e))}"
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An unexpected error occurred: {e}")


async def setup(bot):
    await bot.add_cog(ReadingList(bot))
