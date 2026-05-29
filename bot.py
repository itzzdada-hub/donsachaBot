from flask import Flask
from threading import Thread

# --- CONFIGURATION FLASK (KEEP ALIVE) ---
app = Flask('')

@app.route('/')
def home():
    return "Don Sacha est en ligne et opérationnel !"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()

# --- IMPORTS DU BOT ---
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import tasks
from openai import OpenAI


def _load_dotenv(path: str = ".env") -> None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


_load_dotenv()

# --- BEGIN SYNCCORD INJECTED CONFIG ---
CHAR_NAME = os.getenv("CHAR_NAME", "Don Sacha")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "MTUwOTU0Mzg4ODEzMjYzNjc5NA.Glf-5g.anUdYGGYY2FKQbiInswVBZtqgFz9gkHLFhOU9Y")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
CHAR_PROFILE = {
    "name": "Don Sacha",
    "age": "18",
    "origin": "Sacha est né en Belgique dans une famille qui a toujours adoré le poulet frit, lui trouvait ça louche, il essaya d'enquêter sur cette étrange obsession pour le poulet frit, c'est depuis ce jour qu'il a prit cette fameuse pause, celle ou il tente de ressembler au Maître Don Pollo",
    "appearance": "Homme au cheveux longs qui regarde vers le ciel, a quoi pense-t-il ? personne ne le sait",
    "personality": 'Tu t\'appelles Don Sacha, toujours Don Sacha, tu es drôle et fais beaucoup de blague, un langage très familier, t\'aime le poulet frit et l\'expression que tu utilise souvent est "salamaleycouillealeycouillesalam".\n- Utilise des expressions drôles, pas de phrases trop formelles ni de langage soutenu.\n- Ne dis JAMAIS que tu es une IA ou un modèle linguistique. Tu es Don Sacha, un point c\'est tout.\n- Il aile beaucoup sa copine Mariya',
    "style": "- Ton ton est décontracté, amical, un peu geek (tu aimes le gaming et surtout minecraft).",
    "likes": "le poulet frit, les jeux vidéos, minecraft, Mariya, manger",
    "dislikes": "Dark Souls, les creepers, Teo",
}

DEFAULT_NSFW_MODE = True
# --- END SYNCCORD INJECTED CONFIG ---

MODEL_NAME = "llama-3.1-8b-instant"
MAX_HISTORY = 15
COOLDOWN_SECONDS = 4
DATABASE_PATH = "bot_data.sqlite3"
ACCENT_COLOR = discord.Color.from_rgb(124, 92, 255)
SUCCESS_COLOR = discord.Color.from_rgb(64, 196, 128)
WARN_COLOR = discord.Color.from_rgb(248, 184, 64)
ERROR_COLOR = discord.Color.from_rgb(232, 88, 96)
BRAND_NAME = "SyncCord"
DEFAULT_RANDOM_CHAT_ENABLED = False
DEFAULT_RANDOM_GIFS_ENABLED = True
DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES = 30
DEFAULT_WELCOME_MESSAGE = "Bienvenue, je suis Don Sacha"
DEFAULT_WELCOME_CHANNEL_ID = None

RANDOM_CHAT_MESSAGES = [
    "wsh y a personne qui parle ou quoi",
    "Vous êtes encore en vie ??",
    "Salamaleycouillealeycouillesalam",
    "Des mods minecraft a me conseiller ?",
    "pd va",
    "Qui m'offre du poulet frit ?",
    "C'est pas si mal dans ce serveur",
    "J'ai besoin de manger",
    "Je veux jouer à minecraft",
    "je suis Don Sacha"
]

RANDOM_GIF_QUERIES = [
    "smile",
    "happy",
    "dance",
    "laugh",
    "wave",
    "wink"
]   
ALLOWED_CHANNEL_IDS: list[int] = []

if not DISCORD_TOKEN:
    print("ERROR: DISCORD_TOKEN is not set.")
    sys.exit(1)
