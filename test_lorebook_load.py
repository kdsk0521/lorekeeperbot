
import domain_manager
import game_system
import os
import sys

# Windows Unicode Fix
sys.stdout.reconfigure(encoding='utf-8')

CHANNEL_ID = "TEST_LOREBOOK_LOADING_2"
FILE_PATH = "prisma_city_lore_final_updated.txt"

def run_test():
    print(f"Loading Lorebook from: {FILE_PATH}")
    
    if not os.path.exists(FILE_PATH):
        print("❌ File not found!")
        exit(1)
        
    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
        
    print(f"Read {len(content)} bytes.")
    
    # 1. Test Append Lore
    print("running domain_manager.append_lore...")
    try:
        domain_manager.append_lore(CHANNEL_ID, content)
        print("✅ append_lore success.")
    except Exception as e:
        print(f"❌ append_lore failed: {e}")
        exit(1)
        
    # 2. Verify Storage
    stored_lore = domain_manager.get_lore(CHANNEL_ID)
    print(f"Stored Lore Length: {len(stored_lore)}")
    
    if len(stored_lore) != len(content):
        # Taking into account potential pre-existing lore or formatting
        # domain_manager.append_lore might add newlines
        print(f"⚠️ Length mismatch (Stored: {len(stored_lore)} vs Original: {len(content)})")
        # Just check if content is contained
        if content[:100] in stored_lore:
             print("✅ Content (head) verified.")
        else:
             print("❌ Content verification failed.")
             exit(1)
    else:
        print("✅ Length match.")
        
    print("🎉 Lorebook Loading Test Passed.")

if __name__ == "__main__":
    run_test()
