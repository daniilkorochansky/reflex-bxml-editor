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
TYPE_VECTOR3 = 10
TYPE_BOOL = 11
TYPE_NAMES = {1:'string', 3:'int', 4:'uint', 5:'float', 10:'vector3', 11:'bool'}
PREFIXES = {'_uint:': TYPE_UINT, '_int:': TYPE_INT, '_float:': TYPE_FLOAT, '_vector3:': TYPE_VECTOR3, '_bool:': TYPE_BOOL}

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
    if off<0 or off>=len(pool): raise ValueError(f'Pool offset out of range: {off}')
    if typ==TYPE_INT:
        if off+4>len(pool): raise ValueError('Truncated int in pool')
        return struct.unpack_from('<i',pool,off)[0],4
    if typ==TYPE_UINT:
        if off+4>len(pool): raise ValueError('Truncated uint in pool')
        return struct.unpack_from('<I',pool,off)[0],4
    if typ==TYPE_FLOAT:
        if off+4>len(pool): raise ValueError('Truncated float in pool')
        return struct.unpack_from('<f',pool,off)[0],4
    if typ==TYPE_VECTOR3:
        if off+12>len(pool): raise ValueError('Truncated vector3 in pool')
        return struct.unpack_from('<3f',pool,off),12
    if typ==TYPE_BOOL:
        if off+4>len(pool): raise ValueError('Truncated bool in pool')
        v=struct.unpack_from('<I',pool,off)[0]
        return bool(v),4
    raise ValueError(f'Unsupported pool type {typ}')

def fmt_float(v:float)->str:
    # Close to Delphi's %f but avoid needlessly huge precision.
    return format(v,'.9g')

def value_to_text(v, typ:int)->str:
    if typ==TYPE_INT: return str(v)
    if typ==TYPE_UINT: return str(v)
    if typ==TYPE_FLOAT: return fmt_float(v)
    if typ==TYPE_VECTOR3: return ','.join(fmt_float(x) for x in v)
    if typ==TYPE_BOOL: return 'true' if v else 'false'
    return str(v)

def xml_escape_attr(s:str)->str:
    return s.replace('&','&amp;').replace('"','&quot;').replace('<','&lt;').replace('>','&gt;')

def to_xml_text(parsed:Parsed)->str:
    h=parsed.header; strings=parsed.strings; pool=parsed.pool; attrs=parsed.attrs; nodes=parsed.nodes
    lines=[]
    def emit_range(start:int,count:int,indent:int):
        for idx in range(start,start+count):
            n=nodes[idx]
            if n.name>=len(strings): raise ValueError(f'Invalid node string index {n.name}')
            line=' '*indent+'<'+strings[n.name]
            for j in range(n.attr_count):
                a=attrs[n.attr_index+j]
                if a.name>=len(strings): raise ValueError('Invalid attribute name index')
                name=strings[a.name]
                if a.uses_pool:
                    v,sz=pool_read(pool,a.value_type,a.value)
                    val=('_int:' if a.value_type==3 else '_uint:' if a.value_type==4 else '_float:' if a.value_type==5 else '_vector3:' if a.value_type==10 else '_bool:' if a.value_type==11 else '')+value_to_text(v,a.value_type)
                else:
                    if a.value>=len(strings): raise ValueError('Invalid attribute value index')
                    val=strings[a.value]
                line += f' {name}="{xml_escape_attr(val)}"'
            if n.uses_pool:
                line += f' _valuetype="{TYPE_NAMES.get(n.value_type, str(n.value_type))}"'
            if n.children==0 and n.inner==-1:
                lines.append(line+'/>'); continue
            lines.append(line+'>')
            if n.uses_pool:
                v,_=pool_read(pool,n.value_type, n.inner if n.inner>=0 else 0) if False else (None,0)
                # Node pool values are consumed sequentially, not by NInnerTextIndex.
                # Actual offset is assigned below using a mutable pool cursor.
                raise RuntimeError('internal: pool-node emission requires cursor')
            if n.children==0 and n.inner>=0:
                lines.append(' '*(indent+3)+strings[n.inner])
            if n.children:
                emit_range(n.level,n.children,indent+3)
            lines.append(' '*indent+'</'+strings[n.name]+'>')
    # The original decoder uses a global pool cursor for node values, after
    # attributes are inspected. We reproduce that traversal with an explicit cursor.
    pool_cursor=0
    def emit_range2(start:int,count:int,indent:int):
        nonlocal pool_cursor
        for idx in range(start,start+count):
            n=nodes[idx]; line=' '*indent+'<'+strings[n.name]
            for j in range(n.attr_count):
                a=attrs[n.attr_index+j]
                name=strings[a.name]
                if a.uses_pool:
                    v,sz=pool_read(pool,a.value_type,a.value)
                    val=('_int:' if a.value_type==3 else '_uint:' if a.value_type==4 else '_float:' if a.value_type==5 else '_vector3:' if a.value_type==10 else '_bool:' if a.value_type==11 else '')+value_to_text(v,a.value_type)
                else: val=strings[a.value]
                line += f' {name}="{xml_escape_attr(val)}"'
            if n.uses_pool: line += f' _valuetype="{TYPE_NAMES.get(n.value_type,str(n.value_type))}"'
            if n.children==0 and n.inner==-1:
                lines.append(line+'/>' ); continue
            lines.append(line+'>')
            if n.uses_pool:
                v,sz=pool_read(pool,n.value_type,pool_cursor); pool_cursor += sz
                lines.append(' '*(indent+3)+value_to_text(v,n.value_type))
            elif n.children==0 and n.inner>=0:
                lines.append(' '*(indent+6)+strings[n.inner])
            if n.children: emit_range2(n.level,n.children,indent+3)
            lines.append(' '*indent+'</'+strings[n.name]+'>')
    emit_range2(0,1,0)
    return '\n'.join(lines)+'\n'