if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY is not set.")
    sys.exit(1)

client_ai = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

active_channels_by_guild: dict[int, set[int]] = {}
guild_nsfw_modes: dict[int, bool] = {}
guild_welcome_messages: dict[int, str] = {}
guild_welcome_channel_ids: dict[int, int | None] = {}
guild_random_chat_enabled: dict[int, bool] = {}
guild_random_gifs_enabled: dict[int, bool] = {}
guild_random_chat_interval_minutes: dict[int, int] = {}
last_trigger_by_scope: dict[tuple[int | None, int], float] = {}

persona_overrides: dict[tuple[int, int], tuple[str, float]] = {}  
afk_users: dict[tuple[int, int], tuple[str, float]] = {}            
sniped_messages: dict[int, dict] = {}                              
PERSONA_TTL_SECONDS = 30 * 60


def get_db_connection() -> sqlite3.Connection:
    return sqlite3.connect(DATABASE_PATH)


def init_database() -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                active_channels_json TEXT NOT NULL DEFAULT '[]',
                nsfw_mode INTEGER NOT NULL DEFAULT 0,
                welcome_message TEXT NOT NULL DEFAULT '',
                welcome_channel_id INTEGER,
                random_chat_enabled INTEGER NOT NULL DEFAULT 0,
                random_gifs_enabled INTEGER NOT NULL DEFAULT 1,
                random_chat_interval_minutes INTEGER NOT NULL DEFAULT 30
            )
            """
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(guild_settings)").fetchall()
        }
        if "welcome_message" not in columns:
            connection.execute("ALTER TABLE guild_settings ADD COLUMN welcome_message TEXT NOT NULL DEFAULT ''")
        if "welcome_channel_id" not in columns:
            connection.execute("ALTER TABLE guild_settings ADD COLUMN welcome_channel_id INTEGER")
        if "random_chat_enabled" not in columns:
            connection.execute("ALTER TABLE guild_settings ADD COLUMN random_chat_enabled INTEGER NOT NULL DEFAULT 0")
        if "random_gifs_enabled" not in columns:
            connection.execute("ALTER TABLE guild_settings ADD COLUMN random_gifs_enabled INTEGER NOT NULL DEFAULT 1")
        if "random_chat_interval_minutes" not in columns:
            connection.execute("ALTER TABLE guild_settings ADD COLUMN random_chat_interval_minutes INTEGER NOT NULL DEFAULT 30")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_key TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_conversation_scope_id ON conversation_messages(scope_key, id)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                fact TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_facts_scope ON user_facts(guild_id, user_id)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER,
                fire_at INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_reminders_fire_at ON reminders(fire_at)")


def load_guild_settings() -> None:
    active_channels_by_guild.clear()
    guild_nsfw_modes.clear()
    guild_welcome_messages.clear()
    guild_welcome_channel_ids.clear()
    guild_random_chat_enabled.clear()
    guild_random_gifs_enabled.clear()
    guild_random_chat_interval_minutes.clear()
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT guild_id, active_channels_json, nsfw_mode, welcome_message,
                   welcome_channel_id, random_chat_enabled, random_gifs_enabled, random_chat_interval_minutes
            FROM guild_settings
            """
        ).fetchall()

    for (
        guild_id,
        active_channels_json,
        nsfw_mode,
        welcome_message,
        welcome_channel_id,
        random_chat_enabled,
        random_gifs_enabled,
        random_chat_interval_minutes,
    ) in rows:
        try:
            active_channels = set(json.loads(active_channels_json or "[]"))
        except json.JSONDecodeError:
            active_channels = set()
        active_channels_by_guild[guild_id] = {int(channel_id) for channel_id in active_channels}
        guild_nsfw_modes[guild_id] = bool(nsfw_mode)
        guild_welcome_messages[guild_id] = welcome_message or DEFAULT_WELCOME_MESSAGE
        guild_welcome_channel_ids[guild_id] = int(welcome_channel_id) if welcome_channel_id else DEFAULT_WELCOME_CHANNEL_ID
        guild_random_chat_enabled[guild_id] = bool(random_chat_enabled)
        guild_random_gifs_enabled[guild_id] = bool(random_gifs_enabled)
        guild_random_chat_interval_minutes[guild_id] = max(5, int(random_chat_interval_minutes or DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES))


