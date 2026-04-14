import discord
from discord import app_commands
import a2s
import asyncio
import os
import urllib.parse
import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial

CONFIG_FILE = "config.json"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError("config.json not found")

    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

config = load_config()

TOKEN = config["TOKEN"]
ALLOWED_CHANNEL_ID = config["ALLOWED_CHANNEL_ID"]
GUILD_ID = config["GUILD_ID"]
SERVERS_FILE = config["SERVERS_FILE"]
MAX_WORKERS = (os.cpu_count() or 4) * 5
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

def load_servers():
    if not os.path.exists(SERVERS_FILE):
        return []

    servers = []
    with open(SERVERS_FILE, "r") as f:
        for line in f.read().splitlines():
            if ":" in line:
                ip, port = line.split(":")
                servers.append((ip, int(port)))
    return servers


def save_servers(servers):
    with open(SERVERS_FILE, "w") as f:
        for ip, port in servers:
            f.write(f"{ip}:{port}\n")

class PlayersPager(discord.ui.View):
    def __init__(self, embeds):
        super().__init__(timeout=180)
        self.embeds = embeds
        self.index = 0

    def update_buttons(self):
        self.prev.disabled = self.index == 0
        self.next.disabled = self.index == len(self.embeds) - 1

    async def update_message(self, interaction: discord.Interaction):
        self.update_buttons()
        await interaction.response.edit_message(
            embed=self.embeds[self.index],
            view=self
        )

    @discord.ui.button(label="⬅ Prev", style=discord.ButtonStyle.gray)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index > 0:
            self.index -= 1
        await self.update_message(interaction)

    @discord.ui.button(label="➡ Next", style=discord.ButtonStyle.gray)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index < len(self.embeds) - 1:
            self.index += 1
        await self.update_message(interaction)

def allowed(interaction: discord.Interaction):
    return interaction.channel_id == ALLOWED_CHANNEL_ID

def steam_search(name: str):
    encoded = urllib.parse.quote(name)
    return f"https://steamcommunity.com/search/users/#text={encoded}"

def search_servers(name: str):
    results = []
    servers = load_servers()

    def worker(server):
        try:
            info = a2s.info(server)
            players = a2s.players(server)

            matches = [p for p in players if name.lower() in p.name.lower()]

            if matches:
                return (server, info, matches)

        except Exception as e:
            print(f"❌ {server} -> {e}")

        return None

    futures = [executor.submit(worker, s) for s in servers]

    for f in futures:
        r = f.result()
        if r:
            results.append(r)

    return results

def get_all_servers():
    results = []
    servers = load_servers()

    def worker(server):
        try:
            info = a2s.info(server)
            players = a2s.players(server)
            return (server, info, players)

        except Exception as e:
            print(f"❌ {server} -> {e}")
            return None

    futures = [executor.submit(worker, s) for s in servers]

    for f in futures:
        r = f.result()
        if r:
            results.append(r)

    return results

def find_server_by_name(snippet: str):
    servers = load_servers()
    snippet = snippet.lower()

    result = None

    def worker(server):
        try:
            info = a2s.info(server)

            if snippet in info.server_name.lower():
                return (server[0], server[1], info)

        except Exception:
            pass

        return None

    futures = [executor.submit(worker, s) for s in servers]

    for f in futures:
        r = f.result()
        if r:
            result = r
            break

    return result

