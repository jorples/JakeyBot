from core.ai.assistants import Assistants
from core.ai.core import GenAIConfigDefaults
from core.ai.history import HistoryManagement as histmgmt
from discord.ext import commands
from google.api_core.exceptions import PermissionDenied, InternalServerError
from os import environ, remove
from pathlib import Path
import google.generativeai as genai
import asyncio
import discord
import inspect
import random
import requests

class AI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.author = environ.get("BOT_NAME", "Jakey Bot")

        # Check for gemini API keys
        if environ.get("GOOGLE_AI_TOKEN") is None or environ.get("GOOGLE_AI_TOKEN") == "INSERT_API_KEY":
            raise Exception("GOOGLE_AI_TOKEN is not configured in the dev.env file. Please configure it and try again.")

        genai.configure(api_key=environ.get("GOOGLE_AI_TOKEN"))

    ###############################################
    # Ask command
    ###############################################
    @commands.slash_command(
        contexts={discord.InteractionContextType.guild, discord.InteractionContextType.bot_dm},
        integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}
    )
    @commands.cooldown(3, 6, commands.BucketType.user) # Add cooldown so GenerativeLanguage API won't hit rate limits in one's Google cloud account.
    @discord.option(
        "prompt",
        description="Enter your prompt, ask real questions, or provide a context for the model to generate a response",
        required=True
    )
    @discord.option(
        "attachment",
        description="Attach your files to answer from. Supports image, audio, video, and some text files",
        required=False,
    )
    @discord.option(
        "model",
        description="Choose a model to use for the conversation - flash is the default model",
        choices=["Gemini 1.5 Pro (2M) - advanced chat tasks with low availability",
                "Gemini 1.5 Flash (1M) - general purpose with high availability",],
        default="Gemini 1.5 Flash (1M)",
        required=False
    )
    @discord.option(
        "json_mode",
        description="Configures the response whether to format it in JSON",
        default=False,
    )
    @discord.option(
        "append_history",
        description="Store the conversation to chat history? (This option is void with json_mode)",
        default=True
    )
    async def ask(self, ctx, prompt: str, attachment: discord.Attachment, model: str, json_mode: bool,
        append_history: bool):
        """Ask a question using Gemini-based AI"""
        await ctx.response.defer()

        ###############################################
        # Model configuration
        ###############################################
        # Message history
        # Since pycord 2.6, user apps support is implemented. But in order for this command to work in DMs, it has to be installed as user app
        # Which also exposes the command to the guilds the user joined where the bot is not authorized to send commands. This can cause partial function succession with MissingAccess error
        # One way to check is to check required permissions through @command.has_permissions(send_messages=True) or ctx.interaction.authorizing_integration_owners
        # The former returns "# This raises ClientException: Parent channel not found when ran outside of authorized guilds or DMs" which should be a good basis

        # Check if SHARED_CHAT_HISTORY is enabled
        if environ.get("SHARED_CHAT_HISTORY", "false").lower() == "true":
            guild_id = ctx.guild.id if ctx.guild else ctx.author.id # Always fallback to ctx.author.id for DMs since ctx.guild is None
        else:
            guild_id = ctx.author.id

        # This command is available in DMs
        if ctx.guild is not None:
            # This returns None if the bot is not installed or authorized in guilds
            # https://docs.pycord.dev/en/stable/api/models.html#discord.AuthorizingIntegrationOwners
            if ctx.interaction.authorizing_integration_owners.guild == None:
                await ctx.respond("🚫 This commmand can only be used in DMs or authorized guilds!")
                return

        # Load the context history and initialize the HistoryManagement class
        HistoryManagement = histmgmt(guild_id)
        # Initialize
        await HistoryManagement.initialize()

        try:
            await HistoryManagement.load_history(check_length=True)
        except ValueError:
            await ctx.respond("⚠️ Maximum history reached! Please wipe the conversation using `/sweep` command")
            return
        
        # Set context_history
        context_history = HistoryManagement.context_history

        # Limit prompt characters to 2000
        if len(prompt) > 2000:
            await ctx.respond("Sorry, I can only process prompts with 2000 characters or less!")
            return

        # Initialize GenAIConfigDefaults
        genai_configs = GenAIConfigDefaults()

        # default system prompt - load assistants
        assistants_system_prompt = Assistants()

        # Check whether to output as JSON and disable code execution
        if not json_mode:
            # enable plugins
            enabled_tools = "code_execution"
        else:
            genai_configs.generation_config.update({"response_mime_type": "application/json"})
            enabled_tools = None
            
        # Model configuration - the default model is flash
        if model.split("-")[0].strip() in genai_configs.supported_models:
            # Check if the model is implemented
            if genai_configs.supported_models[model.split("-")[0].strip()] == "unsupported-yet-to-be-implemented":
                await ctx.respond("⚠️ This model is not yet available. Please try again later")
                return

            genai_configs.model_config = genai_configs.supported_models[model.split("-")[0].strip()]

        model_to_use = genai.GenerativeModel(model_name=genai_configs.model_config, safety_settings=genai_configs.safety_settings_config, generation_config=genai_configs.generation_config, system_instruction=assistants_system_prompt.jakey_system_prompt, tools=enabled_tools)

        ###############################################
        # File attachment processing
        ###############################################
        _xfile_uri = None
        # Enable multimodal support if an attachment is provided
        if attachment is not None:
            # Download the attachment
            _xfilename = f"JAKEY.{guild_id}.{random.randint(5000, 6000)}.{attachment.filename}"
            try:
                with requests.get(attachment.url, allow_redirects=True, stream=True) as _xattachments:
                    # Raise error if the request failed
                    _xattachments.raise_for_status()

                    # write to file with random number ID
                    with open(f"{environ.get('TEMP_DIR', 'temp')}/{_xfilename}", "wb") as filepath:
                        for _chunk in _xattachments.iter_content(chunk_size=4096):
                            filepath.write(_chunk)
            except requests.exceptions.HTTPError as httperror:
                # Remove the file if it exists ensuring no data persists even on failure
                if Path(f"{environ.get('TEMP_DIR', 'temp')}/{_xfilename}").exists():
                    remove(f"{environ.get('TEMP_DIR', 'temp')}/{_xfilename}")
                # Raise exception
                raise httperror

            # Upload the file to the server
            try:
                _xfile_uri = genai.upload_file(path=f"{environ.get('TEMP_DIR', 'temp')}/{_xfilename}", display_name=_xfilename)
                _x_msgstatus = None

                # Wait for the file to be uploaded
                while _xfile_uri.state.name == "PROCESSING":
                    if _x_msgstatus is None:
                        _x_msgstatus = await ctx.send("⌛ Processing the file attachment... this may take a while")
                    await asyncio.sleep(3)
                    _xfile_uri = genai.get_file(_xfile_uri.name)

                if _xfile_uri.state.name == "FAILED":
                    await ctx.respond("❌ Sorry, I can't process the file attachment. Please try again.")
                    raise ValueError(_xfile_uri.state.name)
            except Exception as e:
                await ctx.respond(f"❌ An error has occured when uploading the file or the file format is not supported\nLog:\n```{e}```")
                remove(f"{environ.get('TEMP_DIR', 'temp')}/{_xfilename}")
                return

            # Remove the file from the temp directory
            remove(f"{environ.get('TEMP_DIR', 'temp')}/{_xfilename}")

            # Immediately use the "used" status message to indicate that the file API is used
            if _x_msgstatus is not None:
                await _x_msgstatus.edit(content=f"Used: **{attachment.filename}**")
            else:
                await ctx.send(f"Used: **{attachment.filename}**")

        ###############################################
        # Answer generation
        ###############################################
        final_prompt = [_xfile_uri, f'{prompt}'] if _xfile_uri is not None else f'{prompt}'
        chat_session = model_to_use.start_chat(history=context_history["chat_history"], enable_automatic_function_calling=True)

        if not json_mode:
            # Re-write the history if an error has occured
            # For now this is the only workaround that I could find to re-write the history if there are dead file references causing PermissionDenied exception
            # when trying to access the deleted file uploaded using Files API. See:
            # https://discuss.ai.google.dev/t/what-is-the-best-way-to-persist-chat-history-into-file/3804/6?u=zavocc306
            try:
                answer = await chat_session.send_message_async(final_prompt)
            #  Retry the response if an error has occured
            except PermissionDenied:
                context_history["chat_history"] = [
                    {"role": x.role, "parts": [y.text]} 
                    for x in chat_session.history 
                    for y in x.parts 
                    if x.role and y.text
                ]
                answer = await chat_session.send_message_async(final_prompt)
        else:
            answer = await model_to_use.generate_content_async(final_prompt)
    
        # Embed the response if the response is more than 2000 characters
        # Check to see if this message is more than 2000 characters which embeds will be used for displaying the message
        if len(answer.text) > 4096:
            # Send the response as file
            response_file = f"{environ.get('TEMP_DIR', 'temp')}/response{random.randint(6000,7000)}.md"
            with open(response_file, "w+") as f:
                f.write(answer.text)
            await ctx.respond("⚠️ Response is too long. But, I saved your response into a markdown file", file=discord.File(response_file, "response.md"))
        elif len(answer.text) > 2000:
            embed = discord.Embed(
                # Truncate the title to (max 256 characters) if it exceeds beyond that since discord wouldn't allow it
                title=str(prompt)[0:100],
                description=str(answer.text),
                color=discord.Color.random()
            )
            embed.set_author(name=self.author)
            embed.set_footer(text="Responses generated by AI may not give accurate results! Double check with facts!")
            await ctx.respond(embed=embed)
        else:
            await ctx.respond(answer.text)

        # Append the context history if JSON mode is not enabled
        if not json_mode:
            # Append the prompt to prompts history
            context_history["prompt_history"].append(prompt)
            # Also save the ChatSession.history attribute to the context history chat history key so it will be saved through pickle
            context_history["chat_history"] = chat_session.history

        # Print context size and model info
        if not json_mode and append_history:
            await HistoryManagement.save_history()
            await ctx.send(inspect.cleandoc(f"""
                           > 📃 Context size: **{len(context_history["prompt_history"])}** of {environ.get("MAX_CONTEXT_HISTORY", 20)}
                           > ✨ Model used: **{genai_configs.model_config}**
                           """))
        else:
            await ctx.send(f"> 📃 Responses isn't be saved\n> ✨ Model used: **{genai_configs.model_config}**")

    # Handle all unhandled exceptions through error event, handled exceptions are currently image analysis safety settings
    @ask.error
    async def on_application_command_error(self, ctx: discord.ApplicationContext, error: discord.DiscordException):
        # Cooldown error
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.respond(f"🕒 Woah slow down!!! Please wait for few seconds before using this command again!")
            return

        # Check for safety or blocked prompt errors
        _exceptions = [genai.types.BlockedPromptException, genai.types.StopCandidateException, ValueError]

        # Get original exception from the DiscordException.original attribute
        error = getattr(error, "original", error)
        if any(_iter for _iter in _exceptions if isinstance(error, _iter)):
            await ctx.respond("❌ Sorry, I can't answer that question! Please try asking another question.")
        # Check if the error is InternalServerError
        elif isinstance(error, InternalServerError):
            await ctx.respond("⚠️ Something went wrong (500) and its not your fault but its mostly you! If that's the case, please retry or try changing the model or rewrite your prompt.")
        # For failed downloads from attachments
        elif isinstance(error, requests.exceptions.HTTPError):
            await ctx.respond("⚠️ Uh oh! Something went wrong while processing file attachment! Please try again later.")
        else:
            await ctx.respond(f"⚠️ Uh oh! I couldn't answer your question, something happend to our end!\nHere is the logs for reference and troubleshooting:\n ```{error}```")
        
        # Raise error
        raise error

    ###############################################
    # Clear context command
    ###############################################
    @commands.slash_command(
        contexts={discord.InteractionContextType.guild, discord.InteractionContextType.bot_dm},
        integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}
    )
    async def sweep(self, ctx):
        """Clear the context history of the conversation"""
        # Check if SHARED_CHAT_HISTORY is enabled
        if environ.get("SHARED_CHAT_HISTORY", "false").lower() == "true":
            guild_id = ctx.guild.id if ctx.guild else ctx.author.id
        else:
            guild_id = ctx.author.id

        # This command is available in DMs
        if ctx.guild is not None:
            # This returns None if the bot is not installed or authorized in guilds
            # https://docs.pycord.dev/en/stable/api/models.html#discord.AuthorizingIntegrationOwners
            if ctx.interaction.authorizing_integration_owners.guild == None:
                await ctx.respond("🚫 This commmand can only be used in DMs or authorized guilds!")
                return  

        # Initialize history
        HistoryManagement = histmgmt(guild_id)
        await HistoryManagement.initialize()

        # Clear
        await HistoryManagement.clear_history()
        await ctx.respond("✅ Context history cleared!")

    # Handle errors
    @sweep.error
    async def on_application_command_error(self, ctx: discord.ApplicationContext, error: discord.DiscordException):
        # Get original error
        _error = getattr(error, "original")
        if isinstance(_error, PermissionError):
            await ctx.respond("⚠️ An error has occured while clearing chat history, logged the error to the owner")
        elif isinstance(_error, FileNotFoundError):
            await ctx.respond("ℹ️ Chat history is already cleared!")
        else:
            await ctx.respond("❌ Something went wrong, please check the console logs for details.")
            raise error

def setup(bot):
    bot.add_cog(AI(bot))
