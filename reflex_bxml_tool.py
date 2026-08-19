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
import argparse, struct, sys, zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

HEADER = struct.Struct('<9I')
ATTR = struct.Struct('<IIHH')
NODE = struct.Struct('<IiIIIIII')
SIG = 0x4C4D5842

TYPE_STRING = 1
TYPE_INT = 3
TYPE_UINT = 4
TYPE_FLOAT = 5
TYPE_COLOR = 7
TYPE_MATRIX = 8
TYPE_VECTOR3 = 10
TYPE_BOOL = 11

TYPE_NAMES = {
    TYPE_STRING: 'string',
    TYPE_INT: 'int',
    TYPE_UINT: 'uint',
    TYPE_FLOAT: 'float',
    TYPE_COLOR: 'color',
    TYPE_MATRIX: 'matrix',
    TYPE_VECTOR3: 'vector3',
    TYPE_BOOL: 'bool',
}

PREFIXES = {
    '_int:': TYPE_INT,
    '_uint:': TYPE_UINT,
    '_float:': TYPE_FLOAT,
    '_color:': TYPE_COLOR,
    '_matrix:': TYPE_MATRIX,
    '_vector3:': TYPE_VECTOR3,
    '_bool:': TYPE_BOOL,
}

@dataclass
class Header:
    signature:int; version:int; str_count:int; pool_pointer:int; pool_size:int
    attr_count:int; node_count:int; unknown:int; zsize:int

@dataclass
class Attribute:
    name:int; value:int; uses_pool:int; value_type:int

@dataclass
class Node:
    name:int; inner:int; uses_pool:int; value_type:int; level:int; children:int
    attr_index:int; attr_count:int

@dataclass
class Parsed:
    header:Header; strings:list[str]; pool:bytes; attrs:list[Attribute]; nodes:list[Node]
    raw:bytes; compressed:bytes


def parse_header(data:bytes)->Header:
    if len(data)<HEADER.size: raise ValueError('File is smaller than BXML header')
    h=Header(*HEADER.unpack_from(data,0))
    if h.signature != SIG: raise ValueError('Not a BXML file (bad signature)')
    if len(data) != HEADER.size+h.zsize:
        raise ValueError(f'Unexpected file size: header says {h.zsize} compressed bytes, file has {len(data)-HEADER.size}')
    return h

def decode(path:str)->Parsed:
    data=Path(path).read_bytes(); h=parse_header(data)
    comp=data[HEADER.size:]
    raw=zlib.decompress(comp)
    expected=h.pool_pointer+h.pool_size+h.attr_count*ATTR.size+h.node_count*NODE.size
    if len(raw)!=expected: raise ValueError(f'Unexpected decompressed size: {len(raw)} != {expected}')
    strings=[]; p=0
    for i in range(h.str_count):
        e=raw.find(b'\0',p)
        if e<0: raise ValueError(f'Unterminated string #{i}')
        strings.append(raw[p:e].decode('utf-8'))
        p=e+1
    if p != h.pool_pointer: raise ValueError(f'String table ends at {p}, expected PoolPointer {h.pool_pointer}')
    pool=raw[h.pool_pointer:h.pool_pointer+h.pool_size]
    ap=h.pool_pointer+h.pool_size
    attrs=[Attribute(*ATTR.unpack_from(raw,ap+i*ATTR.size)) for i in range(h.attr_count)]
    np=ap+h.attr_count*ATTR.size
    nodes=[Node(*NODE.unpack_from(raw,np+i*NODE.size)) for i in range(h.node_count)]
    return Parsed(h,strings,pool,attrs,nodes,raw,comp)

