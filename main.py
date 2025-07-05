import os
import discord
from discord.ext import commands
from discord import ui
import asyncio
import datetime
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Initialize Groq client for AI tasks
try:
    from groq import Groq
    groq_client = Groq(api_key=GROQ_API_KEY)
except ImportError:
    groq_client = None

# Role IDs (replace with your server's IDs)
ADMIN_ROLE_ID = 1386966204442218547
MOD_ROLE_ID = 1386966260754944030
OWNER_ROLE_ID = 1386965991145345024
EXEMPT_ROLES = {ADMIN_ROLE_ID, MOD_ROLE_ID, OWNER_ROLE_ID}

# Channel IDs (replace with your actual channel IDs)
LOG_CHANNEL_ID = None
MUSIC_VC_ID = 1389227384506421308
COUNTING_CHANNEL_ID = 1389228645570314272

# Strike tracking: {user_id: (strikes_count, last_strike_time)}
strike_data = {}

# Music queue
music_queue = []

@bot.tree.command(name="panel", description="Show the ticket panel (Admin only)")
async def panel(interaction: discord.Interaction):
    user = interaction.user
    if not any(role.id == ADMIN_ROLE_ID for role in user.roles):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    class TicketPanelView(ui.View):
        def __init__(self):
            super().__init__(timeout=None)
        @ui.button(label="Open Ticket", style=discord.ButtonStyle.green)
        async def open_ticket(self, button: ui.Button, interaction: discord.Interaction):
            class TicketModal(ui.Modal, title="Open Ticket"):
                subject = ui.TextInput(label="Subject", placeholder="Brief summary")
                desc = ui.TextInput(label="Description", style=discord.TextStyle.paragraph, placeholder="Describe your issue")
                async def on_submit(self, interaction: discord.Interaction):
                    guild = interaction.guild
                    user_id = interaction.user.id
                    channel_name = f"ticket-{user_id}"
                    # Prevent multiple tickets per user
                    if discord.utils.get(guild.channels, name=channel_name):
                        await interaction.response.send_message("You already have an open ticket.", ephemeral=True)
                        return
                    overwrites = {
                        guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                        discord.Object(id=MOD_ROLE_ID): discord.PermissionOverwrite(view_channel=True, send_messages=True),
                        discord.Object(id=ADMIN_ROLE_ID): discord.PermissionOverwrite(view_channel=True, send_messages=True),
                        discord.Object(id=OWNER_ROLE_ID): discord.PermissionOverwrite(view_channel=True, send_messages=True),
                        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
                    }
                    ticket_channel = await guild.create_text_channel(channel_name, overwrites=overwrites)
                    await ticket_channel.send(f"**Ticket by {interaction.user.mention}:** {self.subject}\\n{self.desc}")
                    # Ticket control buttons
                    class TicketView(ui.View):
                        def __init__(self):
                            super().__init__(timeout=None)
                        @ui.button(label="Close Ticket", style=discord.ButtonStyle.red)
                        async def close(self, button: ui.Button, interaction: discord.Interaction):
                            # Only roles in EXEMPT_ROLES can close
                            if any(role.id in EXEMPT_ROLES for role in interaction.user.roles):
                                await interaction.response.send_message("Closing ticket in 10 seconds...", ephemeral=True)
                                await asyncio.sleep(10)
                                try:
                                    await ticket_channel.delete()
                                except:
                                    pass
                            else:
                                await interaction.response.send_message("You don't have permission to close this ticket.", ephemeral=True)
                        @ui.button(label="Request Close", style=discord.ButtonStyle.grey)
                        async def request_close(self, button: ui.Button, interaction: discord.Interaction):
                            await interaction.response.send_message("A moderator has been notified to close the ticket.", ephemeral=True)
                    await ticket_channel.send("Ticket controls:", view=TicketView())
                    await interaction.response.send_message(f"Ticket created: {ticket_channel.mention}", ephemeral=True)
                    self.stop()
                async def on_error(self, error, interaction: discord.Interaction):
                    await interaction.response.send_message("Error opening ticket.", ephemeral=True)
            await interaction.response.send_modal(TicketModal())
    await interaction.response.send_message("Ticket panel is now active.", view=TicketPanelView())

async def check_inappropriate(content: str) -> bool:
    if not groq_client:
        return False
    try:
        result = groq_client.chat.completions.create(
            model="meta-llama/Llama-Guard-4-12B",
            messages=[{"role": "user", "content": content}]
        )
        answer = result.choices[0].message.content.strip().lower()
        return answer.startswith("unsafe")
    except:
        return False

async def log_action(message: str):
    if LOG_CHANNEL_ID:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            await ch.send(message)

conversations = {}  # user_id -> conversation messages list

