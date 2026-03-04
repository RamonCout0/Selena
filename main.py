import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import sys
import re
from dotenv import load_dotenv

# ============================================================
# INICIALIZAÇÃO
# ============================================================

load_dotenv()
TOKEN = os.getenv('Selena_Token')

if not TOKEN:
    print("❌ ERRO: 'Selena_Token' não encontrado no .env")
    sys.exit()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command('help')

# ============================================================
# CONFIGURAÇÃO YT-DLP
# Usa android_sdkless como cliente principal — não exige PO Token
# em servidores, sendo o mais confiável atualmente.
# tv_embedded como fallback caso o primeiro falhe.
# ============================================================

def build_ydl_opts(search_mode=False):
    opts = {
        'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'source_address': '0.0.0.0',
        'socket_timeout': 15,
        'retries': 3,
        'extractor_retries': 3,
        'extractor_args': {
            'youtube': {
                # android_sdkless: não exige PO Token — mais estável em servidores
                # tv_embedded: fallback seguro para conteúdo restrito
                'player_client': ['android_sdkless', 'tv_embedded'],
                'skip': ['hls']
            }
        },
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Linux; Android 11; Pixel 5) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/90.0.4430.91 Mobile Safari/537.36'
            )
        }
    }
    if search_mode:
        opts['default_search'] = 'ytsearch'
    if os.path.exists("cookies.txt"):
        opts['cookiefile'] = 'cookies.txt'
    return opts

FFMPEG_OPTIONS = {
    'before_options': (
        '-reconnect 1 '
        '-reconnect_streamed 1 '
        '-reconnect_delay_max 5 '
        '-analyzeduration 0 '
        '-loglevel panic'
    ),
    'options': '-vn -b:a 192k'  # Qualidade de áudio melhorada
}

# ============================================================
# ESTADO POR SERVIDOR
# ============================================================

server_data = {}

def get_server(guild_id):
    if guild_id not in server_data:
        server_data[guild_id] = {
            'queue':   [],
            'current': None,
            'current_url': None,
            'radio':   False,
            'volume':  0.5,     # 50% padrão
            'ctx':     None
        }
    return server_data[guild_id]

# ============================================================
# ON READY
# ============================================================

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="!play | Selena 🌙"
    ))
    print(f"🌑 Selena Online: {bot.user}")

# ============================================================
# EXTRAÇÃO DE ÁUDIO (COM RETRY E FALLBACK)
# ============================================================

async def extract_info(query: str, is_url: bool = False) -> dict | None:
    """
    Tenta extrair info com android_sdkless primeiro.
    Se falhar, tenta com tv_embedded isolado.
    Retorna dict com 'url' e 'title' ou None.
    """
    loop = asyncio.get_event_loop()
    search_query = query if is_url else f"ytsearch:{query}"

    # Tentativa 1: android_sdkless + tv_embedded
    try:
        with yt_dlp.YoutubeDL(build_ydl_opts(search_mode=not is_url)) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(search_query, download=False)
            )
            video = info['entries'][0] if 'entries' in info else info
            if video and video.get('url'):
                return {'url': video['url'], 'title': video.get('title', 'Desconhecido'), 'id': video.get('id', '')}
    except Exception as e:
        print(f"[yt-dlp] Tentativa 1 falhou: {e}")

    # Tentativa 2: fallback com tv_embedded isolado
    try:
        fallback_opts = build_ydl_opts(search_mode=not is_url)
        fallback_opts['extractor_args']['youtube']['player_client'] = ['tv_embedded']
        with yt_dlp.YoutubeDL(fallback_opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(search_query, download=False)
            )
            video = info['entries'][0] if 'entries' in info else info
            if video and video.get('url'):
                return {'url': video['url'], 'title': video.get('title', 'Desconhecido'), 'id': video.get('id', '')}
    except Exception as e:
        print(f"[yt-dlp] Tentativa 2 (fallback) falhou: {e}")

    return None

# ============================================================
# MODO ETERNA — BUSCA INTELIGENTE DE RELACIONADAS
# Extrai artista do título e busca músicas relacionadas
# com variação para evitar repetição
# ============================================================

# Histórico para evitar repetir músicas no modo eterna
eterna_history = {}

def extract_artist(title: str) -> str:
    """Tenta extrair artista limpando sufixos comuns do YouTube."""
    cleaned = re.sub(
        r'\(.*?\)|\[.*?\]|Official.*|Video.*|Audio.*|Lyrics.*|ft\..*|feat\..*',
        '', title, flags=re.IGNORECASE
    ).strip()
    # Se tiver " - ", pega a parte antes (artista)
    if ' - ' in cleaned:
        return cleaned.split(' - ')[0].strip()
    return cleaned

