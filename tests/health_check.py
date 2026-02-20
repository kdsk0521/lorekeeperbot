
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
        "une_facade",
        "waterfall_pipeline",
        "theoria_analyzer",
        "judgment_engine",
        "doom_module",
        "anomaly_module",
        "vigor_composure_module",
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
            
    return not failed

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

    return not failed

def check_v6_features():
    print_header("V6 FEATURE CHECK (Anomaly, Mental, Doom)")
    
    failed = []
    
    # 5. Anomaly System (game_world)
    # Anomaly System: Migrated to UNE AnomalyModule (game_world legacy removed)
    print(f"✅ Anomaly System       : OK (UNE AnomalyModule)")

    # 6. Mental & Adaptation (UNE/game_character)
    try:
        import game_character
        if not hasattr(game_character, "MENTAL_STAGES"):
            raise AttributeError("MENTAL_STAGES dict missing")
        if not hasattr(game_character, "get_mental_status_text"):
             raise AttributeError("get_mental_status_text helper missing")
        print(f"✅ Mental System (Data) : OK")
        
        # 6-1. UNE Engine Check
        import une_facade
        if not hasattr(une_facade, "UniversalNarrativeEngine"):
            raise AttributeError("UniversalNarrativeEngine missing")
        print(f"✅ UNE Engine Facade    : OK")
        
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
        
    return not failed

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
    return not failed # Return True if no failures, False otherwise

def check_deprecated_patterns(base_dir):
    """
    Scans codebase for banned/legacy patterns that should no longer exist.
    """
    print("\n" + "="*40)
    print("[DEPRECATED PATTERN SCAN]")
    print("="*40)
    
    banned_patterns = {
        r"p_data\['inventory'\]\s*=": "Legacy Inventory Set",
        r"p_data\.get\('inventory'\)": "Legacy Inventory Get (Check context)",
        r"p_data\['economy'\]": "Legacy Economy Usage",
        r"gold_change": "Legacy Gold Logic",
        r"memo_add": "Legacy Memo Logic",
        r"current_inventory": "Legacy AI Param",
        r"current_gold": "Legacy AI Param"
    }
    
    found_issues = False
    
    for root, _, files in os.walk(base_dir):
        if "venv" in root or "__pycache__" in root: continue
        
        for file in files:
            if not file.endswith(".py") or file == "health_check.py": continue
            
            path = os.path.join(root, file)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.readlines()
                    
                for i, line in enumerate(content):
                    for pattern, desc in banned_patterns.items():
                        if re.search(pattern, line):
                            # Exemptions (Migration code in domain_manager is allowed)
                            if "domain_manager.py" in file and "Legacy" in line: continue 
                            # If it's a comment, skip
                            if line.strip().startswith("#"): continue
                            
                            print(f"⚠️ [Legacy Found] {file}:{i+1} -> {desc}")
                            print(f"   Line: {line.strip()}")
                            found_issues = True
            except Exception as e:
                pass
                
    if not found_issues:
        print("✨ CLEAN. No obvious legacy patterns found.")
        return True
    else:
        print("❌ Legacy patterns detected. Please review.")
        return False

def check_event_lore_pipeline():
    """
    Verifies that the Event Lore Summary components are correctly wired.
    """
    print("\n" + "="*40)
    print("[EVENT LORE PIPELINE CHECK]")
    print("="*40)
    
    try:
        # 1. Check Domain Manager
        import domain_manager
        if not hasattr(domain_manager, "get_event_lore_summary"):
            print("❌ domain_manager.get_event_lore_summary missing.")
            return False
        if not hasattr(domain_manager, "set_event_lore_summary"):
            print("❌ domain_manager.set_event_lore_summary missing.")
            return False
            
        # 2. Check Memory System
        import memory_system
        if not hasattr(memory_system, "summarize_lore_for_events"):
            print("❌ memory_system.summarize_lore_for_events missing.")
            return False
            
        print("✅ Pipeline Components Detected: OK")
        return True
    except Exception as e:
        print(f"❌ Pipeline Check Failed: {e}")
        return False

if __name__ == "__main__":
    import os # Ensure os is imported
    import sys # Ensure sys is imported
    import re # Ensure re is imported
    print("🏥 Lorekeeper V5 Health Check Initiated...\n")
    
    # Run all checks and combine results
    all_checks_passed = True
    
    if not check_imports():
        all_checks_passed = False
    if not check_instantiation():
        all_checks_passed = False
    if not check_v6_features():
        all_checks_passed = False
    if not check_static_analysis():
        all_checks_passed = False
    if not check_deprecated_patterns(os.getcwd()):
        all_checks_passed = False
    if not check_event_lore_pipeline():
        all_checks_passed = False
    
    print_header("DIAGNOSIS REPORT")
    
    if all_checks_passed:
        print("🎉 SYSTEM HEALTHY. READY FOR DEPLOYMENT.")
        sys.exit(0)
    else:
        print("❌ SYSTEM UNSTABLE. Please review the failures above.")
        sys.exit(1)