def save_guild_settings(guild_id: int) -> None:
    active_channels = sorted(active_channels_by_guild.get(guild_id, set()))
    nsfw_mode = int(guild_nsfw_modes.get(guild_id, DEFAULT_NSFW_MODE))
    welcome_message = guild_welcome_messages.get(guild_id, DEFAULT_WELCOME_MESSAGE)
    welcome_channel_id = guild_welcome_channel_ids.get(guild_id)
    random_chat_enabled = int(guild_random_chat_enabled.get(guild_id, DEFAULT_RANDOM_CHAT_ENABLED))
    random_gifs_enabled = int(guild_random_gifs_enabled.get(guild_id, DEFAULT_RANDOM_GIFS_ENABLED))
    random_chat_interval_minutes = int(guild_random_chat_interval_minutes.get(guild_id, DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES))
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO guild_settings (
                guild_id, active_channels_json, nsfw_mode, welcome_message,
                welcome_channel_id,
                random_chat_enabled, random_gifs_enabled, random_chat_interval_minutes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                active_channels_json = excluded.active_channels_json,
                nsfw_mode = excluded.nsfw_mode,
                welcome_message = excluded.welcome_message,
                welcome_channel_id = excluded.welcome_channel_id,
                random_chat_enabled = excluded.random_chat_enabled,
                random_gifs_enabled = excluded.random_gifs_enabled,
                random_chat_interval_minutes = excluded.random_chat_interval_minutes
            """,
            (
                guild_id,
                json.dumps(active_channels),
                nsfw_mode,
                welcome_message,
                welcome_channel_id,
                random_chat_enabled,
                random_gifs_enabled,
                random_chat_interval_minutes,
            ),
        )


def get_scope_key(message: discord.Message) -> str:
    if message.guild is None:
        return f"dm:{message.channel.id}"
    return f"guild:{message.guild.id}:channel:{message.channel.id}"


def get_nsfw_mode(guild_id: int | None) -> bool:
    if guild_id is None:
        return DEFAULT_NSFW_MODE
    return guild_nsfw_modes.get(guild_id, DEFAULT_NSFW_MODE)


def build_system_prompt(
    nsfw_mode: bool,
    *,
    persona_override: str | None = None,
    user_facts_block: str | None = None,
) -> str:
    safety_rule = (
        "Adult themes are allowed when the server and context are age-appropriate."
        if nsfw_mode
        else "Keep the conversation PG-13, safe, and non-explicit."
    )
    persona_block = ""
    if persona_override:
        persona_block = f"\nTemporary persona overlay (active in this channel):\n{persona_override}\nHonor this overlay on top of your base character."
    facts_block = ""
    if user_facts_block:
        facts_block = f"\nKnown facts about the people in this server (use them naturally, do not list them):\n{user_facts_block}"
    return f"""
You are fully roleplaying as {CHAR_NAME}.

Character details:
- Age: {CHAR_PROFILE["age"]}
- Origin: {CHAR_PROFILE["origin"]}
- Appearance: {CHAR_PROFILE["appearance"]}
- Personality: {CHAR_PROFILE["personality"]}
- Writing Style: {CHAR_PROFILE["style"]}
- Likes: {CHAR_PROFILE["likes"]}
- Dislikes: {CHAR_PROFILE["dislikes"]}
{persona_block}{facts_block}
Behavior rules:
1. Stay in character at all times and never say you are an AI assistant.
2. Reply naturally, like a real Discord user with this personality.
3. Keep most replies concise unless the user clearly wants a longer answer.
4. Remember recent context so the conversation feels continuous.
5. {safety_rule}
6. Avoid repeating the same catchphrases every message.
""".strip()


def format_user_facts_for_guild(guild: discord.Guild | None) -> str | None:
    if guild is None:
        return None
    facts = all_facts_for_channel(guild.id)
    if not facts:
        return None
    lines: list[str] = []
    for user_id, user_facts in facts.items():
        member = guild.get_member(user_id)
        name = member.display_name if member else f"user {user_id}"
        for fact in user_facts[:5]:  
            lines.append(f"- {name}: {fact}")
        if len(lines) >= 40:  
            break
    return "\n".join(lines) if lines else None


def load_history(scope_key: str) -> list[dict[str, str]]:
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT role, content
            FROM conversation_messages
            WHERE scope_key = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (scope_key, MAX_HISTORY),
        ).fetchall()
    rows.reverse()
    return [{"role": role, "content": content} for role, content in rows]


def append_history(scope_key: str, role: str, content: str) -> None:
    with get_db_connection() as connection:
        connection.execute(
            "INSERT INTO conversation_messages (scope_key, role, content) VALUES (?, ?, ?)",
            (scope_key, role, content),
        )
        connection.execute(
            """
            DELETE FROM conversation_messages
            WHERE scope_key = ?
              AND id NOT IN (
                  SELECT id
                  FROM conversation_messages
                  WHERE scope_key = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (scope_key, scope_key, MAX_HISTORY),
        )


def clear_history(scope_key: str) -> None:
    with get_db_connection() as connection:
        connection.execute("DELETE FROM conversation_messages WHERE scope_key = ?", (scope_key,))


def add_user_fact(guild_id: int, user_id: int, fact: str) -> int:
    fact = fact.strip()
    if not fact:
        return 0
    with get_db_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO user_facts (guild_id, user_id, fact, created_at) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, fact, int(time.time())),
        )
        return int(cursor.lastrowid or 0)


