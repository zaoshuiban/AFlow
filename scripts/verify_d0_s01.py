"""Read-only S0.1 artifact verification. No program/model executions."""
import json
from pathlib import Path
from scripts.experiment_d0_s01 import ROOT,verify
from scripts.offline_loop_protocol import file_hash

def main():
 verify()
 manifest=json.loads((ROOT/'artifact_manifest.json').read_text())
 for f,h in manifest.items():assert file_hash(Path(f))==h,f
 sources=json.loads((ROOT/'final_source_manifest.json').read_text())
 for f,h in sources.items():assert file_hash(Path(f))==h,f
 a=json.loads((ROOT/'acceptance_gate.json').read_text());cost=json.loads((ROOT/'cost_accounting.json').read_text());assert cost['model_API_requests']==0
 print(json.dumps(dict(status=a['status'],verified_artifacts=len(manifest),API_requests=0),ensure_ascii=False))
if __name__=='__main__':main()
