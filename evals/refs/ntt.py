import sys
MOD=998244353
def ntt(a,inv):
    n=len(a); j=0
    for i in range(1,n):
        bit=n>>1
        while j&bit: j^=bit; bit>>=1
        j^=bit
        if i<j: a[i],a[j]=a[j],a[i]
    length=2
    while length<=n:
        w=pow(3,(MOD-1)//length,MOD)
        if inv: w=pow(w,MOD-2,MOD)
        half=length>>1
        for i in range(0,n,length):
            wn=1
            for k in range(half):
                u=a[i+k]; v=a[i+k+half]*wn%MOD
                a[i+k]=(u+v)%MOD; a[i+k+half]=(u-v)%MOD; wn=wn*w%MOD
        length<<=1
    if inv:
        ninv=pow(n,MOD-2,MOD)
        for i in range(n): a[i]=a[i]*ninv%MOD
    return a
def conv(a,b):
    if not a or not b: return [0]
    rl=len(a)+len(b)-1; n=1
    while n<rl: n<<=1
    fa=a+[0]*(n-len(a)); fb=b+[0]*(n-len(b))
    ntt(fa,False); ntt(fb,False)
    fc=[fa[i]*fb[i]%MOD for i in range(n)]
    ntt(fc,True)
    return fc[:rl]
def main():
    data=sys.stdin.read().split('\n'); pos=0; T=int(data[pos]); pos+=1; res=[]
    for _ in range(T):
        na,nb=map(int,data[pos].split()); pos+=1
        a=list(map(int,data[pos].split())) if data[pos].strip() else []; pos+=1
        b=list(map(int,data[pos].split())) if data[pos].strip() else []; pos+=1
        res.append(' '.join(map(str,conv(a,b))))
    sys.stdout.write('\n'.join(res)+'\n')
main()
