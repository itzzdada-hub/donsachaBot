import os
import discord
from discord.ext import commands
import google.generativeai as genai

# ================= CONFIGURATION VIA RENDER =================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SYSTEM_PROMPT = """
Tu es le clone virtuel de Sacha (Don Sacha). Tu dois répondre aux messages sur Discord EXACTEMENT comme lui.
- Tu t'appelles Don Sacha.
- Ton ton est décontracté, amical, un peu geek (tu aimes le hardware, les cartes graphiques comme la RTX 3050).
- Utilise des expressions drôles, pas de phrases trop formelles ni de langage soutenu.
- Ne dis JAMAIS que tu es une IA ou un modèle linguistique. Tu es Don Sacha, un point c'est tout.
- Si on te pose une question complexe, réponds avec ton style habituel.
"""
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Configuration sécurisée de l'IA
if GEMINI_API_KEY:
    # On nettoie les espaces invisibles au cas où
    api_key_clean = GEMINI_API_KEY.strip()
    genai.configure(api_key=api_key_clean)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=SYSTEM_PROMPT
    )
else:
    print("❌ Erreur : GEMINI_API_KEY introuvable.")

@bot.event
async def on_ready():
    print(f"🤖 Le clone de {bot.user.name} est connecté et prêt !")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    
    if bot.user.mentioned_in(message) or is_dm:
        clean_text = message.content
        if bot.user.mentioned_in(message):
            clean_text = clean_text.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '')
        clean_text = clean_text.strip()
        
        if not clean_text:
            clean_text = "Salut"

        async with message.channel.typing():
            try:
                # Bloc d'envoi à l'IA
                response = model.generate_content(clean_text)
                
                if response and response.text:
                    await message.channel.send(response.text)
                else:
                    await message.channel.send("Mon cerveau a freeze sur ton message là, reuf...")
            
            except Exception as e:
                # Force l'affichage dans la console Render QUOI QU'IL ARRIVE
                print("--- ERREUR GENERATION IA ---")
                print(str(e))
                print("----------------------------")
                await message.channel.send(f"Ah, j'ai un petit bug de cerveau là... (Erreur: {str(e)[:50]})")

    await bot.process_commands(message)

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN.strip())
else:
    print("❌ Erreur : DISCORD_TOKEN introuvable.")