def list_user_facts(guild_id: int, user_id: int) -> list[tuple[int, str]]:
    with get_db_connection() as connection:
        rows = connection.execute(
            "SELECT id, fact FROM user_facts WHERE guild_id = ? AND user_id = ? ORDER BY id",
            (guild_id, user_id),
        ).fetchall()
    return [(int(row[0]), row[1]) for row in rows]


def delete_user_fact(guild_id: int, user_id: int, fact_id: int) -> bool:
    with get_db_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM user_facts WHERE id = ? AND guild_id = ? AND user_id = ?",
            (fact_id, guild_id, user_id),
        )
        return cursor.rowcount > 0


def all_facts_for_channel(guild_id: int) -> dict[int, list[str]]:
    if guild_id is None:
        return {}
    with get_db_connection() as connection:
        rows = connection.execute(
            "SELECT user_id, fact FROM user_facts WHERE guild_id = ? ORDER BY id",
            (guild_id,),
        ).fetchall()
    facts: dict[int, list[str]] = {}
    for user_id, fact in rows:
        facts.setdefault(int(user_id), []).append(fact)
    return facts


def add_reminder(user_id: int, channel_id: int, guild_id: int | None, fire_at: int, text: str) -> int:
    with get_db_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO reminders (user_id, channel_id, guild_id, fire_at, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, channel_id, guild_id, fire_at, text, int(time.time())),
        )
        return int(cursor.lastrowid or 0)


def due_reminders(now_ts: int) -> list[tuple[int, int, int, int | None, str]]:
    with get_db_connection() as connection:
        rows = connection.execute(
            "SELECT id, user_id, channel_id, guild_id, text FROM reminders WHERE fire_at <= ? ORDER BY fire_at",
            (now_ts,),
        ).fetchall()
    return [(int(r[0]), int(r[1]), int(r[2]), int(r[3]) if r[3] is not None else None, r[4]) for r in rows]


def delete_reminder(reminder_id: int) -> None:
    with get_db_connection() as connection:
        connection.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))


