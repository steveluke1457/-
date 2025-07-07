"""
IDK BOT – single‑file Discord bot (main.py)
100 % online deployment (GitHub Actions + UptimeRobot)
"""

#############
# KEEP‑ALIVE
#############
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

def _keep_alive():
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content‑type", "text/plain")
            self.end_headers()
            self.wfile.write(b"IDK BOT is alive!")

    server = HTTPServer(("0.0.0.0", 8000), _Handler)
    server.serve_forever()

threading.Thread(target=_keep_alive, daemon=True).start()

################
# CORE IMPORTS
################
import os
import asyncio
import datetime
from typing import Dict, List

import discord
from discord.ext import commands
from discord import app_commands, ui

from dotenv import load_dotenv
import aiohttp
from groq import Groq
import yt_dlp

load_dotenv()

TOKEN        = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

###########
#  IDs
###########
ADMIN_ROLE_ID  = 1386966204442218547
MOD_ROLE_ID    = 1386966260754944030
OWNER_ROLE_ID  = 1386965991145345024
EXEMPT_ROLES   = {ADMIN_ROLE_ID, MOD_ROLE_ID, OWNER_ROLE_ID}

LOG_CHANNEL_ID       = 0
MUSIC_VC_ID          = 1389227384506421308
COUNTING_CHANNEL_ID  = 1389228645570314272

#############
#  Discord
#############
intents = discord.Intents.default()
intents.message_content = True
intents.members         = True
intents.presences       = True

bot = commands.Bot(command_prefix="!", intents=intents)

groq_client = Groq(api_key=GROQ_API_KEY)

############################
#  GLOBAL STATE / UTILITIES
############################
strike_data: Dict[int, List[datetime.datetime]] = {}
music_queue: List[dict] = []
conversations: Dict[int, List[dict]] = {}

def exempt(member: discord.Member) -> bool:
    return any(role.id in EXEMPT_ROLES for role in member.roles)

async def log(msg: str):
    if LOG_CHANNEL_ID:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            await ch.send(msg)

########################
#  GROQ HELPERS
########################
async def groq_moderate(text: str) -> bool:
    try:
        res = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model="meta-llama/Llama-Guard-4-12B",
            messages=[{"role": "user", "content": text}]
        )
        verdict = res.choices[0].message.content.lower()
        return verdict.startswith("unsafe")
    except Exception:
        return False

async def groq_chat(user_id: int, prompt: str) -> str:
    history = conversations.setdefault(user_id, [])
    history.append({"role": "user", "content": prompt})
    try:
        res = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            messages=history[-20:],
            temperature=0.7
        )
        reply = res.choices[0].message.content.strip()
    except Exception:
        reply = "Sorry, I’m having trouble right now."
    history.append({"role": "assistant", "content": reply})
    conversations[user_id] = history[-30:]
    return reply

########################
#  STRIKE SYSTEM
########################
STRIKE_WINDOW_DAYS = 7

async def give_strike(member: discord.Member, reason: str):
    now = datetime.datetime.utcnow()
    times = [t for t in strike_data.get(member.id, []) if (now - t).days < STRIKE_WINDOW_DAYS]
    times.append(now)
    strike_data[member.id] = times
    count = len(times)

    msg = ""
    if count == 1:
        msg = f"Strike 1 / 8 — You violated server rules ({reason}). Please keep the community safe and friendly."
    elif count == 2:
        msg = f"Strike 2 — Continued rule breaking ({reason}). Final warning before punishments."
    elif count == 3:
        await member.timeout(datetime.timedelta(minutes=5), reason="Strike 3")
        msg = f"Strike 3 — Timed-out for 5 minutes for {reason}."
    elif count == 4:
        await member.timeout(datetime.timedelta(minutes=30), reason="Strike 4")
        msg = f"Strike 4 — Timed-out for 30 minutes for {reason}."
    elif count == 5:
        await member.timeout(datetime.timedelta(days=1), reason="Strike 5")
        msg = f"Strike 5 — Timed-out for 24 hours for {reason}."
    elif count == 6:
        await member.kick(reason="Strike 6 – " + reason)
        msg = f"Strike 6 — You were kicked for repeated {reason}."
    else:
        await member.ban(reason="Strike 7 – " + reason)
        msg = f"Strike 7 — You were banned for continuous infractions."

    try:
        await member.send(msg)
    except Exception:
        pass
    await log(f"{member} received strike {count}: {reason}")

########################
#  TICKET SYSTEM
########################
class TicketControlView(ui.View):
    def __init__(self, ticket_channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.ticket_channel = ticket_channel

    @ui.button(label="Close Ticket", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, _btn: ui.Button):
        if exempt(interaction.user):
            await interaction.response.send_message("Closing in 10 s…", ephemeral=True)
            await asyncio.sleep(10)
            try:
                await self.ticket_channel.delete()
            except Exception:
                pass
        else:
            await interaction.response.send_message("Only staff can close.", ephemeral=True)

    @ui.button(label="Request Close", style=discord.ButtonStyle.grey)
    async def request(self, interaction: discord.Interaction, _btn: ui.Button):
        await interaction.response.send_message("Staff has been notified to close.", ephemeral=True)

class TicketModal(ui.Modal, title="Open Ticket"):
    subject = ui.TextInput(label="Subject", max_length=100)
    desc    = ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        user  = interaction.user
        name  = f"ticket-{user.id}"

        if discord.utils.get(guild.text_channels, name=name):
            await interaction.response.send_message("You already have an open ticket.", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user:               discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
            discord.Object(id=MOD_ROLE_ID):   discord.PermissionOverwrite(view_channel=True, send_messages=True),
            discord.Object(id=ADMIN_ROLE_ID): discord.PermissionOverwrite(view_channel=True, send_messages=True),
            discord.Object(id=OWNER_ROLE_ID): discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        chan = await guild.create_text_channel(name, overwrites=overwrites)
        await chan.send(f"**Ticket created by {user.mention}**\n**Subject:** {self.subject}\n{self.desc}",
                        view=TicketControlView(chan))
        await interaction.response.send_message(f"Ticket created: {chan.mention}", ephemeral=True)

class TicketPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Open Ticket", style=discord.ButtonStyle.green, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, _button: ui.Button):
        await interaction.response.send_modal(TicketModal())

#############
#  MUSIC
#############
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn"
}

async def yt_search(query: str) -> dict:
    ydl_opts = {'format': 'bestaudio/best', 'noplaylist': True, 'quiet': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]
    return {"url": info['webpage_url'], "title": info['title']}

#######################
#  COMMANDS & EVENTS
#######################
@bot.tree.command(description="Admin: deploy ticket panel")
@app_commands.checks.has_role(ADMIN_ROLE_ID)
async def panel(interaction: discord.Interaction):
    await interaction.response.send_message("Ticket panel ready!", view=TicketPanelView(), ephemeral=True)

@bot.event
async def on_ready():
    await bot.tree.sync()
    bot.add_view(TicketPanelView())  # persistent ticket panel view
    print(f"✅ Bot is ready as {bot.user} ({bot.user.id})")

################
#  RUN
################
if __name__ == "__main__":
    if not TOKEN or not GROQ_API_KEY:
        raise RuntimeError("DISCORD_TOKEN or GROQ_API_KEY missing.")
    bot.run(TOKEN)