@bot.event
async def on_message(message):
    await bot.process_commands(message)
    if message.author.bot:
        return

    # Counting game logic
    if message.channel.id == COUNTING_CHANNEL_ID:
        try:
            num = int(message.content)
        except:
            await message.delete()
            return
        last = getattr(message.channel, 'last_number', 0)
        last_user = getattr(message.channel, 'last_user', None)
        if message.author.id == last_user or num != last + 1:
            await message.delete()
            return
        setattr(message.channel, 'last_user', message.author.id)
        setattr(message.channel, 'last_number', num)
        await message.add_reaction("✅")
        return

    # Skip checks for exempt roles
    if any(role.id in EXEMPT_ROLES for role in message.author.roles):
        return

    # Spam detection: 3 messages in 5 seconds
    now = datetime.datetime.utcnow()
    times = getattr(message.author, "recent_times", [])
    times = [t for t in times if (now - t).seconds < 5]
    times.append(now)
    message.author.recent_times = times
    violation = None
    if len(times) >= 3:
        violation = "spamming messages"
    # Basic bad-word check (placeholder)
    bad_words = ["badword1", "badword2"]
    if violation is None and any(b in message.content.lower() for b in bad_words):
        violation = "using prohibited language"
    # AI-based moderation check
    if violation is None and await check_inappropriate(message.content):
        violation = "inappropriate content"

    if violation:
        user_id = message.author.id
        strikes, last_time = strike_data.get(user_id, (0, None))
        if last_time and (now - last_time).days >= 7:
            strikes = 0
        strikes += 1
        strike_data[user_id] = (strikes, now)
        # Compose strike message and apply punishments
        if strikes == 1:
            dm_msg = f"You have violated the server rules ({violation}). Strike 1. Please follow the rules."
        elif strikes == 2:
            dm_msg = f"Strike 2: Continued violation ({violation}). This is your final warning."
        elif strikes == 3:
            dm_msg = f"Strike 3: Further violation ({violation}). You will be timed out for 5 minutes."
            await message.author.timeout(datetime.timedelta(minutes=5), reason="Strike 3")
        elif strikes == 4:
            dm_msg = f"Strike 4: Continued violations ({violation}). You will be timed out for 30 minutes."
            await message.author.timeout(datetime.timedelta(minutes=30), reason="Strike 4")
        elif strikes == 5:
            dm_msg = f"Strike 5: Repeated violations ({violation}). You will be timed out for 1 day."
            await message.author.timeout(datetime.timedelta(days=1), reason="Strike 5")
        elif strikes == 6:
            dm_msg = f"Strike 6: Multiple violations ({violation}). You will be kicked from the server."
            await message.author.kick(reason="Strike 6")
        else:
            dm_msg = f"Strike {strikes}: Continued violations ({violation}). You are now banned."
            await message.author.ban(reason="Strike 7")
        try:
            await message.author.send(dm_msg)
        except:
            pass
        await log_action(f"{message.author} received strike {strikes} for {violation}.")
        return

    # Ticket auto-close monitor
    if message.channel.name.startswith("ticket-"):
        keywords = ["solved", "never mind", "issue resolved"]
        if any(kw in message.content.lower() for kw in keywords):
            await message.channel.send(f"{message.author.mention}, closing ticket in 15 seconds if no response.")
            try:
                await bot.wait_for('message', timeout=15.0, check=lambda m: m.author == message.author and m.channel == message.channel)
                # If user responds, do not close
            except asyncio.TimeoutError:
                await message.channel.delete()
            return

    # AI Chatbot response (Groq)
    if bot.user.mentioned_in(message) or (message.reference and isinstance(message.reference.resolved, discord.Message) and message.reference.resolved.author == bot.user):
        user_id = message.author.id
        convo = conversations.get(user_id, [])
        convo.append({"role": "user", "content": message.content})
        if groq_client:
            try:
                res = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=convo
                )
                answer = res.choices[0].message.content
            except:
                answer = "Sorry, I couldn't process that."
        else:
            answer = "AI not configured."
        await message.channel.send(answer)
        convo.append({"role": "assistant", "content": answer})
        conversations[user_id] = convo

# Music commands
@bot.tree.command(name="add", description="Add a YouTube song to the music queue")
async def add(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    import yt_dlp
    ydl_opts = {'format': 'bestaudio', 'noplaylist': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]
        title = info.get('title', 'Unknown')
        url = info.get('webpage_url')
    music_queue.append(url)
    await interaction.followup.send(f"Added **{title}** to the queue.")

@bot.tree.command(name="play", description="Play the first song in the queue")
async def play(interaction: discord.Interaction):
    if not music_queue:
        await interaction.response.send_message("The queue is empty.", ephemeral=True)
        return
    voice_channel = bot.get_channel(MUSIC_VC_ID)
    if not voice_channel:
        await interaction.response.send_message("Voice channel not found.", ephemeral=True)
        return
    if not voice_channel.guild.voice_client:
        vc = await voice_channel.connect()
    else:
        vc = voice_channel.guild.voice_client
    url = music_queue.pop(0)
    source = await discord.FFmpegOpusAudio.from_probe(url, **{
        'before_options': '-reconnect 1 -reconnect_streamed 1',
        'options': '-vn'
    })
    vc.play(source)
    await interaction.response.send_message(f"Now playing: {url}")

@bot.tree.command(name="list", description="List the current music queue")
async def list_queue(interaction: discord.Interaction):
    if not music_queue:
        await interaction.response.send_message("The queue is empty.")
    else:
        queue_text = "\\n".join(music_queue)
        await interaction.response.send_message(f"**Queue:**\\n{queue_text}")

# Admin/mod commands
@bot.tree.command(name="kick", description="Kick a member (Admin/Mod only)")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not any(role.id in EXEMPT_ROLES for role in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    await member.kick(reason=reason)
    await interaction.response.send_message(f"{member.mention} was kicked for: {reason}")

@bot.tree.command(name="ban", description="Ban a member (Admin/Mod only)")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not any(role.id in EXEMPT_ROLES for role in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    await member.ban(reason=reason)
    await interaction.response.send_message(f"{member.mention} was banned for: {reason}")

@bot.tree.command(name="timeout", description="Timeout a member (Admin/Mod only)")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str):
    if not any(role.id in EXEMPT_ROLES for role in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
    await interaction.response.send_message(f"{member.mention} was timed out for {minutes} minutes.")

@bot.tree.command(name="clear_data", description="Clear strike data for a user (Owner only)")
async def clear_data(interaction: discord.Interaction, member: discord.Member):
    if not any(role.id == OWNER_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    strike_data.pop(member.id, None)
    await interaction.response.send_message(f"Cleared strike data for {member.mention}")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)

bot.run(DISCORD_TOKEN)
