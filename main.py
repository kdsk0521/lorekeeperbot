"""
Lorekeeper TRPG Bot - Main Module
Version: 4.0 (Consolidated)
"""

import discord
import os
import asyncio
import logging
import io
import re
import json
from typing import Optional, Tuple, List, Dict
from collections import defaultdict
from google import genai
from google.genai import types

# =========================================================
# MODULE IMPORTS
# =========================================================
try:
    import config
    import bot_utils
    import input_handler
    import persona
    import fermentation
    
    # Unified Core Modules
    import domain_manager
    import game_system
    import cognition
    import command_handler 
    import session_manager
    import memory_system

except ImportError as e:
    print(f"CRITICAL ERROR: Failed to import modules. {e}")
    exit(1)

# =========================================================
# CONFIGURATION & LOGGING
# =========================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

DISCORD_TOKEN = config.DISCORD_TOKEN
GEMINI_API_KEY = config.GEMINI_API_KEY
MODEL_ID = config.MODEL_ID
MODEL_ID_FLASH = config.MODEL_ID_FLASH

if not GEMINI_API_KEY: logging.warning("GEMINI_API_KEY Missing!")

client_genai = None
try:
    if GEMINI_API_KEY: client_genai = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e: logging.error(f"GenAI Init Failed: {e}")

intents = discord.Intents.default()
intents.message_content = True
client_discord = discord.Client(intents=intents)

channel_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


# =========================================================
# DISCORD EVENTS
# =========================================================
@client_discord.event
async def on_ready():
    logging.info(f'Logged in as {client_discord.user}')
    await client_discord.change_presence(activity=discord.Game(name="!help | TRPG"))

@client_discord.event
async def on_message(message):
    if message.author == client_discord.user: return
    if not isinstance(message.channel, (discord.TextChannel, discord.Thread)): return
    
    asyncio.create_task(_process_message(message))

async def _process_message(message):
    channel_id = str(message.channel.id)
    async with channel_locks[channel_id]:
        try:
            content = message.content.strip()
            parsed = input_handler.parse_input(content)
            
            # 1. COMMANDS
            if parsed and parsed['type'] == 'command':
                sys_trigger = await command_handler.dispatch_command(
                    parsed['command'], message, channel_id, parsed,
                    client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH,
                    domain_manager.get_domain(channel_id)
                )
                if sys_trigger:
                    await generate_ai_response(message, channel_id, sys_trigger)
                return

            # 2. SESSION LOCK CHECK
            if domain_manager.is_session_locked(channel_id):
                status = domain_manager.get_participant_status(channel_id, message.author.id)
                if not status: return # Ignore non-participants

            # 3. SPECIAL INPUTS (Dice, OOC -> handled by dispatch too)
            if parsed and parsed['type'] in ['dice', 'ooc', 'chat_with_ooc']:
                await command_handler.dispatch_command(
                    None, message, channel_id, parsed,
                    client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH,
                    domain_manager.get_domain(channel_id)
                )
                return

            # 4. CHAT LOGGING / RESPONSE
            mode = domain_manager.get_response_mode(channel_id)
            
            if mode == 'waiting':
                mask = domain_manager.get_user_mask(channel_id, message.author.id)
                log_content = message.content
                if message.attachments:
                    for att in message.attachments:
                        txt, _ = await bot_utils.read_attachment_text(att)
                        if txt: log_content += f"\n(Attach: {txt})"
                
                domain_manager.append_history(channel_id, mask, log_content.strip())
                await message.add_reaction("✏️")
                return

            # AUTO MODE
            await generate_ai_response(message, channel_id)

        except Exception as e:
            logging.error(f"Message Error: {e}", exc_info=True)
            await message.channel.send(f"⚠️ Error: {e}")


