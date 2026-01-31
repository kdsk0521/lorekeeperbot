
import asyncio
import sys
import os
from unittest.mock import MagicMock, AsyncMock

# Add project root to path (Parent of tests is lorekeeperbot package, Parent of that is root?)
# Structure: logic is in lorekeeperbot/lorekeeperbot?
# Let's try adding the current directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import command_registry
    import command_handler
except ImportError:
    # Try adding one more level up if package structure is nested
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    import command_registry
    import command_handler

import discord

async def test_smoke():
    print("=== Command Registry Smoke Test ===")
    
    # 2. Check Registry Registration
    # command_handler should have registered commands upon import
    print(f"Registered Commands: {list(command_handler.registry._commands.keys())}")
    
    expected = ["info", "help", "rule", "time", "doom", "export", "analyze"]
    missing = [c for c in expected if c not in command_handler.registry._commands]
    
    if missing:
        print(f"FAIL: Missing commands: {missing}")
    else:
        print("PASS: Core commands found.")
    
    # 3. Test Help Structure
    cats = command_handler.registry.get_commands_by_category()
    print("Categories found:", list(cats.keys()))
    
    print("=== Smoke Test Complete ===")

if __name__ == "__main__":
    asyncio.run(test_smoke())
