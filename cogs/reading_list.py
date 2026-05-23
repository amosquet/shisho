import base64
import json
import os
from datetime import datetime

import sentry_sdk
from discord.ext import commands
from github import Github, GithubException


class ReadingList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.github_token = os.getenv("GITHUB_TOKEN")
        self.repo_name = os.getenv("GITHUB_REPO")
        self.gh = Github(self.github_token)

    @commands.command(name="addbook")
    async def add_book(
        self,
        ctx,
        title: str,
        author: str,
        publish_date: str,
        isbn: str,
        status: str = "planned",
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """Adds a book to the reading list on GitHub.
        Acceptable statuses: read, reading, planned, dropped
        """
        valid_statuses = ["read", "reading", "planned", "dropped"]
        if status.lower() not in valid_statuses:
            await ctx.send(
                f"Invalid status. Please use one of: {', '.join(valid_statuses)}"
            )
            return

        status = status.lower()
        today = datetime.now().strftime("%Y-%m-%d")

        final_start_date = (
            start_date
            if start_date
            else (today if status in ["read", "reading"] else "")
        )
        final_end_date = end_date if end_date else (today if status == "read" else "")

        if not self.repo_name or not self.github_token:
            await ctx.send(
                "Error: GitHub configuration missing in environment variables."
            )
            return

        try:
            try:
                repo = self.gh.get_repo(self.repo_name)
            except GithubException as e:
                if e.status == 404:
                    await ctx.send(f"Error: Repository '{self.repo_name}' not found.")
                    return
                raise e

            file_path = "src/data/reading.json"
            try:
                contents = repo.get_contents(file_path)
                if isinstance(contents, list):
                    await ctx.send(f"Error: '{file_path}' is a directory.")
                    return

                if contents.content is None:
                    await ctx.send(f"Error: '{file_path}' has no content.")
                    return

                current_data = json.loads(
                    base64.b64decode(contents.content).decode("utf-8")
                )
                sha = contents.sha
                if sha is None:
                    await ctx.send(f"Error: Could not retrieve SHA for '{file_path}'.")
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
                "status": status,
                "startDate": final_start_date,
                "endDate": final_end_date,
                "publishDate": publish_date,
                "isbn": isbn,
            }

            current_data.insert(0, new_book)
            updated_content = json.dumps(current_data, indent=2)

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

            await ctx.send(
                f"Successfully added **{title}** by {author} to the reading list!"
            )

        except GithubException as e:
            sentry_sdk.capture_exception(e)
            await ctx.send(
                f"An error occurred while interacting with GitHub: {e.data.get('message', str(e))}"
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await ctx.send(f"An unexpected error occurred: {e}")


async def setup(bot):
    await bot.add_cog(ReadingList(bot))
