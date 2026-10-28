"""Offline trusted engineering references, frozen before hidden validation.
Not operators offered to Agent; each has a separate small exact check.
"""
DIGITS='''class Solution:
 def hasSameDigits(self,s: str) -> bool:
  a=[int(x) for x in s]
  while len(a)>2:a=[(a[i]+a[i+1])%10 for i in range(len(a)-1)]
  return a[0]==a[1]
'''
TABLE='x=int(input());print(sum(i*j for i in range(1,10) for j in range(1,10) if i*j!=x))'
REPLACE='''s=list(input())
for i in range(len(s)-2,-1,-1):
 if s[i]=='W' and s[i+1]=='A':s[i]='A';s[i+1]='C'
print(''.join(s))
'''
SUBSEQUENCE='''class Solution:
 def minMaxSums(self,nums: List[int],k: int) -> int:
  P=1000000007;nums=sorted(nums);n=len(nums)
  fac=[1]*(n+1)
  for i in range(1,n+1):fac[i]=fac[i-1]*i%P
  inv=[1]*(n+1);inv[n]=pow(fac[n],P-2,P)
  for i in range(n,0,-1):inv[i-1]=inv[i]*i%P
  s=1;ans=0
  for i in range(n):
   ans=(ans+(nums[i]+nums[n-i-1])*s)%P
   c=fac[i]*inv[k-1]%P*inv[i-k+1]%P if i>=k-1 else 0
   s=(2*s-c)%P
  return ans
'''
LCS='''N,M=map(int,input().split());S=input().strip();P=998244353
letters=list(set(S));weights=[1]*len(letters)
if len(letters)<26:letters.append('#');weights.append(26-len(set(S)))
trans=[]
for mask in range(1<<N):
 old=[0]
 for i in range(N):old.append(old[-1]+((mask>>i)&1))
 dest={}
 for ch,w in zip(letters,weights):
  new=[0];nm=0
  for i in range(1,N+1):
   new.append(max(old[i],new[-1],old[i-1]+int(S[i-1]==ch)))
   if new[-1]>new[-2]:nm|=1<<(i-1)
  dest[nm]=dest.get(nm,0)+w
 trans.append(list(dest.items()))
dp=[0]*(1<<N);dp[0]=1
for _ in range(M):
 nd=[0]*(1<<N)
 for state,c in enumerate(dp):
  if c:
   for ns,w in trans[state]:nd[ns]=(nd[ns]+c*w)%P
 dp=nd
ans=[0]*(N+1)
for state,c in enumerate(dp):ans[state.bit_count()]=(ans[state.bit_count()]+c)%P
print(*ans)
'''
PREFIX='''class Solution:
 def longestCommonPrefix(self,words: List[str],k: int) -> List[int]:
  n=len(words)
  if n-1<k:return [0]*n
  nxt=[{}];cnt=[0];depth=[0];paths=[]
  for s in words:
   node=0;path=[]
   for ch in s:
    if ch not in nxt[node]:
     nxt[node][ch]=len(nxt);nxt.append({});cnt.append(0);depth.append(depth[node]+1)
    node=nxt[node][ch];cnt[node]+=1;path.append(node)
   paths.append(path)
  maxd=max(depth);good=[[] for _ in range(maxd+1)]
  for node in range(1,len(nxt)):
   if cnt[node]>=k:good[depth[node]].append(node)
  stable=0;best=0;fragile=[-1]*(maxd+1)
  for d,nodes in enumerate(good):
   if nodes:
    best=d
    if len(nodes)>1 or cnt[nodes[0]]>k:stable=d
    else:fragile[d]=nodes[0]
  ans=[]
  for path in paths:
   d=best
   while d>stable and d<=len(path) and path[d-1]==fragile[d]:d-=1
   ans.append(d)
  return ans
'''
REFERENCES={'lcb-v6-3768':DIGITS,'lcb-v6-abc387_b':TABLE,'lcb-v6-abc394_c':REPLACE,'lcb-v6-3714':SUBSEQUENCE,'lcb-v6-abc391_g':LCS,'lcb-v6-3784':PREFIX}
