"""Replay fixed engineering regression in a NEW directory, without API."""
import argparse,asyncio,json,shutil
from pathlib import Path
import scripts.experiment_d0_s01 as original
from scripts.offline_loop_protocol import atomic_json,file_hash

def main():
 a=argparse.ArgumentParser();a.add_argument('--out',required=True);x=a.parse_args();dest=Path(x.out)
 if dest.exists():raise FileExistsError('New directory required')
 original.verify();source=original.ROOT;dest.mkdir(parents=True)
 for name in ['protocol.json','history_before.json','extra_cases.json']:shutil.copyfile(source/name,dest/name)
 atomic_json(dest/'replay_origin.json',dict(origin=str(source),origin_protocol_sha256=file_hash(source/'protocol.json'),replay_run_id=str(dest),independent_research_sample=False,API=0))
 original.ROOT=dest;asyncio.run(original.regression())
if __name__=='__main__':main()
