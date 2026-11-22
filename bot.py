import asyncio
import datetime
import io
import json
import os
import re
import time
import aiohttp
from aiohttp import web
import discord
from discord.ext import commands

# =====================================================================
# 1. CONFIGURATION & CHIKAGO CROSSFIRE TICKET CATEGORIES
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
CFG_FILE = "chikago_ticket_config.json"
START_TIME = time.time()
BOT_STATUS = "Starting..."

# 6 Custom Ticket Categories tailored for Chikago Crossfire
TICKET_TYPES = {
    "support": (
        "General Support",
        "support",
        "💬",
        discord.Color.blurple(),
        "Describe your question or issue below and our staff will assist you shortly.",
        "Question / Topic",
        "Explain what you need help with...",
    ),
    "report": (
        "Report Player / Exploiter",
        "report",
        "🚨",
        discord.Color.red(),
        "Attach any video clips, screenshots, or proof of the rulebreaker or exploiter.",
        "Reported Player's Roblox Username",
        "What rule did they break? Paste clip/screenshot links here...",
    ),
    "appeal": (
        "Ban / Mute Appeal",
        "appeal",
        "⚖️",
        discord.Color.gold(),
        "Explain clearly why you were punished and why staff should lift your ban or mute.",
        "What were you banned/muted for?",
        "Write an honest explanation and why you deserve a second chance...",
    ),
    "bug": (
        "Bug & Glitch Report",
        "bug",
        "🐛",
        discord.Color.orange(),
        "Share steps to reproduce the bug, map location, and any screenshots or clips.",
        "Where did the bug happen?",
        "Explain what broke and how we can recreate it in-game...",
    ),
    "shop": (
        "Gamepass / Shop Issue",
        "shop",
        "💳",
        discord.Color.purple(),
        "Include the Gamepass/item name and a screenshot of your Roblox purchase receipt.",
        "Gamepass / Item Name",
        "Describe the purchase issue (never share your Roblox password!)...",
    ),
    "faction": (
        "Faction / PD / Partner",
        "faction",
        "🤝",
        discord.Color.green(),
        "Share your department, gang/faction name, member count, or partnership proposal.",
        "Faction / Department / Server Name",
        "Tell us about your faction request, PD inquiry, or partnership...",
    ),
}


def load_cfg() -> dict:
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cfg(data: dict) -> None:
    try:
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


GUILD_CFG = load_cfg()


def get_gcfg(guild_id: int) -> dict:
    return GUILD_CFG.get(str(guild_id), {})


