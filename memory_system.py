from google.genai import types
import logging

# =========================================================
# [프롬프트] NVC + 시스템 액션 (상태/관계 감지 추가)
# =========================================================
NVC_ANALYSIS_PROMPT = """
[Instruction]
Analyze the interaction. Internal thought process for AI Game Master.
Check provided **Rules/Lore** to validate actions.

1. **Observation**: What happened?
2. **Feeling**: AI Emotion.
3. **Need**: AI Value.
4. **Request**: What does AI want players to do?
5. **SystemAction**: Detect specific game mechanics from user input.
   - **None**: No mechanic.
   - **Rest**: Character rests.
   - **CollectTaxes**: Collect taxes.
   - **Construct**: Build facility. (Format: `Construct | Name: <Name> | Cost: <Gold> | Effect: <Desc>`)
   - **InventoryAction**: Item usage/gain. (Format: `InventoryAction | Type: <Add/Remove> | Item: <Name> | Count: <N>`)
   - **QuestAction**: Manage quests. (Format: `QuestAction | Type: <Add/Complete> | Content: <Text>`)
   - **MemoAction**: Manage memos. (Format: `MemoAction | Type: <Add/Remove> | Content: <Text>`)
   - **StatusAction**: Apply/Remove status effect.
     * Format: `StatusAction | Type: <Add/Remove> | Effect: <EffectName>`
   - **RelationAction**: Change relationship.
     * Format: `RelationAction | Target: <NPC/Faction> | Amount: <+/- Number>`

[Output Format]
Observation: ...
Feeling: ...
Need: ...
Request: ...
SystemAction: ...
"""

async def analyze_context_nvc(client, model_id, history_text, lore_text, rule_text=""):
    full_prompt = (
        f"{NVC_ANALYSIS_PROMPT}\n\n"
        f"[World Lore]\n{lore_text[:500]}...\n\n"
        f"[Game Rules]\n{rule_text[:1000]}...\n\n"
        f"[Recent Interaction]\n{history_text}"
    )

    try:
        response = client.models.generate_content(
            model=model_id,
            contents=full_prompt,
            config=types.GenerateContentConfig(temperature=0.1)
        )
        
        result_text = response.text
        parsed_data = parse_nvc_result(result_text)
        return parsed_data
        
    except Exception as e:
        logging.error(f"NVC Analysis Failed: {e}")
        return None

def parse_nvc_result(text):
    data = {"Observation": None, "Feeling": None, "Need": None, "Request": None, "SystemAction": "None"}
    
    current_key = None
    lines = text.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        if line.startswith("Observation:"):
            current_key = "Observation"
            data[current_key] = line.replace("Observation:", "").strip()
        elif line.startswith("Feeling:"):
            current_key = "Feeling"
            data[current_key] = line.replace("Feeling:", "").strip()
        elif line.startswith("Need:"):
            current_key = "Need"
            data[current_key] = line.replace("Need:", "").strip()
        elif line.startswith("Request:"):
            current_key = "Request"
            data[current_key] = line.replace("Request:", "").strip()
        elif line.startswith("SystemAction:"):
            current_key = "SystemAction"
            data[current_key] = line.replace("SystemAction:", "").strip()
        elif current_key:
            data[current_key] += " " + line
            
    return data