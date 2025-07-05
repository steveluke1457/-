"""
IDK BOT – single‑file Discord bot (main.py)
100 % online deployment (GitHub Actions + UptimeRobot)

Dependencies (see requirements.txt):
discord.py
python-dotenv
aiohttp           # only for Groq HTTPS calls
yt‑dlp            # YouTube audio
groq              # Groq AI client

All IDs below (roles, channels) must be updated to your server.
"""

#############
# KEEP‑ALIVE
#############
# Starts a tiny webserver on port 8000 so UptimeRobot can ping the Action
# and prevent GitHub Actions from timing‑out (workflow uses `timeout‑minutes: 9999`)
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

LOG_CHANNEL_ID       = 0  # <-- put your log channel id or leave 0 for no logging
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
strike_data: Dict[int, List[datetime.datetime]] = {}     # user_id → list[timestamps]
music_queue: List[dict] = []                             # [{'url':..., 'title':...}, …]
conversations: Dict[int, List[dict]] = {}                # user_id → chat history

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
    """
    Returns True if content is unsafe.
    Uses Llama‑Guard on Groq that returns “unsafe” or “safe”.
    """
    try:
        res = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model="meta-llama/Llama-Guard-4-12B",
            messages=[{"role": "user", "content": text}]
        )
        verdict = res.choices[0].message.content.lower()
        return verdict.startswith("unsafe")
    except Exception:
        return False  # fail‑open (treat as safe if service fails)

async def groq_chat(user_id: int, prompt: str) -> str:
    history = conversations.setdefault(user_id, [])
    history.append({"role": "user", "content": prompt})
    try:
        res = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            messages=history[-20:],      # keep last 20 to stay small
            temperature=0.7
        )
        reply = res.choices[0].message.content.strip()
    except Exception:
        reply = "Sorry, I’m having trouble right now."
    history.append({"role": "assistant", "content": reply})
    conversations[user_id] = history[-30:]  # cap
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

    # Actions by strike level
    msg = ""
    if count == 1:
        msg = ("Strike 1 / 8 — You violated server rules ("+reason+
               "). Please keep the community safe and friendly.")
    elif count == 2:
        msg = ("Strike 2 — Continued rule breaking ("+reason+
               "). Final warning before punishments.")
    elif count == 3:
        await member.timeout(datetime.timedelta(minutes=5), reason="Strike 3")
        msg = ("Strike 3 — You’ve been timed‑out for 5 minutes for "+reason+".")
    elif count == 4:
        await member.timeout(datetime.timedelta(minutes=30), reason="Strike 4")
        msg = ("Strike 4 — Timed‑out 30 minutes for "+reason+".")
    elif count == 5:
        await member.timeout(datetime.timedelta(days=1), reason="Strike 5")
        msg = ("Strike 5 — Timed‑out 24 hours for "+reason+".")
    elif count == 6:
        await member.kick(reason="Strike 6 – "+reason)
        msg = ("Strike 6 — You were kicked for repeated "+reason+".")
    else:  # 7 or more
        await member.ban(reason="Strike 7 – "+reason)
        msg = ("Strike 7 — You were banned for continuous infractions.")
    try:
        await member.send(msg)
    except Exception:
        pass
    await log(f"{member} received strike {count}: {reason}")

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

@bot.tree.command(description="Add song to queue")
async def add(interaction: discord.Interaction, query: str):
    await interaction.response.defer(thinking=True)
    track = await yt_search(query)
    music_queue.append(track)
    await interaction.followup.send(f"**Queued:** {track['title']}")

@bot.tree.command(description="Play first song in queue")
async def play(interaction: discord.Interaction):
    voice_chan = bot.get_channel(MUSIC_VC_ID)
    if not voice_chan:
        await interaction.response.send_message("Music VC not found.", ephemeral=True)
        return
    if not music_queue:
        await interaction.response.send_message("Queue empty.", ephemeral=True)
        return

    if not voice_chan.guild.voice_client:
        vc = await voice_chan.connect()
    else:
        vc = voice_chan.guild.voice_client

    track = music_queue.pop(0)
    source = await discord.FFmpegOpusAudio.from_probe(track["url"], **FFMPEG_OPTIONS)
    vc.play(source)
    await interaction.response.send_message(f"🎶 Now playing: **{track['title']}**")