async def search_related_song(ctx):
    data = get_server(ctx.guild.id)
    if not data['radio'] or not data['current']:
        return

    guild_id = ctx.guild.id
    if guild_id not in eterna_history:
        eterna_history[guild_id] = set()

    artist = extract_artist(data['current'])

    # Queries variadas para não repetir sempre a mesma coisa
    queries = [
        f"{artist} música popular",
        f"{artist} melhores músicas",
        f"músicas parecidas com {artist}",
        f"{data['current']} similar songs"
    ]

    import random
    query = random.choice(queries)

    try:
        loop = asyncio.get_event_loop()
        search_query = f"ytsearch8:{query}"
        with yt_dlp.YoutubeDL(build_ydl_opts()) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(search_query, download=False)
            )

        if not info or 'entries' not in info:
            data['radio'] = False
            return

        # Filtra músicas já tocadas no histórico
        candidates = [
            e for e in info['entries']
            if e and e.get('id') and e['id'] not in eterna_history[guild_id]
        ]

        if not candidates:
            # Limpa histórico se acabaram as opções
            eterna_history[guild_id].clear()
            candidates = [e for e in info['entries'] if e]

        if not candidates:
            data['radio'] = False
            return

        video = random.choice(candidates[:5])  # Escolhe aleatório entre top 5
        eterna_history[guild_id].add(video['id'])

        # Limita histórico para não crescer infinito
        if len(eterna_history[guild_id]) > 50:
            eterna_history[guild_id] = set(list(eterna_history[guild_id])[-25:])

        data['queue'].append((video['url'], video['title'], video.get('id', '')))
        await ctx.send(f"♾️ **Eterna:** adicionei `{video['title']}`")
        play_next(ctx)

    except Exception as e:
        print(f"[Eterna] Erro: {e}")
        data['radio'] = False
        await ctx.send("🌑 *Modo Eterna encerrado por erro.*")

# ============================================================
# PLAYBACK
# ============================================================

def play_next(ctx):
    data = get_server(ctx.guild.id)

    if not ctx.voice_client:
        return

    if len(data['queue']) > 0:
        url, title, *_ = data['queue'].pop(0)
        vid_id = _[0] if _ else ''
        data['current'] = title
        data['current_url'] = url

        try:
            source_raw = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
            # Aplica volume configurável
            source = discord.PCMVolumeTransformer(source_raw, volume=data['volume'])

            def after_play(error):
                if error:
                    print(f"[Playback] Erro: {error}")
                if data['radio']:
                    asyncio.run_coroutine_threadsafe(
                        search_related_song(ctx), bot.loop
                    )
                else:
                    play_next(ctx)

            ctx.voice_client.play(source, after=after_play)
            asyncio.run_coroutine_threadsafe(
                ctx.send(f"🎵 **Tocando:** `{title}`"), bot.loop
            )
        except Exception as e:
            print(f"[Playback] Exceção: {e}")
            play_next(ctx)
    else:
        if data['radio'] and data['current']:
            asyncio.run_coroutine_threadsafe(
                search_related_song(ctx), bot.loop
            )
        else:
            data['current'] = None
            data['current_url'] = None
            asyncio.run_coroutine_threadsafe(
                ctx.send("🌑 *Fila vazia.*"), bot.loop
            )

# ============================================================
# COMANDOS DE MÚSICA
# ============================================================

@bot.command(name="play", aliases=["p"])
async def play(ctx, *, busca: str):
    """Toca uma música por nome ou URL."""
    if not ctx.author.voice:
        return await ctx.send("🌑 *Entre em um canal de voz primeiro.*")
    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()

    msg = await ctx.send("🔎 *Buscando...*")
    data = get_server(ctx.guild.id)
    data['ctx'] = ctx

    is_url = busca.startswith("http")
    result = await extract_info(busca, is_url=is_url)

    if not result:
        return await msg.edit(content="❌ *Não consegui encontrar ou reproduzir essa música. Tente outra.*")

    data['queue'].append((result['url'], result['title'], result['id']))

    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        await msg.edit(content=f"📜 **Adicionado à fila:** `{result['title']}`")
    else:
        await msg.delete()
        play_next(ctx)


@bot.command(name="skip", aliases=["s", "pular"])
async def skip(ctx):
    """Pula a música atual."""
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()
        await ctx.send("⏭️ *Pulando...*")
    else:
        await ctx.send("🌑 *Nada tocando.*")


@bot.command(name="pause", aliases=["pausar"])
async def pause(ctx):
    """Pausa a música."""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ *Pausado.*")
    else:
        await ctx.send("🌑 *Nada tocando.*")


@bot.command(name="resume", aliases=["continuar", "res"])
async def resume(ctx):
    """Retoma a música pausada."""
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ *Continuando.*")
    else:
        await ctx.send("🌑 *Nada pausado.*")


