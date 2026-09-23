"""Persistent editor switching and unsaved-change protection without opening Tk."""
import copy
import unittest
from unittest.mock import Mock, patch

from bike_stunt.library_studio.window import ObstacleEditorWindow, ObstacleLibraryWindow
from bike_stunt.obstacle_library import load_catalog


class EditorSessionTests(unittest.TestCase):
    def setUp(self):
        self.family = load_catalog()['flat']
        self.variant = self.family['variants'][0]
        self.editor = ObstacleEditorWindow.__new__(ObstacleEditorWindow)
        for name in ('window', 'shape_choice', 'shape_mode', 'mode', 'variant_label', 'status', 'fit'):
            setattr(self.editor, name, Mock())
        self.editor.fields = {}
        self.editor.selected = None
        self.editor.dirty = False
        self.editor.load_variant(self.family, self.variant, confirm=False)

    def test_cancel_switch_preserves_working_copy(self):
        self.editor.dirty = True
        original = self.editor.module
        with patch('tkinter.messagebox.askyesnocancel', return_value=None):
            self.assertFalse(self.editor.load_variant(self.family, self.variant))
        self.assertIs(self.editor.module, original)
        self.assertTrue(self.editor.dirty)

    def test_discard_switch_reuses_window_and_resets_editing_state(self):
        window = self.editor.window
        self.editor.dirty = True
        self.editor.changed_shapes.add(('MainPlatform', 'changed'))
        self.editor.drag = ('point', 1)
        other = copy.deepcopy(self.variant)
        other['id'] = 'another-module'
        with patch('tkinter.messagebox.askyesnocancel', return_value=False):
            self.assertTrue(self.editor.load_variant(self.family, other))
        self.assertIs(self.editor.window, window)
        self.assertEqual(self.editor.variant['id'], 'another-module')
        self.assertFalse(self.editor.dirty)
        self.assertFalse(self.editor.changed_shapes)
        self.assertIsNone(self.editor.drag)

    def test_save_failure_prevents_switch_and_exit(self):
        self.editor.dirty = True
        self.editor.save = Mock(return_value=False)
        original = self.editor.module
        library = ObstacleLibraryWindow.__new__(ObstacleLibraryWindow)
        library.editor = self.editor
        destroy = Mock()
        with patch('tkinter.messagebox.askyesnocancel', return_value=True):
            self.assertFalse(self.editor.load_variant(self.family, self.variant))
            library.close(destroy)
        self.assertIs(self.editor.module, original)
        destroy.assert_not_called()

    def test_pending_field_input_requires_confirmation(self):
        self.editor.selected = 0
        self.editor.fields = {'x': Mock(get=Mock(return_value='invalid'))}
        with patch('tkinter.messagebox.askyesnocancel', return_value=None) as confirm:
            self.assertFalse(self.editor.confirm_leave())
            confirm.assert_called_once()

    def test_select_current_module_keeps_unsaved_changes(self):
        library = ObstacleLibraryWindow.__new__(ObstacleLibraryWindow)
        library.editor = self.editor
        self.editor.dirty = True
        with patch('tkinter.messagebox.askyesnocancel') as confirm:
            library.open_editor(self.editor.variant['id'])
            confirm.assert_not_called()
        self.assertTrue(self.editor.dirty)

    def test_successful_save_allows_switch(self):
        self.editor.dirty = True
        self.editor.save = Mock(return_value=True)
        with patch('tkinter.messagebox.askyesnocancel', return_value=True):
            self.assertTrue(self.editor.load_variant(self.family, self.variant))
        self.editor.save.assert_called_once()

    def test_clean_save_keeps_window_open(self):
        self.assertTrue(self.editor.save())
        self.editor.window.destroy.assert_not_called()