@bot.tree.command(description="Show song queue")
async def list(interaction: discord.Interaction):
    if not music_queue:
        await interaction.response.send_message("Queue empty.")
    else:
        txt = "\n".join(f"{i+1}. {t['title']}" for i, t in enumerate(music_queue))
        await interaction.response.send_message(f"**Queue:**\n{txt}")

@bot.tree.command(description="Admin/Mod: kick member")
@app_commands.checks.has_any_role(ADMIN_ROLE_ID, MOD_ROLE_ID, OWNER_ROLE_ID)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"Kicked {member}. Reason: {reason}")

@bot.tree.command(description="Admin/Mod: ban member")
@app_commands.checks.has_any_role(ADMIN_ROLE_ID, MOD_ROLE_ID, OWNER_ROLE_ID)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str):
    await member.ban(reason=reason)
    await interaction.response.send_message(f"Banned {member}. Reason: {reason}")

@bot.tree.command(description="Admin/Mod: timeout member (minutes)")
@app_commands.checks.has_any_role(ADMIN_ROLE_ID, MOD_ROLE_ID, OWNER_ROLE_ID)
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str):
    await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
    await interaction.response.send_message(f"Timed‑out {member} for {minutes} minutes.")

@bot.tree.command(description="Owner: clear strike history for user")
@app_commands.checks.has_role(OWNER_ROLE_ID)
async def clear_data(interaction: discord.Interaction, member: discord.Member):
    strike_data.pop(member.id, None)
    await interaction.response.send_message(f"Strike data cleared for {member}.")

########################
#  EVENTS
########################
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} ({bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await bot.process_commands(message)

    # Counting game
    if message.channel.id == COUNTING_CHANNEL_ID:
        try:
            num = int(message.content.strip())
        except ValueError:
            await message.delete()
            return
        last_num = getattr(message.channel, "last_num", 0)
        last_user = getattr(message.channel, "last_user", None)
        if num != last_num + 1 or message.author.id == last_user:
            await message.delete()
            return
        setattr(message.channel, "last_num", num)
        setattr(message.channel, "last_user", message.author.id)
        await message.add_reaction("✅")
        return

    # Skip exemptions
    if exempt(message.author):
        return

    # Spam (≥3 msgs in 5 s)
    now = datetime.datetime.utcnow()
    stamps = getattr(message.author, "_times", [])
    stamps = [t for t in stamps if (now - t).total_seconds() < 5]
    stamps.append(now)
    message.author._times = stamps

    violation = None
    if len(stamps) >= 3:
        violation = "spamming"

    # AI moderation
    if not violation and await groq_moderate(message.content):
        violation = "inappropriate content"

    if violation:
        await give_strike(message.author, violation)
        try:
            await message.delete()
        except Exception:
            pass
        return

    # AI chatbot (mention or reply)
    if bot.user in message.mentions or (message.reference and
       isinstance(message.reference.resolved, discord.Message) and
       message.reference.resolved.author == bot.user):
        reply = await groq_chat(message.author.id, message.content)
        await message.channel.send(reply)

    # Ticket auto‑close
    if message.channel.name.startswith("ticket-"):
        kw = ("solved", "never mind", "issue resolved")
        if any(k in message.content.lower() for k in kw):
            await message.channel.send(f"{message.author.mention}, I'll close this ticket in 15 s unless you say otherwise.")
            try:
                await bot.wait_for(
                    "message",
                    timeout=15,
                    check=lambda m: m.channel == message.channel and m.author == message.author
                )
            except asyncio.TimeoutError:
                try:
                    await message.channel.delete()
                except Exception:
                    pass

################
#  RUN
################
if __name__ == "__main__":
    if not TOKEN or not GROQ_API_KEY:
        raise RuntimeError("DISCORD_TOKEN or GROQ_API_KEY missing.")
    bot.run(TOKEN)
