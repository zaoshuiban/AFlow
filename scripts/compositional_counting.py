"""Compositional Constraint Counting: independent exact DP and brute oracle."""
from __future__ import annotations
import itertools
import json
import random
import time
from collections import defaultdict

KINDS = ('count', 'adjacent', 'mod', 'forbidden', 'pattern_count', 'position', 'endpoints')
SPEC = '''Compositional Constraint Counting. Input to solve(problem) is a JSON STRING with n (positive integer), alphabet=[0,1,2,3], and constraints (list). Count all length-n sequences over the alphabet satisfying ALL constraints. Positions are 1-based. Exact integer arithmetic, not an answer modulo anything. Each kind occurs at most once. Constraint schemas:
{"kind":"count","symbol":d,"exact":t}: symbol d occurs exactly t times.
{"kind":"adjacent"}: x[i]!=x[i+1] for every adjacent pair.
{"kind":"mod","modulus":m,"residue":r}: sum(x) % m == r.
{"kind":"forbidden","patterns":["012",...]}: no listed contiguous substring occurs.
{"kind":"pattern_count","pattern":"010","exact":t}: the substring occurs exactly t times, counting EVERY starting position, INCLUDING OVERLAP (010 occurs twice in 01010).
{"kind":"position","parity":"even" or "odd","forbidden_symbol":d}: symbol d is forbidden at positions of that parity. Even positions are 2,4,6,...
{"kind":"endpoints"}: first and last symbols differ (so no length-1 sequence satisfies it).
All fields and constraints are public and identical across systems. Absence of a kind means no restriction of that kind. Valid instances only. Return a nonnegative Python int, not a string, dict, float, or a modular residue. No leading-zero restriction: sequences may start with 0.'''


def validate_problem(p):
    if set(p) != {'n','alphabet','constraints'} or type(p['n']) is not int or not 1<=p['n']<=128 or p['alphabet'] != [0,1,2,3]:
        raise ValueError('Invalid task header')
    seen=set()
    for c in p['constraints']:
        k=c['kind']
        if k not in KINDS or k in seen:raise ValueError('Unknown/duplicate kind')
        seen.add(k)
        if k=='count' and (type(c['symbol']) is not int or c['symbol'] not in p['alphabet'] or type(c['exact']) is not int or not 0<=c['exact']<=p['n']):raise ValueError('Invalid count')
        if k=='mod' and (type(c['modulus']) is not int or not 2<=c['modulus']<=13 or type(c['residue']) is not int or not 0<=c['residue']<c['modulus']):raise ValueError('Invalid mod')
        patterns=c.get('patterns',[]) if k=='forbidden' else [c['pattern']] if k=='pattern_count' else []
        if any(not isinstance(s,str) or not 1<=len(s)<=4 or any(d not in '0123' for d in s) for s in patterns):raise ValueError('Invalid pattern')
        if k=='forbidden' and not patterns:raise ValueError('Empty forbidden list')
        if k=='pattern_count' and (type(c['exact']) is not int or not 0<=c['exact']<=max(0,p['n']-len(c['pattern'])+1)):raise ValueError('Invalid occurrence count')
        if k=='position' and (c['parity'] not in ('even','odd') or c['forbidden_symbol'] not in p['alphabet']):raise ValueError('Invalid position')
    return p


def exact_dp(p):
    """Trusted position-DP; never supplied to the model or operator generator."""
    validate_problem(p)
    t=time.monotonic();n=p['n'];cs={c['kind']:c for c in p['constraints']}
    count=cs.get('count');mod=cs.get('mod');pat=cs.get('pattern_count');forbidden=cs.get('forbidden',{}).get('patterns',[])
    longest=max([len(s) for s in forbidden]+([len(pat['pattern'])] if pat else [0]))
    keep=max(longest-1,1 if 'adjacent' in cs else 0)
    # suffix, first (only for endpoint test), count, residue, occurrences
    states={('',-1,0,0,0):1};peak=1;transitions=0
    for i in range(1,n+1):
        nxt=defaultdict(int)
        pos=cs.get('position');allowed=p['alphabet']
        if pos and ((i%2==0)==(pos['parity']=='even')):
            allowed=[d for d in allowed if d!=pos['forbidden_symbol']]
        for (suffix,first,used,residue,occ),ways in states.items():
            for d in allowed:
                transitions+=1;s=str(d)
                if 'adjacent' in cs and suffix and suffix[-1]==s:continue
                if 'endpoints' in cs and i==n and (d==first if i>1 else True):continue
                new_used=used+(d==count['symbol']) if count else 0
                if count and (new_used>count['exact'] or new_used+n-i<count['exact']):continue
                text=suffix+s
                if any(text.endswith(f) for f in forbidden):continue
                new_occ=occ+int(text.endswith(pat['pattern'])) if pat else 0
                if pat and new_occ>pat['exact']:continue
                new_rem=(residue+d)%mod['modulus'] if mod else 0
                new_first=(d if i==1 else first) if 'endpoints' in cs else -1
                nxt[(text[-keep:] if keep else '',new_first,new_used,new_rem,new_occ)]+=ways
        states=nxt;peak=max(peak,len(states))
    answer=sum(ways for (_,_,used,residue,occ),ways in states.items()
        if (not count or used==count['exact']) and (not mod or residue==mod['residue']) and (not pat or occ==pat['exact']))
    return dict(answer=answer,peak_states=peak,transitions=transitions,seconds=time.monotonic()-t)


