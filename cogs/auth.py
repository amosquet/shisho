import re
import secrets
import discord
from discord import app_commands
from discord.ext import commands
import sentry_sdk

from utils.db import get_pb_client, get_discord_user_id, run_in_executor
from utils.discord_helpers import is_user_authorized


class Auth(commands.Cog):
    """Authentication and account management commands for Shisho."""

    def __init__(self, bot):
        self.bot = bot

    def _validate_email(self, email: str) -> bool:
        """Validate email format and length."""
        if not email or len(email) > 254:
            return False
        pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
        return bool(re.match(pattern, email))

    @app_commands.command(
        name="register",
        description="Create and link a Shisho account directly from Discord with an auto-generated PIN."
    )
    @app_commands.describe(
        email="Optional email address for your account. If omitted, a default Discord-linked email is used."
    )
    async def register(self, interaction: discord.Interaction, email: str | None = None):
        """Creates a Shisho account with an auto-generated secure PIN and links it to Discord."""
        await interaction.response.defer(ephemeral=True)

        if not is_user_authorized(interaction.user.id, "Auth"):
            await interaction.followup.send(
                "❌ You are not authorized to use this command.",
                ephemeral=True
            )
            return

        user_id = str(interaction.user.id)

        # Sanitize / determine email
        if email:
            email_clean = email.strip().lower()
            if not self._validate_email(email_clean):
                await interaction.followup.send(
                    "❌ Invalid email format. Please provide a valid email (e.g. `user@example.com`) or omit it to use your Discord ID.",
                    ephemeral=True
                )
                return
        else:
            email_clean = f"{user_id}@discord.shisho.local"

        def _create_user():
            pb = get_pb_client()

            # 1. Check if user is already registered / linked
            existing_pb_id = get_discord_user_id(pb, user_id)
            if existing_pb_id:
                try:
                    user_record = pb.collection("shisho_users").get_one(existing_pb_id)
                    existing_email = getattr(user_record, "email", "Unknown")
                    return {
                        "error": f"You already have a linked Shisho account with email `{existing_email}`.\n"
                                 f"Run `/account` to view your profile or `/resetpin` to generate a new PIN."
                    }
                except Exception:
                    return {
                        "error": "You already have a linked Shisho account. Run `/account` to view your profile."
                    }

            # 2. Check if email is already taken (with escaped filter string against injection)
            try:
                safe_email = email_clean.replace("\\", "\\\\").replace("'", "\\'")
                records = pb.collection("shisho_users").get_full_list(
                    query_params={"filter": f"email='{safe_email}'"}
                )
                if records:
                    return {
                        "error": f"An account with email `{email_clean}` already exists. "
                                 f"If you own this account, please link it in the companion app or use a different email."
                    }
            except Exception as e:
                sentry_sdk.capture_exception(e)

            # 3. Generate cryptographically secure 8-digit PIN (10000000-99999999, ~26.5 bits of entropy).
            # Note: This is an intentional design choice for user convenience when logging into
            # the companion app alongside Discord OAuth, not a standalone high-entropy password.
            generated_pin = f"{secrets.randbelow(90000000) + 10000000}"

            # 4. Create PocketBase shisho_users record with duplicate key handling
            try:
                new_record = pb.collection("shisho_users").create({
                    "email": email_clean,
                    "password": generated_pin,
                    "passwordConfirm": generated_pin,
                    "discord_id": user_id,
                })
            except Exception as e:
                err_str = str(e).lower()
                if "unique" in err_str or "already exists" in err_str or "validation_not_unique" in err_str:
                    return {
                        "error": "An account with this email or Discord ID already exists."
                    }
                raise

            return {
                "success": True,
                "email": email_clean,
                "pin": generated_pin,
                "user_id": getattr(new_record, "id", "")
            }

        try:
            result = await run_in_executor(_create_user)
            if "error" in result:
                await interaction.followup.send(f"⚠️ {result['error']}", ephemeral=True)
                return

            embed = discord.Embed(
                title="🎉 Shisho Account Created!",
                description=(
                    "Your Shisho account has been created and linked to your Discord profile.\n"
                    "You can now immediately use all Shisho commands (`/addbook`, `/note`, `/remind`)."
                ),
                color=discord.Color.green()
            )
            embed.add_field(name="📧 Account Email", value=f"`{result['email']}`", inline=False)
            embed.add_field(
                name="🔑 Secure Auto-Generated PIN",
                value=f"||`{result['pin']}`||\n*(Click the spoiler above to reveal your PIN)*",
                inline=False
            )
            embed.add_field(
                name="📱 Companion App Access",
                value="If you ever use the Shisho Companion App, log in using the email and PIN above.",
                inline=False
            )
            embed.set_footer(text="Keep your PIN safe! You can reset it anytime with /resetpin.")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(
                "❌ Something went wrong creating your account. Please try again later.",
                ephemeral=True
            )

    @app_commands.command(
        name="account",
        description="View your linked Shisho account information."
    )
    async def account(self, interaction: discord.Interaction):
        """Displays linked Shisho account details."""
        await interaction.response.defer(ephemeral=True)

        if not is_user_authorized(interaction.user.id, "Auth"):
            await interaction.followup.send(
                "❌ You are not authorized to use this command.",
                ephemeral=True
            )
            return

        user_id = str(interaction.user.id)

        def _get_account():
            pb = get_pb_client()
            pb_user_id = get_discord_user_id(pb, user_id)
            if not pb_user_id:
                return None
            return pb.collection("shisho_users").get_one(pb_user_id)

        try:
            record = await run_in_executor(_get_account)
            if not record:
                await interaction.followup.send(
                    "⚠️ You do not have a linked Shisho account yet.\n"
                    "Run `/register` to create one instantly!",
                    ephemeral=True
                )
                return

            email = getattr(record, "email", "N/A")
            created = getattr(record, "created", "N/A")

            embed = discord.Embed(
                title="👤 Shisho Account Profile",
                color=discord.Color.blue()
            )
            embed.add_field(name="📧 Email", value=f"`{email}`", inline=True)
            embed.add_field(name="🆔 Discord ID", value=f"`{user_id}`", inline=True)
            embed.add_field(name="📅 Member Since", value=f"{created}", inline=False)
            embed.set_footer(text="To change your PIN, run /resetpin.")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(
                "❌ Something went wrong retrieving your account details. Please try again later.",
                ephemeral=True
            )

    @app_commands.command(
        name="resetpin",
        description="Generate a new secure PIN for your linked Shisho account."
    )
    async def resetpin(self, interaction: discord.Interaction):
        """Generates a new secure PIN and updates the user's PocketBase account."""
        await interaction.response.defer(ephemeral=True)

        if not is_user_authorized(interaction.user.id, "Auth"):
            await interaction.followup.send(
                "❌ You are not authorized to use this command.",
                ephemeral=True
            )
            return

        user_id = str(interaction.user.id)

        def _reset_user_pin():
            pb = get_pb_client()
            pb_user_id = get_discord_user_id(pb, user_id)
            if not pb_user_id:
                return None

            # Generate cryptographically secure 8-digit PIN (~26.5 bits entropy for companion app access).
            new_pin = f"{secrets.randbelow(90000000) + 10000000}"
            pb.collection("shisho_users").update(pb_user_id, {
                "password": new_pin,
                "passwordConfirm": new_pin,
            })
            return new_pin

        try:
            new_pin = await run_in_executor(_reset_user_pin)
            if not new_pin:
                await interaction.followup.send(
                    "⚠️ You do not have a linked Shisho account yet.\n"
                    "Run `/register` to create an account!",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="🔑 Shisho PIN Reset Successful",
                description="Your account PIN has been regenerated. Use this new PIN if you log in to the companion app.",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="New Secure PIN",
                value=f"||`{new_pin}`||\n*(Click the spoiler to reveal)*",
                inline=False
            )
            embed.set_footer(text="Keep this PIN private.")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(
                "❌ Something went wrong resetting your PIN. Please try again later.",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(Auth(bot))