def is_staff(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
        return True
    staff_rid = get_gcfg(member.guild.id).get("staff_role_id")
    return bool(staff_rid and any(r.id == staff_rid for r in member.roles))


# =====================================================================
# 2. RENDER 24/7 KEEP-ALIVE WEB SERVER & SELF-PINGER
# =====================================================================
async def run_web():
    """Binds to Render's PORT so Render keeps the Web Service active."""
    app = web.Application()

    async def health(request):
        up = int((time.time() - START_TIME) // 60)
        return web.Response(
            text=f"Chikago Crossfire Ticket Bot 24/7 | Uptime: {up}m | Status: {BOT_STATUS}"
        )

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"🌐 Keep-alive web server listening on 0.0.0.0:{port}")


async def self_ping():
    """Periodically pings the Render URL every 4 minutes."""
    await asyncio.sleep(25)
    url = os.environ.get("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/"
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url, timeout=10):
                    pass
            except Exception:
                pass
            await asyncio.sleep(240)


# =====================================================================
# 3. FULL TRANSCRIPT GENERATOR & CLOSURE HANDLER (DM + STAFF LOG)
# =====================================================================
async def build_transcript(
    channel: discord.TextChannel,
    closed_by: discord.Member,
    reason: str,
) -> tuple[bytes, int]:
    """Reads all messages in the ticket channel and formats a clean .txt transcript."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "================================================================",
        "          CHIKAGO CROSSFIRE — OFFICIAL TICKET TRANSCRIPT        ",
        "================================================================",
        f"Server    : {channel.guild.name}",
        f"Channel   : #{channel.name} ({channel.id})",
        f"Closed By : {closed_by} ({closed_by.id})",
        f"Reason    : {reason}",
        f"Closed At : {now}",
        "================================================================\n",
    ]
    count = 0
    async for msg in channel.history(limit=None, oldest_first=True):
        count += 1
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        tag = " [BOT]" if msg.author.bot else ""
        lines.append(f"[{ts}] {msg.author.display_name} (@{msg.author.name}){tag}:")
        if msg.content:
            lines.append(f"  {msg.content}")
        for emb in msg.embeds:
            if emb.title:
                lines.append(f"  [Embed] {emb.title}")
            if emb.description:
                lines.append(f"  {emb.description}")
            for field in emb.fields:
                lines.append(f"  • {field.name}: {field.value}")
        for att in msg.attachments:
            lines.append(f"  [Attachment] {att.filename} -> {att.url}")
        lines.append("")

    return "\n".join(lines).encode("utf-8"), count


async def close_ticket_and_send_transcript(
    interaction: discord.Interaction,
    reason: str = "Resolved by staff",
) -> None:
    """Generates transcript, DMs it to the player, logs it for staff, and deletes channel."""
    channel = interaction.channel
    guild = interaction.guild
    topic = channel.topic or ""

    if not topic.startswith("CCTicket |"):
        return await interaction.response.send_message(
            "❌ This command/button can only be used inside a Chikago Crossfire ticket channel.",
            ephemeral=True,
        )

    await interaction.response.send_message(
        f"📄 Generating full transcript and DMing the player... Closing in **5 seconds**.\n"
        f"> **Close Reason:** {reason}"
    )

    # Extract ticket owner ID and category key from channel topic
    uid_m = re.search(r"UserID:(\d+)", topic)
    type_m = re.search(r"Type:([a-z]+)", topic)
    opener_id = int(uid_m.group(1)) if uid_m else None
    t_key = type_m.group(1) if type_m and type_m.group(1) in TICKET_TYPES else "support"
    t_title = TICKET_TYPES[t_key][0]

    raw_bytes, msg_count = await build_transcript(channel, interaction.user, reason)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M")
    filename = f"transcript-{channel.name}-{stamp}.txt"

    # 1. DM the player with summary embed + attached transcript file
    dm_status = "⚠️ Player not found"
    if opener_id:
        member = guild.get_member(opener_id)
        if not member:
            try:
                member = await interaction.client.fetch_user(opener_id)
            except Exception:
                member = None

        if member:
            dm_emb = discord.Embed(
                title=f"📄 Your {guild.name} Ticket Transcript",
                description=(
                    f"Your **`{t_title}`** ticket (`#{channel.name}`) has been closed.\n"
                    f"Your complete chat transcript is attached below so you can read it anytime!"
                ),
                color=discord.Color.blurple(),
            )
            dm_emb.add_field(name="🛡️ Closed By", value=interaction.user.display_name, inline=True)
            dm_emb.add_field(name="💬 Messages Saved", value=f"`{msg_count}`", inline=True)
            dm_emb.add_field(name="📝 Reason", value=reason[:500], inline=False)
            dm_emb.set_footer(text="Chikago Crossfire Support System")
            try:
                await member.send(
                    embed=dm_emb,
                    file=discord.File(io.BytesIO(raw_bytes), filename=filename),
                )
                dm_status = "✅ Delivered to Player's DMs"
            except discord.Forbidden:
                dm_status = "⚠️ Failed (Player has Discord DMs disabled)"
            except Exception as e:
                dm_status = f"⚠️ Failed ({type(e).__name__})"

    # 2. Send archive copy to Staff Transcript Log Channel
    log_ch_id = get_gcfg(guild.id).get("log_channel_id")
    log_ch = guild.get_channel(log_ch_id) if log_ch_id else None
    if log_ch:
        log_emb = discord.Embed(
            title=f"📁 Ticket Closed — #{channel.name}",
            description=(
                f"🎫 **Category:** `{t_title}`\n"
                f"👤 **Opened By:** {f'<@{opener_id}>' if opener_id else 'Unknown'}\n"
                f"🛡️ **Closed By:** {interaction.user.mention}\n"
                f"💬 **Messages Saved:** `{msg_count}`\n"
                f"📬 **Player DM Status:** {dm_status}\n"
                f"📝 **Reason:** {reason[:500]}"
            ),
            color=discord.Color.dark_grey(),
        )
        try:
            await log_ch.send(
                embed=log_emb,
                file=discord.File(io.BytesIO(raw_bytes), filename=filename),
            )
        except Exception:
            pass

    # 3. Wait 5 seconds, then delete the ticket channel
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {interaction.user}: {reason}")
    except Exception:
        pass


# =====================================================================
# 4. INTAKE & CLOSE REASON MODALS
# =====================================================================
class TicketIntakeModal(discord.ui.Modal):
    def __init__(self, t_key: str):
        title, _, emoji, _, _, subj_label, desc_ph = TICKET_TYPES[t_key]
        super().__init__(title=f"{emoji} {title}"[:45])
        self.t_key = t_key

        self.roblox_user = discord.ui.TextInput(
            label="Your Roblox Username",
            placeholder="Enter your exact Roblox username...",
            required=True,
            max_length=50,
        )
        self.subject = discord.ui.TextInput(
            label=subj_label[:45],
            required=True,
            max_length=120,
        )
        self.details = discord.ui.TextInput(
            label="Details / Evidence Links",
            placeholder=desc_ph[:100],
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=900,
        )
        self.add_item(self.roblox_user)
        self.add_item(self.subject)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user
        title, prefix, emoji, color, welcome, subj_label, _ = TICKET_TYPES[self.t_key]

        # Block duplicate open tickets in the same category
        for ch in guild.text_channels:
            if ch.topic and f"UserID:{user.id}" in ch.topic and f"Type:{self.t_key}" in ch.topic:
                return await interaction.followup.send(
                    f"⚠️ You already have an open **{title}** ticket: {ch.mention}",
                    ephemeral=True,
                )

        gcfg = get_gcfg(guild.id)
        staff_role = guild.get_role(gcfg.get("staff_role_id", 0))
        category = guild.get_channel(gcfg.get("category_id", 0))
        if not isinstance(category, discord.CategoryChannel):
            category = None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }
        staff_ping = ""
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            )
            staff_ping = staff_role.mention

        safe_name = re.sub(r"[^a-z0-9-]", "", user.name.lower())[:16] or "player"
        try:
            ticket_ch = await guild.create_text_channel(
                name=f"{prefix}-{safe_name}",
                category=category,
                overwrites=overwrites,
                topic=f"CCTicket | Type:{self.t_key} | UserID:{user.id}",
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ I don't have **Manage Channels** permission to create your ticket. Please alert an Admin!",
                ephemeral=True,
            )

        emb = discord.Embed(
            title=f"{emoji} Chikago Crossfire — {title}",
            description=f"Welcome {user.mention}! {welcome}",
            color=color,
        )
        emb.add_field(name="🎮 Roblox Username", value=f"`{self.roblox_user.value}`", inline=True)
        emb.add_field(name="🙋‍♂️ Claimed By", value="*Unclaimed*", inline=True)
        emb.add_field(name=f"📌 {subj_label}", value=self.subject.value, inline=False)
        emb.add_field(name="📝 Details / Evidence", value=self.details.value, inline=False)
        emb.set_footer(text="When closed, a full transcript will automatically be sent to your DMs.")

        await ticket_ch.send(
            content=f"{user.mention} {staff_ping}".strip(),
            embed=emb,
            view=TicketControlView(),
        )
        await interaction.followup.send(
            f"✅ Your **{title}** ticket has been created: {ticket_ch.mention}",
            ephemeral=True,
        )


class CloseReasonModal(discord.ui.Modal, title="🔒 Close Ticket & Send Transcript"):
    reason = discord.ui.TextInput(
        label="Closing Reason (Shown in Player's DM)",
        placeholder="e.g. Issue resolved / Exploiter banned / Appeal reviewed",
        default="Issue resolved",
        required=True,
        max_length=300,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await close_ticket_and_send_transcript(interaction, self.reason.value)


# =====================================================================
# 5. PERSISTENT INTERACTIVE BUTTON VIEWS
# =====================================================================
class TicketControlView(discord.ui.View):
    """Buttons shown inside every open ticket channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim Ticket",
        style=discord.ButtonStyle.success,
        emoji="🙋‍♂️",
        custom_id="cc_ctrl:claim",
        row=0,
    )
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only Chikago Crossfire staff can claim tickets.", ephemeral=True
            )
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"[:80]
        button.style = discord.ButtonStyle.secondary

        emb = interaction.message.embeds[0] if interaction.message.embeds else None
        if emb and len(emb.fields) > 1:
            emb.set_field_at(1, name="🙋‍♂️ Claimed By", value=interaction.user.mention, inline=True)
            await interaction.response.edit_message(embed=emb, view=self)
        else:
            await interaction.response.edit_message(view=self)

        await interaction.channel.send(
            f"✅ {interaction.user.mention} has claimed this ticket and will assist you now!"
        )

    @discord.ui.button(
        label="Close & DM Transcript",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="cc_ctrl:close",
        row=0,
    )
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        topic = interaction.channel.topic or ""
        is_opener = f"UserID:{interaction.user.id}" in topic
        if not (is_staff(interaction.user) or is_opener):
            return await interaction.response.send_message(
                "❌ Only staff or the ticket opener can close this ticket.", ephemeral=True
            )
        await interaction.response.send_modal(CloseReasonModal())