def pool_read(pool:bytes, typ:int, off:int):
    if off < 0 or off >= len(pool):
        raise ValueError(f'Pool offset out of range: {off}')

    if typ in (TYPE_INT, TYPE_UINT, TYPE_FLOAT):
        if off + 4 > len(pool):
            raise ValueError(f'Truncated {TYPE_NAMES.get(typ, typ)} in pool')
        if typ == TYPE_INT:
            return struct.unpack_from('<i', pool, off)[0], 4
        if typ == TYPE_UINT:
            return struct.unpack_from('<I', pool, off)[0], 4
        return struct.unpack_from('<f', pool, off)[0], 4

    if typ == TYPE_COLOR:
        if off + 16 > len(pool):
            raise ValueError('Truncated color in pool')
        return struct.unpack_from('<4f', pool, off), 16

    if typ == TYPE_MATRIX:
        if off + 64 > len(pool):
            raise ValueError('Truncated matrix in pool')
        return struct.unpack_from('<16f', pool, off), 64

    if typ == TYPE_VECTOR3:
        if off + 12 > len(pool):
            raise ValueError('Truncated vector3 in pool')
        return struct.unpack_from('<3f', pool, off), 12

    if typ == TYPE_BOOL:
        if off + 4 > len(pool):
            raise ValueError('Truncated bool in pool')
        v = struct.unpack_from('<I', pool, off)[0]
        return bool(v), 4

    raise ValueError(f'Unsupported pool type {typ}')

def fmt_float(v:float)->str:
    # Close to Delphi's %f but avoid needlessly huge precision.
    return format(v,'.9g')

def value_to_text(v, typ:int)->str:
    if typ in (TYPE_INT, TYPE_UINT):
        return str(v)
    if typ == TYPE_FLOAT:
        return fmt_float(v)
    if typ in (TYPE_COLOR, TYPE_VECTOR3, TYPE_MATRIX):
        return ','.join(fmt_float(x) for x in v)
    if typ == TYPE_BOOL:
        return 'true' if v else 'false'
    return str(v)

def xml_escape_attr(s:str)->str:
    return s.replace('&','&amp;').replace('"','&quot;').replace('<','&lt;').replace('>','&gt;')

def to_xml_text(parsed:Parsed)->str:
    strings = parsed.strings
    pool = parsed.pool
    attrs = parsed.attrs
    nodes = parsed.nodes
    lines = []

    def attr_value_text(a:Attribute) -> str:
        if a.uses_pool:
            v, _ = pool_read(pool, a.value_type, a.value)
            prefix = {
                TYPE_INT: '_int:',
                TYPE_UINT: '_uint:',
                TYPE_FLOAT: '_float:',
                TYPE_COLOR: '_color:',
                TYPE_MATRIX: '_matrix:',
                TYPE_VECTOR3: '_vector3:',
                TYPE_BOOL: '_bool:',
            }.get(a.value_type, '')
            return prefix + value_to_text(v, a.value_type)

        if a.value < 0 or a.value >= len(strings):
            raise ValueError('Invalid attribute value index')
        return strings[a.value]

    def emit_range(start:int, count:int, indent:int):
        for idx in range(start, start + count):
            n = nodes[idx]
            if n.name < 0 or n.name >= len(strings):
                raise ValueError(f'Invalid node string index {n.name}')

            line = ' ' * indent + '<' + strings[n.name]

            for j in range(n.attr_count):
                ai = n.attr_index + j
                if ai < 0 or ai >= len(attrs):
                    raise ValueError(f'Invalid attribute index {ai}')
                a = attrs[ai]
                if a.name < 0 or a.name >= len(strings):
                    raise ValueError(f'Invalid attribute name index {a.name}')
                line += f' {strings[a.name]}="{xml_escape_attr(attr_value_text(a))}"'

            if n.uses_pool:
                line += f' _valuetype="{TYPE_NAMES.get(n.value_type, str(n.value_type))}"'

            # Leaf without a value.
            if n.children == 0 and n.inner == -1:
                lines.append(line + '/>')
                continue

            lines.append(line + '>')

            # Node values in these game BXML files use NInnerTextIndex as the
            # actual pool offset. This is important for scene files, which
            # contain UInt/Color/Matrix values.
            if n.uses_pool:
                v, _ = pool_read(pool, n.value_type, n.inner)
                lines.append(' ' * (indent + 3) + value_to_text(v, n.value_type))
            elif n.children == 0 and n.inner >= 0:
                if n.inner >= len(strings):
                    raise ValueError(f'Invalid node inner-text string index {n.inner}')
                lines.append(' ' * (indent + 3) + strings[n.inner])

            if n.children:
                emit_range(n.level, n.children, indent + 3)

            lines.append(' ' * indent + '</' + strings[n.name] + '>')

    emit_range(0, 1, 0)
    return '\n'.join(lines) + '\n'

