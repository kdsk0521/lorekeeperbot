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
    import session_manager
    import memory_system
    import game_character
    import game_world
    import npc_manager

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

            # [NEW] Whitelist Check (Ignore if bot inactive)
            if not domain_manager.get_bot_active(channel_id):
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

async def process_time_flow(channel_id: str, time_flow: Dict) -> Optional[str]:
    """
    시간 흐름을 처리하고 매 5틱마다 둠 수치를 계산하며, 필요시 시간대를 진행합니다.
    """
    if not time_flow:
        return None
    
    duration = time_flow.get("duration", "instant")
    ticks = time_flow.get("ticks", 0)
    explicit_hours = time_flow.get("explicit_hours")
    
    messages = []

    # 1. 명시적 시간 경과 처리 (시간당 5틱으로 계산)
    if duration == "explicit" and explicit_hours:
        ticks = int(explicit_hours * 5)
    
    if ticks <= 0:
        return None
    
    # 현재 틱 카운터 가져오기
    world = domain_manager.get_world_state(channel_id)
    current_ticks = world.get("time_ticks", 0)
    new_ticks_total = current_ticks + ticks

    # 2. 둠 체크 (매 5틱 경계선 넘을 때마다 실행)
    old_doom_period = current_ticks // 5
    new_doom_period = new_ticks_total // 5
    
    if new_doom_period > old_doom_period:
        for _ in range(new_doom_period - old_doom_period):
            # 정산은 하되, 메시지는 수집하지 않음 (채팅창 정돈)
            game_world.process_doom_tick(channel_id)
    
    # 3. 시간대 진행 (매 24틱마다)
    if new_ticks_total >= config.TIME_TICKS_PER_SLOT:
        slots_to_advance = new_ticks_total // config.TIME_TICKS_PER_SLOT
        remaining_ticks = new_ticks_total % config.TIME_TICKS_PER_SLOT
        
        world["time_ticks"] = remaining_ticks
        domain_manager.update_world_state(channel_id, world)
        
        for _ in range(slots_to_advance):
            msg = game_system.advance_time(channel_id)
            if msg:
                messages.append(msg)
    else:
        # 틱만 누적
        world["time_ticks"] = new_ticks_total
        domain_manager.update_world_state(channel_id, world)
        
    return "\n".join(messages) if messages else None

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
            
            # [NEW] Passives Context for Cognition
            uid = str(message.author.id)
            user_data = domain_manager.get_participant_data(channel_id, uid)
            passives_txt = game_character.get_passives_for_context(user_data)
            
            # History for Analysis (Smart Context Window: Min 1500 chars)
            # Re-implemented as per request for better memory retention
            all_hist = domain_data.get('history', [])
            target_len = 1500
            # Start with default lines (20) defined in fermentation or config
            default_lines = getattr(fermentation, "RECENT_HISTORY_FOR_ANALYSIS", 20)
            slice_idx = -default_lines
            
            while True:
                # Ensure slice_idx doesn't exceed bounds
                if abs(slice_idx) > len(all_hist): 
                     slice_idx = -len(all_hist)
                
                subset = all_hist[slice_idx:]
                # Construct temp text to check length
                hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in subset])
                
                # Check exit conditions: Met target length OR Hit max history OR Hit safety cap (e.g. 60 lines)
                if len(hist_text) >= target_len or abs(slice_idx) >= len(all_hist) or abs(slice_idx) >= 60:
                    break
                
                slice_idx -= 5 # Expand window backwards
            
            # Final Text Construction
            history = all_hist[slice_idx:]
            hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history]) + f"\nUser: {action_text}"
            
            active_quests = game_system.get_quest_board(channel_id).get("active", [])
            quest_txt = " | ".join(active_quests) if active_quests else "None"
            notebook_txt = game_system.get_notebook_text(channel_id) # [V5.1]
            
            uid = str(message.author.id)
            p_data = domain_manager.get_participant_data(channel_id, uid)
            p_ctx = game_system.get_status_summary(p_data) if p_data else "" 
            
            # 3. COGNITION: ANALYSIS (Theoria)
            # [NEW] Inject NPC Time Hints for better inference
            npc_hints = game_system.get_npc_time_progression(channel_id)
            if npc_hints:
                rule_txt += "\n\n### [NPC ACTIVITY HINTS (Time-based)]\n" + "\n".join(npc_hints)

            # [NEW] Retrieve Existing NPC Attitudes
            existing_attitudes = domain_manager.get_npc_attitudes(channel_id)

            # =========================================================
            # STEP 1: FLASH ANALYSIS (Observe & Select)
            # =========================================================
            # Uses Flash for 100k context reading. Cheap.
            flash_res = await cognition.analyze_context_flash(
                client_genai, MODEL_ID_FLASH, hist_text, lore_txt, rule_txt, quest_txt, 
                notebook=notebook_txt, player_context=p_ctx, 
                existing_npc_attitudes=existing_attitudes
            )
            
            # Extract Flash Outputs
            user_intent = flash_res.get("UserIntent", "Unknown")
            observation = flash_res.get("Observation", "No observation")
            relevant_context = flash_res.get("RelevantContext", [])
            
            # =========================================================
            # STEP 2: PRO JUDGMENT (Judge Actions)
            # =========================================================
            # Uses Pro for Logic/Dice on SELECTED context. High Quality.
            # Unconditional execution as per user request ("Charm of everyday judgment")
            pro_res = await cognition.judge_action_pro(
                client_genai, MODEL_ID, 
                user_intent, observation, relevant_context, 
                history_tail=hist_text[-500:] # Pass recent chat for context flow
            )
            
            # Merge Results into Unified 'nvc_res' for compatibility
            nvc_res = {**flash_res, **pro_res}
            
            # [NEW] V2.5 / Implementation Request #1 v3 Compliant
            pos_data = nvc_res.get("Position", {})
            eff_data = nvc_res.get("Effect", {})
            aspects = nvc_res.get("Aspects", [])
            pos_val = pos_data.get("value", "N/A")
            eff_val = eff_data.get("value", "N/A")
            
            logging.info(f"[IR#1 v3] Position: {pos_val} | Effect: {eff_val} | Intent: {user_intent}")
            logging.info(f"[IR#1 v3] Action: {nvc_res.get('ActionJudgment', {}).get('action')} | GM Move: {nvc_res.get('GMMove', {}).get('type', 'None')}")
            
            # Updates from Analysis
            if nvc_res.get("CurrentLocation"): domain_manager.set_current_location(channel_id, nvc_res["CurrentLocation"])
            if nvc_res.get("LocationRisk"): domain_manager.set_current_risk(channel_id, nvc_res["LocationRisk"])
            
            # [NEW] Check & Update NPC Attitudes
            new_attitudes = nvc_res.get("NPCAttitudes")
            if new_attitudes:
                for n_name, n_data in new_attitudes.items():
                    # [V5 Restoration] Auto-register Session NPCs
                    existing_npc = npc_manager.get_npc(channel_id, n_name)
                    if not existing_npc:
                        # Create new Session NPC
                        npc_manager.update_npc(channel_id, n_name, {
                            "source": "session", 
                            "desc": "Auto-detected by AI",
                            "status": "active"
                        })
                        logging.info(f"Auto-created Session NPC: {n_name}")

                    # Update Attitude
                    domain_manager.update_npc_attitude(channel_id, n_name, n_data.get("attitude", "neutral"), n_data.get("reason", ""))
                
                # Refresh existing_attitudes for use in prompt
                existing_attitudes = domain_manager.get_npc_attitudes(channel_id)
            
            # [NEW] Automatic Time Flow Processing
            time_flow = nvc_res.get("TimeFlow", {})
            time_msg = await process_time_flow(channel_id, time_flow)
            
            if time_msg:
                # Send time update message to channel
                await message.channel.send(time_msg)
                
                # Refresh World Context immediately if time changed
                world_ctx = game_system.get_world_context(channel_id)
                re_npc_hints = game_system.get_npc_time_progression(channel_id)
                if re_npc_hints:
                     rule_txt += "\n\n### [UPDATED NPC ACTIVITY HINTS]\n" + "\n".join(re_npc_hints)


            # =========================================================
            # [NEW] V6 ANOMALY SYSTEM TRIGGER
            # =========================================================
            w_state = domain_manager.get_world_state(channel_id)
            c_doom = w_state.get("doom", 0)
            
            # Check SceneType (Skip if Summary/TimeSkip or Intimate)
            scene_type = nvc_res.get("SceneType", "normal")
            
            # Check Trigger (Every Turn, unless Summary/Intimate)
            if scene_type not in ["summary", "intimate"] and game_world.should_trigger_anomaly(c_doom):
                logging.info(f"[Anomaly] Triggered at Doom {c_doom}")
                
                # Context for Generation
                anom_lore = domain_manager.get_event_lore_summary(channel_id) or domain_manager.get_lore(channel_id)[:1000]
                anom_loc = domain_manager.get_current_location(channel_id)
                anom_genres = domain_data.get("active_genres", ["Unknown"])
                
                # Generate Event (Async, Flash Model)
                anom_evt = await game_world.generate_anomaly_event(
                    client_genai, channel_id, c_doom, anom_lore, anom_loc, anom_genres,
                    model_id=MODEL_ID_FLASH
                )
                
                if anom_evt:
                    # 1. Announce Event
                    evt_msg = (
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚡ **이변 발생: [{anom_evt.get('tag', 'Unknown')}]**\n"
                        f"{anom_evt.get('description', '...')}\n"
                        f"💡 *{anom_evt.get('effect_hint', '대처하십시오.')}*\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                    await message.channel.send(evt_msg)
                    
                    # 2. Tension Release (Doom -3)
                    game_world.change_doom(channel_id, config.ANOMALY_DOOM_COST)
                    
                    # 3. Adaptation Checks (For all active participants)
                    participants = domain_data.get("participants", {})
                    adapt_results = []
                    
                    for uid, p_data in participants.items():
                        if p_data.get("status") == "active":
                            # Perform Roll
                            p_data, adapt_msg = game_character.check_adaptation_roll(
                                p_data, 
                                tag=anom_evt.get('tag', 'Unknown'),
                                category=anom_evt.get('category') # Use category if AI world gen provided it (optional)
                            )
                            # Save User Data
                            domain_manager.save_participant_data(channel_id, uid, p_data)
                            
                            # Format Message
                            user_name = p_data.get("mask") or p_data.get("name", "Unknown")
                            adapt_results.append(f"**{user_name}**: {adapt_msg.strip()}")

                    # Send Bulk Result
                    if adapt_results:
                        tag = anom_evt.get('tag', 'Unknown')
                        await message.channel.send(f"━━━━━━━━━━━━━━━━━━━━\n🎲 **적응 판정 결과: [{tag}]**\n" + "\n".join(adapt_results) + "\n━━━━━━━━━━━━━━━━━━━━")

            # [NEW] GM Judgment System Integration
            judgment_context = ""
            action_judgment = nvc_res.get("ActionJudgment")
            if action_judgment and isinstance(action_judgment, dict):
                 try:
                     # Calculate Roll
                     # Ensure fields exist
                     act = action_judgment.get("action", "Unknown Action")
                     diff = action_judgment.get("difficulty", "normal")
                     reason = action_judgment.get("difficulty_reason", "")
                     mods = action_judgment.get("modifiers", [])
                     
                     # [NEW] Pass Bonus Dice
                     b_dice = p_data.get("temp_bonus_dice", 0) if p_data else 0
                     judgment_data = cognition.build_action_judgment_with_roll(act, diff, reason, mods, bonus_dice=b_dice)
                     
                     # Reset Bonus Dice after use
                     if b_dice > 0 and p_data:
                         p_data["temp_bonus_dice"] = 0
                         domain_manager.save_participant_data(channel_id, uid, p_data)

                     # [NEW] Inject GM Move info from GMMove field
                     gm_m = nvc_res.get("GMMove", {})
                     judgment_data["potential_gm_move"] = gm_m.get("type")
                     judgment_data["gm_move_description"] = gm_m.get("description")
                     
                     # [Safety Logic] Downgrade Critical Failure in Intimate Scenes
                     if scene_type == "intimate" and judgment_data.get("result") == "critical_failure":
                         judgment_data["result"] = "failure"
                         judgment_data["final_roll"] = max(2, judgment_data["final_roll"]) # Min 2 to avoid crit range visual confusion if relevant
                         logging.info("Downgraded Critical Failure due to Intimate Scene.")

                     # Build Log & Context
                     roll_log = cognition.build_judgment_context_with_roll(judgment_data)
                     
                     # [V6.2] Doom Penalty/Bonus & Action Tax (Silent Update)
                     res_key = judgment_data.get("result")
                     if res_key == "failure":
                         game_world.change_doom(channel_id, 1)
                     elif res_key == "critical_failure":
                         game_world.change_doom(channel_id, 4)
                     elif res_key == "critical_success":
                         # Item 1: Critical Success provides relief
                         game_world.change_doom(channel_id, -1)
                     elif res_key in ["success", "partial"]:
                         # Action Tax: Every move increases entropy
                         # Item 3: Buffer tax in Rest/Intimate scenes
                         if scene_type not in ["rest", "intimate"]:
                             game_world.change_doom(channel_id, config.DOOM_ACTION_TAX)

                     # We only need the string context for the prompt, but the user sees the log immediately
                     judgment_context = roll_log # Synced
                     
                     await message.channel.send(roll_log)
                 except Exception as e:
                     logging.error(f"Failed to process/send roll log: {e}")

            # System Action (Quest/Memo from logic)
            sys_action = nvc_res.get("SystemAction")
            if sys_action:
                 auto_msg = await command_handler.process_ai_system_action(channel_id, sys_action)
                 if auto_msg: await message.channel.send(f"🤖 {auto_msg}")
            
            # [V6 Refactor] Use PromptBuilder for consistent structure & caching
            builder = persona.PromptBuilder()
            
            # 1. Gather Basic Parameters
            active_genres = domain_manager.get_active_genres(channel_id)
            custom_tone = domain_manager.get_custom_tone(channel_id)
            p_name = p_data.get("mask", "Unknown") if p_data else "Unknown"
            p_desc = p_data.get("ai_memory", {}).get("appearance", "") if p_data else ""
            
            # 2. Build Analysis Summary (NVC)
            temporal = nvc_res.get("TemporalOrientation", {})
            suggested_focus = temporal.get("suggested_focus", "")
            
            # [NEW] IR#1 v3 Segment
            pos_data = nvc_res.get("Position", {})
            eff_data = nvc_res.get("Effect", {})
            aspects = nvc_res.get("Aspects", [])
            gm_m = nvc_res.get("GMMove", {})
            off_hint = nvc_res.get("OffscreenHint")
            
            nvc_summary = (
                f"### COGNITIVE ANALYSIS (IR#1 v3)\n"
                f"- **Observation**: {nvc_res.get('Observation')}\n"
                f"- **User Intent**: {nvc_res.get('UserIntent')}\n"
                f"- **Position (Risk/Stakes)**: {pos_data.get('value', 'N/A')} ({pos_data.get('reason', '')})\n"
                f"- **Effect (Potential)**: {eff_data.get('value', 'N/A')} ({eff_data.get('reason', '')})\n"
                f"- **Aspects**: {', '.join(aspects) if aspects else 'None'}\n"
            )
            
            if off_hint:
                nvc_summary += f"\n- **Offscreen Hint**: {off_hint}\n"

            if gm_m:
                 nvc_summary += f"\n- **Proposed GM Move**: {gm_m.get('type')} ({gm_m.get('description', '')})\n"
            
            nvc_summary += f"\nFocus: {suggested_focus}"

            if existing_attitudes:
                att_lines = [f"- {n}: {d['attitude']} ({d['reason']})" for n, d in existing_attitudes.items()]
                nvc_summary += f"\n\n### [NPC ATTITUDES TOWARD PC]\n" + "\n".join(att_lines)
            
            if judgment_context:
                nvc_summary += f"\n\n{judgment_context}"

            # 3. Offscreen Context
            offscreen_npcs = temporal.get("offscreen_npcs", [])
            offscreen_context = ""
            if offscreen_npcs:
                offscreen_context = (
                    "### [OFFSCREEN WORLD]\n"
                    "While this scene unfolds, elsewhere:\n"
                    + "\n".join([f"- {npc}" for npc in offscreen_npcs])
                    + "\n"
                    "**Instruction:** Naturally weave 1-2 of these background events into the narrative. "
                    "Show the world continuing without the PC (sounds, distant voices, NPCs passing by, etc.)\n"
                )

            # 4. Active Threads Context
            active_threads = temporal.get("active_threads", [])
            threads_context = ""
            if active_threads:
                threads_context = (
                    "### [ACTIVE PLOT THREADS]\n"
                    + "\n".join([f"- {thread}" for thread in active_threads])
                    + "\n"
                )

            # 5. Populate Builder
            fermented_summaries = [e["summary"] for e in domain_data.get("fermented_history", []) if e.get("summary")]
            fermented_summary_text = "\n---\n".join(fermented_summaries)
            
            builder.set_genres(active_genres)
            builder.set_custom_tone(custom_tone)
            builder.set_scene_type(scene_type)
            builder.set_lore(lore_txt, rule_txt)
            builder.set_player_info(p_name, p_desc)
            builder.set_roles(character_descriptions="")
            builder.set_fermented(fermented_summary_text, domain_data.get("deep_memory", ""))
            
            # Dynamic sections
            dynamic_world_state = f"{world_ctx}\n\n"
            if threads_context: dynamic_world_state += f"{threads_context}\n"
            if offscreen_context: dynamic_world_state += f"{offscreen_context}\n"
            
            builder.set_current_context(
                recent_chat="", # History injected via Session Adapter
                world_state=dynamic_world_state,
                nvc_analysis=nvc_summary
            )
            
            builder.set_cognition_data(nvc_summary)
            pc_reminder = f"### CRITICAL WARNING: DO NOT WRITE FOR [{p_name}]\n{p_name} is the PLAYER. You must NOT generate their dialogue or actions."
            builder.set_user_message(material=action_text, ooc_content=pc_reminder)
            
            # 6. Build Final Prompt
            full_prompt = builder.build_dynamic_prompt()

            # 4. GENERATION (Persona)
            # (Parameters already gathered in step 1-2 above)
            
            session = persona.create_risu_style_session(
                client_genai, MODEL_ID, lore_txt, rule_txt, active_genres, custom_tone,
                domain_data.get("deep_memory", ""), fermented_summary=fermented_summary_text,
                character_descriptions="", scene_type=scene_type,
                player_name=p_name, player_desc=p_desc,
                nvc_summary=nvc_summary
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
                # Request 3: Post-Response Impersonation Filter
                response, violations = persona.filter_pc_impersonation(response, [p_name])
                if violations:
                     warning_msg = "\n".join(violations)
                     # Warn user but keep text (safer than auto-delete for now)
                     await message.channel.send(f"{warning_msg}")

                await bot_utils.send_long_message(message.channel, response)
                domain_manager.append_history(channel_id, "User", action_text)
                domain_manager.append_history(channel_id, "Char", response)
                try:
                    # Gather data
                    # inv/gold removed [V5.3 Refactor]
                    status = p_data.get("status_effects", []) if p_data else []
                    
                    mem = domain_manager.get_ai_memory(channel_id, uid)
                    rels = mem.get("relationships", {})
                    passives = mem.get("passives", [])
                    
                    # Hint Generation Heuristics
                    extraction_hints = {
                        "physical": any(kw in response for kw in ['아이템','골드','금화','은화','돈','획득','주웠','얻었','잃었','버렸','사용','먹었','마셨','부상','치료','회복','피해']),
                        "social": (list(domain_manager.get_npcs(channel_id).keys()) and any(n in response for n in domain_manager.get_npcs(channel_id).keys())) or ('"' in response or '「' in response),
                        "narrative": any(kw in response for kw in ['처음으로','마침내','성공','실패','죽','살','마법','괴물','이상한','기이한']) or bool(nvc_res.get("AbnormalElements")),
                        "quest": any(kw in response for kw in ['퀘스트','임무','목표','의뢰','부탁','완료','달성','단서','정보','비밀'])
                    }

                    # Phase 1: Immediate Notebook/Physical Update
                    if extraction_hints["physical"]:
                        phys_res = await cognition._extract_physical(
                            client_genai, MODEL_ID_FLASH, action_text, response, 
                            notebook_txt, status 
                        )
                        if phys_res:
                            msgs = []
                            changed = False
                            
                            # [V5.1] Notebook Update
                            nb_upd = phys_res.get("notebook_update")
                            if nb_upd and nb_upd != notebook_txt:
                                game_system.update_notebook_text(channel_id, nb_upd)
                                msgs.append("📔 노트북 기록됨")
                            
                            # gold_change removed [V5.3 Refactor] - relying on Notebook text
                            
                            if msgs: await message.channel.send(" | ".join(msgs))
                    
                    # Phase 2: Background Extraction (Async)
                    async def background_update():
                        try:
                            bg_hints = {k: v for k, v in extraction_hints.items() if k != "physical" and v}
                            if not bg_hints: return
                            
                            # [V5.2] Context Aware Extraction
                            p_desc = p_data.get("desc", "") if p_data else ""
                            
                            bg_res = await cognition.extract_all_updates(
                                client_genai, MODEL_ID_FLASH, action_text, response,
                                notebook=game_system.get_notebook_text(channel_id), 
                                current_status=status,
                                current_relationships=rels,
                                current_passives=passives,
                                current_quests=game_system.get_active_quests(channel_id),
                                lore_npc_names=list(domain_manager.get_npcs(channel_id).keys()),
                                fermented_context=fermented_summary_text,
                                extraction_hints=bg_hints,
                                player_context=p_desc # Info for subjective judgment
                            )
                            
                            bg_msgs = []
                            # Memory
                            pmu = bg_res.get("PlayerMemoryUpdate")
                            if pmu:
                                if pmu.get("relationships"):
                                    domain_manager.update_ai_memory(channel_id, uid, {"relationships": pmu["relationships"]})
                                    bg_msgs.append("💞 관계도")
                                if pmu.get("passives"):
                                    for p in pmu["passives"]: domain_manager.add_to_ai_memory_list(channel_id, uid, "passives", p)
                                    bg_msgs.append(f"🏆 패시브: {len(pmu['passives'])}개")
                            
                            # Quests
                            qu = bg_res.get("QuestUpdate")
                            if qu:
                                if qu.get("quest_add"): 
                                    for q in qu["quest_add"]: game_system.add_quest(channel_id, q); bg_msgs.append(f"🔥 New: {q}")
                                if qu.get("quest_complete"):
                                    for q in qu["quest_complete"]: game_system.complete_quest(channel_id, q); bg_msgs.append(f"✅ Done: {q}")

                            # Abnormal
                            if domain_manager.get_abnormal_mode(channel_id) and bg_res.get("AbnormalTrigger"):
                                trigger_name = bg_res["AbnormalTrigger"]
                                trigger_cat = bg_res.get("AbnormalCategory")
                                
                                fp_data = domain_manager.get_participant_data(channel_id, uid) # Fresh load
                                fp_data, p_msg = game_system.expose_to_abnormal(fp_data, trigger_name, category=trigger_cat)
                                if p_msg: 
                                    bg_msgs.append(p_msg)
                                    # [V6.2] Item 6: Detect Mastery Signal and reduce Doom
                                    if "마스터리 달성" in p_msg:
                                        game_world.change_doom(channel_id, -5)
                                domain_manager.save_participant_data(channel_id, uid, fp_data)

                            if bg_msgs: await message.channel.send("📋 " + " | ".join(bg_msgs))
                        except Exception as e:
                            logging.error(f"Background Extraction Error: {e}")

                    asyncio.create_task(background_update())

                except Exception as ue:
                    logging.warning(f"Extraction Error: {ue}")

                    # Abnormal Adaptation (Fallback)
                    if domain_manager.get_abnormal_mode(channel_id):
                        trigger = nvc_res.get("AbnormalTrigger") 
                        category = nvc_res.get("AbnormalCategory")
                        if trigger:
                             p_data, p_msg = game_system.expose_to_abnormal(p_data, trigger, category=category)
                             if p_msg: msgs.append(p_msg)
                             domain_manager.save_participant_data(channel_id, uid, p_data)

                    if msgs: await message.channel.send(" | ".join(msgs))

        except Exception as e:
            logging.error(f"Generation Error: {e}", exc_info=True)
            await message.channel.send(f"⚠️ Error: {e}")

if __name__ == "__main__":
    if DISCORD_TOKEN and GEMINI_API_KEY:
        client_discord.run(DISCORD_TOKEN)
    else:
        print("MISSING API KEYS")
