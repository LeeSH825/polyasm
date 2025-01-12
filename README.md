# PolyAsm
PolyAsm is a flexible assembler designed for a custom processor architecture. Inspired by popular, high-star repositories like **NASM**, **YASM**, **FASM**, **GNU Assembler (GAS)**, and **LLVM's Integrated Assembler**, PolyAsm combines multi-pass symbol resolution, customizable field widths/offsets, and robust error reporting with visually rich output courtesy of the [Rich](https://github.com/willmcgugan/rich) library.

<p align="center">
    <img src="https://raw.githubusercontent.com/willmcgugan/rich/master/docs/_static/logo.png" alt="Rich Logo" width="180">
</p>

---

## Table of Contents

- [PolyAsm](#polyasm)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Installation \& Requirements](#installation--requirements)
    - [Steps](#steps)
  - [Getting Started](#getting-started)
  - [Command-Line Options](#command-line-options)
  - [Usage Example](#usage-example)
    - [Example Assembly Code](#example-assembly-code)
  - [Logging \& Output](#logging--output)
  - [Contributing](#contributing)
  - [License](#license)

---

## Features

1. **Block-Based Assembly**  
     - Parse and assemble **function blocks**, **memory blocks**, and **macro blocks**.  
     - Processed in source order for intuitive code flow.  
     - Detect overlapping blocks with detailed error messages.

2. **Multi-Pass Symbol Resolution**  
     - Resolves function addresses, aliases, and macros over multiple passes.  
     - Notifies you if symbols remain unresolved or if blocks overlap.

3. **Customizable Field Widths & Offsets**  
     - Adjust opcode/parameter bit widths via CLI flags.  
     - Override default code/data section offsets with a single command.

4. **Rich Terminal Output**  
     - Powered by [Rich](https://github.com/willmcgugan/rich) for colorized, structured logs and final summaries.  
     - Summaries of blocks, functions, aliases, and macros displayed in neat tables and panels.

5. **Robust Logging & Error Handling**  
     - Warnings for redefinitions (macros, aliases).  
     - Errors for unrecognized opcodes, unresolved symbols, overlapping memory blocks, and more.  
     - Log files are optional and can be generated for debugging or audit trails.

6. **Flexible Output Options**  
     - Produces a bitstring text file of 32-bit machine code lines.  
     - Optionally generate a human-readable text file (`-r`) with an expanded breakdown of instructions, macros, and memory data.

---

## Installation & Requirements

- **Python:** 3.12.8 or higher  
- **Rich:** Install via `pip install rich`

### Steps

1. **Clone the Repository**:  
     ```bash
     git clone https://github.com/LeeSH825/polyasm.git
     cd polyasm
     ```

2. **Install Dependencies** (either via requirements.txt or manually):
     ```bash
     pip install -r requirements.txt
     ```

3. **Verify Installation**:
     ```bash
     pip install -r requirements.txt
     ```

     (Make sure Python is at least 3.12.8.)

## Getting Started
After installing, run polyasm.py specifying your assembly input (.asm) and desired output file. PolyAsm parses the code, checks for errors or overlaps, and outputs a final bitstring file along with optional logs and a human-readable report.

## Command-Line Options
```bash
python polyasm.py
usage: polyasm.py [-h] -i INPUT -o OUTPUT [-m MEMORY_OFFSET] [-w FIELD_WIDTH] [-r] [-v] [-l] [-d] [-f {hex,dec,bin}] [-e {big,little}]

PolyASM: Customizable Assembler for Custom-Designed Processor

options:
        -h, --help            show this help message and exit
        -i INPUT, --input INPUT
                                                                                                Input assembly file path.
        -o OUTPUT, --output OUTPUT
                                                                                                Output bitstring text file path.
        -m MEMORY_OFFSET, --memory_offset MEMORY_OFFSET
                                                                                                Override default memory section offsets. Format: code=<value>,data=<value>. Example(Default): -m code=0,data=0x50
        -w FIELD_WIDTH, --field_width FIELD_WIDTH
                                                                                                Override default field widths. Format: opcode=<value>,param1=<value>,param2=<value>,param3=<value>. Example(Default): -w opcode=5,param1=14,param2=5,param3=6
        -r, --readable        Generate a readable text file with detailed information.
        -v, --verbose         Enable verbose output.
        -l, --log             Enable log file output.
        -d, --debug           Enable debugging mode.
        -f {hex,dec,bin}, --param_format {hex,dec,bin}
                                                                                                Parameter format in the readable file (hex, dec, bin).
        -e {big,little}, --endianess {big,little}
                                                                                                Endianness for the output binary (ignored since binary is now text).
```

## Usage Example
```bash
python polyasm.py \
        -i sample.asm \
        -o output.bin \
        -r -v -l \
        -w opcode=5,param1=14,param2=5,param3=6
```
### Example Assembly Code
Below is a short snippet showcasing macros, aliases, and memory/function blocks:
```sample.asm
// sample.asm

#macro BOOT_VECTOR 0x200
#macro R1 0x05
#macro REG_SET1 0b00011000
#macro REG_SET2 0x23
#macro 1 0x01
#macro 2 0x02
#macro AX 0x10
#macro Jump_Register 0x20
#macro RESET_MODE 0xFF

#memory BootSection:
"REG_SET1", "REG_SET2", "0b00010010", "0x11"
0010 0010 1101 0000 0010 0000 0010 0000  // data line
"AX|RESET_MODE", "1 + 2", "0x20", "0b10001000" #alias DATA

function init():
        setreg ["0"] [#Jump_Register] [@DATA]
        add [Jump_Register] [AX] []     #alias jumper
        jump [init():]

function main():
        setreg ["1"] [#R1] [#BOOT_VECTOR]
        jump [main():]
```

```
// Readable File

00000 | p=0 c=1 p3=010010 p2=00000 p1=00000000000000 | func=init, opcode=setreg, param1=0x0, param2=0x20, param3=0x52
00001 | p=0 c=0 p3=000001 p2=00001 p1=00000000000000 | func=init, opcode=setreg, param1=0x0, param2=0x20, param3=0x52 <- alias: jumper
00002 | p=0 c=0 p3=000000 p2=10000 p1=00000000000010 | func=init, opcode=add, param1=0x2, param2=0x10, param3=0x0
00003 | p=1 c=0 p3=000000 p2=00000 p1=00000000000000 | func=init, opcode=jump, param1=0x0, param2=0x0, param3=0x0
00004 | p=1 c=1 p3=000000 p2=00101 p1=00000000000001 | func=main, opcode=setreg, param1=0x1, param2=0x5, param3=0x200
00005 | p=1 c=0 p3=001000 p2=00000 p1=00000000000000 | func=main, opcode=setreg, param1=0x1, param2=0x5, param3=0x200
00006 | p=0 c=0 p3=000000 p2=00000 p1=00000000000100 | func=main, opcode=jump, param1=0x4, param2=0x0, param3=0x0
00050 | 00011000 00100011 00010010 00010001 | mem=BootSection, 0x18 0x23 0x12 0x11
00051 | 00100010 11010000 00100000 00100000 | mem=BootSection, 0x22 0xD0 0x20 0x20
00052 | 11111111 00000011 00100000 10001000 | mem=BootSection, 0xFF 0x03 0x20 0x88 <- alias: DATA
```


1. **Parses & Assigns Addresses**:  
        PolyAsm organizes blocks (BootSection, init, main), checks overlap, and resolves macro/alias references.
2. **Multi-Pass Symbol Resolution**:  
        Repeatedly resolves function addresses (init, main), macros (BOOT_VECTOR), and aliases (BOOT_DATA).
3. **Outputs**:  
        - output.bin: Contains final 32-bit bitstrings.  
        - output.bin_readable.txt (via -r): Summarizes instructions, aliases, macros, memory usage.  
        - output.bin.log (via -l): Writes logs if enabled.

## Logging & Output
PolyAsm uses [Rich](https://github.com/Textualize/rich) to enhance Python’s logging with color and markup.  
During assembly, you’ll see color-coded messages for each pass, warnings for redefinitions, and final summary panels/tables.  
If `-d` or `-v` is used, it prints debug panels with function addresses, memory blocks, macro values, etc.

## Contributing
Welcome to pull requests and issues!  

1. Fork the repository and clone to your local machine.  
2. Create a feature branch for your changes.  
3. Open a Pull Request against the main branch.  
   
For major changes, please open an issue first to discuss the proposed modifications. Make sure to include tests or examples if you add a major feature.

## License
PolyAsm is licensed under the MIT License. See LICENSE for more details.