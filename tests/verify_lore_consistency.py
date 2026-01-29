import domain_manager
import config
import session_manager
import asyncio
import sys
from unittest.mock import MagicMock

# Windows Unicode Fix
sys.stdout.reconfigure(encoding='utf-8')

async def test_lore_consistency():
    channel_id = "VERIFY_LORE_CONSISTENCY"
    
    # 1. Reset domain
    print("Resetting domain...")
    domain_manager.reset_domain(channel_id)
    
    # 2. Check get_lore
    lore = domain_manager.get_lore(channel_id)
    print(f"Current Lore: '{lore}'")
    print(f"Is Default: {lore == config.DEFAULT_LORE}")
    
    # 3. Simulate !ready (session_manager.check_preparation)
    print("\nSimulating !ready...")
    mock_message = MagicMock()
    mock_message.channel.id = channel_id
    
    # We need to capture the response sent to the channel
    sent_messages = []
    async def mock_send(content):
        sent_messages.append(content)
        
    mock_message.channel.send = mock_send
    
    sm = session_manager.SessionManager()
    await sm.check_preparation(mock_message)
    
    ready_response = sent_messages[0]
    print(f"!ready Response: \n{ready_response}")
    
    # 4. Verify !ready says "Not Set"
    if "❌ 세계관 미설정" in ready_response:
        print("✅ !ready correctly identifies lore as not set.")
    else:
        print("❌ !ready FAILED to identify lore as not set.")
        exit(1)

    # 5. Verify !ready marked as NOT prepared
    domain_data = domain_manager.get_domain(channel_id)
    if not domain_data.get("prepared"):
        print("✅ session state correctly marked as not prepared.")
    else:
        print("❌ session state FAILED (marked as prepared when it shouldn't be).")
        exit(1)
        
    print("\n🎉 Lore Consistency Verification Passed.")

if __name__ == "__main__":
    asyncio.run(test_lore_consistency())
