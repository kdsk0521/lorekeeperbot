
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
        if not hasattr(game_system, "get_active_quests"):
            raise AttributeError("get_active_quests missing in game_system facade")
        if not hasattr(game_system, "get_notebook_text"):
            raise AttributeError("get_notebook_text missing in game_system facade")
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

def check_v6_features():
    print_header("V6 FEATURE CHECK (Anomaly, Mental, Doom)")
    
    failed = []
    
    # 5. Anomaly System (game_world)
    try:
        import game_world
        if not hasattr(game_world, "ANOMALY_TONE_MAP"):
            raise AttributeError("ANOMALY_TONE_MAP missing")
        if not hasattr(game_world, "should_trigger_anomaly"):
            raise AttributeError("should_trigger_anomaly logic missing")
        if not hasattr(game_world, "generate_anomaly_event"):
            raise AttributeError("generate_anomaly_event generator missing")
        print(f"✅ Anomaly System       : OK")
    except Exception as e:
        print(f"❌ Anomaly System       : FAILED ({e})")
        failed.append("AnomalySystem")

    # 6. Mental & Adaptation (game_character)
    try:
        import game_character
        if not hasattr(game_character, "MENTAL_STAGES"):
            raise AttributeError("MENTAL_STAGES dict missing")
        if not hasattr(game_character, "check_adaptation_roll"):
            raise AttributeError("check_adaptation_roll logic missing")
        if not hasattr(game_character, "get_mental_status_text"):
             raise AttributeError("get_mental_status_text helper missing")
        print(f"✅ Mental System        : OK")
        
        # 6-1. Relationship & Export Helpers
        if not hasattr(game_character, "get_recent_relationships"):
            raise AttributeError("get_recent_relationships helper missing")
        if not hasattr(game_character, "export_session_history"):
            raise AttributeError("export_session_history missing")
        if not hasattr(game_character, "export_chronicle_book"):
            raise AttributeError("export_chronicle_book missing")
        print(f"✅ Info/Export Helpers  : OK")
        
    except Exception as e:
        print(f"❌ Mental/Info System   : FAILED ({e})")
        failed.append("MentalSystem")

    # 7. Abnormal Mode (domain_manager)
    try:
        import domain_manager
        # Manual check of default value (mock)
        dummy_dom = domain_manager._get_default_session()
        if not dummy_dom["settings"].get("abnormal_mode", False):
            # It should be True by default now
            print(f"⚠️ Abnormal Mode Default: False (Expected True?)")
        else:
            print(f"✅ Abnormal Mode Default: True (OK)")
            
        if not hasattr(domain_manager, "get_abnormal_mode"):
            raise AttributeError("get_abnormal_mode accessor missing")
        print(f"✅ Domain Settings      : OK")
    except Exception as e:
        print(f"❌ Domain Settings      : FAILED ({e})")
        failed.append("DomainSettings")
        
    return failed

def check_static_analysis():
    print_header("STATIC ANALYSIS (AST)")
    
    import ast
    # Import modules for hasattr checks
    import domain_manager
    import game_system
    import game_character
    import game_world
    import npc_manager
    import session_manager
    import cognition
    import persona
    
    files_to_scan = ["main.py", "command_handler.py", "game_system.py"]
    modules_to_check = {
        "domain_manager": domain_manager, 
        "game_system": game_system,
        "game_character": game_character,
        "game_world": game_world,
        "npc_manager": npc_manager,
        "session_manager": session_manager,
        "cognition": cognition,
        "persona": persona
    }
    
    failed = []
    
    for filename in files_to_scan:
        if not os.path.exists(filename):
            print(f"⚠️ Skipping {filename}: File not found.")
            continue
            
        print(f"🔍 Scanning {filename}...")
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=filename)
                
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute):
                        # check for module.method()
                        if isinstance(node.func.value, ast.Name):
                            mod_name = node.func.value.id
                            method_name = node.func.attr
                            
                            if mod_name in modules_to_check:
                                module = modules_to_check[mod_name]
                                if not hasattr(module, method_name):
                                    print(f"❌ {filename}:{node.lineno} -> {mod_name}.{method_name} NOT FOUND")
                                    failed.append(f"{filename}::{mod_name}.{method_name}")
        except Exception as e:
            print(f"❌ Failed to parse {filename}: {e}")
            failed.append(filename)
            
    if not failed:
        print("✅ Static Analysis Check  : OK")
        
    return failed

if __name__ == "__main__":
    print("🏥 Lorekeeper V5 Health Check Initiated...")
    
    import_fails = check_imports()
    logic_fails = check_instantiation()
    v6_fails = check_v6_features()
    static_fails = check_static_analysis()
    
    print_header("DIAGNOSIS REPORT")
    
    if not import_fails and not logic_fails and not v6_fails and not static_fails:
        print("🎉 SYSTEM HEALTHY. READY FOR DEPLOYMENT.")
        exit(0)
    else:
        print("⚠️ SYSTEM UNSTABLE.")
        if import_fails: print(f"Import Failures: {import_fails}")
        if logic_fails: print(f"Logic Failures: {logic_fails}")
        if v6_fails: print(f"V6 Feature Failures: {v6_fails}") # Added this line for clarity
        if static_fails: print(f"Static Failures: {static_fails}")
        exit(1)