# =========================================================
# AI GENERATION CORE
# =========================================================
async def generate_ai_response(message, channel_id: str, system_trigger: str = None) -> None:
    if not client_genai:
        await message.channel.send("⚠️ No AI Configured")
        return

    domain_data = domain_manager.get_domain(channel_id)
    if not domain_data: return

    # 1. PREPARE INPUT
    user_input = system_trigger if system_trigger else message.content
    if not system_trigger and message.attachments:
        for att in message.attachments:
            txt, err = await bot_utils.read_attachment_text(att)
            if err: 
                await message.channel.send(err); return
            if txt: user_input += f"\n(Attach):\n{txt}"
    
    user_input = user_input.strip()
    if not user_input and not system_trigger: return
    
    # Log User Input (if not system trigger)
    user_mask = "System"
    if not system_trigger:
        user_mask = domain_manager.get_user_mask(channel_id, message.author.id)
        domain_manager.append_history(channel_id, user_mask, user_input)
    
    parsed = input_handler.parse_input(user_input) if not system_trigger else {'content': user_input, 'style': {}}
    
    async with message.channel.typing():
        try:
            # Ensure participant active
            if not system_trigger:
                 domain_manager.update_participant(channel_id, message.author)
            
            # Format Action Text
            if system_trigger:
                action_text = system_trigger
            else:
                style = parsed.get('style', 'Description')
                content = parsed['content'] if parsed else user_input
                if style == 'Dialogue': action_text = f"[{user_mask}] says: {content}"
                elif style == 'Action': action_text = f"[{user_mask}] does: {content}"
                else: action_text = f"[{user_mask}]: {content}"

            # 2. GATHER CONTEXT
            lore_txt = domain_manager.get_lore_with_npcs(channel_id)
            rule_txt = domain_manager.get_rules(channel_id)
            world_ctx = game_system.get_world_context(channel_id)
            obj_ctx = game_system.get_objective_context(channel_id)
            
            # History for Analysis
            history = domain_data.get('history', [])[-fermentation.RECENT_HISTORY_FOR_ANALYSIS:]
            hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history]) + f"\nUser: {action_text}"
            
            active_quests = game_system.get_quest_board(channel_id).get("active", [])
            quest_txt = " | ".join(active_quests) if active_quests else "None"
            
            uid = str(message.author.id)
            p_data = domain_manager.get_participant_data(channel_id, uid)
            p_ctx = game_system.get_status_summary(p_data) if p_data else "" # Simplified
            
            # 3. COGNITION: ANALYSIS (Theoria)
            nvc_res = await cognition.analyze_context_nvc(
                client_genai, MODEL_ID, hist_text, lore_txt, rule_txt, quest_txt, player_context=p_ctx
            )
            
            # Updates from Analysis
            if nvc_res.get("CurrentLocation"): domain_manager.set_current_location(channel_id, nvc_res["CurrentLocation"])
            if nvc_res.get("LocationRisk"): domain_manager.set_current_risk(channel_id, nvc_res["LocationRisk"])
            
            # System Action (Quest/Memo from logic)
            sys_action = nvc_res.get("SystemAction")
            if sys_action:
                 auto_msg = await command_handler.process_ai_system_action(channel_id, sys_action)
                 if auto_msg: await message.channel.send(f"🤖 {auto_msg}")
            
            # Build Context String for Generation
            # (Skipping complex manual context builders to save token/complexity, trusting NVC summary + raw inputs)
            
            nvc_summary = f"Loc: {nvc_res.get('CurrentLocation')}\nObs: {nvc_res.get('Observation')}\nNeed: {nvc_res.get('Need')}"
            
            fermented_summaries = [e["summary"] for e in domain_data.get("fermented_history", []) if e.get("summary")]
            fermented_summary_text = "\n---\n".join(fermented_summaries)
            
            full_prompt = (
                f"### World State\n{world_ctx}\n\n"
                f"### Player Status\n{p_ctx}\n\n"
                f"### Analysis\n{nvc_summary}\n\n"
                f"### User Action\n{action_text}\n\n"
                "Generate narrative response in Korean. 3rd person."
            )

            # 4. GENERATION (Persona)
            active_genres = domain_manager.get_active_genres(channel_id)
            custom_tone = domain_manager.get_custom_tone(channel_id)
            scene_type = nvc_res.get("SceneType", "normal")
            
            session = persona.create_risu_style_session(
                client_genai, MODEL_ID, lore_txt, rule_txt, active_genres, custom_tone,
                domain_data.get("deep_memory", ""), fermented_summary=fermented_summary_text,
                character_descriptions="", scene_type=scene_type
            )
            
            # Inject History
            for h in domain_data.get('history', []):
                 role = "user" if h['role'] == "User" else "model"
                 session.history.append(types.Content(role=role, parts=[types.Part(text=str(h['content']))]))
            
            response = await persona.generate_response_with_retry(client_genai, session, full_prompt)
            
            # Clean
            if response:
                response = re.sub(r'```system_update[\s\S]*?```', '', response, flags=re.IGNORECASE).strip()
            
            if response:
                await bot_utils.send_long_message(message.channel, response)
                domain_manager.append_history(channel_id, "User", action_text)
                domain_manager.append_history(channel_id, "Char", response)
                
                # 5. COGNITION: EXTRACTION (Logos)
                try:
                    # Gather data for extraction
                    inv = p_data.get("inventory", {}) if p_data else {}
                    gold = p_data.get("economy", {}).get("gold", 0) if p_data else 0
                    status = p_data.get("status_effects", []) if p_data else []
                    
                    mem = domain_manager.get_ai_memory(channel_id, uid)
                    rels = mem.get("relationships", {})
                    passives = mem.get("passives", [])
                    
                    u_res = await cognition.extract_all_updates(
                        client_genai, MODEL_ID_FLASH, action_text, response,
                        current_inventory=inv, current_gold=gold, current_status=status,
                        current_relationships=rels, current_passives=passives,
                        current_quests=game_system.get_active_quests(channel_id),
                        current_memos=game_system.get_memos(channel_id),
                        lore_npc_names=list(domain_manager.get_npcs(channel_id).keys()),
                        fermented_context=fermented_summary_text
                    )
                    
                    # Apply Updates
                    # (Simplified application - could be moved to game_system helpers)
                    msgs = []
                    
                    # Player Update
                    pu = u_res.get("PlayerUpdate")
                    if pu:
                        if pu.get("inventory_add"): 
                            for k,v in pu["inventory_add"].items(): 
                                _, m = game_system.update_inventory(p_data, "add", k, v); msgs.append(m)
                        if pu.get("inventory_remove"):
                            for k,v in pu["inventory_remove"].items(): 
                                _, m = game_system.update_inventory(p_data, "remove", k, v); msgs.append(m)
                        if pu.get("gold_change"):
                             economy = p_data.get("economy", {"gold":0})
                             economy["gold"] += pu["gold_change"]
                             p_data["economy"] = economy
                             msgs.append(f"💰 Gold {pu['gold_change']:+}")
                        if pu.get("status_add"):
                             for s in pu["status_add"]:
                                 _, m = game_system.update_status_effect(p_data, "add", s); msgs.append(m)
                        if pu.get("status_remove"):
                             for s in pu["status_remove"]:
                                 _, m = game_system.update_status_effect(p_data, "remove", s); msgs.append(m)
                        
                        domain_manager.save_participant_data(channel_id, uid, p_data)
                    
                    # Memory Update
                    pmu = u_res.get("PlayerMemoryUpdate")
                    if pmu:
                        if pmu.get("relationships"):
                             domain_manager.update_ai_memory(channel_id, uid, {"relationships": pmu["relationships"]})
                             msgs.append("💞 관계도 업데이트됨")
                        if pmu.get("passives"):
                             added_passives = []
                             for p_item in pmu["passives"]:
                                 domain_manager.add_to_ai_memory_list(channel_id, uid, "passives", p_item)
                                 added_passives.append(p_item)
                             msgs.append(f"🏆 패시브/칭호: {', '.join(added_passives)}")

                    # Quest Update
                    qu = u_res.get("QuestUpdate")
                    if qu:
                        if qu.get("quest_add"): 
                            for q in qu["quest_add"]: game_system.add_quest(channel_id, q); msgs.append(f"🔥 New Quest: {q}")
                        if qu.get("quest_complete"): 
                            for q in qu["quest_complete"]: game_system.complete_quest(channel_id, q); msgs.append(f"✅ Completed: {q}")
                        if qu.get("memo_add"):
                             for m in qu["memo_add"]: game_system.add_memo(channel_id, m); msgs.append(f"📝 Memo: {m}")

                    # Abnormal Adaptation (If Enabled)
                    if domain_manager.get_abnormal_mode(channel_id):
                        trigger = u_res.get("AbnormalTrigger")
                        if trigger:
                             # Re-load participant data to be safe or reuse p_data if updated
                             # Assuming p_data is fresh enough or reused. 
                             # We updated p_data in PlayerUpdate block, so reuse p_data dict 
                             # but re-save is needed if we change it again.
                             p_data, p_msg = game_system.expose_to_abnormal(p_data, trigger)
                             if p_msg: msgs.append(p_msg)
                             domain_manager.save_participant_data(channel_id, uid, p_data)

                    if msgs: await message.channel.send(" | ".join(msgs))
                    
                except Exception as ue:
                     logging.warning(f"Extraction Error: {ue}")

        except Exception as e:
            logging.error(f"Generation Error: {e}", exc_info=True)
            await message.channel.send(f"⚠️ Error: {e}")

if __name__ == "__main__":
    if DISCORD_TOKEN and GEMINI_API_KEY:
        client_discord.run(DISCORD_TOKEN)
    else:
        print("MISSING API KEYS")
