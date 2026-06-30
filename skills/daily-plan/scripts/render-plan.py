#!/usr/bin/env python3
"""
Render plan by extracting blocks or reassembling with beautified blocks.

This script handles the mechanical extraction and reassembly of plan blocks.
Beautification is handled externally via list-manager skill invocation.

Usage:
    # Extract blocks from plan
    python render-plan.py extract <plan-file> <output-dir>

    # Reassemble plan with beautified blocks
    python render-plan.py reassemble <plan-file> <blocks-dir>

Example:
    python render-plan.py extract plan.yaml /tmp/plan-blocks/
    # ... beautify blocks via list-manager skill ...
    python render-plan.py reassemble plan.yaml /tmp/plan-blocks/
"""

import sys
import re
from pathlib import Path


def extract_blocks(yaml_content):
    """Extract named blocks from YAML. Returns dict of block_name -> content."""
    blocks = {}
    block_pattern = r'<!-- BEGIN (\w+) -->(.*?)<!-- END \1 -->'

    for match in re.finditer(block_pattern, yaml_content, re.DOTALL):
        block_name = match.group(1)
        block_content = match.group(2).strip()
        blocks[block_name] = block_content

    return blocks


def extract_command(plan_file, output_dir):
    """Extract blocks from plan file and write to output directory."""
    try:
        plan_path = Path(plan_file)
        with open(plan_path, 'r') as f:
            yaml_content = f.read()
    except Exception as e:
        print(f"Error reading plan file: {e}", file=sys.stderr)
        return False

    blocks = extract_blocks(yaml_content)

    if not blocks:
        print("No blocks found in plan file", file=sys.stderr)
        return False

    # Write blocks to separate files
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    try:
        for block_name, block_content in blocks.items():
            block_file = output_path / f"plan_{block_name.lower()}.yaml"
            with open(block_file, 'w') as f:
                f.write(block_content)
            print(f"Extracted: {block_file}")
        return True
    except Exception as e:
        print(f"Error writing blocks: {e}", file=sys.stderr)
        return False


def reassemble_command(plan_file, blocks_dir):
    """Reassemble plan with beautified blocks."""
    try:
        plan_path = Path(plan_file)
        with open(plan_path, 'r') as f:
            yaml_content = f.read()
    except Exception as e:
        print(f"Error reading plan file: {e}", file=sys.stderr)
        return False

    # Read beautified blocks
    blocks_path = Path(blocks_dir)
    beautified_blocks = {}

    try:
        for block_file in blocks_path.glob("plan_*.yaml"):
            block_name = block_file.stem.replace('plan_', '').upper()
            with open(block_file, 'r') as f:
                beautified_blocks[block_name] = f.read()
    except Exception as e:
        print(f"Error reading beautified blocks: {e}", file=sys.stderr)
        return False

    # Replace blocks in YAML
    result = yaml_content
    for block_name, beautified_content in beautified_blocks.items():
        pattern = f'<!-- BEGIN {block_name} -->.*?<!-- END {block_name} -->'
        replacement = f'<!-- BEGIN {block_name} -->\n{beautified_content}\n<!-- END {block_name} -->'
        result = re.sub(pattern, replacement, result, flags=re.DOTALL)

    # Display result
    print(result)
    return True


def main():
    if len(sys.argv) < 3:
        print("Usage: python render-plan.py <extract|reassemble> <plan-file> <dir>", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    plan_file = sys.argv[2]
    dir_arg = sys.argv[3] if len(sys.argv) > 3 else None

    if command == "extract":
        if not dir_arg:
            print("Error: output directory required for extract", file=sys.stderr)
            sys.exit(1)
        success = extract_command(plan_file, dir_arg)
    elif command == "reassemble":
        if not dir_arg:
            print("Error: blocks directory required for reassemble", file=sys.stderr)
            sys.exit(1)
        success = reassemble_command(plan_file, dir_arg)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
