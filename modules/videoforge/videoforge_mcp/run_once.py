from __future__ import annotations
import json,os
from pathlib import Path
from .backends import LTXVideoBackend
from .config import JOB_DIR,OUTPUT_DIR
from .queue import JobQueue
from .runner import run_one

def main():
 profile=json.loads(Path(os.environ['VIDEOFORGE_PROFILE_PATH']).read_text())
 queue=JobQueue(JOB_DIR,Path(os.environ['VIDEOFORGE_INPUT_ROOT']))
 run_one(queue,{profile['profile_id']:profile},{'ltx_video':LTXVideoBackend()},OUTPUT_DIR)
if __name__=='__main__':main()