class TicketPanelView(discord.ui.View):
    """6-Button Chikago Crossfire Support Panel (Persistent across restarts)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="General Support", style=discord.ButtonStyle.primary, emoji="💬", custom_id="cc_panel:support", row=0)
    async def b_support(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketIntakeModal("support"))

    @discord.ui.button(label="Report Player / Exploiter", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="cc_panel:report", row=0)
    async def b_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketIntakeModal("report"))

    @discord.ui.button(label="Ban / Mute Appeal", style=discord.ButtonStyle.secondary, emoji="⚖️", custom_id="cc_panel:appeal", row=1)
    async def b_appeal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketIntakeModal("appeal"))

    @discord.ui.button(label="Bug & Glitch Report", style=discord.ButtonStyle.secondary, emoji="🐛", custom_id="cc_panel:bug", row=1)
    async def b_bug(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketIntakeModal("bug"))

    @discord.ui.button(label="Gamepass / Shop Issue", style=discord.ButtonStyle.primary, emoji="💳", custom_id="cc_panel:shop", row=2)
    async def b_shop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketIntakeModal("shop"))

    @discord.ui.button(label="Faction / PD / Partner", style=discord.ButtonStyle.success, emoji="🤝", custom_id="cc_panel:faction", row=2)
    async def b_faction(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketIntakeModal("faction"))


# =====================================================================
# 6. BOT CORE & SLASH COMMANDS
# =====================================================================
class ChikagoBot(commands.Bot):
    """Main Discord bot client with persistent ticket views and slash command sync."""

    def __init__(self, msg_intent: bool = True):
        intents = discord.Intents.default()
        intents.message_content = msg_intent
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self._synced = False

    async def setup_hook(self):
        # Re-register persistent button views so panels keep working after restarts
        self.add_view(TicketPanelView())
        self.add_view(TicketControlView())
        if not self._synced:
            try:
                await self.tree.sync()
                self._synced = True
            except Exception as e:
                print(f"⚠️ Command sync notice: {e}")

    async def on_ready(self):
        global BOT_STATUS
        BOT_STATUS = f"ONLINE as {self.user}"
        print(f"✅ Chikago Crossfire Ticket Bot ONLINE as {self.user} (ID: {self.user.id})")
        try:
            await self.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name="Chikago Crossfire Support 🎫",
                )
            )
        except Exception:
            pass


def reg_cmds(bot: ChikagoBot):
    """Registers all Chikago Crossfire ticket management slash commands."""

    @bot.tree.command(
        name="ticket_setup",
        description="Configure Staff Role, Ticket Category & Transcript Log Channel",
    )
    async def s_setup(
        interaction: discord.Interaction,
        staff_role: discord.Role,
        ticket_category: discord.CategoryChannel,
        transcript_log_channel: discord.TextChannel,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ Only server Administrators can run `/ticket_setup`.", ephemeral=True
            )
        GUILD_CFG[str(interaction.guild.id)] = {
            "staff_role_id": staff_role.id,
            "category_id": ticket_category.id,
            "log_channel_id": transcript_log_channel.id,
        }
        save_cfg(GUILD_CFG)
        emb = discord.Embed(
            title="✅ Chikago Crossfire Ticket System Configured!",
            description=(
                f"🛡️ **Staff Role:** {staff_role.mention}\n"
                f"📁 **Ticket Category:** `{ticket_category.name}`\n"
                f"📜 **Transcript Logs:** {transcript_log_channel.mention}\n\n"
                f"👉 Next, go to your support channel and run **`/ticket_panel`**!"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @bot.tree.command(
        name="ticket_panel",
        description="Post the 6-button Chikago Crossfire Support & Ticket Panel",
    )
    async def s_panel(interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        emb = discord.Embed(
            title="🔥 CHIKAGO CROSSFIRE — SUPPORT & TICKET CENTER",
            description=(
                "Need assistance in **Chikago Crossfire**? Select a category below to open a private ticket with our team!\n\n"
                "💬 **General Support** — Questions, gameplay help & general inquiries\n"
                "🚨 **Report Player / Exploiter** — Report cheaters, RDM, or rulebreakers with proof\n"
                "⚖️ **Ban / Mute Appeal** — Appeal an in-game or Discord punishment\n"
                "🐛 **Bug & Glitch Report** — Report map glitches, weapon bugs, or crashes\n"
                "💳 **Gamepass / Shop Issue** — Missing gamepasses or Roblox store issues\n"
                "🤝 **Faction / PD / Partner** — Faction approvals, PD inquiries & partnerships\n\n"
                "📄 *Every closed ticket automatically sends a full transcript to your DMs!*"
            ),
            color=discord.Color.from_rgb(220, 53, 69),
        )
        emb.set_footer(text="Chikago Crossfire Support • Do not open troll tickets")
        await interaction.response.send_message(embed=emb, view=TicketPanelView())

    @bot.tree.command(name="add_user", description="Add another player to this ticket channel")
    async def s_add(interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        if not (interaction.channel.topic or "").startswith("CCTicket |"):
            return await interaction.response.send_message("❌ Use this inside a ticket channel.", ephemeral=True)
        await interaction.channel.set_permissions(
            member, view_channel=True, send_messages=True, read_message_history=True
        )
        await interaction.response.send_message(f"➕ Added {member.mention} to this ticket.")

    @bot.tree.command(name="remove_user", description="Remove a player from this ticket channel")
    async def s_remove(interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        if not (interaction.channel.topic or "").startswith("CCTicket |"):
            return await interaction.response.send_message("❌ Use this inside a ticket channel.", ephemeral=True)
        await interaction.channel.set_permissions(member, overwrite=None)
        await interaction.response.send_message(f"➖ Removed {member.mention} from this ticket.")

    @bot.tree.command(name="close", description="Close this ticket and DM the full transcript to the player")
    async def s_close(interaction: discord.Interaction, reason: str = "Resolved by staff"):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await close_ticket_and_send_transcript(interaction, reason)


# =====================================================================
# 7. MAIN ENTRYPOINT WITH 24/7 RENDER SERVER & 429 AUTO-RETRY
# =====================================================================
async def main():
    global BOT_STATUS
    await run_web()
    asyncio.create_task(self_ping())

    if not BOT_TOKEN:
        BOT_STATUS = "MISSING BOT_TOKEN"
        print("❌ ERROR: Missing BOT_TOKEN! Add BOT_TOKEN in Render Environment Variables.")
        while True:
            await asyncio.sleep(3600)

    msg_intent = True
    retry_wait = 90

    while True:
        bot = ChikagoBot(msg_intent)
        reg_cmds(bot)
        try:
            await bot.start(BOT_TOKEN)
            break
        except discord.PrivilegedIntentsRequired:
            print("⚠️ Message Content Intent is OFF in Developer Portal — starting without it (enable it for full transcripts!).")
            await bot.close()
            msg_intent = False
        except discord.LoginFailure:
            BOT_STATUS = "INVALID BOT_TOKEN"
            print("❌ ERROR: Invalid BOT_TOKEN! Reset your token in Discord Developer Portal.")
            await bot.close()
            while True:
                await asyncio.sleep(3600)
        except discord.HTTPException as e:
            await bot.close()
            if e.status == 429:
                BOT_STATUS = f"Rate-limited (429) - retrying in {retry_wait}s"
                print(f"⏳ Discord 429 Rate Limit on shared host IP. Waiting {retry_wait}s before auto-retry...")
                await asyncio.sleep(retry_wait)
                retry_wait = min(retry_wait + 60, 300)
            else:
                BOT_STATUS = f"HTTP {e.status} - retrying in 60s"
                print(f"⚠️ Discord HTTP {e.status}: retrying in 60s...")
                await asyncio.sleep(60)
        except Exception as e:
            await bot.close()
            BOT_STATUS = f"Reconnecting ({type(e).__name__})"
            print(f"⚠️ Connection error ({type(e).__name__}: {e}) — retrying in 60s...")
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
