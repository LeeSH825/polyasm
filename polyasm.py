#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PolyASM: Customizable Assembler for Custom-Designed Processor:
  - Manage with function and memory blocks in input order.
  - Assign function addresses based on their order and size after instruction expansion.
  - Handle memory blocks with predefined or sequential offsets.
  - Ensure no overlapping between blocks.
  
Author : SungHo Lee
Date   : 2025-01-12

"""
version = "1.0.0"
# -------------------------------------------------
# pythen version : 3.12.8
# -------------------------------------------------

import sys
import time
import argparse
import re

import logging
from rich.console import Console, Group
from rich import box
from rich.table import Table
from rich.logging import RichHandler
from rich.panel import Panel
from rich.columns import Columns


# --------------------------------------------------
# map (opcode/flag/register)    -> Customizable
# --------------------------------------------------
opcode_map = {
    "jump":    "00010",
    "add": "00011",
    "setreg":  "00001",
    # ...
}
register_map = {
    "Jump_Register":    2,
    "R1":    5,
    "REG_MOD": 1,
    "REG_CMR": 2,
    # ...
}
flag_map = {        # -> also configurable with macro
    "REG_SET1": "00100000",  # => 0x20
    "REG_SET2": "00010000",  # => 0x10
    # ...
}

MAX_PASS = 10   # for resolve_symbols

class AsmError(Exception):
    pass

# --------------------------------------------------
# Definition of Block
# --------------------------------------------------
class BlockType:
    FUNCTION = "function"
    MEMORY = "memory"
    MACRO = "macro"

class Block:
    def __init__(self, type, name, lineno):
        self.type = type          # 'function' or 'memory' or 'macro'
        self.name = name          # name of the block
        self.lineno = lineno      # line number where the block starts
        self.content = []         # IRLine list (instruction/data/alias)
        self.start_addr = None    # start address (int) or None
        self.size = 0             # size of the block (int) or 0
# --------------------------------------------------
# Symbol Table: alias/function/macro
# --------------------------------------------------
class SymbolTable:
    def __init__(self):
        # alias_map: { alias_name: address (int) or None }
        self.alias_map = {}
        self.reverse_alias_map = {}
        # function_map: { func_name: address (int) or None }
        self.function_map = {}
        # macro_map: { macro_name: value (int) }
        self.macro_map = {}

    def define_alias(self, aname, addr, lineno):
        old = self.alias_map.get(aname, None)
        if old is not None:
            if old != addr and addr is not None:
                # error: redefined alias
                raise AsmError(f"line {lineno}: Alias '{aname}' redefined: old=0x{old:X}, new=0x{addr:X}")
            else:
                # warning: redefined with the same address
                if old == addr:
                    if g_debug:
                        # console.print(f"[bold yellow][WARNING][/bold yellow] line {lineno}: Alias '{aname}' redefined with the same address 0x{addr:X}. Ignoring.")
                        logger.warning(f"line {lineno}: Alias '{aname}' redefined with the same address 0x{addr:X}. Ignoring.")
        else:
            # define new alias
            self.alias_map[aname] = addr
            if addr not in self.reverse_alias_map:
                self.reverse_alias_map[addr] = []
            self.reverse_alias_map[addr].append(aname)

    def get_alias_addr(self, aname):
        return self.alias_map.get(aname, None)

    def get_alias_name(self, addr):
        return self.reverse_alias_map.get(addr, [])

    def define_function(self, fname, addr):
        old = self.function_map.get(fname, None)
        # error: redefined function
        if old is not None and old != addr and addr is not None:
            raise AsmError(f"Function '{fname}' redefined: old=0x{old:X}, new=0x{addr:X}")
        self.function_map[fname] = addr

    def get_function_addr(self, fname):
        return self.function_map.get(fname, None)
    
    def define_macro(self, mname, value):
        old = self.macro_map.get(mname, None)
        # error: redefined macro
        if old is not None and old != value:
            raise AsmError(f"Macro '{mname}' redefined: old=0x{old:X}, new=0x{value:X}")
        self.macro_map[mname] = value

    def get_macro_value(self, mname):
        return self.macro_map.get(mname, None)

# --------------------------------------------------
# IR 
# --------------------------------------------------
class IRType:
    MACRO         = 0   # Macro definition
    MEMORY_DATA   = 1   # Data line in memory block
    ALIAS         = 2   # Alias definition
    FUNC_DEFINE   = 3   # Function definition
    INSTRUCTION   = 4   # Instruction line in function block

class IRLine:
    def __init__(self, t, content, lineno):
        self.type = t
        self.content = content
        self.mem = None
        self.lineno = lineno
        self.expanded_bits = [] # expanded 32-bit instruction
        self.addresses = []    # list of addresses of expanded instructions
        # Instruction details
        self.func = None
        self.opcode = None
        self.param1 = None
        self.param2 = None
        self.param3 = None

# --------------------------------------------------
# parse_input_to_blocks
# --------------------------------------------------
def parse_input_to_blocks(lines, symbol_table, verbose=False):
    """
    read input file and create a list of blocks.
    - function block: starts with "function something():"
    - memory block: starts with "#memory something:"
    - block contains content until next function or memory block.
    macro definition is also handled here.

    return: list of blocks
    """
    blocks = []
    current_block = None
    unnamed_memory_count = 0
    internal_line_index = 0
    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        # handle comments
        if '//' in line:
            line = line.split('//')[0].rstrip()
        if not line:
            continue

        # handle macro definition
        # ex) "#macro BOOT_ADDR 0x100"
        if line.startswith("#macro"):
            parts = line.split()
            if len(parts) != 3:
                raise AsmError(f"line {idx}: Invalid macro definition")
            mname = parts[1]
            mvalue = parse_any_int(parts[2])
            symbol_table.define_macro(mname, mvalue)
            if g_debug:
                logger.debug(f"line {idx}: Defined macro '{mname}' with value 0x{mvalue:X}")
            continue

        # handle memory block
        # ex) "#memory Boot_Param:"
        if line.startswith("#memory"):
            internal_line_index = 0
            parts = line.split()
            if len(parts) == 1:
                # has no memory name
                unnamed_memory_count += 1
                mname = f"Unnamed_Memory_{unnamed_memory_count}"
                if verbose:
                    logger.warning(f"line {idx}: no memory name => {mname}")
            else:
                mname = parts[1].rstrip(":")
                if not mname:
                    unnamed_memory_count +=1
                    mname = f"Unnamed_Memory_{unnamed_memory_count}"
                    if verbose:
                        logger.warning(f"line {idx}: empty memory name => {mname}")

            # start a new memory block
            current_block = Block(BlockType.MEMORY, mname, idx)
            blocks.append(current_block)
            if g_debug:
                logger.debug(f"line {idx}: Started memory block '{mname}'")
            continue

        # handle function block
        # ex) "function boot():"
        if line.startswith("function"):
            internal_line_index = 0
            after_func = line[len("function"):].strip()
            fname = after_func.split("(")[0].strip().rstrip(":")
            if not fname:
                raise AsmError(f"line {idx}: Function name missing.")
            # start a new function block
            current_block = Block(BlockType.FUNCTION, fname, idx)
            blocks.append(current_block)
            # register to symbol table (initially None)
            symbol_table.define_function(fname, None)
            if g_debug:
                logger.debug(f"line {idx}: Started function block '{fname}'")
            continue

        # handle instruction or data line (or alias) in function or memory block
        match = re.search(r"#alias\s+(.+)$", line)
        tokens = line.split()
        maybe_opc = tokens[0].lower()
        if maybe_opc in opcode_map:
            # instruction line
            if match:
                line = line.split('#alias')[0].strip()
            irline = IRLine(IRType.INSTRUCTION, line, idx)
            if current_block is None:
                # if no block, create a default function block 'main'
                current_block = Block(BlockType.FUNCTION, "main", idx)
                blocks.append(current_block)
                if verbose:
                    logger.warning(f"line {idx}: Started default function block 'main'")
            current_block.content.append(irline)
            internal_line_index += 1
            if g_debug:
                logger.debug(f"line {idx}: Added instruction to block '{current_block.name}'")
        else:
            # data line
            if match:
                line = line.split('#alias')[0].strip()
            irline = IRLine(IRType.MEMORY_DATA, line, idx)
            if current_block is None or current_block.type != BlockType.MEMORY:
                # if no block or not memory block, create a default memory block
                unnamed_memory_count +=1
                mname = f"Unnamed_Memory_{unnamed_memory_count}"
                current_block = Block(BlockType.MEMORY, mname, idx)
                blocks.append(current_block)
                if verbose:
                    logger.warning(f"line {idx}: Started unnamed memory block '{mname}'")
            current_block.content.append(irline)
            internal_line_index += 1
            if g_debug:
                logger.debug(f"line {idx}: Added data to memory block '{current_block.name}'")

        # handle alias definition
        # ex) "#alias ALIAS_NAME"
        if match:
            aliases = match.group(1).strip().split()
            for a in aliases:
                if a == "":
                    raise AsmError(f"line {idx}: Empty alias name.")
                irline = IRLine(IRType.ALIAS, a, internal_line_index - 1) # Corrected
                current_block.content.append(irline)
                if g_debug:
                    logger.debug(f"line {idx}: Added alias '{a}' to block '{current_block.name}'")
    return blocks

# --------------------------------------------------
# Overlap check
# --------------------------------------------------
def check_block_overlap(blocks):
    """
    Check for overlaps between blocks.

    return: None if no overlap, raise AsmError if overlap.
    """
    sorted_blocks = sorted(blocks, key=lambda b: b.start_addr if b.start_addr is not None else 0)
    for i in range(len(sorted_blocks)):
        b1 = sorted_blocks[i]
        if b1.start_addr is None:
            continue
        b1_min = b1.start_addr
        b1_max = b1.start_addr + b1.size -1
        for j in range(i+1, len(sorted_blocks)):
            b2 = sorted_blocks[j]
            if b2.start_addr is None:
                continue
            b2_min = b2.start_addr
            b2_max = b2.start_addr + b2.size -1
            if not (b1_max < b2_min or b1_min > b2_max):
                raise AsmError(f"Block '{b1.name}' (0x{b1_min:X}-0x{b1_max:X}) overlaps with '{b2.name}' (0x{b2_min:X}-0x{b2_max:X})")
            
def check_section_overlap(code_start, code_size, data_start, data_size):
    """
    Check for overlap between Code section and Data section.

    return: None if no overlap, raise AsmError if overlap.
    """
    code_min = code_start
    code_max = code_start + code_size - 1
    data_min = data_start
    data_max = data_start + data_size - 1

    # if code section is after data section
    if code_max < data_min:
        return
    # if data section is after code section
    elif data_max < code_min:
        return
    else:
        raise AsmError(f"Code section (0x{code_min:X}-0x{code_max:X}) overlaps with Data section (0x{data_min:X}-0x{data_max:X})")


# --------------------------------------------------
# resolve_symbols: multi-pass
# --------------------------------------------------
def resolve_symbols(blocks, symbol_table, code_start=0x0, data_start=0x50, opcode_width=5, param1_width=14, param2_width=5, param3_width=6, verbose=False):
    """
    Resolve symbols in the block list and assign sizes and addresses to blocks.

    return: True if any block size or address has changed.
    """
    updated = False
    current_code_address = code_start
    current_data_address = data_start

    for block in blocks:
        if block.type == BlockType.FUNCTION:
            # set the start address of the function block to the current address
            if symbol_table.get_function_addr(block.name) is None:
                symbol_table.define_function(block.name, current_code_address)
                block.start_addr = current_code_address
                if g_debug:
                    logger.debug(f"Assigned function '{block.name}' to address 0x{current_code_address:X}")
            else:
                # if there is a predefined address, use it (if overlaps with previous block, use the last address of the block)
                block.start_addr = symbol_table.get_function_addr(block.name)
                if g_debug:
                    logger.debug(f"Function '{block.name}' has predefined address 0x{block.start_addr:X}")

            # save the previous size and initialize the block size
            initial_size = block.size
            block.size = 0 # initialize the block size

            # handle instructions
            for irline in block.content:
                if irline.type == IRType.INSTRUCTION:
                    opc = irline.content.split()[0].lower()
                    opcode_bits = opcode_map.get(opc)
                    if opcode_bits is None:
                        raise AsmError(f"line {irline.lineno}: unknown opcode '{opc}'")
                    if len(opcode_bits) != opcode_width:
                        raise AsmError(f"line {irline.lineno}: opcode '{opc}' width mismatch: expected {opcode_width} bits but got {len(opcode_bits)} bits")
                    
                    # Parse parameters
                    param_toks = irline.content.split()[1:]
                    p1, p2, p3 = parse_three_params(param_toks, symbol_table)
                    irline.opcode = opc
                    irline.param1 = p1
                    irline.param2 = p2
                    irline.param3 = p3
                    irline.func = block.name

                    if g_debug:
                        logger.debug(f"line {irline.lineno}: Parsed instruction '{opc}' with params {p1}, {p2}, {p3}")
                    # expand instruction
                    expansions = expand_instr(opcode_bits, p1, p2, p3, opcode_width, param1_width, param2_width, param3_width)
                    irline.expanded_bits = expansions
                    irline.addresses = list(range(current_code_address, current_code_address + len(expansions)))
                    block.size += len(expansions)
                    current_code_address += len(expansions)

                    if g_debug:
                        logger.debug(f"Instruction '{opc}' in function '{block.name}' expanded to {len(expansions)} instructions at addresses 0x{irline.addresses[0]:X}-0x{irline.addresses[-1]:X}")
                elif irline.type == IRType.ALIAS:
                    alias_name = irline.content
                    line_index = irline.lineno
                    alias_addr = block.start_addr + line_index
                    try:
                        if symbol_table.get_alias_addr(alias_name) is None:
                            symbol_table.define_alias(alias_name, alias_addr, irline.lineno)
                            if g_debug:
                                logger.debug(f"Alias '{alias_name}' set to address 0x{alias_addr:X} (Block '{block.name}', Line {line_index + 1})")
                            updated = True
                    except AsmError as e:
                        raise e
            # check if the block size has changed
            if block.size != initial_size:
                if g_debug:
                    logger.debug(f"Function block '{block.name}' size changed: {initial_size} -> {block.size}")
                updated = True
        elif block.type == BlockType.MEMORY:
            # set the start address of the memory block to the current address
            block.start_addr = current_data_address
            if g_debug:
                logger.debug(f"Assigned memory block '{block.name}' to address 0x{block.start_addr:X}")
            
            # save the previous size and initialize the block size
            initial_size = block.size
            block.size = 0  # initialize the block size

            # handle data lines
            for irline in block.content:
                if irline.type == IRType.MEMORY_DATA:
                    irline.mem = block.name
                    data_bits = parse_data_line(irline.content, irline.lineno, symbol_table)
                    irline.expanded_bits = data_bits
                    irline.addresses = list(range(current_data_address, current_data_address + len(data_bits)))
                    block.size += len(data_bits)
                    current_data_address += len(data_bits)
                    if g_debug:
                        logger.debug(f"Added {len(data_bits)} data lines to memory block '{block.name}' at addresses 0x{irline.addresses[0]:X}-0x{irline.addresses[-1]:X}")
                elif irline.type == IRType.ALIAS:
                    alias_name = irline.content
                    line_index = irline.lineno
                    alias_addr = block.start_addr + line_index
                    try:
                        if symbol_table.get_alias_addr(alias_name) is None:
                            symbol_table.define_alias(alias_name, alias_addr, irline.lineno)
                            if g_debug:
                                logger.debug(f"Alias '{alias_name}' set to address 0x{alias_addr:X} (Block '{block.name}', Line {line_index + 1})")
                            updated = True
                    except AsmError as e:
                        raise e
            # check if the block size has changed
            if block.size != initial_size:
                if g_debug:
                    logger.debug(f"Function block '{block.name}' size changed: {initial_size} -> {block.size}")
                updated = True
    return updated

    # --------------------------------------------------
    # parse_three_params
    # --------------------------------------------------
def parse_three_params(ptoks, symtbl):

    """
    parse the parameter tokens and return three integers.

    return : tuple of three integers
    """
    p = [0, 0, 0]
    for i in range(min(3, len(ptoks))):
        p[i] = parse_one_param(ptoks[i], symtbl)
    return tuple(p)

def parse_one_param(tok, symtbl):
    """
    parse a single parameter and return an integer.

    return : integer
    """
    t = tok.strip()
    if t == "[]":
        return 0
    if t.startswith("[") and t.endswith("]"):
        inside = t[1:-1].strip()
        if inside.endswith("():"):
            # Handle [function():]
            fname = inside[:-3].strip()
            faddr = symtbl.get_function_addr(fname)
            if faddr is None:
                return 0
            return faddr
        elif inside.startswith("#"):
            # Handle [#macro]
            mname = inside[1:]
            mval = symtbl.get_macro_value(mname)
            if mval is None:
                return 0
            return mval
        elif inside.startswith("@"):
            # Handle [@alias]
            an = inside[1:]
            aaddr = symtbl.get_alias_addr(an)
            if aaddr is None:
                return 0
            return aaddr
        else:
            # handle register or immediate value
            if inside in register_map:
                return register_map[inside]
            elif inside in symtbl.macro_map:
                return symtbl.macro_map[inside]
            else:
                return parse_any_int(inside)
    else:
        return parse_any_int(t)

def parse_any_int(x):
    """
    parse a string to an integer. support 0b, 0x, or decimal.

    return : integer
    """
    x = x.strip().replace("_","").replace("\"","")
    if x.lower().startswith("0b"):
        if len(x) == 2:
            raise AsmError("'0b' but no digits")
        return int(x[2:], 2)
    elif x.lower().startswith("0x"):
        if len(x) == 2:
            raise AsmError("'0x' but no digits")
        return int(x, 16)
    else:
        if x == "":
            raise AsmError("empty string for parse_any_int")
        return int(x, 10)

# --------------------------------------------------
# expand_instr
# --------------------------------------------------
def expand_instr(opcode_bits, p1, p2, p3, opcode_width=5, param1_width=14, param2_width=5, param3_width=6):
    """
    expand the given opcode and parameters into 32-bit bitstrings.

    - opcode_bits: 5-bit string (LSB first)
    - p1: first parameter (Default: 14-bit)
    - p2: second parameter (Default: 5-bit)
    - p3: third parameter (Default: 6-bit)

    return: list of 32-bit bitstrings
    """
    # convert opcode_bits to an array of bits (LSB first)
    op_arr = [int(x) for x in reversed(opcode_bits)]
    
    # convert each parameter to an array of bits (LSB first)
    arr1 = int_to_lsb_array(p1)  # p1: 14-bit(default)
    arr2 = int_to_lsb_array(p2)  # p2: 5-bit(default)
    arr3 = int_to_lsb_array(p3)  # p3: 6-bit(default)
    
    result = []
    
    while True:
        out31 = [0] * (opcode_width + param1_width + param2_width + param3_width + 1)  # initialize 31-bit array(Default)
        # if g_debug:
        #     logger.debug(f"arr1, arr2, arr3, op_arr  : {arr1}, {arr2}, {arr3}, {op_arr}")
        
        # opcode (Default: 5-bit)
        for i in range(opcode_width):
            if op_arr:
                out31[i] = op_arr.pop(0)
            else:
                out31[i] = 0

        # param1 (Default: 14-bit)
        for i in range(param1_width):
            if arr1:
                out31[opcode_width + i] = arr1.pop(0)
            else:
                out31[opcode_width + i] = 0

        # param2 (Default: 5-bit)
        for i in range(param2_width):
            if arr2:
                out31[opcode_width + param1_width + i] = arr2.pop(0)
            else:
                out31[opcode_width + param1_width + i] = 0

        # param3 (Default: 6-bit)
        for i in range(param3_width):
            if arr3:
                out31[opcode_width + param1_width + param2_width + i] = arr3.pop(0)
            else:
                out31[opcode_width + param1_width + param2_width + i] = 0
        
        # cbit: continue bit (1 if any of arr1, arr2, arr3 has bits left)
        if arr1 or arr2 or arr3:
            cbit = 1
        else:
            cbit = 0
        out31[opcode_width + param1_width + param2_width + param3_width] = cbit  # set cbit at the end
        
        # pbit: parity bit (Even Parity)
        ones = sum(out31[:opcode_width + param1_width + param2_width + param3_width + 1])
        pbit = ones % 2
        full32 = out31 + [pbit]  # set pbit at the end
        
        # convert the bit array to a string (MSB first)
        bitstr = "".join(str(b) for b in reversed(full32))

        result.append(bitstr)
        if (not arr1) and (not arr2) and (not arr3):
            break
        
    return result

def int_to_lsb_array(v):
    """
    convert the given integer to a bit array from LSB to MSB.

    return: bit array (LSB first)
    """
    r = []
    while v > 0:
        r.append(v & 1)
        v >>= 1
    return r

# --------------------------------------------------
# parse_data_line
# --------------------------------------------------
def parse_data_line(line_str, lineno, symtbl):
    """
    parse the data line and return a list of 32-bit bitstrings.

    return: list of 32-bit bitstrings
    """
    s = line_str.strip()
    
    # Case 1: Quoted data
    if '"' in s:
        # split the line by quoted strings
        # Ex: "0b00010010" "0x11" "REG_SET1" "REG_SET2"
        parts = re.findall(r'"([^"]+)"', s)
        if len(parts) != 4:
            raise AsmError(f"line {lineno}: Expected 4 quoted strings for data line, got {len(parts)}")
        
        bitstrings = []
        for part in parts:
            part = part.strip()
            bitstrings.append(parse_8_bit_data(part, lineno, symtbl))
        
        # conbine 4 8-bit bitstrings to create a 32-bit bitstring
        full_bitstr = ''.join(bitstrings)
        return [full_bitstr]
    else:
        # Case 2: Binary string without quotes
        # Ex: 0b00010010 0x11 REG_SET1 REG_SET2
        bits = ''.join(s.split())
        if not all(c in '01' for c in bits):
            raise AsmError(f"line {lineno}: Non-binary characters in data line.")
        
        if len(bits) == 32:
            return [bits]
        elif len(bits) < 32:
            # if bits are less than 32, pad with '0's
            padded_bits = bits.ljust(32, '0')
            logger.warning(f"line {lineno}: Binary data line has fewer than 32 bits. Padding with '0's.")
            return [padded_bits]
        else:
            raise AsmError(f"line {lineno}: Binary data line exceeds 32 bits.")

def parse_8_bit_data(part, lineno, symtbl):
    if ("|" in part) or ("&" in part) or ("^" in part) or ("~" in part) or ("+" in part) or ("-" in part):
        # handle flag combination with operators
        # Ex: "REG_SET1|REG_SET2", "REG_SET1&REG_SET2", "REG_SET1^REG_SET2", "REG_SET1~REG_SET2", "REG_SET1+REG_SET2", "REG_SET1-REG_SET2"
        operators = re.findall(r'[|&^~+-]', part)
        flags = re.split(r'[|&^~+-]', part)
        if g_debug:
            logger.debug(f"line {lineno}: Flag combination '{part}' with operators '{operators}'")
        byte = 0
        for i, flag in enumerate(flags):
            flag = flag.strip()
            if flag in symtbl.macro_map:  # if flag is a macro
                bits = bin(symtbl.get_macro_value(flag))[2:]  # remove '0b'
            elif flag in flag_map:  # if flag is a predefined flag
                bits = flag_map[flag].replace(" ", "")
            else:
                raise AsmError(f"line {lineno}: Unknown flag '{flag}'")
            if len(bits) > 8:
                raise AsmError(f"line {lineno}: Flag '{flag}({bits})' exceeds 8 bits.")
            flag_value = int(bits, 2)
            if i == 0:
                byte = flag_value
            else:
                operator = operators[i - 1]
                if operator == '|':
                    byte |= flag_value
                elif operator == '&':
                    byte &= flag_value
                elif operator == '^':
                    byte ^= flag_value
                elif operator == '~':
                    byte = ~byte & 0xFF  # apply bitwise NOT and mask to 8 bits
                elif operator == '+':
                    byte += flag_value
                elif operator == '-':
                    byte -= flag_value
        if byte >= 256:
            raise AsmError(f"line {lineno}: Flag combination '{part}({byte})' exceeds 8 bits.")
        bitstr = "{:08b}".format(byte)
    elif part.startswith("0b"):
        # handle binary literal
        # Ex: "0b00010010"
        bits = part[2:]
        if len(bits) > 8:
            raise AsmError(f"line {lineno}: Binary literal '{part}({bits})' exceeds 8 bits.")
        if not all(c in '01' for c in bits):
            raise AsmError(f"line {lineno}: Invalid binary literal '{part}'.")
        byte = int(bits, 2)
        bitstr = "{:08b}".format(byte)
    elif part.startswith("0x"):
        # handle hexadecimal literal
        # Ex: "0x11"
        try:
            byte = int(part, 16)
        except ValueError:
            raise AsmError(f"line {lineno}: Invalid hexadecimal literal '{part}'.")
        if byte >= 256:
            raise AsmError(f"line {lineno}: Hex literal '{part}({byte})' exceeds 8 bits.")
        bitstr = "{:08b}".format(byte)
    else:
        # handle decimal literal or macro
        # Ex: "21" or "MACRO_NAME"
        try:
            if part in symtbl.macro_map:
                byte = symtbl.get_macro_value(part)
            else:
                byte = int(part, 10)
        except ValueError:
            raise AsmError(f"line {lineno}: Invalid decimal literal '{part}'.")
        if byte >= 256:
            if part in symtbl.macro_map:
                raise AsmError(f"line {lineno}: Macro '{part}({byte})' exceeds 8 bits.")
            else:
                raise AsmError(f"line {lineno}: Decimal literal '{part}({byte})' exceeds 8 bits.")
        bitstr = "{:08b}".format(byte)
    return bitstr

def parse_8bit(x):
    """
    parse a string to an 8-bit integer.
    
    return: 8-bit integer
    """
    return parse_any_int(x) & 0xFF

def format_32bit(val):
    """
    format an integer as a 32-bit binary string.
    
    return: 32-bit binary string
    """
    return "{:032b}".format(val)

# --------------------------------------------------
# emit_files
# --------------------------------------------------
def emit_files(blocks, symtbl, outbitfile, outread=None, endian='big', fmt='hex', verbose=False):
    """
    Emit output files based on the block list.
    - outbitfile: text file with '0's and '1's
    - outread: human-readable text file (optional)
    """
    # Find the maximum address
    max_addr = -1
    for block in blocks:
        for irline in block.content:
            for addr in irline.addresses:
                if addr > max_addr:
                    max_addr = addr

    mem_size = max_addr + 1
    memory = ["0"*32 for _ in range(mem_size)]

    for block in blocks:
        for irline in block.content:
            if irline.expanded_bits and irline.addresses:
                if len(irline.expanded_bits) != len(irline.addresses):
                    raise AsmError("mismatch expanded_bits vs addresses.")
                for bstr, ad in zip(irline.expanded_bits, irline.addresses):
                    if ad >= mem_size:
                        raise AsmError(f"Address 0x{ad:X} out of memory bounds.")
                    memory[ad] = bstr

    # Write bitstring text file
    with open(outbitfile, "w", encoding="utf-8") as f:
        for bitstr in memory:
            f.write(bitstr + "\n")
    if verbose:
        logger.info(f"Wrote bitstring text file => {outbitfile}, size={mem_size*33} bytes (including newline)")

    # Write readable text file
    if outread:
        with open(outread, "w", encoding="utf-8") as rf:
            for block in blocks:
            # Write function block
                for irline in block.content:
                    if not irline.expanded_bits or not irline.addresses:
                        continue
                    for bstr, ad in zip(irline.expanded_bits, irline.addresses):
                        if block.type == BlockType.FUNCTION:
                            # if g_debug:
                                # print("[DEBUG] bstr  : ", bstr, block.type, irline.type, bstr[0], bstr[31])
                            # Extract bits
                            pbit = bstr[0]
                            cbit = bstr[1]
                            p3 = bstr[2:8]
                            p2 = bstr[8:13]
                            p1 = bstr[13:27]
                            # Depending on the IRLine type
                            if block.type == BlockType.FUNCTION and irline.type == IRType.INSTRUCTION:
                                func = irline.func if irline.func else "None"
                                opcode = irline.opcode if irline.opcode else "unknown"
                                param1 = irline.param1
                                param2 = irline.param2
                                param3 = irline.param3
                                aliases = symtbl.get_alias_name(ad)
                                alias_str =  ", ".join(aliases) if aliases else "None"
                                if fmt == "hex": # Format params in hexadecimal
                                    param1_hex = f"0x{param1:X}"
                                    param2_hex = f"0x{param2:X}"
                                    param3_hex = f"0x{param3:X}"
                                    if aliases:
                                        line_str = f"{ad:05x} | p={pbit} c={cbit} p3={p3} p2={p2} p1={p1} | func={func}, opcode={opcode}, param1={param1_hex}, param2={param2_hex}, param3={param3_hex} <- alias: {alias_str}"
                                    else:
                                        line_str = f"{ad:05x} | p={pbit} c={cbit} p3={p3} p2={p2} p1={p1} | func={func}, opcode={opcode}, param1={param1_hex}, param2={param2_hex}, param3={param3_hex}"
                                elif fmt == "dec": # Format params in decimal
                                    param1_dec = str(param1)
                                    param2_dec = str(param2)
                                    param3_dec = str(param3)
                                    if aliases:
                                        line_str = f"{ad:05x} | p={pbit} c={cbit} p3={p3} p2={p2} p1={p1} | func={func}, opcode={opcode}, param1={param1_dec}, param2={param2_dec}, param3={param3_dec} <- alias: {alias_str}"
                                    else:
                                        line_str = f"{ad:05x} | p={pbit} c={cbit} p3={p3} p2={p2} p1={p1} | func={func}, opcode={opcode}, param1={param1_dec}, param2={param2_dec}, param3={param3_dec}"
                                else:
                                    if aliases:
                                        line_str = f"{ad:05x} | p={pbit} c={cbit} p3={p3} p2={p2} p1={p1} | func={func}, opcode={opcode}, param1={param1}, param2={param2}, param3={param3} <- alias: {alias_str}"
                                    else:
                                        line_str = f"{ad:05x} | p={pbit} c={cbit} p3={p3} p2={p2} p1={p1} | func={func}, opcode={opcode}, param1={param1}, param2={param2}, param3={param3}"
                            rf.write(line_str + "\n")
            for block in blocks:
            # Write memory block
                for irline in block.content:
                    for bstr, ad in zip(irline.expanded_bits, irline.addresses):
                        if block.type == BlockType.MEMORY and irline.type == IRType.MEMORY_DATA:
                            mname = irline.mem if irline.mem else "None"
                            byte0 = bstr[24:32]
                            byte1 = bstr[16:24]
                            byte2 = bstr[8:16]
                            byte3 = bstr[0:8]
                            aliases = symtbl.get_alias_name(ad)
                            alias_str =  ", ".join(aliases) if aliases else "None"
                            if fmt == "hex": # Format params in hexadecimal
                                byte0_hex = f"0x{int(byte0, 2):02X}"
                                byte1_hex = f"0x{int(byte1, 2):02X}"
                                byte2_hex = f"0x{int(byte2, 2):02X}"
                                byte3_hex = f"0x{int(byte3, 2):02X}"
                                if aliases:
                                    line_str = f"{ad:05x} | {byte3} {byte2} {byte1} {byte0} | mem={mname}, {byte3_hex} {byte2_hex} {byte1_hex} {byte0_hex} <- alias: {alias_str}"
                                else:
                                    line_str = f"{ad:05x} | {byte3} {byte2} {byte1} {byte0} | mem={mname}, {byte3_hex} {byte2_hex} {byte1_hex} {byte0_hex}"
                            elif fmt == "dec": # Format params in decimal
                                byte0_dec = str(int(byte0, 2))
                                byte1_dec = str(int(byte1, 2))
                                byte2_dec = str(int(byte2, 2))
                                byte3_dec = str(int(byte3, 2))
                                if aliases:
                                    line_str = f"{ad:05x} | {byte3} {byte2} {byte1} {byte0} | mem={mname}, {byte3_dec} {byte2_dec} {byte1_dec} {byte0_dec} <- alias: {alias_str}"
                                else:
                                    line_str = f"{ad:05x} | {byte3} {byte2} {byte1} {byte0} | mem={mname}, {byte3_dec} {byte2_dec} {byte1_dec} {byte0_dec}"
                            else:
                                if aliases:
                                    line_str = f"{ad:05x} | {byte3} {byte2} {byte1} {byte0} | mem={mname}, {int(byte3, 2)} {int(byte2, 2)} {int(byte1, 2)} {int(byte0, 2)} <- alias: {alias_str}"
                                else:
                                    line_str = f"{ad:05x} | {byte3} {byte2} {byte1} {byte0} | mem={mname}, {int(byte3, 2)} {int(byte2, 2)} {int(byte1, 2)} {int(byte0, 2)}"
                            rf.write(line_str + "\n")
            if verbose:
                logger.info(f"Wrote readable text file => {outread}")

# --------------------------------------------------
# main
# --------------------------------------------------
def main():
    start_time = time.time()

    # 0) Parse arguments
    global args
    parser = argparse.ArgumentParser(description="PolyASM: Customizable Assembler for Custom-Designed Processor")
    parser.add_argument("-i","--input",required=True,
                        help="Input assembly file path.")
    parser.add_argument("-o","--output",required=True,
                        help="Output bitstring text file path.")
    parser.add_argument("-m","--memory_offset",type=str,default="code=0,data=0x50",
                        help="Override default memory section offsets. Format: code=<value>,data=<value>. Example(Default): -m code=0,data=0x50")
    parser.add_argument("-w", "--field_width", type=str, default="opcode=5,param1=14,param2=5,param3=6",
                        help="Override default field widths. Format: opcode=<value>,param1=<value>,param2=<value>,param3=<value>. Example(Default): -w opcode=5,param1=14,param2=5,param3=6")
    parser.add_argument("-r","--readable",action="store_true",
                        help="Generate a readable text file with detailed information.")
    parser.add_argument("-v","--verbose",action="store_true",
                        help="Enable verbose output.")
    parser.add_argument("-l","--log",action="store_true",
                        help="Enable log file output.")
    parser.add_argument("-d","--debug",action="store_true",
                        help="Enable debugging mode.")
    parser.add_argument("-f","--param_format",choices=["hex","dec","bin"],default="hex",
                        help="Parameter format in the readable file (hex, dec, bin).")
    parser.add_argument("-e","--endianess",choices=["big","little"],default="big",
                        help="Endianness for the output binary (ignored since binary is now text).")
    args = parser.parse_args()
    
    # Initialize console and logger
    # console = Console()
    logging.basicConfig(
        level=logging.DEBUG,
        format="    %(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True)]
    )
    global logger
    logger = logging.getLogger("rich")

    LOG_FORMAT="%(asctime)s [%(levelname)s] %(message)s[%(filename)s:%(lineno)s]"
    log_file_handler = logging.FileHandler(f"{args.output}.log", mode="w", encoding="utf-8") if args.log else logging.NullHandler()
    log_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(log_file_handler)

    global g_debug
    g_debug = args.debug

    # Initialize symbol table and blocks
    symtbl = SymbolTable()
    blocks = []

    # 1) Read input
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        raise AsmError(f"Input file '{args.input}' not found.")

    logger.info(f"Parsing input => [bold magenta]{args.input}[/bold magenta]")

    # 2) Override default section offsets if provided
    # Ex: -m code=0,data=0x50
    code_start = 0
    data_start = 0x50
    if args.memory_offset:
        overrides = args.memory_offset.split(",")
        for ov in overrides:
            if '=' not in ov:
                raise AsmError(f"Invalid memory_offset format: '{ov}'. Expected format: key=value.")
            key, val = ov.split("=")
            key = key.strip().lower()
            try:
                val = int(val, 0)
            except ValueError:
                raise AsmError(f"Invalid offset value for '{key}': '{val}'")
            if key == "code":
                code_start = val
                if args.verbose:
                    logger.info(f"Overridden 'code' section offset to 0x{code_start:X}")
            elif key == "data":
                data_start = val
                if args.verbose:
                    logger.info(f"Overridden 'data' section offset to 0x{data_start:X}")
            else:
                raise AsmError(f"Unknown section key '{key}'. Supported keys: code, data.")
            

    # 3) Override default field widths if provided
    # Ex: -w opcode=5,param1=14,param2=5,param3=6
    opcode_width = 5
    param1_width = 14
    param2_width = 5
    param3_width = 6
    if args.field_width:
        overrides = args.field_width.split(",")
        for ov in overrides:
            if '=' not in ov:
                raise AsmError(f"Invalid field_width format: '{ov}'. Expected format: key=value.")
            key, val = ov.split("=")
            key = key.strip().lower()
            try:
                val = int(val, 0)
            except ValueError:
                raise AsmError(f"Invalid width value for '{key}': '{val}'")
            if key == "opcode":
                opcode_width = val
                if args.verbose:
                    logger.info(f"Overridden 'opcode' field width to {opcode_width}")
            elif key == "param1":
                param1_width = val
                if args.verbose:
                    logger.info(f"Overridden 'param1' field width to {param1_width}")
            elif key == "param2":
                param2_width = val
                if args.verbose:
                    logger.info(f"Overridden 'param2' field width to {param2_width}")
            elif key == "param3":
                param3_width = val
                if args.verbose:
                    logger.info(f"Overridden 'param3' field width to {param3_width}")
            else:
                raise AsmError(f"Unknown field key '{key}'. Supported keys: opcode, param1, param2, param3.")

    # 4) Parse input into blocks
    blocks = parse_input_to_blocks(lines, symtbl, verbose=args.verbose)

    # print Parsed Blocks
    if args.debug:
        logger.debug("Parsed Blocks:")
        # Blocks Table
        parsed_blocks_table = Table(title="Parsed Blocks", box=box.MINIMAL_DOUBLE_HEAD)
        parsed_blocks_table.add_column("Type", style="cyan", no_wrap=True)
        parsed_blocks_table.add_column("Function", style="magenta")
        parsed_blocks_table.add_column("Memory", style="magenta")
        parsed_blocks_table.add_column("opcode", style="green")
        parsed_blocks_table.add_column("param1", style="green")
        parsed_blocks_table.add_column("param2", style="green")
        parsed_blocks_table.add_column("param3", style="green")
        parsed_blocks_table.add_column("content", style="yellow")
        parsed_blocks_table.add_column("expanded_bits", style="yellow")
        parsed_blocks_table.add_column("addresses", style="white")
        parsed_blocks_table.add_column("lineno", style="magenta")
        for block in blocks:
            logger.debug(f"Block {block.type} '{block.name}': defined at line {block.lineno}")
            for irline in block.content:
                irtype_str = {
                    IRType.MEMORY_DATA: "MEMORY_DATA",
                    IRType.ALIAS: "ALIAS",
                    IRType.FUNC_DEFINE: "FUNC_DEFINE",
                    IRType.INSTRUCTION: "INSTRUCTION"
                }.get(irline.type, "UNKNOWN")
                parsed_blocks_table.add_row(
                    irtype_str,
                    str(irline.func) if irline.func else "-",
                    str(irline.mem) if irline.mem else "-",
                    str(irline.opcode) if irline.opcode else "-",
                    str(irline.param1) if irline.param1 else "-",
                    str(irline.param2) if irline.param2 else "-",
                    str(irline.param3) if irline.param3 else "-",
                    str(irline.content),
                    str(irline.expanded_bits),
                    str(irline.addresses) if irline.addresses else "-",
                    str(irline.lineno)
                )
        debug_panel_parsed = Panel.fit(parsed_blocks_table, title="[bold green][DEBUG][/bold green] [bold white]Information[/bold white]", style="bold green")
        # logger.debug(debug_panel_parsed) # logger does not support Panel => TODO: find a way to print Panel to logger
        console.print(debug_panel_parsed)

    # 5) Multi-pass symbol resolution
    if args.verbose:
        logger.info("Resolving symbols...")

    for pass_i in range(MAX_PASS):
        updated = False
        if args.verbose:
            logger.info(f"Starting pass {pass_i}...")
        try:
            updated = resolve_symbols(blocks, symtbl, code_start, data_start, opcode_width, param1_width, param2_width, param3_width, verbose=args.verbose)
        except AsmError as e:
            raise AsmError(f"During pass {pass_i}: {e}")
        # After resolving, re-assign addresses based on block sizes
        # Check for changes (use updated flag)
        if updated:
            if args.verbose:
                logger.info("Updated blocks, re-assigning addresses...")
        # Check for changes (fully search for changes)
        if pass_i == 0:
            prev_blocks = [ (b.start_addr, b.size) for b in blocks ]
        else:
            current_blocks = [ (b.start_addr, b.size) for b in blocks ]
            if current_blocks == prev_blocks:
                if args.verbose:
                    logger.info(f"No change in pass {pass_i}, done.")
                break
            prev_blocks = current_blocks
    else:
        raise AsmError(f"Symbol resolution not converged after {MAX_PASS} passes.")
    if args.verbose:
        logger.info("Symbol resolution complete.")

    # print Resolved Blocks
    if args.debug:
        logger.debug("Resolved Blocks:")
        # Blocks Table
        resolved_blocks_table = Table(title="Resolved Blocks", box=box.MINIMAL_DOUBLE_HEAD)
        resolved_blocks_table.add_column("Type", style="cyan", no_wrap=True)
        resolved_blocks_table.add_column("Function", style="magenta")
        resolved_blocks_table.add_column("Memory", style="magenta")
        resolved_blocks_table.add_column("opcode", style="green")
        resolved_blocks_table.add_column("param1", style="green")
        resolved_blocks_table.add_column("param2", style="green")
        resolved_blocks_table.add_column("param3", style="green")
        resolved_blocks_table.add_column("content", style="yellow")
        resolved_blocks_table.add_column("expanded_bits", style="yellow")
        resolved_blocks_table.add_column("addresses", style="white")
        resolved_blocks_table.add_column("lineno", style="magenta")
        for block in blocks:
            logger.debug(f"Block {block.type} '{block.name}': defined at line {block.lineno}")
            for irline in block.content:
                irtype_str = {
                    IRType.MEMORY_DATA: "MEMORY_DATA",
                    IRType.ALIAS: "ALIAS",
                    IRType.FUNC_DEFINE: "FUNC_DEFINE",
                    IRType.INSTRUCTION: "INSTRUCTION"
                }.get(irline.type, "UNKNOWN")
                resolved_blocks_table.add_row(
                    irtype_str,
                    str(irline.func) if irline.func else "-",
                    str(irline.mem) if irline.mem else "-",
                    str(irline.opcode) if irline.opcode else "-",
                    str(irline.param1) if irline.param1 else "-",
                    str(irline.param2) if irline.param2 else "-",
                    str(irline.param3) if irline.param3 else "-",
                    str(irline.content),
                    str(irline.expanded_bits),
                    str(irline.addresses) if irline.addresses else "-",
                    str(irline.lineno)
                )
        debug_panel_resolve = Panel.fit(resolved_blocks_table, title="[bold green][DEBUG][/bold green] [bold white]Information[/bold white]", style="bold green")
        console.print(debug_panel_resolve) # logger does not support Panel => TODO: find a way to print Panel to logger

    # 6) Check unresolved symbols
    # Check for unresolved symbols
    if args.verbose:
        logger.info("Checking for unresolved symbols...")
    for an, ad in symtbl.alias_map.items():
        if ad is None:
            raise AsmError(f"Unresolved alias '{an}' => None")
    for fn, fa in symtbl.function_map.items():
        if fa is None:
            raise AsmError(f"Unresolved function '{fn}' => None")

    # 7) Check overlap
    # Check for overlap in blocks
    try:
        if args.verbose:
            logger.info("Checking for block overlap...")
        check_block_overlap(blocks)
    except AsmError as e:
        raise e

    # Check for overlap in sections
    code_size = sum(block.size for block in blocks if block.type == BlockType.FUNCTION)
    data_size = sum(block.size for block in blocks if block.type == BlockType.MEMORY)
    try:
        if args.verbose:
            logger.info("Checking for section overlap...")
        check_section_overlap(code_start, code_size, data_start, data_size)
    except AsmError as e:
        raise e
    
    logger.info("Assembly complete.")

    # 8) Emit output files
    outread = None
    if args.readable:
        outread = args.output + "_readable.txt"

    logger.info("Emitting output files...")

    emit_files(blocks, symtbl,
               outbitfile=args.output,
               outread=outread,
               endian=args.endianess,
               fmt=args.param_format,
               verbose=args.verbose)

    finish_time = time.time()

    # 9) Final verbose output
    if args.debug:
        # Blocks Table
        blocks_table = Table(title="Blocks", box=box.MINIMAL_DOUBLE_HEAD)
        blocks_table.add_column("Type", style="cyan", no_wrap=True)
        blocks_table.add_column("Name", style="magenta")
        blocks_table.add_column("Offset", style="yellow")
        blocks_table.add_column("Size", style="green")
        for block in blocks:
            blocks_table.add_row(block.type.capitalize(), block.name, f"0x{block.start_addr:X}", str(block.size))

        # Functions Table
        functions_table = Table(title="Functions", box=box.MINIMAL_DOUBLE_HEAD)
        functions_table.add_column("Function Name", style="magenta", no_wrap=True)
        functions_table.add_column("Offset", style="yellow")
        for fn, ofs in symtbl.function_map.items():
            functions_table.add_row(fn, f"0x{ofs:X}")

        # Aliases Table
        aliases_table = Table(title="Aliases", box=box.MINIMAL_DOUBLE_HEAD)
        aliases_table.add_column("Alias Name", style="magenta", no_wrap=True)
        aliases_table.add_column("Address", style="yellow")
        for an, ad in symtbl.alias_map.items():
            aliases_table.add_row(an, f"0x{ad:X}")

        # Macros Table
        macros_table = Table(title="Macros", box=box.MINIMAL_DOUBLE_HEAD)
        macros_table.add_column("Macro Name", style="magenta", no_wrap=True)
        macros_table.add_column("Value", style="yellow")
        for mn, mv in symtbl.macro_map.items():
            macros_table.add_row(mn, f"0x{mv:X}")

        debug_panel = Panel.fit(Columns([blocks_table, functions_table, aliases_table, macros_table]), title="[bold green][DEBUG][/bold green] [bold white]Information[/bold white]", style="bold green", padding=(4, 1))
        console.print(debug_panel)

    # Output Table
    output_table = Table(title="[bold white]Output File:[/bold white]", title_justify="left", box=box.MINIMAL_DOUBLE_HEAD, show_lines=True)
    output_table.add_column("Type", style="white", no_wrap=True)
    output_table.add_column("File", style="magenta")
    output_table.add_row("Binary File(Bitstring)", args.output)
    if outread:
        output_table.add_row("Readable File(Text)", outread)

    summary = f"[bold white]Total Used Memory Space[/bold white]: [bold blue]{code_size + data_size}[/bold blue]\n\n\
[bold white]Code section:[/bold white] [bold blue]0x{code_start:X}\t- 0x{code_start+code_size-1:X}[/bold blue]\n\
[bold white]Data section:[/bold white] [bold blue]0x{data_start:X}\t- 0x{data_start+data_size-1:X}[/bold blue]\n\n\
[bold white]Input File:[/bold white]\t[bold magenta]{args.input}[/bold magenta]"
    
    if args.verbose or args.debug:
        summary = f"[bold white]Elapsed Time: [/bold white]: [bold green]{finish_time-start_time:.4f}[/bold green] seconds\n\n\
[bold white]Total Used Memory Space[/bold white]: [bold blue]{code_size + data_size}[/bold blue]\n\n\
[bold white]Total Blocks:[/bold white] [bold green]{len(blocks)}[/bold green]\n\
[bold white]Total Functions: [bold green]{len(symtbl.function_map)}[/bold green]\n\
[bold white]Total Aliases:[/bold white] [bold green]{len(symtbl.alias_map)}[/bold green]\n\
[bold white]Total Macros:[/bold white] [bold green]{len(symtbl.macro_map)}[/bold green]\n\n\
[bold white]Code Size:[/bold white] [bold green]{code_size}[/bold green]\n\
[bold white]Code section:[/bold white] [bold blue]0x{code_start:X}\t- 0x{code_start+code_size-1:X}[/bold blue]\n\
[bold white]Data Size:[/bold white] [bold green]{data_size}[/bold green]\n\
[bold white]Data section:[/bold white] [bold blue]0x{data_start:X}\t- 0x{data_start+data_size-1:X}[/bold blue]\n\n\
[bold white]Input File:[/bold white]\t[bold magenta]{args.input}[/bold magenta]"
        
    panel = Panel.fit(Group(summary, output_table), title="[bold blue][INFO][/bold blue] Assembly Summary", subtitle=f"Assembler v{version}", style="bold blue", padding=(2, 1))
    console.print("\n", panel)

if __name__=="__main__":
    global console
    console = Console()
    try:
        main()
    except AsmError as e:
        logger.error(f"{e}")
        summary = f"[bold red]Assembly Failed with AsmError[/bold red]\n\nCheck:\n[bold white]{e}[/bold white]"
        panel = Panel.fit(summary, title="Assembly Summary", subtitle=f"PolyASM v{version}", style="bold red", padding=(2, 1))
        console.print(panel)
        sys.exit(1)

    except Exception as ex:
        logger.critical(f"{ex}")
        summary = f"[bold red]Assembly Failed with Exception[/bold red]\n\nCheck:\n[bold white]{ex}[/bold white]"
        panel = Panel.fit(summary, title="Assembly Summary", subtitle=f"Assembler v{version}", style="bold red", padding=(2, 1))
        console.print(panel)
        sys.exit(1)