def parse_typed(text:str):
    for p,t in PREFIXES.items():
        if text.startswith(p):
            body=text[len(p):]
            if t==TYPE_UINT:
                v=int(body)
                if v < 0 or v > 0xFFFFFFFF: raise ValueError(f'Invalid uint32: {text}')
                return t,v
            if t==TYPE_INT: return t,int(body)
            if t==TYPE_FLOAT: return t,float(body)
            if t==TYPE_VECTOR3:
                parts=[float(x.strip()) for x in body.split(',')]
                if len(parts)!=3: raise ValueError(f'vector3 requires 3 components: {text}')
                return t,tuple(parts)
            if t==TYPE_BOOL:
                if body.lower() in ('true','1'): return t,True
                if body.lower() in ('false','0'): return t,False
                raise ValueError(f'Invalid bool: {text}')
    return TYPE_STRING,text

def add_string(strings, index, s):
    if s not in index:
        index[s]=len(strings); strings.append(s)
    return index[s]

def encode_xml(xml_path:str, out_path:str, version:int=66538, unknown:int=0, verify_source:Optional[str]=None):
    root=ET.parse(xml_path).getroot()
    strings=[]; sidx={}; pool=bytearray(); attrs=[]

    def pool_add(typ,val):
        off=len(pool)
        if typ==TYPE_INT: pool.extend(struct.pack('<i',val))
        elif typ==TYPE_UINT: pool.extend(struct.pack('<I',val))
        elif typ==TYPE_FLOAT: pool.extend(struct.pack('<f',val))
        elif typ==TYPE_VECTOR3: pool.extend(struct.pack('<3f',*val))
        elif typ==TYPE_BOOL: pool.extend(struct.pack('<I',1 if val else 0))
        else: raise ValueError(f'Cannot put type {typ} in pool')
        return off

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
                attr_specs.append((ni,vi,0,TYPE_STRING,None))
            else:
                attr_specs.append((ni,None,1,typ,val))
        # Pool allocation for attributes is in node order, before node value.
        for ni,vi,up,typ,val in attr_specs:
            if up:
                off=pool_add(typ,val); attrs.append(Attribute(ni,off,1,typ))
            else:
                attrs.append(Attribute(ni,vi,0,typ))
        uses=0; vtype=0
        if vt_text is not None:
            vtype={'string':1,'int':3,'uint':4,'float':5,'vector3':10,'bool':11}.get(vt_text)
            if vtype is None: raise ValueError(f'Unknown _valuetype: {vt_text}')
            uses=1
            if vtype==TYPE_STRING:
                inner=add_string(strings,sidx,text) if text else -1
                pool_inner=inner
            else:
                _,val=parse_typed({'int':'_int:','uint':'_uint:','float':'_float:','vector3':'_vector3:','bool':'_bool:'}[TYPE_NAMES[vtype]]+text)
                pool_inner=pool_add(vtype,val)
            inner=pool_inner
        return name_i,inner,uses,vtype,len(attr_specs)

    # Build a temporary tree with the exact XML child order.
    class T:
        __slots__=('elem','children','node')
        def __init__(self,e): self.elem=e; self.children=[T(c) for c in list(e)]; self.node=None
    tree=T(root)

    # The BXML node table is level-order (breadth-first): all siblings on a
    # level are stored before their children. NLevelId points to the first
    # child in that flattened table.
    levels=[[tree]]
    while True:
        nxt=[]
        for t in levels[-1]: nxt.extend(t.children)
        if not nxt: break
        levels.append(nxt)

    # String-table/pool generation follows the same level order for node data.
    # Attribute records remain contiguous by node order.
    nodes=[]
    attr_cursor=0
    for level in levels:
        for t in level:
            name_i,inner,uses,vtype,ac=make_node(t.elem,attr_cursor)
            t.node=(name_i,inner,uses,vtype,attr_cursor,ac)
            attr_cursor += ac
            nodes.append(t)
    node_index={id(t):i for i,t in enumerate(nodes)}
    node_objs=[]
    for i,t in enumerate(nodes):
        name_i,inner,uses,vtype,ai,ac=t.node
        if t.children:
            first=node_index[id(t.children[0])]
            cc=len(t.children)
        else:
            first=len(nodes)
            cc=0
        node_objs.append(Node(name_i,inner,uses,vtype,first,cc,ai,ac))

    raw=bytearray()
    for s in strings: raw.extend(s.encode('utf-8')); raw.append(0)
    pool_pointer=len(raw); raw.extend(pool)
    for a in attrs: raw.extend(ATTR.pack(a.name,a.value,a.uses_pool,a.value_type))
    for n in node_objs: raw.extend(NODE.pack(n.name,n.inner,n.uses_pool,n.value_type,n.level,n.children,n.attr_index,n.attr_count))
    comp=zlib.compress(bytes(raw))
    h=HEADER.pack(SIG,version,len(strings),pool_pointer,len(pool),len(attrs),len(node_objs),unknown,len(comp))
    Path(out_path).write_bytes(h+comp)
    if verify_source:
        src=decode(verify_source)
        if src.raw!=bytes(raw):
            m=min(len(src.raw),len(raw)); at=next((i for i in range(m) if src.raw[i]!=raw[i]),m)
            raise ValueError(f'Round-trip raw mismatch at offset {at}: original={src.raw[at:at+16].hex()} new={bytes(raw)[at:at+16].hex()}')
        if src.compressed!=comp:
            raise ValueError('Raw data matches, but zlib stream differs')

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
    e=sub.add_parser('encode'); e.add_argument('input'); e.add_argument('output'); e.add_argument('--verify-source')
    i=sub.add_parser('inspect'); i.add_argument('input')
    r=sub.add_parser('roundtrip'); r.add_argument('input'); r.add_argument('--keep-xml',action='store_true')
    a=ap.parse_args()
    try:
        if a.cmd=='decode': Path(a.output).write_text(to_xml_text(decode(a.input)),encoding='utf-8')
        elif a.cmd=='encode': encode_xml(a.input,a.output,verify_source=a.verify_source)
        elif a.cmd=='inspect': inspect(a.input)
        elif a.cmd=='roundtrip':
            p=Path(a.input); xml=p.with_suffix(p.suffix+'.roundtrip.xml'); out=p.with_suffix(p.suffix+'.roundtrip.bxml')
            xml.write_text(to_xml_text(decode(str(p))),encoding='utf-8')
            encode_xml(str(xml),str(out))
            if semantic_tree_from_bxml(str(out)) != semantic_tree_from_bxml(str(p)):
                raise ValueError('Round-trip semantic comparison failed')
            print(f'OK: semantic round-trip: {out}')
            if not a.keep_xml: xml.unlink()
    except Exception as ex:
        print(f'ERROR: {ex}',file=sys.stderr); return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
