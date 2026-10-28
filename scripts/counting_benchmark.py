"""Freeze 90 development and 240 sealed formal CCC instances, no model calls."""
from __future__ import annotations
import itertools
import json
import random
from pathlib import Path
from scripts.compositional_counting import KINDS,SPEC,make_instance,exact_dp,oracle_selfcheck
from scripts.offline_loop_protocol import atomic_json,read_json,digest,file_hash

ROOT=Path('results/compositional_counting_20261014_v1')
HELD_OUT=[('count','mod','pattern_count'),('adjacent','position','endpoints'),
          ('count','forbidden','pattern_count','endpoints'),('adjacent','mod','position','endpoints'),
          ('count','adjacent','forbidden','position','endpoints'),
          ('count','mod','forbidden','pattern_count','position','endpoints')]
BANDS=((8,12),(16,24),(28,36))


def pools():
    rng=random.Random(261014601);held={frozenset(x) for x in HELD_OUT};out={}
    for tier,sizes in [('Easy',(1,2)),('Medium',(3,4)),('Hard',(5,6,7))]:
        masks=[c for k in sizes for c in itertools.combinations(KINDS,k) if frozenset(c) not in held]
        rng.shuffle(masks)
        # Guarantee each permitted category-count level, then fill a ten-mask pool.
        chosen=[next(c for c in masks if len(c)==k) for k in sizes]
        chosen += [c for c in masks if c not in chosen][:10-len(chosen)]
        out[tier]=chosen
    return out


def make_rows(seed,count,tier,masks,seen,ood=None):
    rng=random.Random(seed);rows=[]
    for i in range(count):
        scale=i%3;kinds=masks[(i//3)%len(masks)]
        for attempt in range(200):
            lo,hi=BANDS[scale] if ood!='parameter' else (40,48)
            n=rng.randint(lo,hi);p=make_instance(rng,kinds,n);pid=digest(p)
            if pid not in seen:break
        else:raise ValueError('Duplicate generation exhausted')
        seen.add(pid)
        answer=exact_dp(p)
        rows.append(dict(id=pid[:20],tier=tier,scale=('small','medium','large')[scale],ood=ood,
            constraint_kinds=list(kinds),problem=json.dumps(p,sort_keys=True),expected=answer['answer'],reference=answer))
    return rows


def prepare(root=ROOT):
    root=Path(root)
    if (root/'manifest.json').exists():return read_json(root/'manifest.json')
    root.mkdir(parents=True,exist_ok=True)
    checks=oracle_selfcheck();atomic_json(root/'oracle_crosscheck.json',checks)
    masks=pools();seen=set();dev=[]
    for i,tier in enumerate(('Easy','Medium','Hard')):
        dev+=make_rows(261014610+i,30,tier,masks[tier],seen)
    atomic_json(root/'development.json',dev)
    # Test contents never enter calibration inputs, generation prompts or reports.
    test=[]
    for i,tier in enumerate(('Easy','Medium','Hard')):
        test+=make_rows(261014710+i,60,tier,masks[tier],seen)
    for i,tier in enumerate(('Easy','Medium','Hard')):
        test+=make_rows(261014810+i,10,'OOD',masks[tier],seen,ood='parameter')
    test+=make_rows(261014910,30,'OOD',HELD_OUT,seen,ood='combination')
    atomic_json(root/'sealed/formal.json',test)
    for p in (root/'sealed').glob('*'):p.chmod(0o400)
    (root/'sealed').chmod(0o500)
    manifest=dict(schema_version=1,stage='development_calibration',spec=SPEC,
        counts=dict(development=90,formal=dict(Easy=60,Medium=60,Hard=60,OOD=60)),
        bands=BANDS,complexity=dict(Easy=[1,2],Medium=[3,4],Hard=[5,6,7]),
        development_combination_pools=masks,held_out_combinations=HELD_OUT,
        formal_seal=dict(sha256=file_hash(root/'sealed/formal.json'),file='sealed/formal.json',
            policy='Never load in calibration or candidate generation. Local permission/hash seal; not external cryptographic custody.'),
        development_sha256=file_hash(root/'development.json'),oracle_crosscheck_sha256=file_hash(root/'oracle_crosscheck.json'),
        oracle_crosscheck_count=len(checks),formal_started=False,operator_generated=False,
        sampling_policy='80% witness-based and 20% one-parameter perturbation; no accuracy/answer-based filtering or task replacement.',
        source_hashes={p:file_hash(Path(p)) for p in ('scripts/compositional_counting.py','scripts/counting_benchmark.py')})
    atomic_json(root/'manifest.json',manifest)
    print(json.dumps(dict(prepared=True,development=90,sealed_formal=240,crosschecks=len(checks),formal_sha256=manifest['formal_seal']['sha256'])),flush=True)
    return manifest


if __name__=='__main__':prepare()