@bot.command(name="volume", aliases=["vol"])
async def volume(ctx, nivel: int):
    """
    Ajusta o volume. Use de 1 a 100.
    Exemplo: !volume 75
    """
    if not 1 <= nivel <= 100:
        return await ctx.send("❌ *Use um valor entre 1 e 100.*")

    data = get_server(ctx.guild.id)
    data['volume'] = nivel / 100

    # Aplica imediatamente se estiver tocando
    if ctx.voice_client and ctx.voice_client.source:
        if isinstance(ctx.voice_client.source, discord.PCMVolumeTransformer):
            ctx.voice_client.source.volume = data['volume']

    await ctx.send(f"🔊 Volume: **{nivel}%**")


@bot.command(name="fila", aliases=["q", "queue"])
async def queue_cmd(ctx):
    """Mostra a fila de músicas."""
    data = get_server(ctx.guild.id)

    if not data['current'] and not data['queue']:
        return await ctx.send("📜 *Fila vazia.*")

    embed = discord.Embed(title="📜 Fila de Músicas", color=0x2f3136)

    if data['current']:
        embed.add_field(
            name="🎵 Tocando agora",
            value=f"`{data['current']}`",
            inline=False
        )

    if data['queue']:
        lista = "\n".join([
            f"`{i+1}.` {item[1]}" for i, item in enumerate(data['queue'][:10])
        ])
        if len(data['queue']) > 10:
            lista += f"\n*...e mais {len(data['queue']) - 10} músicas*"
        embed.add_field(name=f"A seguir ({len(data['queue'])})", value=lista, inline=False)

    embed.set_footer(text=f"♾️ Eterna: {'ON' if data['radio'] else 'OFF'} • 🔊 Volume: {int(data['volume']*100)}%")
    await ctx.send(embed=embed)


@bot.command(name="remover", aliases=["rm", "remove"])
async def remove(ctx, index: int):
    """Remove uma música da fila pelo número."""
    data = get_server(ctx.guild.id)
    if 0 < index <= len(data['queue']):
        removida = data['queue'].pop(index - 1)[1]
        await ctx.send(f"🗑️ Removido: `{removida}`")
    else:
        await ctx.send("❌ *Posição inválida.*")


@bot.command(name="eterna")
async def eterna(ctx):
    """Ativa/desativa o modo Eterna (músicas relacionadas automáticas)."""
    data = get_server(ctx.guild.id)
    data['radio'] = not data['radio']
    data['ctx'] = ctx

    if data['radio']:
        await ctx.send("♾️ **Modo Eterna: ON** — Vou buscar músicas relacionadas automaticamente.")
    else:
        await ctx.send("♾️ **Modo Eterna: OFF**")


@bot.command(name="stop", aliases=["sair", "dc"])
async def stop(ctx):
    """Para tudo e sai do canal de voz."""
    data = get_server(ctx.guild.id)
    data.update({
        'queue': [],
        'current': None,
        'current_url': None,
        'radio': False
    })
    if guild_id := ctx.guild.id:
        eterna_history.pop(guild_id, None)

    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ctx.send("🌑 *Até a próxima.*")


@bot.command(name="tocando", aliases=["np", "nowplaying"])
async def now_playing(ctx):
    """Mostra o que está tocando agora."""
    data = get_server(ctx.guild.id)
    if data['current']:
        embed = discord.Embed(
            title="🎵 Tocando agora",
            description=f"`{data['current']}`",
            color=0x2f3136
        )
        embed.set_footer(text=f"Volume: {int(data['volume']*100)}% • Eterna: {'ON' if data['radio'] else 'OFF'}")
        await ctx.send(embed=embed)
    else:
        await ctx.send("🌑 *Nada tocando no momento.*")


# ============================================================
# HELP
# ============================================================

@bot.command(name="help", aliases=["ajuda"])
async def help_command(ctx):
    embed = discord.Embed(title="🌙 Selena — Comandos", color=0x2f3136)
    embed.add_field(name="🎵 Música", inline=False, value=(
        "`!play <nome/url>` — Tocar música\n"
        "`!pause` — Pausar\n"
        "`!resume` — Continuar\n"
        "`!skip` — Pular\n"
        "`!stop` — Parar e sair\n"
        "`!volume 1-100` — Ajustar volume"
    ))
    embed.add_field(name="📜 Fila", inline=False, value=(
        "`!fila` — Ver fila atual\n"
        "`!tocando` — Ver música atual\n"
        "`!remover <nº>` — Remover da fila"
    ))
    embed.add_field(name="♾️ Eterna", inline=False, value=(
        "`!eterna` — Ativa/desativa músicas relacionadas automáticas"
    ))
    await ctx.send(embed=embed)


# ============================================================
# RUN
# ============================================================

bot.run(TOKEN)