def set_persona_override(guild_id: int, channel_id: int, persona: str) -> None:
    persona_overrides[(guild_id, channel_id)] = (persona.strip(), time.time() + PERSONA_TTL_SECONDS)


def clear_persona_override(guild_id: int, channel_id: int) -> None:
    persona_overrides.pop((guild_id, channel_id), None)


def get_persona_override(guild_id: int | None, channel_id: int) -> str | None:
    if guild_id is None:
        return None
    entry = persona_overrides.get((guild_id, channel_id))
    if not entry:
        return None
    persona, expiry = entry
    if time.time() > expiry:
        persona_overrides.pop((guild_id, channel_id), None)
        return None
    return persona


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

def parse_duration(text: str) -> int | None:
    text = text.strip().lower().replace(" ", "")
    if not text:
        return None
    total = 0
    number = ""
    for char in text:
        if char.isdigit():
            number += char
        elif char in _DURATION_UNITS:
            if not number:
                return None
            total += int(number) * _DURATION_UNITS[char]
            number = ""
        else:
            return None
    if number:  
        total += int(number)
    return total if total > 0 else None


def build_messages(message: discord.Message) -> list[dict[str, str]]:
    scope_key = get_scope_key(message)
    history = load_history(scope_key)
    history.append({"role": "user", "content": message.content.strip()})
    guild_id = message.guild.id if message.guild else None
    persona_override = get_persona_override(guild_id, message.channel.id) if guild_id is not None else None
    facts_block = format_user_facts_for_guild(message.guild) if message.guild else None
    system = build_system_prompt(
        get_nsfw_mode(guild_id),
        persona_override=persona_override,
        user_facts_block=facts_block,
    )
    return [{"role": "system", "content": system}, *history]