def parse_float_list(body:str, count:int, label:str):
    parts = [float(x.strip()) for x in body.split(',') if x.strip() != '']
    if len(parts) != count:
        raise ValueError(f'{label} requires {count} components: {body}')
    return tuple(parts)

def parse_typed(text:str):
    for pfx, typ in PREFIXES.items():
        if text.startswith(pfx):
            body = text[len(pfx):].strip()

            if typ == TYPE_INT:
                if body.lower() == 'none':
                    return typ, 0
                return typ, int(body)

            if typ == TYPE_UINT:
                if body.lower() == 'none':
                    return typ, 0
                value = int(body, 10)
                if not 0 <= value <= 0xFFFFFFFF:
                    raise ValueError(f'uint32 out of range: {body}')
                return typ, value

            if typ == TYPE_FLOAT:
                return typ, float(body)

            if typ == TYPE_COLOR:
                return typ, parse_float_list(body, 4, 'color')

            if typ == TYPE_MATRIX:
                return typ, parse_float_list(body, 16, 'matrix')

            if typ == TYPE_VECTOR3:
                return typ, parse_float_list(body, 3, 'vector3')

            if typ == TYPE_BOOL:
                if body.lower() in ('true', '1'):
                    return typ, True
                if body.lower() in ('false', '0'):
                    return typ, False
                raise ValueError(f'Invalid bool: {text}')

    return TYPE_STRING, text

def add_string(strings, index, s):
    if s not in index:
        index[s]=len(strings); strings.append(s)
    return index[s]

def _raw_equal_value(a_type: int, a_value, b_type: int, b_value) -> bool:
    if a_type != b_type:
        return False
    if a_type == TYPE_FLOAT:
        return struct.pack('<f', float(a_value)) == struct.pack('<f', float(b_value))
    if a_type in (TYPE_COLOR, TYPE_MATRIX, TYPE_VECTOR3):
        return struct.pack('<' + 'f' * len(a_value), *a_value) == struct.pack('<' + 'f' * len(b_value), *b_value)
    return a_value == b_value


def _pool_add_raw(pool: bytearray, typ: int, val) -> int:
    off = len(pool)
    if typ == TYPE_INT:
        pool.extend(struct.pack('<i', int(val)))
    elif typ == TYPE_UINT:
        pool.extend(struct.pack('<I', int(val)))
    elif typ == TYPE_FLOAT:
        pool.extend(struct.pack('<f', float(val)))
    elif typ == TYPE_COLOR:
        pool.extend(struct.pack('<4f', *val))
    elif typ == TYPE_MATRIX:
        pool.extend(struct.pack('<16f', *val))
    elif typ == TYPE_VECTOR3:
        pool.extend(struct.pack('<3f', *val))
    elif typ == TYPE_BOOL:
        pool.extend(struct.pack('<I', 1 if val else 0))
    else:
        raise ValueError(f'Cannot put type {typ} in pool')
    return off


