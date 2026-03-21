"""
Tests for smart/persona_wizard.py — AI-guided persona creation.
"""

import json
import os
import unittest

from conftest import make_temp_dir, cleanup_temp_dir, read_json
from smart.persona_wizard import (
    PersonaWizard, build_system_prompt, build_config, DEFAULT_QUESTIONS,
)


class TestDefaultQuestions(unittest.TestCase):
    """Test question definitions."""

    def test_has_required_questions(self):
        required = [q for q in DEFAULT_QUESTIONS if q.get("required")]
        self.assertGreaterEqual(len(required), 3)

    def test_all_questions_have_id(self):
        for q in DEFAULT_QUESTIONS:
            self.assertIn("id", q)
            self.assertTrue(len(q["id"]) > 0)

    def test_all_have_english_question(self):
        for q in DEFAULT_QUESTIONS:
            self.assertIn("question", q)

    def test_bilingual_support(self):
        zh_count = sum(1 for q in DEFAULT_QUESTIONS if "question_zh" in q)
        self.assertGreater(zh_count, 0)


class TestBuildSystemPrompt(unittest.TestCase):
    """Test system prompt generation."""

    def test_basic_prompt(self):
        answers = {
            "name": "Luna",
            "role": "a creative assistant",
            "personality": "warm and friendly",
            "language": "English",
        }
        prompt = build_system_prompt(answers)
        self.assertIn("Luna", prompt)
        self.assertIn("creative assistant", prompt)
        self.assertIn("warm and friendly", prompt)
        self.assertIn("English", prompt)

    def test_expertise_included(self):
        answers = {
            "name": "Test",
            "role": "assistant",
            "personality": "smart",
            "language": "English",
            "expertise": "Python and machine learning",
        }
        prompt = build_system_prompt(answers)
        self.assertIn("Python and machine learning", prompt)
        self.assertIn("Expertise", prompt)

    def test_restrictions_included(self):
        answers = {
            "name": "Test",
            "role": "assistant",
            "personality": "smart",
            "language": "English",
            "restrictions": "no medical advice",
        }
        prompt = build_system_prompt(answers)
        self.assertIn("no medical advice", prompt)
        self.assertIn("NEVER", prompt)

    def test_greeting_included(self):
        answers = {
            "name": "Test",
            "role": "assistant",
            "personality": "smart",
            "language": "English",
            "greeting": "Hey there!",
        }
        prompt = build_system_prompt(answers)
        self.assertIn("Hey there!", prompt)

    def test_tone_included(self):
        answers = {
            "name": "Test",
            "role": "assistant",
            "personality": "smart",
            "language": "English",
            "tone": "casual with emoji",
        }
        prompt = build_system_prompt(answers)
        self.assertIn("casual with emoji", prompt)

    def test_defaults_for_missing_fields(self):
        prompt = build_system_prompt({})
        self.assertIn("AI Assistant", prompt)

    def test_no_excessive_blank_lines(self):
        prompt = build_system_prompt({"name": "Test", "role": "bot", "personality": "nice", "language": "en"})
        lines = prompt.split("\n")
        consecutive_empty = 0
        for line in lines:
            if line.strip() == "":
                consecutive_empty += 1
                self.assertLessEqual(consecutive_empty, 1, "Should not have consecutive empty lines")
            else:
                consecutive_empty = 0


class TestBuildConfig(unittest.TestCase):
    """Test full config generation."""

    def test_config_structure(self):
        answers = {
            "name": "Luna",
            "role": "assistant",
            "personality": "friendly",
            "language": "English",
        }
        config = build_config(answers)
        self.assertIn("persona", config)
        self.assertIn("ai_provider", config)
        self.assertIn("security_level", config)
        self.assertEqual(config["persona"]["name"], "Luna")

    def test_config_has_system_prompt(self):
        answers = {"name": "Test", "role": "bot", "personality": "cool", "language": "en"}
        config = build_config(answers)
        self.assertIn("system_prompt", config["persona"])
        self.assertTrue(len(config["persona"]["system_prompt"]) > 50)

    def test_config_metadata(self):
        config = build_config({"name": "X", "role": "y", "personality": "z", "language": "en"})
        self.assertEqual(config["_generated_by"], "Permafrost Persona Wizard")
        self.assertIn("_generated_at", config)


