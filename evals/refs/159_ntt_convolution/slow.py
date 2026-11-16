import sys
# Correct but too slow: Karatsuba convolution mod p instead of an NTT.
MOD=998244353
CUT=48
def school(a,b):
    out=[0]*(len(a)+len(b)-1)
    for i,x in enumerate(a):
        if x==0: continue
        for j,y in enumerate(b):
            out[i+j]+=x*y
    return [v%MOD for v in out]
def kara(a,b):
    n=len(a)
    if n<=CUT: return school(a,b)
    h=(n+1)//2; pad=[0]*(2*h-n)
    a0,a1=a[:h],a[h:]+pad; b0,b1=b[:h],b[h:]+pad
    z0=kara(a0,b0); z2=kara(a1,b1)
    zm=kara([(x+y)%MOD for x,y in zip(a0,a1)],[(x+y)%MOD for x,y in zip(b0,b1)])
    res=[0]*(4*h-1)
    for i in range(2*h-1):
        res[i]+=z0[i]; res[i+h]+=zm[i]-z0[i]-z2[i]; res[i+2*h]+=z2[i]
    return [v%MOD for v in res[:2*n-1]]
def conv(a,b):
    if not a or not b: return [0]
    rl=len(a)+len(b)-1; n=max(len(a),len(b))
    return kara(a+[0]*(n-len(a)),b+[0]*(n-len(b)))[:rl]
def main():
    data=sys.stdin.read().split('\n'); pos=0; T=int(data[pos]); pos+=1; res=[]
    for _ in range(T):
        na,nb=map(int,data[pos].split()); pos+=1
        a=list(map(int,data[pos].split())) if data[pos].strip() else []; pos+=1
        b=list(map(int,data[pos].split())) if data[pos].strip() else []; pos+=1
        res.append(' '.join(map(str,conv(a,b))))
    sys.stdout.write('\n'.join(res)+'\n')
main()