def _encode_xml_fresh(root: ET.Element, version: int, unknown: int) -> bytes:
    """Encode without a source BXML. This is the original/new-file path."""
    strings=[]; sidx={}; pool=bytearray(); attrs=[]

    def add_pool(typ, val):
        return _pool_add_raw(pool, typ, val)

    def make_node(elem, attr_index):
        name_i=add_string(strings,sidx,elem.tag)
        vt_text=elem.attrib.get('_valuetype')
        text=(elem.text or '').strip()
        has_children=len(elem)>0
        if vt_text is None and not has_children and text:
            inner=add_string(strings,sidx,text)
        else:
            inner=-1
        attr_specs=[]
        for k,vtext in elem.attrib.items():
            if k=='_valuetype': continue
            typ,val=parse_typed(vtext)
            ni=add_string(strings,sidx,k)
            if typ==TYPE_STRING:
                vi=add_string(strings,sidx,val)
                attr_specs.append(Attribute(ni,vi,0,TYPE_STRING))
            else:
                off=add_pool(typ,val)
                attr_specs.append(Attribute(ni,off,1,typ))
        attrs.extend(attr_specs)
        uses=0; vtype=TYPE_STRING if inner >= 0 else 0
        if vt_text is not None:
            vtype={
                'string': TYPE_STRING, 'int': TYPE_INT, 'uint': TYPE_UINT,
                'float': TYPE_FLOAT, 'color': TYPE_COLOR, 'matrix': TYPE_MATRIX,
                'vector3': TYPE_VECTOR3, 'bool': TYPE_BOOL,
            }.get(vt_text)
            if vtype is None:
                raise ValueError(f'Unknown _valuetype: {vt_text}')
            uses=1
            if vtype == TYPE_STRING:
                inner=add_string(strings,sidx,text) if text else -1
            else:
                _, val=parse_typed({
                    TYPE_INT:'_int:', TYPE_UINT:'_uint:', TYPE_FLOAT:'_float:',
                    TYPE_COLOR:'_color:', TYPE_MATRIX:'_matrix:',
                    TYPE_VECTOR3:'_vector3:', TYPE_BOOL:'_bool:'
                }[vtype] + text)
                inner=add_pool(vtype,val)
        return name_i,inner,uses,vtype,len(attr_specs)

    class T:
        __slots__=('elem','children','node','index')
        def __init__(self,e):
            self.elem=e; self.children=[T(c) for c in list(e)]; self.node=None; self.index=-1

    tree=T(root); levels=[[tree]]
    while True:
        nxt=[]
        for t in levels[-1]: nxt.extend(t.children)
        if not nxt: break
        levels.append(nxt)

    nodes=[]; attr_cursor=0
    for level in levels:
        for t in level:
            t.index=len(nodes)
            data=make_node(t.elem,attr_cursor)
            t.node=(data[0],data[1],data[2],data[3],attr_cursor,data[4])
            attr_cursor += data[4]
            nodes.append(t)

    node_objs=[]
    for t in nodes:
        name_i,inner,uses,vtype,ai,ac=t.node
        first=t.children[0].index if t.children else len(nodes)
        cc=len(t.children)
        node_objs.append(Node(name_i,inner,uses,vtype,first,cc,ai,ac))

    return _build_bxml_bytes(strings,pool,attrs,node_objs,version,unknown)


def _build_bxml_bytes(strings, pool, attrs, nodes, version, unknown) -> tuple[bytes, bytes]:
    """Build a BXML blob and return (file_bytes, decompressed_raw)."""
    raw = bytearray()
    for s in strings:
        raw.extend(s.encode('utf-8'))
        raw.append(0)
    pool_pointer = len(raw)
    raw.extend(pool)
    for a in attrs:
        raw.extend(ATTR.pack(a.name, a.value, a.uses_pool, a.value_type))
    for n in nodes:
        raw.extend(NODE.pack(n.name, n.inner, n.uses_pool, n.value_type,
                             n.level, n.children, n.attr_index, n.attr_count))
    raw_bytes = bytes(raw)
    comp = zlib.compress(raw_bytes)
    h = HEADER.pack(SIG, version, len(strings), pool_pointer, len(pool),
                    len(attrs), len(nodes), unknown, len(comp))
    return h + comp, raw_bytes

