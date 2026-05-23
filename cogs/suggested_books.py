import base64
import json
import os
from datetime import datetime

import sentry_sdk
from discord.ext import commands
from github import Github, GithubException


class SuggestedBooks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.github_token = os.getenv("GITHUB_TOKEN")
        self.repo_name = os.getenv("GITHUB_REPO")
        self.gh = Github(self.github_token)

    @commands.command(name="suggest")
    async def suggest_book(self, ctx, *, query: str):
        """Suggests a book. Provide "Title" "Author" and/or an ISBN.
        Usage: !suggest "Project Hail Mary" "Andy Weir"
        Usage: !suggest 9780593135211
        """
        title = ""
        author = ""
        isbn = ""

        parts = query.split('"')
        clean_parts = [p.strip() for p in parts if p.strip()]

        if len(clean_parts) == 1:
            if clean_parts[0].isdigit():
                isbn = clean_parts[0]
                display_name = f"ISBN: {isbn}"
            else:
                title = clean_parts[0]
                display_name = f"**{title}**"
        elif len(clean_parts) >= 2:
            title = clean_parts[0]
            author = clean_parts[1]
            display_name = f"**{title}** by {author}"
            if len(clean_parts) >= 3 and clean_parts[2].isdigit():
                isbn = clean_parts[2]
        else:
            await ctx.send("Please provide a title, author, or ISBN.")
            return

        if not self.repo_name or not self.github_token:
            await ctx.send("Error: GitHub configuration missing.")
            return

        try:
            repo = self.gh.get_repo(self.repo_name)
            file_path = "src/data/suggested_books.json"

            try:
                contents = repo.get_contents(file_path)
                if isinstance(contents, list) or contents.content is None:
                    raise Exception("Invalid file structure")
                current_data = json.loads(
                    base64.b64decode(contents.content).decode("utf-8")
                )
                sha = contents.sha
            except GithubException as e:
                if e.status == 404:
                    current_data = []
                    sha = None
                else:
                    raise e

            entry = {
                "title": title,
                "author": author,
                "isbn": isbn,
                "suggestedBy": str(ctx.author),
                "dateSuggested": datetime.now().strftime("%Y-%m-%d"),
            }

            current_data.insert(0, entry)
            updated_content = json.dumps(current_data, indent=2)

            commit_msg = f"New suggested book: {title if title else isbn}"
            if sha:
                repo.update_file(file_path, commit_msg, updated_content, sha)
            else:
                repo.create_file(
                    file_path,
                    f"Initialise {file_path} and add suggested book",
                    updated_content,
                )

            await ctx.send(
                f"Thanks {ctx.author.mention}! {display_name} has been added to the suggested books list."
            )

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await ctx.send(f"An error occurred: {e}")


async def setup(bot):
    await bot.add_cog(SuggestedBooks(bot))
