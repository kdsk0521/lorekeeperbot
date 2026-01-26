
import sys
import os
import importlib
import logging
import sys

# Windows Unicode Fix
sys.stdout.reconfigure(encoding='utf-8')

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("HealthCheck")

def print_header(title):
    print(f"\n{'='*40}\n[{title}]\n{'='*40}")

def check_imports():
    print_header("MODULE IMPORT CHECK")
    modules = [
        "config",
        "domain_manager",
        "game_system",
        "game_world",
        "game_character",
        "npc_manager",
        "cognition",
        "persona",
        "memory_system",
        "session_manager",
        "command_handler",
        "main"
    ]
    
    failed = []
    
    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
            print(f"✅ {mod_name:<20} : OK")
        except ImportError as e:
            print(f"❌ {mod_name:<20} : IMPORT ERROR ({e})")
            failed.append(mod_name)
        except SyntaxError as e:
            print(f"❌ {mod_name:<20} : SYNTAX ERROR ({e})")
            failed.append(mod_name)
        except Exception as e:
            print(f"❌ {mod_name:<20} : FAILED ({e})")
            failed.append(mod_name)
            
    return failed

def check_instantiation():
    print_header("INSTANTIATION & SMOKE TEST")
    
    failed = []
    
    # 1. Config Consistency
    try:
        import config
        if not hasattr(config, "STATUS_EFFECTS"):
            raise AttributeError("STATUS_EFFECTS missing in config")
        if not hasattr(config, "DEFAULT_RULES"):
            raise AttributeError("DEFAULT_RULES missing in config")
        print(f"✅ Config Check         : OK")
    except Exception as e:
        print(f"❌ Config Check         : FAILED ({e})")
        failed.append("Config")

    # 2. Domain Manager (Dependencies: config)
    try:
        import domain_manager
        # Attempt to access a safe read-only function
        # Using a dummy channel ID
        _ = domain_manager.get_domain("HEALTH_CHECK_DUMMY")
        if not hasattr(domain_manager, "set_current_risk"):
             raise AttributeError("set_current_risk missing in domain_manager")
        print(f"✅ Domain Manager       : OK")
    except Exception as e:
        print(f"❌ Domain Manager       : FAILED ({e})")
        failed.append("DomainManager")

    # 3. Game System (Dependencies: game_world, game_character, npc_manager)
    try:
        import game_system
        # Check facade exports
        if not hasattr(game_system, "advance_time"):
            raise AttributeError("advance_time missing in game_system facade")
        if not hasattr(game_system, "perform_check"):
            raise AttributeError("perform_check missing in game_system facade")
        if not hasattr(game_system, "get_quest_board"):
            raise AttributeError("get_quest_board missing in game_system facade")
        if not hasattr(game_system, "get_world_context"):
            raise AttributeError("get_world_context missing in game_system facade")
        print(f"✅ Game System Facade   : OK")
    except Exception as e:
        print(f"❌ Game System Facade   : FAILED ({e})")
        failed.append("GameSystem")

    # 4. Command Handler (Dependencies: game_system, etc.)
    try:
        import command_handler
        # Just check existence, calling logic requires async/discord mocks
        if not hasattr(command_handler, "handle_participant_command"):
            raise AttributeError("handle_participant_command missing")
        print(f"✅ Command Handler      : OK")
    except Exception as e:
        print(f"❌ Command Handler      : FAILED ({e})")
        failed.append("CommandHandler")

    return failed

if __name__ == "__main__":
    print("🏥 Lorekeeper V5 Health Check Initiated...")
    
    import_fails = check_imports()
    logic_fails = check_instantiation()
    
    print_header("DIAGNOSIS REPORT")
    
    if not import_fails and not logic_fails:
        print("🎉 SYSTEM HEALTHY. READY FOR DEPLOYMENT.")
        exit(0)
    else:
        print("⚠️ SYSTEM UNSTABLE.")
        if import_fails: print(f"Import Failures: {import_fails}")
        if logic_fails: print(f"Logic Failures: {logic_fails}")
        exit(1)