def _map_xml_to_source_nodes(root: ET.Element, source: Parsed) -> dict[int, ET.Element]:
    """Map each source node-table index to its corresponding XML element.

    BXML nodes are stored breadth-first/level-order, while XML is naturally
    serialized depth-first. Therefore a simple XML BFS comparison is wrong for
    nested scene data. The Node.level/children range gives us the exact mapping.
    """
    mapping: dict[int, ET.Element] = {}

    def visit(src_index: int, elem: ET.Element) -> None:
        if src_index in mapping:
            raise ValueError(f'Duplicate source node mapping at index {src_index}')
        mapping[src_index] = elem
        n = source.nodes[src_index]
        children = list(elem)
        if len(children) != n.children:
            raise ValueError(
                f'Node {src_index} ({source.strings[n.name]}) has {n.children} '
                f'BXML children, but XML has {len(children)} children.'
            )
        if n.children:
            first = n.level
            if first < 0 or first + n.children > len(source.nodes):
                raise ValueError(
                    f'Node {src_index} child range {first}:{first+n.children} is invalid'
                )
            for off, child in enumerate(children):
                visit(first + off, child)

    visit(0, root)
    if len(mapping) != len(source.nodes):
        missing=sorted(set(range(len(source.nodes))) - set(mapping))
        raise ValueError(f'XML does not cover all BXML nodes; missing indices: {missing[:10]}')
    return mapping


