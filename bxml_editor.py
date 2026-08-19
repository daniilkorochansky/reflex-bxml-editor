#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------------------------------------------------
#   Reflex BXML Editor — An editor for game files with a BXML structure.
#   Copyright (C) 2026  Daniil Korochansky
#
#   This file is part of Reflex BXML Editor.
#
#   Reflex BXML Editor is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   Reflex BXML Editor is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with Reflex BXML Editor.  If not, see <https://www.gnu.org/licenses/>.
# -------------------------------------------------------------------------------------------------------------------

from __future__ import annotations

import shutil
import subprocess
import sys
import os
from pathlib import Path

import wx
import wx.adv
import xml.etree.ElementTree as ET

import reflex_bxml_tool as bxml
import bxml_database_tool as dbxml


APP_NAME = "Reflex BXML Editor"

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title=APP_NAME, size=(1000, 700))
        self.current_bxml: Path | None = None
        self.current_xml: Path | None = None
        self.is_database = False
        
        self.SetMinSize((1000, 700))
        self.SetIcon(wx.Icon(resource_path("icon.ico")))
        
        self.parsed = None
        self.modified = False
        self._closing = False
        self._selected_element = None
        self._property_rows = []
        self._loading_properties = False
        self._suspend_xml_events = False
        self._property_controls = []
        self._property_originals = {}
        self._property_editing = False
        self._property_sizer = None

        self._build_menu()
        self._build_ui()
        self._bind_events()
        self.CreateStatusBar(2)
        self.SetStatusText("Ready", 0)
        self.SetStatusText("No file", 1)
        self.Centre()

    # ---------- UI ----------

    def _build_menu(self):
        bar = wx.MenuBar()

        file_menu = wx.Menu()
        self.mi_open = file_menu.Append(wx.ID_OPEN, "&Open File...\tCtrl+O")
        self.mi_decode = file_menu.Append(wx.ID_ANY, "Decode to XML...")
        self.mi_decode.Enable(False)
        self.mi_encode = file_menu.Append(wx.ID_ANY, "Build File...\tCtrl+B")
        self.mi_encode.Enable(False)
        file_menu.AppendSeparator()
        self.mi_save_xml = file_menu.Append(wx.ID_SAVE, "Save XML...\tCtrl+S")
        self.mi_save_xml.Enable(False)
        file_menu.AppendSeparator()
        self.mi_exit = file_menu.Append(wx.ID_EXIT, "Exit")
        bar.Append(file_menu, "&File")

        tools = wx.Menu()
        self.mi_inspect = tools.Append(wx.ID_ANY, "Inspect File")
        self.mi_inspect.Enable(False)
        self.mi_validate = tools.Append(wx.ID_ANY, "Validate Current BXML")
        self.mi_validate.Enable(False)
        tools.AppendSeparator()
        self.mi_open_folder = tools.Append(wx.ID_ANY, "Open File Location")
        self.mi_open_folder.Enable(False)
        bar.Append(tools, "&Tools")

        help_menu = wx.Menu()
        self.mi_about = help_menu.Append(wx.ID_ABOUT, "&About")
        bar.Append(help_menu, "&Help")
        self.SetMenuBar(bar)

    def _build_ui(self):
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.ToolBar(panel, style=wx.TB_HORIZONTAL | wx.TB_TEXT | wx.TB_FLAT | wx.TB_NODIVIDER)
        tb_open = toolbar.AddTool(wx.ID_OPEN, "Open...", wx.ArtProvider.GetBitmap(wx.ART_FILE_OPEN, wx.ART_TOOLBAR))
        tb_build = toolbar.AddTool(wx.ID_ANY, "Build File...", wx.ArtProvider.GetBitmap(wx.ART_FILE_SAVE, wx.ART_TOOLBAR))
        toolbar.AddSeparator()
        tb_apply_raw_xml = toolbar.AddTool(wx.ID_ANY, "Apply XML", wx.ArtProvider.GetBitmap(wx.ART_FILE_SAVE, wx.ART_TOOLBAR))
        tb_apply_raw_xml.Enable(False)
        tb_build.Enable(False)
        toolbar.Realize()
        self.tb_open = tb_open
        self.tb_build = tb_build
        self.tb_apply_raw_xml = tb_apply_raw_xml
        self.toolbar = toolbar
        outer.Add(toolbar, 0, wx.EXPAND)

        splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE)
        left = wx.Panel(splitter)
        right = wx.Panel(splitter)
        right.SetMinSize((520, 600))

        # Tree
        ls = wx.BoxSizer(wx.VERTICAL)
        ls.Add(wx.StaticText(left, label="BXML Structure"), 0, wx.ALL, 8)
        self.tree = wx.TreeCtrl(left, style=wx.TR_HAS_BUTTONS | wx.TR_LINES_AT_ROOT | wx.TR_SINGLE)
        ls.Add(self.tree, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        left.SetSizer(ls)

        # Properties editor
        rs = wx.BoxSizer(wx.VERTICAL)
        prop_title = wx.StaticText(right, label="Properties")
        rs.Add(prop_title, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        # A normal wx.Panel with sizers is used instead of wx.Grid. This avoids
        # platform-specific wx.Grid painting/width glitches seen on Windows.
        self.properties_panel = wx.ScrolledWindow(
            right, style=wx.VSCROLL | wx.HSCROLL | wx.BORDER_SIMPLE
        )
        self.properties_panel.SetScrollRate(0, 12)
        self.properties_panel.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW))
        self.properties_panel.SetMinSize((-1, 140))
        self._property_sizer = wx.BoxSizer(wx.VERTICAL)
        self.properties_panel.SetSizer(self._property_sizer)
        rs.Add(self.properties_panel, 2, wx.EXPAND | wx.ALL, 8)

        self.prop_hint = wx.StaticText(
            right,
            label="Select an element to edit its properties."
        )
        self.prop_hint.SetForegroundColour(wx.Colour(90, 90, 90))
        rs.Add(self.prop_hint, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 8)

        raw_title = wx.StaticText(right, label="XML")
        rs.Add(raw_title, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.xml_text = wx.TextCtrl(
            right, style=wx.TE_MULTILINE | wx.TE_RICH2 | wx.HSCROLL
        )
        self.xml_text.SetMinSize((-1, 240))
        rs.Add(self.xml_text, 3, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 8)

       

        # Attach the complete right-side layout. Without this, wxWidgets does
        # not resize the Properties/Raw XML controls with the panel.
        right.SetSizer(rs)

        splitter.SplitVertically(left, right, 390)
        outer.Add(splitter, 1, wx.EXPAND)

        panel.SetSizer(outer)
        panel.SetMinSize((850, -1))


    def _bind_events(self):
        self.Bind(wx.EVT_MENU, self.on_open, self.mi_open)
        self.Bind(wx.EVT_MENU, self.on_decode, self.mi_decode)
        self.Bind(wx.EVT_MENU, self.on_build, self.mi_encode)
        self.Bind(wx.EVT_MENU, self.on_save_xml, self.mi_save_xml)
        self.Bind(wx.EVT_MENU, self.on_inspect, self.mi_inspect)
        self.Bind(wx.EVT_MENU, self.on_validate, self.mi_validate)
        self.Bind(wx.EVT_MENU, self.on_open_folder, self.mi_open_folder)
        self.Bind(wx.EVT_MENU, self.on_about, self.mi_about)
        self.Bind(wx.EVT_MENU, self.on_exit, self.mi_exit)
        self.Bind(wx.EVT_TOOL, self.on_open, id=self.tb_open.GetId())
        self.Bind(wx.EVT_TOOL, self.on_build, id=self.tb_build.GetId())
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_select)
        self.Bind(wx.EVT_TOOL, self.on_apply, id=self.tb_apply_raw_xml.GetId())
        self.xml_text.Bind(wx.EVT_TEXT, self._on_xml_text_changed)

        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.on_close_window)

    def _on_xml_text_changed(self, evt):
        if not self._suspend_xml_events:
            self._set_modified(True)
        evt.Skip()

    def _mark_modified(self):
        if self.current_xml or self.current_bxml:
            self._set_modified(True)

    # ---------- modified state ----------

    def _set_modified(self, value=True):
        self.modified = value
        if value:
            self.SetStatusText("Modified — Unsaved Changes", 0)

        else:
            self.SetStatusText("Ready", 0)
     

    def _confirm_discard_changes(self):
        if not self.modified:
            return True

        name = self.current_bxml.name if self.current_bxml else "current file"
        dlg = wx.MessageDialog(
            self,
            f'"{name}" has unsaved changes.\n\n'
            "Do you want to discard them and open another file?",
            "Unsaved changes",
            wx.YES_NO | wx.CANCEL | wx.ICON_WARNING
        )
        
        result = dlg.ShowModal()
        dlg.Destroy()
        return result == wx.ID_YES

    # ---------- file handling ----------

    def on_open(self, evt):
        if not self._confirm_discard_changes():
            return
        with wx.FileDialog(
            self, "Open file", wildcard="BXML, Level and Database files (*.bxml;*.level;*.database)|*.bxml;*.database|BXML files (*.bxml)|*.bxml|Level files (*.level)|*.level|Database files (*.database)|*.database|All files|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self.load_bxml(Path(dlg.GetPath()))

    def load_bxml(self, path: Path):
        try:
            self.is_database = path.suffix.lower() == ".database"
            backend = dbxml if self.is_database else bxml
            self.parsed = backend.decode(str(path))
            xml = backend.to_xml_text(self.parsed)
            self.current_bxml = path
            self.current_xml = None
            self.xml_text.SetValue(xml)
            self._xml_root = ET.fromstring(xml)
            self._capture_originals(self._xml_root)
            self._set_modified(False)
            self._populate_tree(xml)
            
            self.tb_apply_raw_xml.Enable(True)
            self.tb_build.Enable(True)
            self.toolbar.Realize()

            self.mi_decode.Enable(True)
            self.mi_encode.Enable(True)
            self.mi_save_xml.Enable(True)
            self.mi_inspect.Enable(True)
            self.mi_validate.Enable(True)
            self.mi_open_folder.Enable(True)
            
            h = self.parsed.header
            self.SetStatusText(
                f"{'BXML'} | Strings {h.str_count} | Pool {h.pool_size} | "
                f"Attributes {h.attr_count} | Nodes {h.node_count}", 0
            )
            self.SetStatusText(str(path), 1)
        except Exception as exc:
            wx.MessageBox(str(exc), "BXML error", wx.OK | wx.ICON_ERROR)

    def on_decode(self, evt):
        if not self.current_bxml:
            self.on_open(evt)
            if not self.current_bxml:
                return
        with wx.FileDialog(
            self, "Save XML", wildcard="XML files (*.xml)|*.xml|All files|*.*",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            Path(dlg.GetPath()).write_text(self.xml_text.GetValue(), encoding="utf-8")
            self.current_xml = Path(dlg.GetPath())
            self._set_modified(False)
            self.SetStatusText(f"XML saved: {self.current_xml.name}", 0)

    def on_save_xml(self, evt):
        if not self.current_xml:
            self.on_decode(evt)
            return
        self.current_xml.write_text(self.xml_text.GetValue(), encoding="utf-8")
        self._set_modified(False)
        self.SetStatusText(f"XML saved: {self.current_xml.name}", 0)

    def on_build(self, evt):
        if not self.current_bxml:
            wx.MessageBox("Open a BXML, Level or Database file first.", "Build", wx.OK | wx.ICON_INFORMATION)
            return

        try:
            ET.fromstring(self.xml_text.GetValue())
        except Exception as exc:
            wx.MessageBox(f"XML is invalid:\n\n{exc}", "Build file", wx.OK | wx.ICON_ERROR)
            return

        with wx.FileDialog(
            self, "Save file", wildcard=("Database files (*.database)|*.database|All files|*.*" if self.is_database else "BXML files (*.bxml)|*.bxml|Level files (*.level)|*.level|All files|*.*"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            defaultFile=self.current_bxml.name
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            out = Path(dlg.GetPath())

        # Write a temporary XML file so the proven backend remains the single source
        # of truth for serialization.
        tmp = out.with_suffix(".tmp.xml")
        try:
            tmp.write_text(self.xml_text.GetValue(), encoding="utf-8")

            # Back up an existing destination.
            if out.exists():
                bak = out.with_suffix(out.suffix + ".bak")
                shutil.copy2(out, bak)

            backend = dbxml if self.is_database else bxml
            if self.is_database:
                backend.encode_xml(str(tmp), str(out))
            else:
                backend.encode_xml(str(tmp), str(out), source_bxml=str(self.current_bxml))
            self.current_bxml = out
            self.parsed = backend.decode(str(out))
            self._capture_originals(self._xml_root)
            self._set_modified(False)
            self.SetStatusText(f"Built: {out.name}", 0)
            wx.MessageBox(
                f"BXML successfully built.\n\n{out}",
                "Build complete",
                wx.OK | wx.ICON_INFORMATION
            )
        except Exception as exc:
            wx.MessageBox(str(exc), "Build error", wx.OK | wx.ICON_ERROR)
        finally:
            tmp.unlink(missing_ok=True)

    # ---------- tree ----------

    def _populate_tree(self, xml_text):
        self.tree.DeleteAllItems()
        root = ET.fromstring(xml_text)
        self._xml_root = root

        root_item = self.tree.AddRoot(self._tree_label(root))
        self.tree.SetItemData(root_item, root)

        def add(parent_item, elem):
            item = self.tree.AppendItem(parent_item, self._tree_label(elem))
            self.tree.SetItemData(item, elem)
            for child in list(elem):
                add(item, child)
            return item

        for child in list(root):
            add(root_item, child)

        self.tree.Expand(root_item)
        self._select_element(root_item)

    @staticmethod
    def _tree_label(elem):
        label = elem.tag
        if elem.attrib:
            label += "  [" + ", ".join(f"{k}={v}" for k, v in elem.attrib.items()) + "]"
        if elem.text and elem.text.strip() and not list(elem):
            preview = " ".join(elem.text.split())
            if len(preview) > 60:
                preview = preview[:57] + "..."
            label += f"  → {preview}"
        return label

    @staticmethod
    def _value_type(value):
        if value.startswith("_uint:"):
            return "uint"
        if value.startswith("_int:"):
            return "int"
        if value.startswith("_float:"):
            return "float"
        if value.startswith("_vector3:"):
            return "vector3"
        if value.startswith("_color:"):
            return "color"
        if value.startswith("_matrix:"):
            return "matrix"
        if value.startswith("_bool:"):
            return "bool"
        return "string"

    @staticmethod
    @staticmethod
    def _format_float_game(value):
        s = f"{float(value):.8f}".rstrip("0").rstrip(".")
        if s == "-0":
            s = "0"
        return s

    @staticmethod
    def _parse_game_float(raw):
        return float(raw.strip().replace(",", "."))

    @staticmethod
    def _parse_game_vector3(raw):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) != 3:
            raise ValueError("vector3 must contain three values")
        return [float(p) for p in parts]

    @classmethod
    def _format_game_vector3(cls, values):
        return ",".join(cls._format_float_game(v) for v in values)

    @classmethod
    def _parse_numeric_list(cls, raw, count, label):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) != count:
            raise ValueError(f"{label} must contain {count} values")
        return [float(p) for p in parts]

    @classmethod
    def _format_numeric_list(cls, values):
        return ",".join(cls._format_float_game(v) for v in values)

    def _clear_properties(self):
        for child in list(self.properties_panel.GetChildren()):
            child.Destroy()
        self._property_sizer.Clear(delete_windows=False)
        self._property_controls = []
        self._property_originals = {}
        self._property_editing = False
        self.properties_panel.Layout()
        self.properties_panel.FitInside()
        best = self.properties_panel.GetBestVirtualSize()
        viewport_w, viewport_h = self.properties_panel.GetClientSize()
        self.properties_panel.SetVirtualSize(
            max(best.width, 850, viewport_w + 1),
            max(best.height, viewport_h)
        )
        self.properties_panel.Refresh()

    def _add_property_header(self, title):
        label = wx.StaticText(self.properties_panel, label=title)
        font = label.GetFont()
        font.MakeBold()
        label.SetFont(font)
        self._property_sizer.Add(label, 0, wx.TOP | wx.BOTTOM, 6)

    def _add_property_row(self, name, value, kind, apply_callback, original=None):
        row_panel = wx.Panel(self.properties_panel)
        row = wx.BoxSizer(wx.HORIZONTAL)

        name_label = wx.StaticText(row_panel, label=name, size=(155, -1))
        name_label.SetToolTip(f"Property: {name}")

        editor = None
        if kind == "bool":
            editor = wx.CheckBox(row_panel)
            editor.SetValue(value.lower() in ("true", "1"))
            editor.Bind(wx.EVT_CHECKBOX, lambda e, cb=apply_callback, c=editor: cb(c.GetValue()))
        elif kind == "uint":
            editor = wx.SpinCtrl(row_panel, min=0, max=4294967295)
            editor.SetValue(int(value))
            editor.Bind(wx.EVT_SPINCTRL, lambda e, cb=apply_callback, c=editor: cb(c.GetValue()))
        elif kind == "int":
            editor = wx.SpinCtrl(row_panel, min=-2147483648, max=2147483647)
            editor.SetValue(int(value))
            editor.Bind(wx.EVT_SPINCTRL, lambda e, cb=apply_callback, c=editor: cb(c.GetValue()))
        elif kind == "float":
            editor = wx.SpinCtrlDouble(row_panel, min=-1e12, max=1e12, inc=0.01)
            editor.SetDigits(4)
            editor.SetValue(self._parse_game_float(value))
            editor.Bind(wx.EVT_SPINCTRLDOUBLE, lambda e, cb=apply_callback, c=editor: cb(c.GetValue()))
        else:
            editor = wx.TextCtrl(row_panel, value=value, style=wx.TE_PROCESS_ENTER)
            editor.Bind(wx.EVT_TEXT_ENTER, lambda e, cb=apply_callback, c=editor: cb(c.GetValue()))
            editor.SetHint("Press Enter to apply")

        if original is None:
            original = value
        self._property_originals[id(editor)] = original

        row.Add(name_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        editor.SetMinSize((360, -1))
        editor.SetMaxSize((360, -1))
        row.Add(editor, 0, wx.EXPAND)
        original_text = wx.StaticText(row_panel, label=f"Original: {original}")
        original_text.SetMinSize((180, -1))
        original_text.SetForegroundColour(wx.Colour(120, 120, 120))
        original_text.SetToolTip("Original value loaded from the BXML file.")
        row.Add(original_text, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)

        row_panel.SetSizer(row)
        row_panel.SetMinSize((850, -1))
        self._property_sizer.Add(row_panel, 0, wx.EXPAND | wx.BOTTOM, 5)
        editor._original_label = original_text
        editor._property_name = name
        editor._property_kind = kind
        self._property_controls.append(editor)



    @staticmethod
    def _safe_float_callback(callback, ctrl):
        try:
            callback(ctrl.GetValue())
        except (ValueError, TypeError):
            pass

    def _add_vector3_row(self, name, raw, apply_callback, original=None):
        panel = wx.Panel(self.properties_panel)
        outer = wx.BoxSizer(wx.VERTICAL)
        top = wx.BoxSizer(wx.HORIZONTAL)
        label = wx.StaticText(panel, label=name, size=(155, -1))
        top.Add(label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        try:
            values = self._parse_game_vector3(raw)
        except Exception:
            values = [0.0, 0.0, 0.0]

        ctrls = []
        for axis, val in zip(("X", "Y", "Z"), values):
            top.Add(wx.StaticText(panel, label=axis), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 3)
            c = wx.SpinCtrlDouble(panel, min=-1e12, max=1e12, inc=0.01, size=(120, -1))
            c.SetDigits(4)
            c.SetValue(val)
            c.SetToolTip(f"{name} — {axis}")
            top.Add(c, 0, wx.RIGHT, 7)
            ctrls.append(c)

        def changed(evt):
            try:
                new_values = [c.GetValue() for c in ctrls]
                apply_callback(self._format_game_vector3(new_values))
            except Exception:
                pass
            evt.Skip()

        for c in ctrls:
            c.Bind(wx.EVT_SPINCTRLDOUBLE, changed)

        original_label = wx.StaticText(panel, label=f"Original: {original if original is not None else raw}")
        original_label.SetForegroundColour(wx.Colour(120, 120, 120))
        original_label.SetToolTip("Original value loaded from the file.")
        outer.Add(original_label, 0, wx.LEFT | wx.TOP, 163)

        panel.SetSizer(outer)
        panel._vector_controls = ctrls
        panel._original_label = original_label
        panel._property_name = name
        self._property_sizer.Add(panel, 0, wx.EXPAND | wx.BOTTOM, 6)
        self._property_controls.extend(ctrls)

    def _add_numeric_list_row(self, name, raw, count, label, apply_callback, original=None):
        panel = wx.Panel(self.properties_panel)
        outer = wx.BoxSizer(wx.HORIZONTAL)
        name_label = wx.StaticText(panel, label=name, size=(155, -1))
        outer.Add(name_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        try:
            values = self._parse_numeric_list(raw, count, label)
        except Exception:
            values = [0.0] * count
        ctrls = []
        for idx, value in enumerate(values):
            axis = str(idx + 1)
            if count == 4 and label == "color":
                axis = ("R", "G", "B", "A")[idx]
            elif count == 16 and label == "matrix":
                axis = f"M{idx // 4 + 1},{idx % 4 + 1}"
            c = wx.SpinCtrlDouble(panel, min=-1e12, max=1e12, inc=0.01, size=(96, -1))
            c.SetDigits(6)
            c.SetValue(value)
            c.SetToolTip(f"{name} — {axis}")
            outer.Add(c, 0, wx.RIGHT, 5)
            ctrls.append(c)

        def changed(evt):
            try:
                apply_callback(self._format_numeric_list([c.GetValue() for c in ctrls]))
            except Exception:
                pass
            evt.Skip()

        for c in ctrls:
            c.Bind(wx.EVT_SPINCTRLDOUBLE, changed)

        original_label = wx.StaticText(panel, label=f"Original: {original if original is not None else raw}")
        original_label.SetForegroundColour(wx.Colour(120, 120, 120))
        original_label.SetToolTip("Original value loaded from the file.")
        outer.Add(original_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)

        panel.SetSizer(outer)
        panel.SetMinSize((850, -1))
        panel._original_label = original_label
        self._property_sizer.Add(panel, 0, wx.EXPAND | wx.BOTTOM, 6)
        self._property_controls.extend(ctrls)

    def _original_value(self, elem, name, current):
        originals = getattr(elem, "_bxml_originals", None)
        if originals is not None:
            return originals.get(name, current)
        return current

    def _capture_originals(self, root):
        # ElementTree.Element objects don't reliably accept custom attributes,
        # so keep originals keyed by object id.
        self._original_values = {}
        for elem in root.iter():
            self._original_values[id(elem)] = {
                "attrs": dict(elem.attrib),
                "text": elem.text,
            }

    def _get_original_attr(self, elem, name, current):
        return self._original_values.get(id(elem), {}).get("attrs", {}).get(name, current)

    def _get_original_text(self, elem, current):
        return self._original_values.get(id(elem), {}).get("text", current)


    def _select_element(self, item):
        if self._closing:
            return
        try:
            elem = self.tree.GetItemData(item)
        except RuntimeError:
            return
        if elem is None:
            return

        self._selected_element = elem
        self._loading_properties = True
        try:
            self._clear_properties()
            self._add_property_header(f"<{elem.tag}>")

            if elem.attrib:
                for name, value in elem.attrib.items():
                    if value.startswith("_color:"):
                        original_value = self._get_original_attr(elem, name, value)
                        original_body = original_value[7:] if original_value.startswith("_color:") else value[7:]
                        self._add_numeric_list_row(
                            name, value[7:], 4, "color",
                            lambda v, n=name: self._set_attribute_typed(n, "_color:" + v),
                            original_body
                        )
                    elif value.startswith("_matrix:"):
                        original_value = self._get_original_attr(elem, name, value)
                        original_body = original_value[8:] if original_value.startswith("_matrix:") else value[8:]
                        self._add_numeric_list_row(
                            name, value[8:], 16, "matrix",
                            lambda v, n=name: self._set_attribute_typed(n, "_matrix:" + v),
                            original_body
                        )
                    elif value.startswith("_vector3:"):
                        self._add_vector3_row(
                            name, value[9:],
                            lambda v, n=name: self._set_attribute_typed(n, "_vector3:" + v),
                            self._get_original_attr(elem, name, value)[9:] if self._get_original_attr(elem, name, value).startswith("_vector3:") else value[9:]
                        )
                    elif value.startswith("_uint:"):
                        self._add_property_row(
                            name, value[6:], "uint",
                            lambda v, n=name: self._set_attribute_typed(n, "_uint:" + str(int(v))),
                            self._get_original_attr(elem, name, value)[6:] if self._get_original_attr(elem, name, value).startswith("_uint:") else value[6:]
                        )
                    elif value.startswith("_float:"):
                        self._add_property_row(
                            name, value[7:], "float",
                            lambda v, n=name: self._set_attribute_typed(n, "_float:" + self._format_float_game(v)),
                            self._get_original_attr(elem, name, value)[7:] if self._get_original_attr(elem, name, value).startswith("_float:") else value[7:]
                        )
                    elif value.startswith("_int:"):
                        raw = value[5:]
                        # None is a special XML representation; preserve it as text.
                        if raw.strip().lower() == "none":
                            self._add_property_row(
                                name, raw, "string",
                                lambda v, n=name: self._set_attribute_typed(n, "_int:" + str(v))
                            )
                        else:
                            self._add_property_row(
                                name, raw, "int",
                                lambda v, n=name: self._set_attribute_typed(n, "_int:" + str(int(v))),
                                self._get_original_attr(elem, name, value)[5:] if self._get_original_attr(elem, name, value).startswith("_int:") else raw
                            )
                    elif value.startswith("_bool:"):
                        self._add_property_row(
                            name, value[6:], "bool",
                            lambda v, n=name: self._set_attribute_typed(n, "_bool:" + ("true" if v else "false")),
                            self._get_original_attr(elem, name, value)[6:] if self._get_original_attr(elem, name, value).startswith("_bool:") else value[6:]
                        )
                    else:
                        self._add_property_row(
                            name, value, "string",
                            lambda v, n=name: self._set_attribute_typed(n, v),
                            self._get_original_attr(elem, name, value)
                        )

            if elem.text and elem.text.strip() and not list(elem):
                self._add_property_header("Text")
                self._add_property_row(
                    "(text)", " ".join(elem.text.split()), "string",
                    self._set_text_value,
                    " ".join(self._get_original_text(elem, elem.text).split()) if self._get_original_text(elem, elem.text) else ""
                )

            if not elem.attrib and not (elem.text and elem.text.strip() and not list(elem)):
                self.prop_hint.SetLabel("This element has no editable properties.")
            else:
                self.prop_hint.SetLabel(
                    "Changes are applied immediately. Build BXML when finished."
                )

            self.properties_panel.Layout()
            self.properties_panel.FitInside()
        finally:
            self._loading_properties = False
        self._suspend_xml_events = False

    def _set_attribute_typed(self, name, value):
        if self._loading_properties or self._selected_element is None:
            return
        self._selected_element.set(name, value)
        self._refresh_after_property_edit()

    def _set_text_value(self, value):
        if self._loading_properties or self._selected_element is None:
            return
        self._selected_element.text = str(value)
        self._refresh_after_property_edit()

    def _refresh_after_property_edit(self):
        if not hasattr(self, "_xml_root"):
            return
        self._suspend_xml_events = True
        try:
            self.xml_text.ChangeValue(ET.tostring(self._xml_root, encoding="unicode"))
        finally:
            self._suspend_xml_events = False
        self._set_modified(True)
        self._update_selected_tree_label()

    def _refresh_selected_properties(self):
        if self._closing or self._selected_element is None:
            return
        # Find the selected tree item and rebuild its property controls.
        root = self.tree.GetRootItem()
        if not root.IsOk():
            return
        def walk(item):
            try:
                if self.tree.GetItemData(item) is self._selected_element:
                    self._select_element(item)
                    return True
                child, cookie = self.tree.GetFirstChild(item)
                while child.IsOk():
                    if walk(child):
                        return True
                    child, cookie = self.tree.GetNextChild(item, cookie)
            except RuntimeError:
                return False
            return False
        walk(root)

    def _update_selected_tree_label(self):
        # Find the tree item by matching stored ElementTree object.
        root = self.tree.GetRootItem()
        if not root.IsOk():
            return

        def walk(item):
            try:
                if self.tree.GetItemData(item) is self._selected_element:
                    self.tree.SetItemText(item, self._tree_label(self._selected_element))
                    return True
                child, cookie = self.tree.GetFirstChild(item)
                while child.IsOk():
                    if walk(child):
                        return True
                    child, cookie = self.tree.GetNextChild(item, cookie)
            except RuntimeError:
                return False
            return False

        walk(root)


    def on_tree_select(self, evt):
        if self._closing:
            return
        item = evt.GetItem()
        if not item.IsOk():
            return
        self._select_element(item)
        evt.Skip()

    def on_apply(self, evt):
        try:
            root = ET.fromstring(self.xml_text.GetValue())
            # Normalize only enough to validate and rebuild the tree.
            self._suspend_xml_events = True
            try:
                self.xml_text.ChangeValue(ET.tostring(root, encoding="unicode"))
            finally:
                self._suspend_xml_events = False
            self._populate_tree(self.xml_text.GetValue())
            self._set_modified(True)
            self.SetStatusText("XML changes applied", 0)
        except Exception as exc:
            wx.MessageBox(str(exc), "XML error", wx.OK | wx.ICON_ERROR)

    # ---------- tools ----------

    def on_inspect(self, evt):
        if not self.current_bxml:
            return
        p = self.parsed
        backend = dbxml if self.is_database else bxml
        h = p.header
        counts = {}
        for a in p.attrs:
            key = ("pool:" + backend.TYPE_NAMES.get(a.value_type, str(a.value_type))) if a.uses_pool else "string"
            counts[key] = counts.get(key, 0) + 1
        lines = [
            f"Signature:   0x{h.signature:08X}",
            f"Version:     {h.version}",
            f"Strings:     {h.str_count}",
            f"PoolPointer: {h.pool_pointer}",
            f"PoolSize:    {h.pool_size}",
            f"Attributes:  {h.attr_count}",
            f"Nodes:       {h.node_count}",
            f"Compressed:  {h.zsize}",
            f"Raw size:    {len(p.raw)}",
            "",
            "Attribute values:",
        ]
        lines += [f"  {k}: {v}" for k, v in counts.items()]
        wx.MessageBox("\n".join(lines), "Database Inspector" if self.is_database else "BXML Inspector", wx.OK | wx.ICON_INFORMATION)

    def on_validate(self, evt):
        if not self.current_bxml:
            return
        try:
            backend = dbxml if self.is_database else bxml
            backend.decode(str(self.current_bxml))
            wx.MessageBox(
                "Database BXML is structurally valid and can be decoded." if self.is_database
                else "BXML is structurally valid and can be decoded.", "Validation", wx.OK | wx.ICON_INFORMATION)
        except Exception as exc:
            wx.MessageBox(str(exc), "Validation failed", wx.OK | wx.ICON_ERROR)

    def on_open_folder(self, evt):
        if not self.current_bxml:
            return
        path = self.current_bxml.parent
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def on_about(self, evt):
        wx.MessageBox(
            "Reflex BXML Editor\n\n"
            "An editor for game files with a BXML structure.\n\nVersion: 1.1.0\nAuthor: Daniil Korochansky\nLicense: GNU General Public License v3.0",
            "About",
            wx.OK | wx.ICON_INFORMATION
        )

    def on_exit(self, evt):
        if self._closing:
            return
        if self.modified:
            dlg = wx.MessageDialog(
                self,
                "There are unsaved changes.\n\nDo you want to exit without saving?",
                "Unsaved changes",
                wx.YES_NO | wx.ICON_WARNING
            )
            result = dlg.ShowModal()
            dlg.Destroy()
            if result != wx.ID_YES:
                return

        self._closing = True
        try:
            self.tree.Unbind(wx.EVT_TREE_SEL_CHANGED)
        except Exception:
            pass
        self.Close()

    def on_close_window(self, evt):
        if self._closing:
            evt.Skip()
            return
        self.on_exit(evt)

    def on_key(self, evt):
        if evt.ControlDown() and evt.GetKeyCode() == ord("O"):
            self.on_open(evt)
        elif evt.ControlDown() and evt.GetKeyCode() == ord("B"):
            self.on_build(evt)
        elif evt.ControlDown() and evt.GetKeyCode() == ord("S"):
            self.on_save_xml(evt)
        else:
            evt.Skip()


class App(wx.App):
    def OnInit(self):
        frame = MainFrame()
        frame.Show()
        return True


if __name__ == "__main__":
    app = App(False)
    app.MainLoop()
