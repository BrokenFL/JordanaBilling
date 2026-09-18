#!/usr/bin/env python3
"""Prepare (never publish) an explicit update offer for a verified release."""
import argparse
import hashlib
import json
import re
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('dmg',type=Path)
parser.add_argument('--commit',required=True)
parser.add_argument('--notes-file',type=Path,required=True)
parser.add_argument('--output',type=Path,default=Path('updates/jordana.json'))
args=parser.parse_args()
match=re.fullmatch(r'JordanaBilling-(v(\d+\.\d+\.\d+)-test\.(\d+))-([a-f0-9]{12})-macos-arm64.dmg',args.dmg.name)
if not match or not re.fullmatch('[a-f0-9]{40}',args.commit) or not args.commit.startswith(match[4]):
    parser.error('DMG name and full source commit must match.')
digest=hashlib.sha256(args.dmg.read_bytes()).hexdigest()
checksum=Path(str(args.dmg)+'.sha256').read_text().split()[0]
if digest != checksum:
    parser.error('DMG checksum failed.')
value=dict(enabled=True,version=f'{match[2]}.post{match[3]}',release_label=match[1],commit=args.commit,
           sha256=digest,url=f'https://github.com/BrokenFL/JordanaBilling/releases/download/{match[1]}/{args.dmg.name}',
           notes=args.notes_file.read_text()[:4000])
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(value,indent=2)+'\n')
print(f'Prepared {args.output}. Not published; verify the release and review this offer before publishing.')
