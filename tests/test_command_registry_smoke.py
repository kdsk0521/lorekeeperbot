
import asyncio
import sys
import os
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lorekeeperbot")))

import command_registry
import command_handler
import discord

async def test_smoke():
    print("=== Command Registry Smoke Test ===")
    
    # 1. Setup Mock Context
    mock_msg = AsyncMock(spec=discord.Message)
    mock_msg.content = "!info"
    mock_msg.author.id = 12345
    mock_msg.channel.id = 999
    mock_msg.reply = AsyncMock()
    mock_msg.channel.send = AsyncMock()
    
    ctx = command_registry.CommandContext(
        message=mock_msg,
        client=AsyncMock(),
        genai_client=AsyncMock(),
        model_id="gemini-test",
        channel_id="999",
        user_id="12345",
        trigger="info",
        args=[],
        raw_args=""
    )
    
    # 2. Check Registry Registration
    # command_handler should have registered commands upon import
    print(f"Registered Commands: {list(command_handler.registry._commands.keys())}")
    
    if "info" not in command_handler.registry._commands:
        print("FAIL: 'info' command not registered.")
        return
        
    print("PASS: 'info' command found.")
    
    # 3. Test Help (to check help structure)
    if "help" in command_handler.registry._commands:
         print("PASS: 'help' command found.")
    else:
         print("FAIL: 'help' command not registered.")

    # 4. Simulate Dispatch (Mocking the actual handler to avoid side effects if possible, 
    # or just checking dispatch logic)
    # Since handlers are async, we can just check if dispatch returns True
    
    # We won't actually call dispatch because cmd_info tries to call config/domain_manager which might fail in this script environment without real DB/API.
    # But checking registration is the main "Smoke Test" for the Refactor.
    
    cats = command_handler.registry.get_commands_by_category()
    print("Categories found:", list(cats.keys()))
    
    print("=== Smoke Test Complete ===")

if __name__ == "__main__":
    asyncio.run(test_smoke())