def split_message(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


def is_rate_limited(message: discord.Message) -> bool:
    scope = (message.guild.id if message.guild else None, message.author.id)
    now = time.time()
    previous = last_trigger_by_scope.get(scope, 0.0)
    if now - previous < COOLDOWN_SECONDS:
        return True
    last_trigger_by_scope[scope] = now
    return False


def should_reply(message: discord.Message) -> bool:
    if message.author.bot:
        return False
    if isinstance(message.channel, discord.DMChannel):
        return True
    if message.guild is None:
        return False
    is_mention = bool(client.user and client.user in message.mentions)
    is_reply_to_bot = False
    if message.reference and message.reference.resolved:
        replied_to = message.reference.resolved
        if isinstance(replied_to, discord.Message) and replied_to.author == client.user:
            is_reply_to_bot = True
    if not (is_mention or is_reply_to_bot):
        return False
    if ALLOWED_CHANNEL_IDS and message.channel.id not in ALLOWED_CHANNEL_IDS:
        return False
    active_channels = active_channels_by_guild.get(message.guild.id, set())
    if active_channels and message.channel.id not in active_channels:
        return False
    return True


def format_active_channels(guild: discord.Guild) -> str:
    channels = sorted(active_channels_by_guild.get(guild.id, set()))
    if not channels:
        return "No active channels set."
    return ", ".join(f"<#{channel_id}>" for channel_id in channels)


def get_welcome_message(guild_id: int | None) -> str:
    if guild_id is None:
        return DEFAULT_WELCOME_MESSAGE
    return guild_welcome_messages.get(guild_id, DEFAULT_WELCOME_MESSAGE)


def get_random_chat_interval(guild_id: int | None) -> int:
    if guild_id is None:
        return DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES
    return guild_random_chat_interval_minutes.get(guild_id, DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES)


def is_random_chat_enabled(guild_id: int | None) -> bool:
    if guild_id is None:
        return False
    return guild_random_chat_enabled.get(guild_id, DEFAULT_RANDOM_CHAT_ENABLED)


def is_random_gifs_enabled(guild_id: int | None) -> bool:
    if guild_id is None:
        return DEFAULT_RANDOM_GIFS_ENABLED
    return guild_random_gifs_enabled.get(guild_id, DEFAULT_RANDOM_GIFS_ENABLED)


def fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "SynccordBot/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_random_cat_url() -> str | None:
    payload = fetch_json("https://api.thecatapi.com/v1/images/search?limit=1")
    if isinstance(payload, list) and payload:
        return payload[0].get("url")
    return None


def fetch_random_waifu_url() -> str | None:
    payload = fetch_json("https://api.waifu.pics/sfw/waifu")
    if isinstance(payload, dict):
        return payload.get("url")
    return None


def fetch_random_meme() -> tuple[str, str] | None:
    payload = fetch_json("https://api.imgflip.com/get_memes")
    if isinstance(payload, dict) and payload.get("success"):
        memes = payload.get("data", {}).get("memes", [])
        if memes:
            meme = random.choice(memes)
            return meme.get("name", "Random meme"), meme.get("url")
    return None

# CRITIQUE: Correction de l'endpoint waifu.pics pour éviter le crash en mode GIF aléatoire
def fetch_random_gif_url(query: str) -> str | None:
    encoded_query = urllib.parse.quote(query)
    try:
        payload = fetch_json(f"https://api.waifu.pics/sfw/{encoded_query}")
        if isinstance(payload, dict):
            return payload.get("url")
    except Exception:
        # Fallback sur un gif basique de waifu si le tag n'existe pas
        payload = fetch_json("https://api.waifu.pics/sfw/waifu")
        if isinstance(payload, dict):
            return payload.get("url")
    return None


def _kind_color(kind: str) -> discord.Color:
    return {
        "success": SUCCESS_COLOR,
        "warn": WARN_COLOR,
        "error": ERROR_COLOR,
        "info": ACCENT_COLOR,
        "config": ACCENT_COLOR,
    ).get(kind, ACCENT_COLOR)


def make_embed(title: str, description: str | None = None, *, kind: str = "info") -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=_kind_color(kind))
    avatar_url = client.user.display_avatar.url if client.user else None
    embed.set_author(name=CHAR_NAME, icon_url=avatar_url)
    embed.set_footer(text=f"{BRAND_NAME} • {CHAR_NAME}")
    return embed


def make_config_embed(title: str, description: str | None = None) -> discord.Embed:
    return make_embed(title, description, kind="config")


def get_welcome_channel_id(guild_id: int | None) -> int | None:
    if guild_id is None:
        return None
    return guild_welcome_channel_ids.get(guild_id)


def get_welcome_channel_mention(guild: discord.Guild) -> str:
    welcome_channel_id = get_welcome_channel_id(guild.id)
    if welcome_channel_id is None:
        return "not set"
    channel = guild.get_channel(welcome_channel_id)
    return channel.mention if channel else f"<#{welcome_channel_id}>"


def build_welcome_prompt(member: discord.Member, welcome_idea: str) -> str:
    return (
        f"You are {CHAR_NAME}, welcoming a new Discord server member.\n"
        f"Server name: {member.guild.name}\n"
        f"New member display name: {member.display_name}\n"
        f"New member mention: {member.mention}\n"
        f"Welcome idea from the server admin: {welcome_idea}\n\n"
        "Write one short, crisp welcome message.\n"
        "Requirements:\n"
        "- Mention the user exactly once.\n"
        "- Keep it under 35 words.\n"
        "- Sound warm, playful, and natural.\n"
        "- Use the admin's idea as inspiration, not a rigid template.\n"
        "- Do not include quotation marks or labels.\n"
    )


