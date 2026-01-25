"""
Lorekeeper TRPG Bot - Session Manager Module
Manages session lifecycle (Reset, Check Prep, Start).
Replaces: session_manager.py
"""

import discord
import asyncio
import logging
from typing import Optional

import domain_manager
# game_system might be needed if logic requires it, but for now mostly domain IO

RESET_CONFIRM_TIMEOUT = 5.0
RESET_CONFIRM_EMOJI = "💥"
FALLBACK_PURGE_DELAY = 2

class SessionManager:
    """Manages session lifecycle events."""
    
    async def execute_reset(self, message: discord.Message, client: discord.Client) -> None:
        """Fully resets the session by blowing up the channel."""
        channel_id = str(message.channel.id)
        
        confirm_msg = await message.channel.send(
            "🧨 **[WARNING: NUCLEAR RESET]**\n"
            "This will **DELETE THE CHANNEL** and wipe all history permanently.\n"
            f"React with {RESET_CONFIRM_EMOJI} within {RESET_CONFIRM_TIMEOUT}s to confirm."
        )
        await confirm_msg.add_reaction(RESET_CONFIRM_EMOJI)
        
        def check(reaction, user):
            return (user == message.author and str(reaction.emoji) == RESET_CONFIRM_EMOJI and reaction.message.id == confirm_msg.id)
        
        try:
            await client.wait_for('reaction_add', timeout=RESET_CONFIRM_TIMEOUT, check=check)
            
            # Reset Data
            domain_manager.reset_domain(channel_id) # Clears cache and files
            
            # Recreate Channel
            await self._recreate_channel(message)
            
        except asyncio.TimeoutError:
            try:
                await confirm_msg.delete()
                await message.channel.send("❌ Reset cancelled (Timeout).", delete_after=5)
            except: pass
    
    async def _recreate_channel(self, message: discord.Message) -> None:
        original = message.channel
        try:
            new_ch = await original.clone(reason="Session Reset")
            try: await new_ch.edit(position=original.position)
            except: pass
            
            await original.delete(reason="Session Reset (Old)")
            await new_ch.send("✨ **Session Reset Complete.**\nNew timeline started.\nType `!ready` to begin setup.")
        except Exception as e:
            await self._fallback_purge(original, e)

    async def _fallback_purge(self, channel, error) -> None:
        await channel.send(f"⚠️ **Regeneration Failed:** {error}\nFalling back to message purge in {FALLBACK_PURGE_DELAY}s...")
        await asyncio.sleep(FALLBACK_PURGE_DELAY)
        try:
            deleted = await channel.purge(limit=None, check=lambda m: not m.pinned)
            await channel.send(f"🧹 **Purged {len(deleted)} messages.**\nType `!ready`.")
        except Exception as e:
            await channel.send(f"❌ Purge failed: {e}")

    async def execute_clear(self, message: discord.Message) -> None:
        """Clears chat messages but keeps session data."""
        try:
            await message.channel.send("🧹 **Cleaning up chat...**")
            await asyncio.sleep(1)
            deleted = await message.channel.purge(limit=None, check=lambda m: not m.pinned)
            await message.channel.send(f"✨ **Chat Cleared.** ({len(deleted)} msgs removed)", delete_after=5)
        except Exception as e:
            await message.channel.send(f"⚠️ Clear failed: {e}")

    async def check_preparation(self, message: discord.Message) -> None:
        """Checks if session is ready to start (Lore/Rules)."""
        channel_id = str(message.channel.id)
        lore = domain_manager.get_lore(channel_id)
        
        ready = True
        msg = "🔍 **System Check**\n"
        
        if lore and lore.strip() and lore != "No Lore Saved": # Check default
             msg += "✅ Lore OK\n"
        else:
             # Actually config.DEFAULT_LORE is what we check against?
             # Just checking if valid string length > 100 or something?
             # domain_manager.get_lore returns default if file missing.
             # We assume if it equals default constant, it's not ready?
             # Let's simple check length.
             if len(lore) < 50:
                 msg += "❌ Lore Missing (`!lore [file/text] required`)\n"
                 ready = False
             else:
                 msg += "✅ Lore OK\n"

        rules_mode = domain_manager.get_rules_mode(channel_id)
        msg += f"✅ Rules: {rules_mode.capitalize()}\n"
        
        if ready:
            # We don't have set_prepared in new domain_manager?
            # We should probably add it or just check manually in start.
            # Using data dict directly for now.
            d = domain_manager.get_domain(channel_id)
            d["prepared"] = True
            domain_manager.save_domain(channel_id, d)
            
            msg += "\n✨ **Ready!** Set commands: `!mask [Name]` then `!start`."
        else:
            d = domain_manager.get_domain(channel_id)
            d["prepared"] = False
            domain_manager.save_domain(channel_id, d)
            msg += "\n❗ **Not Ready.**"
            
        await message.channel.send(msg)

    async def start_session(self, message: discord.Message, client_genai, model_id: str) -> bool:
        channel_id = str(message.channel.id)
        d = domain_manager.get_domain(channel_id)
        
        if not d.get("prepared"):
            await message.channel.send("⚠️ Please run `!ready` first.")
            return False
            
        if d["settings"].get("session_locked"):
            await message.channel.send("⚠️ Session already in progress.")
            return False
            
        domain_manager.set_session_lock(channel_id, True)
        await message.channel.send("🎬 **Session Started.**\nExternal interference locked. AI generating opening...")
        return True

manager = SessionManager()
