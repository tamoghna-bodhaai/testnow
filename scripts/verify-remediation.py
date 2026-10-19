#!/usr/bin/env python3
"""Run the local remediation gates without requiring production configuration."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-build',action='store_true')
    args=parser.parse_args()
    environment={**os.environ,'DATABASE_URL':'sqlite+pysqlite:///:memory:','SEED_DEMO_DATA':'false'}
    environment['PYTHONPATH']=str(ROOT/'apps/api')+os.pathsep+environment.get('PYTHONPATH','')
    if not environment.get('TESSDATA_PREFIX') and shutil.which('tesseract'):
        languages=subprocess.run(['tesseract','--list-langs'],text=True,capture_output=True,check=False)
        match=re.search(r'in "([^"]+)"',languages.stdout+languages.stderr)
        if match and (Path(match[1])/'eng.traineddata').exists():environment['TESSDATA_PREFIX']=match[1]
    commands=[[sys.executable,'-m','pytest','apps/api/tests','-q'],['npm','run','test:web']]
    if not args.skip_build:commands.append(['npm','run','build:web'])
    for command in commands:
        print('Running: '+' '.join(command),flush=True)
        subprocess.run(command,cwd=ROOT,env=environment,check=True)
    subprocess.run(['git','diff','--check'],cwd=ROOT,check=True)
    print('Local remediation gates passed. Production staging/data gates remain separate.',flush=True)

if __name__=='__main__':main()