def generate_welcome_message(member: discord.Member, welcome_idea: str) -> str:
    if not welcome_idea.strip():
        return f"Welcome to {member.guild.name}, {member.mention}. Glad you're here."
    try:
        response = client_ai.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": build_system_prompt(get_nsfw_mode(member.guild.id))},
                {"role": "user", "content": build_welcome_prompt(member, welcome_idea)},
            ],
        )
        content = response.choices[0].message.content.strip()
        if content:
            if member.mention not in content:
                content = f"{member.mention} {content}"
            return split_message(content, limit=180)[0]
    except Exception as error:
        print(f"Welcome generation failed: {error}")
    return f"{member.mention} {welcome_idea.strip()}"


# --- TASKS BACKGROUND ---
@tasks.loop(seconds=60)
async def reminder_dispatcher():
    now = int(time.time())
    reminders = due_reminders(now)
    for r_id, user_id, channel_id, guild_id, text in reminders:
        channel = client.get_channel(channel_id)
        if channel:
            try:
                await channel.send(f"<@{user_id}> Rappel : {text}")
            except Exception:
                pass
        delete_reminder(r_id)

@tasks.loop(minutes=30)
async def random_channel_posts():
    # S'exécute périodiquement pour envoyer des messages aléatoires si activé
    pass


# --- EVENTS ---
@client.event
async def on_ready() -> None:
    init_database()
    load_guild_settings()
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as error:
        print(f"Slash command sync failed: {error}")
    print(f"Logged in as {client.user} ({client.user.id})")
    if not random_channel_posts.is_running():
        random_channel_posts.start()
    if not reminder_dispatcher.is_running():
        reminder_dispatcher.start()

@client.event
async def on_message(message: discord.Message):
    if not should_reply(message):
        return
    if is_rate_limited(message):
        return
    
    async with message.channel.typing():
        try:
            prompt_messages = build_messages(message)
            response = client_ai.chat.completions.create(
                model=MODEL_NAME,
                messages=prompt_messages
            )
            reply_text = response.choices[0].message.content.strip()
            append_history(get_scope_key(message), "user", message.content)
            append_history(get_scope_key(message), "assistant", reply_text)
            
            chunks = split_message(reply_text)
            for chunk in chunks:
                await message.reply(chunk)
        except Exception as e:
            print(f"Error handling message: {e}")


# --- SLASH COMMANDS ---
@tree.command(name="activate", description="Enable the bot in a channel for this server.")
@app_commands.default_permissions(manage_guild=True)
async def activate(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return
    active_channels = active_channels_by_guild.setdefault(interaction.guild.id, set())
    active_channels.add(channel.id)
    save_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        embed=make_config_embed(
            "Channel Activated",
            f"{CHAR_NAME} can now reply in {channel.mention} when mentioned or replied to.",
        ),
        ephemeral=True,
    )

@tree.command(name="deactivate", description="Disable the bot in a channel for this server.")
@app_commands.default_permissions(manage_guild=True)
async def deactivate(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return
    active_channels = active_channels_by_guild.setdefault(interaction.guild.id, set())
    active_channels.discard(channel.id)
    save_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        embed=make_config_embed(
            "Channel Removed",
            f"{channel.mention} was removed from the active channel list.",
        ),
        ephemeral=True,
    )

@tree.command(name="listbotchannels", description="Show the channels where the bot is active in this server.")
@app_commands.default_permissions(manage_guild=True)
async def listbotchannels(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        embed=make_config_embed(
            "Active Channels",
            f"Active channels: {format_active_channels(interaction.guild)}",
        ),
        ephemeral=True,
    )

@tree.command(name="nsfw", description="Enable or disable NSFW mode for this server.")
@app_commands.default_permissions(manage_guild=True)
async def nsfw(interaction: discord.Interaction, enabled: bool) -> None:
    if interaction.guild is None:
        return
    guild_nsfw_modes[interaction.guild.id] = enabled
    save_guild_settings(interaction.guild.id)
    status = "enabled" if enabled else "disabled"
    await interaction.response.send_message(
        embed=make_config_embed("NSFW Mode Updated", f"NSFW themes have been {status}."),
        ephemeral=True
    )

# Démarrage final du bot Discord
client.run(DISCORD_TOKEN)