def _encode_xml_preserve_source(root: ET.Element, source: Parsed) -> bytes:
    """Re-encode XML while preserving source string table, pool and record layout.

    Unchanged values retain their original indexes/offsets, so decode -> encode
    can reproduce the original raw and compressed bytes. New/changed strings or
    pool values are appended rather than rewriting existing data.
    """
    elem_by_idx = _map_xml_to_source_nodes(root, source)

    strings=list(source.strings)
    sidx={s:i for i,s in enumerate(strings)}
    pool=bytearray(source.pool)

    def add_string_preserve(s: str) -> int:
        return add_string(strings, sidx, s)

    def typed_for_attr(text: str):
        return parse_typed(text)

    attrs=[]
    attr_cursor=0
    node_objs=[]

    # Iterate in source node-table order. The XML element corresponding to a
    # source node was found recursively from its child ranges above.
    for node_index, src_node in enumerate(source.nodes):
        elem = elem_by_idx[node_index]
        node_name_i = add_string_preserve(elem.tag)
        source_node_name = source.strings[src_node.name]
        if elem.tag != source_node_name:
            # Name changed: use the new string-table index. Node topology is unchanged.
            pass

        old_attrs=source.attrs[src_node.attr_index:src_node.attr_index+src_node.attr_count]
        old_by_name={source.strings[a.name]: a for a in old_attrs}
        # Keep XML attribute order. Existing names reuse their old record shape;
        # added names get new records.
        for k,vtext in elem.attrib.items():
            if k == '_valuetype':
                continue
            ni=add_string_preserve(k)
            typ,val=typed_for_attr(vtext)
            old=old_by_name.get(k)
            if old is not None and old.uses_pool and typ == old.value_type:
                old_val,_=pool_read(pool, old.value_type, old.value)
                if _raw_equal_value(typ, val, old.value_type, old_val):
                    attrs.append(Attribute(ni, old.value, old.uses_pool, old.value_type)); continue
            if old is not None and not old.uses_pool and typ == TYPE_STRING:
                old_val=source.strings[old.value]
                if val == old_val:
                    attrs.append(Attribute(ni, old.value, 0, TYPE_STRING)); continue
            if typ == TYPE_STRING:
                attrs.append(Attribute(ni, add_string_preserve(val), 0, TYPE_STRING))
            else:
                attrs.append(Attribute(ni, _pool_add_raw(pool, typ, val), 1, typ))

        vt_text=elem.attrib.get('_valuetype')
        text=(elem.text or '').strip()
        has_children=len(elem)>0

        uses=src_node.uses_pool
        vtype=src_node.value_type
        # Preserve the source node value mode when the XML still describes the
        # same kind. Otherwise, use the XML's explicit _valuetype.
        if vt_text is not None:
            wanted={
                'string': TYPE_STRING, 'int': TYPE_INT, 'uint': TYPE_UINT,
                'float': TYPE_FLOAT, 'color': TYPE_COLOR, 'matrix': TYPE_MATRIX,
                'vector3': TYPE_VECTOR3, 'bool': TYPE_BOOL,
            }.get(vt_text)
            if wanted is None:
                raise ValueError(f'Unknown _valuetype: {vt_text}')
            uses = src_node.uses_pool if src_node.uses_pool != 0 and src_node.value_type == wanted else 1
            vtype=wanted
        elif not has_children and text:
            uses=0; vtype=TYPE_STRING
        else:
            uses=src_node.uses_pool; vtype=src_node.value_type

        old_text = None
        if src_node.uses_pool:
            old_text,_ = pool_read(source.pool, src_node.value_type, src_node.inner)
        elif src_node.inner >= 0 and src_node.inner < len(source.strings):
            old_text = source.strings[src_node.inner]

        if uses:
            if vtype == TYPE_STRING:
                inner = add_string_preserve(text) if text else -1
                # A source string node has no pool value, despite ValueType=string.
            else:
                # Reuse source pool slot iff type/value is bit-identical.
                if src_node.uses_pool and src_node.value_type == vtype and old_text is not None:
                    _, new_val = parse_typed({
                        TYPE_INT:'_int:', TYPE_UINT:'_uint:', TYPE_FLOAT:'_float:',
                        TYPE_COLOR:'_color:', TYPE_MATRIX:'_matrix:',
                        TYPE_VECTOR3:'_vector3:', TYPE_BOOL:'_bool:'
                    }[vtype] + text)
                    if _raw_equal_value(vtype, new_val, src_node.value_type, old_text):
                        inner = src_node.inner
                    else:
                        inner = _pool_add_raw(pool, vtype, new_val)
                else:
                    _, new_val = parse_typed({
                        TYPE_INT:'_int:', TYPE_UINT:'_uint:', TYPE_FLOAT:'_float:',
                        TYPE_COLOR:'_color:', TYPE_MATRIX:'_matrix:',
                        TYPE_VECTOR3:'_vector3:', TYPE_BOOL:'_bool:'
                    }[vtype] + text)
                    inner = _pool_add_raw(pool, vtype, new_val)
        else:
            if not has_children:
                # Preserve the original string-table index when the source node
                # referenced a string, including the important empty-string case.
                # XML text is stripped by the decoder, so compare against the
                # stripped source string before deciding whether the value changed.
                if src_node.inner >= 0 and src_node.inner < len(source.strings):
                    old_str = source.strings[src_node.inner]
                    if text == old_str.strip():
                        inner = src_node.inner
                    else:
                        inner = add_string_preserve(text)
                elif text:
                    inner = add_string_preserve(text)
                else:
                    inner = -1
            else:
                inner = -1

        ai=attr_cursor
        ac=len([k for k in elem.attrib if k != '_valuetype'])
        attr_cursor += ac

        # Child indexing in the BFS node table is unchanged because shape was checked.
        first = node_index + 1 if list(elem) and False else None
        node_objs.append(Node(node_name_i, inner, uses, vtype,
                              0, len(list(elem)), ai, ac))

    # Child ranges are part of the original node table. Keep them byte-for-byte
    # identical unless a future structural-edit mode explicitly rebuilds them.
    for i, src_node in enumerate(source.nodes):
        n=node_objs[i]
        node_objs[i]=Node(n.name,n.inner,n.uses_pool,n.value_type,
                          src_node.level,src_node.children,n.attr_index,n.attr_count)

    return _build_bxml_bytes(strings,pool,attrs,node_objs,source.header.version,source.header.unknown)[0]


