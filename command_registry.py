"""
Lorekeeper TRPG Bot - Command Registry Module
Provides the infrastructure for command registration, dispatching, and context management.
"""

import logging
import inspect
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Awaitable

import discord
# We avoid importing domain_manager here to prevent circular imports if possible,
# or we import it inside methods if needed. Ideally, this module is pure logic.

logger = logging.getLogger(__name__)

@dataclass
class CommandContext:
    """
    Standardized Context Object for all commands.
    Encapsulates everything a command handler needs to know.
    """
    message: discord.Message
    client: discord.Client
    # Client for Generative AI (google.genai.Client)
    genai_client: Any 
    model_id: str
    
    # Checkpoint data
    channel_id: str
    user_id: str
    
    # Command parsing info
    trigger: str        # The command trigger (e.g. "info")
    args: List[str]     # Parsed arguments (e.g. ["add", "potion"])
    raw_args: str       # Raw argument string (e.g. "add potion")

    # Helper method to reply quickly
    async def reply(self, content: str = None, embed: discord.Embed = None):
        """Helper to reply to the command message."""
        if content or embed:
            await self.message.reply(content=content, embed=embed, mention_author=False)

    async def send(self, content: str = None, embed: discord.Embed = None):
        """Helper to send to the channel."""
        if content or embed:
            await self.message.channel.send(content=content, embed=embed)


class CommandRegistry:
    """
    Central registry for bot commands.
    Supports decorator-based registration and alias resolution.
    """
    
    def __init__(self):
        self._commands: Dict[str, Callable[[CommandContext], Awaitable[None]]] = {}
        self._aliases: Dict[str, str] = {}
        self._descriptions: Dict[str, str] = {}
        self._categories: Dict[str, str] = {} # cmd_name -> category

    def register(self, name: str, category: str = "General", aliases: List[str] = None, description: str = ""):
        """
        Decorator to register a command handler.
        
        Usage:
            @registry.register("info", category="Player", aliases=["내정보"], description="Show stats")
            async def cmd_info(ctx: CommandContext):
                ...
        """
        def decorator(func: Callable[[CommandContext], Awaitable[None]]):
            cmd_name = name.lower()
            
            if cmd_name in self._commands:
                logger.warning(f"Command '{cmd_name}' is being overwritten!")

            self._commands[cmd_name] = func
            self._descriptions[cmd_name] = description
            self._categories[cmd_name] = category
            
            if aliases:
                for alias in aliases:
                    alias_lower = alias.lower()
                    if alias_lower in self._aliases:
                        logger.warning(f"Alias '{alias_lower}' for '{cmd_name}' conflicts with '{self._aliases[alias_lower]}'")
                    self._aliases[alias_lower] = cmd_name
            
            logger.info(f"Registered command '{cmd_name}' (Category: {category})")
            return func
        return decorator

    async def dispatch(self, ctx: CommandContext) -> bool:
        """
        Dispatches a command based on the context.
        Returns True if a command was found and executed, False otherwise.
        """
        trigger = ctx.trigger.lower()
        
        # 1. Resolve Alias
        cmd_name = self._aliases.get(trigger, trigger)
        
        # 2. Lookup Command
        handler = self._commands.get(cmd_name)
        
        if not handler:
            return False
            
        try:
            # 3. Execute
            logger.info(f"Dispatching command '{cmd_name}' for user {ctx.user_id}")
            result = await handler(ctx)
            # Return either the explicit handler result or True to indicate command was handled
            return result if result is not None else True
        except Exception as e:
            logger.error(f"Error executing command '{cmd_name}': {e}", exc_info=True)
            await ctx.send(f"❌ 명령어 실행 중 오류가 발생했습니다: {e}")
            return True # Error handled, but command was found

    def get_commands_by_category(self) -> Dict[str, List[Dict[str, Any]]]:
        """Returns all commands grouped by category for Help generation."""
        grouped = {}
        for name, func in self._commands.items():
            cat = self._categories.get(name, "Uncategorized")
            if cat not in grouped:
                grouped[cat] = []
            
            desc = self._descriptions.get(name, "No description")
            
            # Find aliases for this command
            my_aliases = [k for k, v in self._aliases.items() if v == name]
            
            grouped[cat].append({
                "name": name,
                "aliases": my_aliases,
                "description": desc
            })
        return grouped
