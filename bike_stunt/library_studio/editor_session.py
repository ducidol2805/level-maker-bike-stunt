"""Lifecycle of the single persistent library editor."""
import copy
from tkinter import messagebox

from bike_stunt.obstacle_library import GROUPS, build_variant


class EditorSession:
    def pending_fields(self) -> bool:
        if self.selected is None:
            return False
        point = self.current()[2][self.selected]
        expected = dict(x=point['x'], y=point['y'],
                        in_x=point['tangentIn']['x'], in_y=point['tangentIn']['y'],
                        out_x=point['tangentOut']['x'], out_y=point['tangentOut']['y'])
        try:
            return any(float(self.fields[key].get()) != value for key, value in expected.items())
        except ValueError:
            return True

    def confirm_leave(self) -> bool:
        if not self.dirty and not self.pending_fields():
            return True
        answer = messagebox.askyesnocancel(
            'Unsaved changes',
            f"Save changes to {self.variant['id']}?\n"
            'Yes: save changes. No: discard changes. Cancel: keep editing.',
            parent=self.window)
        if answer is None:
            return False
        return self.save() if answer else True

    def load_variant(self, family, variant, *, confirm=True) -> bool:
        # Validate/build before touching the current working copy.
        variant = copy.deepcopy(variant)
        module = build_variant(family, variant, scale=family.get('_worldScale', 1))
        shapes = [(group, shape) for group in GROUPS for shape in module[group]
                  if 'points' in shape]
        if not shapes:
            raise ValueError('Module has no editable shapes')
        if confirm and not self.confirm_leave():
            return False
        self.family, self.variant, self.module, self.shapes = family, variant, module, shapes
        self.shape_index = 0
        self.selected = self.drag = self.pan_anchor = None
        self.dirty = False
        self.changed_shapes = set()
        self.added_shapes = {(group, shape['id']) for group, shapes in
                             variant.get('addedShapes', {}).items() for shape in shapes}
        self.converted_shapes = set(variant.get('shapeTypeOverrides', {}))
        self.shape_choice.configure(values=[f'{group} / {shape["id"]}' for group, shape in self.shapes])
        self.shape_choice.current(0)
        self.shape_mode.set(self.shapes[0][0])
        for field in self.fields.values():
            field.set('')
        self.mode.set('broken')
        self.window.title(f"Edit obstacle - {variant['id']}")
        self.variant_label.configure(text=variant['id'])
        self.status.set('Select a point to edit.')
        self.fit()
        return True

    def revert(self) -> None:
        if (self.dirty or self.pending_fields()) and not messagebox.askyesno(
                'Discard changes?', 'Discard unsaved changes to this module?', parent=self.window):
            return
        self.load_variant(self.family, self.variant, confirm=False)

    def close(self) -> None:
        # The editor is a persistent companion to the library, not a disposable dialog.
        self.status.set('Editor stays open with Library Studio. Close the library to exit.')
        self.window.lift()
