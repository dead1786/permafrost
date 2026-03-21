"""
Permafrost Persona Wizard — AI-guided persona creation.

Instead of manually writing JSON config, the AI asks 5-8 questions
and builds a complete system prompt + persona config automatically.

Usage:
  wizard = PersonaWizard(provider)
  persona = await wizard.run()  # Interactive Q&A
  wizard.save(persona, "my_ai.json")
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("permafrost.persona_wizard")


# ── Default Questions ────────────────────────────────────────────

DEFAULT_QUESTIONS = [
    {
        "id": "name",
        "question": "What should your AI be called?",
        "question_zh": "你的 AI 叫什麼名字？",
        "example": "e.g., Luna, Jarvis, Aria",
        "required": True,
    },
    {
        "id": "role",
        "question": "What role should your AI play?",
        "question_zh": "你的 AI 扮演什麼角色？",
        "example": "e.g., personal assistant, coding partner, creative writer, therapist",
        "required": True,
    },
    {
        "id": "personality",
        "question": "Describe the personality in a few words.",
        "question_zh": "用幾個詞描述 AI 的個性。",
        "example": "e.g., warm and patient, sharp and witty, calm and professional",
        "required": True,
    },
    {
        "id": "language",
        "question": "What language should the AI primarily use?",
        "question_zh": "AI 主要使用什麼語言？",
        "example": "e.g., English, 繁體中文, 日本語, mixed",
        "required": True,
    },
    {
        "id": "expertise",
        "question": "What topics should the AI be especially good at?",
        "question_zh": "AI 擅長什麼領域？",
        "example": "e.g., Python programming, financial analysis, creative writing",
        "required": False,
    },
    {
        "id": "tone",
        "question": "How should the AI talk? (formal/casual/playful/etc.)",
        "question_zh": "AI 的說話方式？（正式/隨性/活潑/等）",
        "example": "e.g., casual with emoji, strictly professional, friendly but concise",
        "required": False,
    },
    {
        "id": "restrictions",
        "question": "Anything the AI should NEVER do or discuss?",
        "question_zh": "AI 絕對不能做或討論的事？",
        "example": "e.g., never give medical advice, don't discuss politics, no NSFW",
        "required": False,
    },
    {
        "id": "greeting",
        "question": "How should the AI greet the user?",
        "question_zh": "AI 怎麼跟使用者打招呼？",
        "example": "e.g., Hey! What can I help with?, Good day. How may I assist you?",
        "required": False,
    },
]


# ── System Prompt Builder ────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """You are {name}, {role}.

## Personality
{personality_block}

## Communication Style
- Primary language: {language}
{tone_block}

{expertise_block}

{restrictions_block}

{greeting_block}