def brute_force(p):
    """Independent full-sequence enumeration, intentionally no DP-state reuse."""
    validate_problem(p);total=0
    for xs in itertools.product(p['alphabet'],repeat=p['n']):
        text=''.join(map(str,xs));ok=True
        for c in p['constraints']:
            k=c['kind']
            if k=='count':test=xs.count(c['symbol'])==c['exact']
            elif k=='adjacent':test=all(a!=b for a,b in zip(xs,xs[1:]))
            elif k=='mod':test=sum(xs)%c['modulus']==c['residue']
            elif k=='forbidden':test=all(s not in text for s in c['patterns'])
            elif k=='pattern_count':
                s=c['pattern'];test=sum(text[i:i+len(s)]==s for i in range(len(text)-len(s)+1))==c['exact']
            elif k=='position':test=all(d!=c['forbidden_symbol'] for i,d in enumerate(xs,1) if (i%2==0)==(c['parity']=='even'))
            elif k=='endpoints':test=xs[0]!=xs[-1]
            if not test:ok=False;break
        total+=ok
    return total


def occurrences(text,pattern):
    return sum(text[i:i+len(pattern)]==pattern for i in range(len(text)-len(pattern)+1))


def make_instance(rng,kinds,n):
    # 80% witness-based, 20% one parameter perturbed. No oracle-result filtering.
    pos_symbol=rng.randrange(4);parity=rng.choice(('even','odd'));xs=[]
    for i in range(1,n+1):
        allowed=[d for d in range(4) if not ('adjacent' in kinds and xs and d==xs[-1])
                 and not ('position' in kinds and ((i%2==0)==(parity=='even')) and d==pos_symbol)
                 and not ('endpoints' in kinds and i==n and xs and d==xs[0])]
        xs.append(rng.choice(allowed))
    text=''.join(map(str,xs));cs=[]
    for k in kinds:
        if k=='count':
            d=rng.randrange(4);c=dict(kind=k,symbol=d,exact=xs.count(d))
        elif k in ('adjacent','endpoints'):c=dict(kind=k)
        elif k=='mod':
            m=rng.choice((3,5,7,11));c=dict(kind=k,modulus=m,residue=sum(xs)%m)
        elif k=='forbidden':
            choices=[''.join(ds) for ds in itertools.product('0123',repeat=3) if ''.join(ds) not in text]
            c=dict(kind=k,patterns=[rng.choice(choices)])
        elif k=='pattern_count':
            pattern=rng.choice(('01','010','00','121','012','101'))
            c=dict(kind=k,pattern=pattern,exact=occurrences(text,pattern))
        elif k=='position':c=dict(kind=k,parity=parity,forbidden_symbol=pos_symbol)
        cs.append(c)
    if rng.random()<.2:
        adjustable=[c for c in cs if c['kind'] in ('count','mod','pattern_count')]
        if adjustable:
            c=rng.choice(adjustable)
            if c['kind']=='mod':c['residue']=(c['residue']+1)%c['modulus']
            else:
                top=n if c['kind']=='count' else max(0,n-len(c['pattern'])+1)
                c['exact']=min(top,c['exact']+1)
    return validate_problem(dict(n=n,alphabet=[0,1,2,3],constraints=cs))


def oracle_selfcheck():
    rng=random.Random(261014501);checks=[]
    masks=[(k,) for k in KINDS]+[KINDS]
    masks += [tuple(rng.sample(KINDS,rng.randint(1,7))) for _ in range(72)]
    for index,kinds in enumerate(masks):
        p=make_instance(rng,kinds,rng.randint(1,7))
        a=exact_dp(p)['answer'];b=brute_force(p)
        checks.append(dict(problem=p,dp=a,brute=b,passed=a==b))
    edge_tasks=[dict(n=5,alphabet=[0,1,2,3],constraints=[dict(kind='pattern_count',pattern='010',exact=2)]),
        dict(n=1,alphabet=[0,1,2,3],constraints=[dict(kind='endpoints')]),
        dict(n=4,alphabet=[0,1,2,3],constraints=[dict(kind='adjacent'),dict(kind='pattern_count',pattern='00',exact=1)]),
        dict(n=3,alphabet=[0,1,2,3],constraints=[dict(kind='forbidden',patterns=['0','1','2','3'])])]
    for p in edge_tasks:
        a=exact_dp(p)['answer'];b=brute_force(p);checks.append(dict(problem=p,dp=a,brute=b,passed=a==b))
    assert all(c['passed'] for c in checks)
    assert checks[-4]['dp']==1  # Only 01010 contains two overlapping 010s at n=5.
    return checks