def encode_xml(xml_path:str, out_path:str, version:int=66538, unknown:int=0,
               verify_source:Optional[str]=None, source_bxml:Optional[str]=None):
    root=ET.parse(xml_path).getroot()
    source_path=source_bxml or verify_source
    if source_path:
        source=decode(source_path)
        blob = _encode_xml_preserve_source(root, source)

        # If the rebuilt decompressed payload is exactly identical to the
        # source payload, return the original file byte-for-byte. This keeps
        # the original zlib stream as well as every header byte.
        rebuilt_raw = zlib.decompress(blob[HEADER.size:])
        original_blob = Path(source_path).read_bytes()
        if rebuilt_raw == source.raw:
            blob = original_blob

        Path(out_path).write_bytes(blob)

        if verify_source:
            original = Path(verify_source).read_bytes()
            if blob != original:
                m = min(len(blob), len(original))
                at = next((i for i in range(m) if blob[i] != original[i]), m)
                raise ValueError(
                    f'Byte mismatch at offset {at}: '
                    f'original={original[at:at+16].hex()} new={blob[at:at+16].hex()}'
                )
        return

    blob=_encode_xml_fresh(root, version, unknown)
    Path(out_path).write_bytes(blob)
    if verify_source:
        src=decode(verify_source)
        if src.raw != zlib.decompress(blob[HEADER.size:]):
            raise ValueError('Raw data mismatch')


def inspect(path:str):
    p=decode(path); h=p.header
    print(f'Signature:   0x{h.signature:08X}')
    print(f'Version:     {h.version}')
    print(f'Strings:     {h.str_count}')
    print(f'PoolPointer: {h.pool_pointer}')
    print(f'PoolSize:    {h.pool_size}')
    print(f'Attributes:  {h.attr_count}')
    print(f'Nodes:       {h.node_count}')
    print(f'Compressed:  {h.zsize}')
    print(f'Raw size:    {len(p.raw)}')
    counts={}
    for a in p.attrs:
        key=('pool:'+TYPE_NAMES.get(a.value_type,str(a.value_type))) if a.uses_pool else 'string'
        counts[key]=counts.get(key,0)+1
    print('Attribute values:')
    for k,v in counts.items(): print(f'  {k:16} {v}')

def semantic_tree_from_bxml(path:str):
    # Convert decoded BXML to the same logical tree representation used for
    # XML comparisons. Formatting/indentation and string-table order are ignored.
    root=ET.fromstring(to_xml_text(decode(path)))
    def c(e):
        return (e.tag, tuple(sorted(e.attrib.items())), (e.text or '').strip(), tuple(c(x) for x in e))
    return c(root)

def semantic_tree_from_xml(path:str):
    root=ET.parse(path).getroot()
    def c(e):
        return (e.tag, tuple(sorted(e.attrib.items())), (e.text or '').strip(), tuple(c(x) for x in e))
    return c(root)

def main():
    ap=argparse.ArgumentParser(description='MX vs ATV Reflex BXML tool')
    sub=ap.add_subparsers(dest='cmd',required=True)
    d=sub.add_parser('decode'); d.add_argument('input'); d.add_argument('output')
    e=sub.add_parser('encode'); e.add_argument('input'); e.add_argument('output'); e.add_argument('--source'); e.add_argument('--verify-source')
    i=sub.add_parser('inspect'); i.add_argument('input')
    r=sub.add_parser('roundtrip'); r.add_argument('input'); r.add_argument('--keep-xml',action='store_true')
    a=ap.parse_args()
    try:
        if a.cmd=='decode': Path(a.output).write_text(to_xml_text(decode(a.input)),encoding='utf-8')
        elif a.cmd=='encode': encode_xml(a.input,a.output,verify_source=a.verify_source,source_bxml=a.source)
        elif a.cmd=='inspect': inspect(a.input)
        elif a.cmd=='roundtrip':
            p=Path(a.input); xml=p.with_suffix(p.suffix+'.roundtrip.xml'); out=p.with_suffix(p.suffix+'.roundtrip.bxml')
            xml.write_text(to_xml_text(decode(str(p))),encoding='utf-8')
            encode_xml(str(xml),str(out),source_bxml=str(p),verify_source=str(p))
            if semantic_tree_from_bxml(str(out)) != semantic_tree_from_bxml(str(p)):
                raise ValueError('Round-trip semantic comparison failed')
            if Path(out).read_bytes() != p.read_bytes():
                raise ValueError('Round-trip byte comparison failed')
            print(f'OK: byte-identical round-trip: {out}')
            if not a.keep_xml: xml.unlink()
    except Exception as ex:
        print(f'ERROR: {ex}',file=sys.stderr); return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