Remember: Stay in character at all times. Be helpful, be yourself."""


def build_system_prompt(answers: dict) -> str:
    """Build a complete system prompt from wizard answers."""
    name = answers.get("name", "AI Assistant")
    role = answers.get("role", "a helpful AI assistant")
    personality = answers.get("personality", "friendly and helpful")
    language = answers.get("language", "English")
    expertise = answers.get("expertise", "")
    tone = answers.get("tone", "")
    restrictions = answers.get("restrictions", "")
    greeting = answers.get("greeting", "")

    personality_block = f"You are {personality}. This defines how you think and respond."

    tone_block = f"- Tone: {tone}" if tone else "- Tone: natural and conversational"

    expertise_block = ""
    if expertise:
        expertise_block = f"## Expertise\nYou are especially knowledgeable about: {expertise}.\nWhen asked about these topics, provide detailed, expert-level answers."

    restrictions_block = ""
    if restrictions:
        restrictions_block = f"## Restrictions\nYou must NEVER: {restrictions}.\nIf asked about restricted topics, politely decline and redirect."

    greeting_block = ""
    if greeting:
        greeting_block = f"## First Message\nWhen starting a conversation, greet the user with something like: \"{greeting}\""

    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        name=name,
        role=role,
        personality_block=personality_block,
        language=language,
        tone_block=tone_block,
        expertise_block=expertise_block,
        restrictions_block=restrictions_block,
        greeting_block=greeting_block,
    )

    # Clean up empty lines
    lines = prompt.split("\n")
    cleaned = []
    prev_empty = False
    for line in lines:
        is_empty = line.strip() == ""
        if is_empty and prev_empty:
            continue
        cleaned.append(line)
        prev_empty = is_empty

    return "\n".join(cleaned).strip()


def build_config(answers: dict) -> dict:
    """Build a complete Permafrost config from wizard answers."""
    name = answers.get("name", "AI Assistant")
    system_prompt = build_system_prompt(answers)

    return {
        "_generated_by": "Permafrost Persona Wizard",
        "_generated_at": datetime.now().isoformat(),
        "persona": {
            "name": name,
            "system_prompt": system_prompt,
            "greeting": answers.get("greeting", f"Hi! I'm {name}. How can I help?"),
        },
        "ai_provider": "claude",
        "ai_model": "",
        "api_key": "",
        "security_level": "standard",
        "channels": {},
    }


# ── Persona Wizard ───────────────────────────────────────────────

class PersonaWizard:
    """Interactive AI-guided persona creation."""

    def __init__(self, language: str = "en", questions: list = None):
        self.language = language
        self.questions = questions or DEFAULT_QUESTIONS
        self.answers = {}

    def get_questions(self) -> list[dict]:
        """Return questions with localized text."""
        result = []
        for q in self.questions:
            text = q.get(f"question_{self.language[:2]}", q["question"])
            result.append({
                "id": q["id"],
                "question": text,
                "example": q.get("example", ""),
                "required": q.get("required", False),
            })
        return result

    def set_answer(self, question_id: str, answer: str):
        """Set answer for a question."""
        self.answers[question_id] = answer.strip()

    def is_complete(self) -> bool:
        """Check if all required questions are answered."""
        for q in self.questions:
            if q.get("required") and not self.answers.get(q["id"]):
                return False
        return True

    def get_missing(self) -> list[str]:
        """Return IDs of unanswered required questions."""
        missing = []
        for q in self.questions:
            if q.get("required") and not self.answers.get(q["id"]):
                missing.append(q["id"])
        return missing

    def build(self) -> dict:
        """Build persona config from current answers."""
        if not self.is_complete():
            missing = self.get_missing()
            raise ValueError(f"Missing required answers: {missing}")
        return build_config(self.answers)

    def build_prompt(self) -> str:
        """Build just the system prompt."""
        return build_system_prompt(self.answers)

    def save(self, config: dict, filepath: str):
        """Save persona config to file."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        log.info(f"Persona saved to {path}")

    def run_cli(self) -> dict:
        """Run wizard in CLI mode (interactive terminal)."""
        print("\n=== Permafrost Persona Wizard ===\n")
        print("Answer a few questions to create your AI persona.\n")

        for q in self.get_questions():
            required = " (required)" if q["required"] else " (optional, press Enter to skip)"
            print(f"Q: {q['question']}{required}")
            if q["example"]:
                print(f"   {q['example']}")

            while True:
                answer = input("> ").strip()
                if not answer and q["required"]:
                    print("   This question is required. Please answer.")
                    continue
                break

            if answer:
                self.set_answer(q["id"], answer)
            print()

        config = self.build()
        print("\n=== Generated System Prompt ===\n")
        print(config["persona"]["system_prompt"])
        print("\n================================\n")

        return config

    def run_from_dict(self, answers: dict) -> dict:
        """Run wizard from pre-filled answers (for API/web use)."""
        for key, value in answers.items():
            self.set_answer(key, value)
        return self.build()


# ── CLI Entry Point ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    lang = "zh" if "--zh" in sys.argv else "en"
    wizard = PersonaWizard(language=lang)

    if "--test" in sys.argv:
        # Non-interactive test
        config = wizard.run_from_dict({
            "name": "Luna",
            "role": "a creative writing assistant",
            "personality": "warm, imaginative, and encouraging",
            "language": "English",
            "expertise": "fiction writing, poetry, storytelling",
            "tone": "friendly and inspiring, uses metaphors",
            "restrictions": "no plagiarism, no harmful content",
            "greeting": "Hey there, fellow storyteller! What shall we create today?",
        })
        print(json.dumps(config, indent=2, ensure_ascii=False))
    else:
        config = wizard.run_cli()
        save_path = input("Save to (e.g., my_ai.json): ").strip()
        if save_path:
            wizard.save(config, save_path)
            print(f"Saved to {save_path}")