class TestPersonaWizard(unittest.TestCase):
    """Test PersonaWizard class."""

    def test_get_questions_english(self):
        wiz = PersonaWizard(language="en")
        questions = wiz.get_questions()
        self.assertEqual(len(questions), len(DEFAULT_QUESTIONS))
        for q in questions:
            self.assertIn("id", q)
            self.assertIn("question", q)

    def test_get_questions_chinese(self):
        wiz = PersonaWizard(language="zh")
        questions = wiz.get_questions()
        # Chinese questions should use question_zh
        has_chinese = any("?" not in q["question"] for q in questions)
        self.assertTrue(has_chinese)

    def test_set_answer(self):
        wiz = PersonaWizard()
        wiz.set_answer("name", "  Luna  ")
        self.assertEqual(wiz.answers["name"], "Luna")

    def test_is_complete_false(self):
        wiz = PersonaWizard()
        self.assertFalse(wiz.is_complete())

    def test_is_complete_true(self):
        wiz = PersonaWizard()
        for q in DEFAULT_QUESTIONS:
            if q.get("required"):
                wiz.set_answer(q["id"], "test")
        self.assertTrue(wiz.is_complete())

    def test_get_missing(self):
        wiz = PersonaWizard()
        wiz.set_answer("name", "Luna")
        missing = wiz.get_missing()
        self.assertNotIn("name", missing)
        self.assertIn("role", missing)

    def test_build_raises_if_incomplete(self):
        wiz = PersonaWizard()
        with self.assertRaises(ValueError):
            wiz.build()

    def test_build_success(self):
        wiz = PersonaWizard()
        wiz.set_answer("name", "Luna")
        wiz.set_answer("role", "assistant")
        wiz.set_answer("personality", "friendly")
        wiz.set_answer("language", "English")
        config = wiz.build()
        self.assertEqual(config["persona"]["name"], "Luna")

    def test_build_prompt(self):
        wiz = PersonaWizard()
        wiz.set_answer("name", "Luna")
        prompt = wiz.build_prompt()
        self.assertIn("Luna", prompt)

    def test_run_from_dict(self):
        wiz = PersonaWizard()
        config = wiz.run_from_dict({
            "name": "Aria",
            "role": "writing tutor",
            "personality": "encouraging",
            "language": "English",
        })
        self.assertEqual(config["persona"]["name"], "Aria")


class TestPersonaWizardSave(unittest.TestCase):
    """Test saving persona to file."""

    def setUp(self):
        self.tmp = make_temp_dir()

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_save_creates_file(self):
        wiz = PersonaWizard()
        config = wiz.run_from_dict({
            "name": "Test",
            "role": "bot",
            "personality": "nice",
            "language": "en",
        })
        path = os.path.join(self.tmp, "test-persona.json")
        wiz.save(config, path)
        self.assertTrue(os.path.exists(path))

    def test_save_valid_json(self):
        wiz = PersonaWizard()
        config = wiz.run_from_dict({
            "name": "Test",
            "role": "bot",
            "personality": "nice",
            "language": "en",
        })
        path = os.path.join(self.tmp, "test.json")
        wiz.save(config, path)
        loaded = read_json(path)
        self.assertEqual(loaded["persona"]["name"], "Test")

    def test_save_creates_parent_dirs(self):
        wiz = PersonaWizard()
        config = wiz.run_from_dict({
            "name": "Test",
            "role": "bot",
            "personality": "nice",
            "language": "en",
        })
        path = os.path.join(self.tmp, "sub", "dir", "test.json")
        wiz.save(config, path)
        self.assertTrue(os.path.exists(path))

    def test_save_unicode(self):
        wiz = PersonaWizard()
        config = wiz.run_from_dict({
            "name": "小月",
            "role": "中文助手",
            "personality": "溫暖",
            "language": "繁體中文",
        })
        path = os.path.join(self.tmp, "zh.json")
        wiz.save(config, path)
        loaded = read_json(path)
        self.assertEqual(loaded["persona"]["name"], "小月")


if __name__ == "__main__":
    unittest.main()
