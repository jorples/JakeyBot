from core.ai.assistants import Assistants
from core.ai.core import GenAIConfigDefaults
from discord.ext import commands
from os import environ
import aiohttp
import aiofiles
import asyncio
import discord
import google.generativeai as genai
import logging
import random

class GenAIApps(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.author = environ.get("BOT_NAME", "Jakey Bot")

        # Logging format
        # LEVEL NAME: (message)
        logging.basicConfig(format='%(levelname)s: %(message)s')

        # Check for gemini API keys
        if environ.get("GOOGLE_AI_TOKEN") is None or environ.get("GOOGLE_AI_TOKEN") == "INSERT_API_KEY":
            raise Exception("GOOGLE_AI_TOKEN is not configured in the dev.env file. Please configure it and try again.")

        genai.configure(api_key=environ.get("GOOGLE_AI_TOKEN"))

        # Default generative model settings
        self._genai_configs = GenAIConfigDefaults()

        # Assistants
        self._system_prompt = Assistants()

    async def _media_download(self, url, save_path):
        async with aiohttp.ClientSession(raise_for_status=True) as session:
            # Check if the file size is too large (max 3MB)
            async with session.head(url) as _xattachments:
                _file_size = int(_xattachments.headers.get("Content-Length", None))
                if _file_size is None:
                    raise ValueError("File size is not available")

                if int(_file_size) > 3 * 1024 * 1024:
                    raise MemoryError("File size is too large to download")

            async with session.get(url, allow_redirects=True) as _xattachments:
                # write to file with random number ID
                async with aiofiles.open(save_path, "wb") as filepath:
                    async for _chunk in _xattachments.content.iter_chunked(8192):
                        await filepath.write(_chunk)

        _uploaded_file = await asyncio.to_thread(genai.upload_file, save_path)
         # Wait for the file to be uploaded
        while _uploaded_file.state.name == "PROCESSING":
            await asyncio.sleep(2.75)
            _uploaded_file = await asyncio.to_thread(genai.get_file, _uploaded_file.name)

        if _uploaded_file.state.name == "FAILED":
            raise SystemError("File upload failed")
        
        return _uploaded_file

    ###############################################
    # Rephrase command
    ###############################################
    @commands.message_command(
        name="Rephrase this message",
        contexts={discord.InteractionContextType.guild},
        integration_types={discord.IntegrationType.guild_install},
    )
    async def rephrase(self, ctx, message: discord.Message):
        """Rephrase this message"""
        await ctx.response.defer(ephemeral=True)
        
        # Generative model settings
        _model = genai.GenerativeModel(model_name=self._genai_configs.model_config, system_instruction=self._system_prompt.message_rephraser_prompt, generation_config=self._genai_configs.generation_config)
        _answer = await _model.generate_content_async(f"Rephrase this message:\n{str(message.content)}")

        # Send message in an embed format
        _embed = discord.Embed(
                title="Rephrased Message",
                description=str(_answer.text),
                color=discord.Color.random()
        )
        _embed.set_footer(text="Responses generated by AI may not give accurate results! Double check with facts!")
        _embed.add_field(name="Referenced messages:", value=message.jump_url, inline=False)
        await ctx.respond(embed=_embed)

    @rephrase.error
    async def on_application_command_error(self, ctx: discord.ApplicationContext, error: discord.DiscordException):
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.respond("❌ Sorry, this feature is not supported in DMs, please use this command inside the guild.")
            return
        
         # Check for safety or blocked prompt errors
        _exceptions = [genai.types.BlockedPromptException, genai.types.StopCandidateException, ValueError]

        # Get original exception from the DiscordException.original attribute
        error = getattr(error, "original", error)
        if any(_iter for _iter in _exceptions if isinstance(error, _iter)):
            await ctx.respond("❌ Sorry, I couldn't rephrase that message. I'm still learning!")
        
        raise error

    ###############################################
    # Explain command
    ###############################################
    @commands.message_command(
        name="Explain this message",
        contexts={discord.InteractionContextType.guild},
        integration_types={discord.IntegrationType.guild_install}
    )
    async def explain(self, ctx, message: discord.Message):
        """Explain this message"""
        await ctx.response.defer(ephemeral=True)

        # Download attachments
        _attachment_data = ["By the way, here are additional attachments that you can refer to:"]
        if message.attachments and len(message.attachments) > 0:
            for _x in message.attachments:
                _filename = f"{environ.get('TEMP_DIR')}/JAKEY.{random.randint(5000, 6000)}.{_x.filename}"

                # Max files is 5
                if len(_attachment_data) > 6:
                    break

                try:
                    _attachment_data.append((await self._media_download(_x.url, _filename)))
                except Exception as e:
                    logging.warning("apps>Explain this message: I cannot upload or attach files reason %s", e)
                    continue

        # Generative model settings
        _model = genai.GenerativeModel(model_name=self._genai_configs.model_config, system_instruction=self._system_prompt.message_summarizer_prompt, generation_config=self._genai_configs.generation_config)
        _answer = await _model.generate_content_async(
            _attachment_data if len(_attachment_data) > 1 else [] +
            [f"Explain and summarize:\n{str(message.content)}"]
        )

        # Send message in an embed format
        _embed = discord.Embed(
                title="Explain this message",
                description=str(_answer.text),
                color=discord.Color.random()
        )
        _embed.set_footer(text="Responses generated by AI may not give accurate results! Double check with facts!")
        _embed.add_field(name="Referenced messages:", value=message.jump_url, inline=False)
        await ctx.respond(embed=_embed)

    @explain.error
    async def on_application_command_error(self, ctx: discord.ApplicationContext, error: discord.DiscordException):
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.respond("❌ Sorry, this feature is not supported in DMs, please use this command inside the guild.")
            return
        
         # Check for safety or blocked prompt errors
        _exceptions = [genai.types.BlockedPromptException, genai.types.StopCandidateException, ValueError]

        # Get original exception from the DiscordException.original attribute
        error = getattr(error, "original", error)
        if any(_iter for _iter in _exceptions if isinstance(error, _iter)):
            await ctx.respond("❌ Sorry, I couldn't explain that message. I'm still learning!")
        
        raise error


    ###############################################
    # Suggestions command
    ###############################################
    @commands.message_command(
        name="Suggest a response",
        contexts={discord.InteractionContextType.guild},
        integration_types={discord.IntegrationType.guild_install}
    )
    async def suggest(self, ctx, message: discord.Message):
        """Suggest a response based on this message"""
        await ctx.response.defer(ephemeral=True)

        # Download attachments
        _attachment_data = ["By the way, here are additional attachments that you can refer to:"]
        if message.attachments and len(message.attachments) > 0:
            for _x in message.attachments:
                _filename = f"{environ.get('TEMP_DIR')}/JAKEY.{random.randint(5000, 6000)}.{_x.filename}"

                # Max files is 5
                if len(_attachment_data) > 6:
                    break

                try:
                    _attachment_data.append((await self._media_download(_x.url, _filename)))
                except Exception as e:
                    logging.warning("apps>Suggest this message: I cannot upload or attach files reason %s", e)
                    continue

        # Generative model settings
        _model = genai.GenerativeModel(model_name=self._genai_configs.model_config, system_instruction=self._system_prompt.message_suggestions_prompt, generation_config=self._genai_configs.generation_config)
        _answer = await _model.generate_content_async(
            _attachment_data if len(_attachment_data) > 1 else [] +
            [f"Suggest a response:\n{str(message.content)}"]
        )

        # To protect privacy, send the message to the user
        # Send message in an embed format
        _embed = discord.Embed(
                title="Suggested Responses",
                description=str(_answer.text),
                color=discord.Color.random()
        )
        _embed.set_footer(text="Responses generated by AI may not give accurate results! Double check with facts!")
        _embed.add_field(name="Referenced messages:", value=message.jump_url, inline=False)
        await ctx.respond(embed=_embed)

    @suggest.error
    async def on_application_command_error(self, ctx: discord.ApplicationContext, error: discord.DiscordException):
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.respond("❌ Sorry, this feature is not supported in DMs, please use this command inside the guild.")
            return
        
         # Check for safety or blocked prompt errors
        _exceptions = [genai.types.BlockedPromptException, genai.types.StopCandidateException, ValueError]

        # Get original exception from the DiscordException.original attribute
        error = getattr(error, "original", error)
        if any(_iter for _iter in _exceptions if isinstance(error, _iter)):
            await ctx.respond("❌ Sorry, this is embarrasing but I couldn't suggest good responses. I'm still learning!")
        
        raise error
    
def setup(bot):
    bot.add_cog(GenAIApps(bot))