@tree.command(name="findplayer", description="Search for a player", guild=discord.Object(id=GUILD_ID))
async def findplayer(interaction: discord.Interaction, name: str):

    if not allowed(interaction):
        await interaction.response.send_message("❌ Wrong channel", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    results = await asyncio.to_thread(search_servers, name)

    if not results:
        await interaction.followup.send("❌ No players found.")
        return

    for (ip, port), info, players in results:

        embed = discord.Embed(
            title=info.server_name,
            description=f"`{ip}:{port}`",
            color=discord.Color.blue()
        )

        embed.add_field(name="🗺 Map", value=info.map_name, inline=True)
        embed.add_field(name="👥 Matches", value=str(len(players)), inline=True)
        embed.add_field(name=":video_game: Game", value=f"{info.game}", inline=True)

        player_list = "\n".join([f"• {p.name}" for p in players])
        embed.add_field(name="🎯 Players", value=player_list or "None", inline=False)
        embed.add_field(
            name="🔎 Steam Search",
            value=f"[Search Steam]({steam_search(name)})",
            inline=False
        )

        await interaction.followup.send(embed=embed)

@tree.command(name="players", description="List all servers", guild=discord.Object(id=GUILD_ID))
async def players(interaction: discord.Interaction):

    if not allowed(interaction):
        await interaction.response.send_message("❌ Wrong channel", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    results = await asyncio.to_thread(get_all_servers)

    if not results:
        await interaction.followup.send("❌ No servers online.")
        return

    embeds = []

    for (ip, port), info, players in results:

        embed = discord.Embed(
            title=info.server_name,
            description=f"`{ip}:{port}`",
            color=discord.Color.green()
        )

        embed.add_field(name="🗺 Map", value=info.map_name, inline=True)
        embed.add_field(name="👥 Players", value=f"{len(players)}/{info.max_players}", inline=True)
        embed.add_field(name=":video_game: Game", value=f"{info.game}", inline=True)

        player_list = "\n".join([f"• {p.name}" for p in players]) or "No players"
        embed.add_field(name="Players", value=player_list, inline=False)

        embeds.append(embed)

    view = PlayersPager(embeds)

    view.update_buttons()

    await interaction.followup.send(embed=embeds[0], view=view)

@tree.command(name="addip", description="Add server IP", guild=discord.Object(id=GUILD_ID))
async def addip(interaction: discord.Interaction, ip: str, port: int):

    if not allowed(interaction):
        await interaction.response.send_message("❌ Wrong channel", ephemeral=True)
        return

    servers = load_servers()

    if (ip, port) in servers:
        await interaction.response.send_message("⚠️ Already exists", ephemeral=True)
        return

    servers.append((ip, port))
    save_servers(servers)

    embed = discord.Embed(title="✅ Added server", description=f"`{ip}:{port}`")
    await interaction.response.send_message(embed=embed)


@tree.command(name="removeip", description="Remove server IP", guild=discord.Object(id=GUILD_ID))
async def removeip(interaction: discord.Interaction, ip: str, port: int):

    if not allowed(interaction):
        await interaction.response.send_message("❌ Wrong channel", ephemeral=True)
        return

    servers = load_servers()

    if (ip, port) not in servers:
        await interaction.response.send_message("⚠️ Not found", ephemeral=True)
        return

    servers.remove((ip, port))
    save_servers(servers)

    embed = discord.Embed(title="🗑 Removed server", description=f"`{ip}:{port}`")
    await interaction.response.send_message(embed=embed)


@tree.command(name="iplist", description="List servers", guild=discord.Object(id=GUILD_ID))
async def iplist(interaction: discord.Interaction):

    if not allowed(interaction):
        await interaction.response.send_message("❌ Wrong channel", ephemeral=True)
        return

    servers = load_servers()

    if not servers:
        await interaction.response.send_message("No servers stored.")
        return

    embed = discord.Embed(title="📡 Server List")
    embed.description = "\n".join([f"`{ip}:{port}`" for ip, port in servers])

    await interaction.response.send_message(embed=embed)


@tree.command(
    name="playerlist",
    description="Show players for a specific server (by name)",
    guild=discord.Object(id=GUILD_ID)
)
async def playerlist(interaction: discord.Interaction, server: str):

    if interaction.channel_id != ALLOWED_CHANNEL_ID:
        await interaction.response.send_message("❌ Wrong channel", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    result = await asyncio.to_thread(find_server_by_name, server)

    if not result:
        await interaction.followup.send("❌ Server not found.")
        return

    ip, port, info = result

    try:
        players = await asyncio.to_thread(a2s.players, (ip, port))
    except Exception:
        players = []

    embed = discord.Embed(
        title=info.server_name,
        description=f"`{ip}:{port}`",
        color=discord.Color.blurple()
    )

    embed.add_field(name="🗺 Map", value=info.map_name, inline=True)
    embed.add_field(name="👥 Players", value=f"{len(players)}/{info.max_players}", inline=True)
    embed.add_field(name=":video_game: Game", value=f"{info.game}", inline=True)

    player_list = "\n".join([f"• {p.name}" for p in players]) if players else "No players online"
    embed.add_field(name="👤 Player List", value=player_list, inline=False)

    await interaction.followup.send(embed=embed)
    
@client.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    await tree.sync(guild=guild)
    print(f"JewScanner By TheSecretSauce67420 Initalized!")

client.run(TOKEN)
