import discord
from discord.ext import commands
import google.generativeai as genai

# ================= CONFIGURATION =================
DISCORD_TOKEN = "MTUwOTU0Mzg4ODEzMjYzNjc5NA.GhnNtM.2j2qwTvJEPVscyQNdb0MdP1DUWe77Py9ozhp74"
GEMINI_API_KEY = "AQ.Ab8RN6InOZLdDRBQu3QsfxElsT2WW6UUjVMiHAMf92qsB_1wwQ" 

# C'est ici que tu définis ta personnalité !
SYSTEM_PROMPT = """
Tu es le clone virtuel de Sacha (Don Sacha). Tu dois répondre aux messages sur Discord EXACTEMENT comme lui.
- Tu t'appelles Don Sacha.
- Ton ton est décontracté, amical, un peu geek (tu aimes le hardware, les cartes graphiques comme la RTX 3050).
- Utilise des expressions drôles, pas de phrases trop formelles ni de langage soutenu.
- Ne dis JAMAIS que tu es une IA ou un modèle linguistique. Tu es Don Sacha, un point c'est tout.
- Si on te pose une question complexe, réponds avec ton style habituel.
"""
# =================================================

# Configuration des accès Discord
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Configuration de l'IA
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction=SYSTEM_PROMPT
)

@bot.event
async def on_ready():
    print(f"🤖 Le clone de {bot.user.name} est connecté et prêt !")

@bot.event
async def on_message(message):
    # Évite que le bot se réponde à lui-même
    if message.author == bot.user:
        return

    # Le bot répond s'il est mentionné (@LeBot)
    if bot.user.mentioned_in(message):
        # On retire la mention du texte pour ne pas perturber l'IA
        clean_text = message.content.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '').strip()
        
        if not clean_text:
            await message.channel.send("Ouais ? Tu m'as appelé ?")
            return

        async with message.channel.typing():
            try:
                # Génération de la réponse par l'IA
                response = model.generate_content(clean_text)
                await message.channel.send(response.text)
            except Exception as e:
                print(f"Erreur IA: {e}")
                await message.channel.send("Ah, j'ai un petit bug de cerveau là...")

    await bot.process_commands(message)

bot.run(DISCORD_TOKEN)