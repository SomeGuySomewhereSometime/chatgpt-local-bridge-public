"""Instruction composition preserves scope, ordering and filesystem policy."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import bridge
import mcp_server
from memory_store import MemoryService


class InstructionContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bridge-instructions-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.child = self.project / 'child'
        self.child.mkdir(parents=True)
        self.global_path = self.root / 'AGENTS.md'
        self.global_path.write_text('GLOBAL RULES FIXTURE')
        self.parent_path = self.project / 'AGENTS.md'
        self.parent_path.write_text('PROJECT RULES FIXTURE')
        self.child_path = self.child / 'AGENTS.md'
        self.child_path.write_text('CHILD RULES FIXTURE')
        for obj, key, value in ((bridge, 'WORKSPACE', self.root),
                                (bridge, 'BRIDGE_CONFIG', {}),
                                (bridge, 'memory', MemoryService({})),
                                (mcp_server, 'GLOBAL_RULES_PATH', self.global_path)):
            p = patch.object(obj, key, value)
            p.start()
            self.addCleanup(p.stop)

    def test_global_once_project_ancestors_preserved_memory_separate(self):
        context = mcp_server.get_session_context('project/child')
        self.assertEqual(context['global_instructions'].count('GLOBAL RULES FIXTURE'), 1)
        self.assertEqual([r['content'] for r in context['project_rules']],
                         ['PROJECT RULES FIXTURE', 'CHILD RULES FIXTURE'])
        self.assertEqual(context['memory'], {'status': 'disabled'})
        self.assertNotIn('PROJECT RULES FIXTURE', context['global_instructions'])

    def test_distinct_project_file_with_identical_text_is_not_dropped(self):
        self.parent_path.write_text('GLOBAL RULES FIXTURE')
        result = mcp_server.get_session_context('project')
        self.assertEqual(len(result['project_rules']), 1)
        self.assertEqual(result['project_rules'][0]['content'], 'GLOBAL RULES FIXTURE')

    def test_symlink_to_global_is_not_duplicated(self):
        self.parent_path.unlink()
        self.parent_path.symlink_to(self.global_path)
        result = mcp_server.get_session_context('project/child')
        self.assertEqual([r['content'] for r in result['project_rules']], ['CHILD RULES FIXTURE'])

    def test_missing_global_does_not_drop_project_rules(self):
        self.global_path.unlink()
        result = mcp_server.get_session_context('project/child')
        self.assertNotIn('Local global instructions:', result['global_instructions'])
        self.assertEqual(len(result['project_rules']), 2)

    def test_project_rules_still_obey_current_path_policy(self):
        with patch.object(bridge, 'BRIDGE_CONFIG', {'filesystem': {'denied_paths': [str(self.parent_path)]}}):
            with self.assertRaisesRegex(mcp_server.ToolError, 'DENIED'):
                mcp_server.get_session_context('project/child')
        with self.assertRaises(HTTPException):
            mcp_server.get_session_context('/etc')

    def test_global_outside_workspace_and_workspace_rules_are_both_retained(self):
        with patch.object(bridge, 'WORKSPACE', self.project):
            result = mcp_server.get_session_context('child')
        self.assertIn('GLOBAL RULES FIXTURE', result['global_instructions'])
        self.assertEqual([r['content'] for r in result['project_rules']],
                         ['PROJECT RULES FIXTURE', 'CHILD RULES FIXTURE'])

    def test_external_bridge_document_is_composed_and_reloaded(self):
        document = self.root / 'BRIDGE_INSTRUCTIONS.md'
        document.write_text('BRIDGE PROCEDURES FIXTURE')
        with patch.object(mcp_server, 'BRIDGE_RULES_PATH', document):
            context = mcp_server.get_session_context('project')
            text = context['global_instructions']
            self.assertEqual(text.count('BRIDGE PROCEDURES FIXTURE'), 1)
            self.assertEqual(text.count('GLOBAL RULES FIXTURE'), 1)
            document.write_text('UPDATED PROCEDURES FIXTURE')
            text = mcp_server.get_session_context('project')['global_instructions']
            self.assertIn('UPDATED PROCEDURES FIXTURE', text)
            self.assertNotIn('BRIDGE PROCEDURES FIXTURE', text)

    def test_missing_bridge_document_does_not_silently_drop_procedures(self):
        with patch.object(mcp_server, 'BRIDGE_RULES_PATH', self.root / 'missing.md'):
            with self.assertRaises(FileNotFoundError):
                mcp_server.effective_instructions()
