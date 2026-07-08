import sys
def _add(a,b):
    out=[]; carry=0
    for i in range(max(len(a),len(b))):
        s=carry+(a[i] if i<len(a) else 0)+(b[i] if i<len(b) else 0)
        out.append(s&0xFF); carry=s>>8
    if carry: out.append(carry)
    return out
def _sub(a,b):
    out=[]; borrow=0
    for i in range(len(a)):
        d=a[i]-borrow-(b[i] if i<len(b) else 0)
        if d<0: d+=256; borrow=1
        else: borrow=0
        out.append(d)
    while len(out)>1 and out[-1]==0: out.pop()
    return out
def _school(a,b):
    out=[0]*(len(a)+len(b))
    for i,x in enumerate(a):
        if x==0: continue
        carry=0
        for j,y in enumerate(b):
            s=out[i+j]+x*y+carry; out[i+j]=s&0xFF; carry=s>>8
        k=i+len(b)
        while carry: s=out[k]+carry; out[k]=s&0xFF; carry=s>>8; k+=1
    while len(out)>1 and out[-1]==0: out.pop()
    return out
def karat(a,b):
    if len(a)<=32 or len(b)<=32: return _school(a,b)
    m=min(len(a),len(b))//2
    a0,a1=a[:m],a[m:]; b0,b1=b[:m],b[m:]
    z0=karat(a0,b0); z2=karat(a1,b1)
    z1=_sub(_sub(karat(_add(a0,a1),_add(b0,b1)),z0),z2)
    out=[0]*(len(a)+len(b)+4)
    for i,v in enumerate(z0): out[i]+=v
    for i,v in enumerate(z1): out[i+m]+=v
    for i,v in enumerate(z2): out[i+2*m]+=v
    carry=0
    for i in range(len(out)):
        s=out[i]+carry; out[i]=s&0xFF; carry=s>>8
    while len(out)>1 and out[-1]==0: out.pop()
    return out
def main():
    data=sys.stdin.read().split('\n'); pos=0; T=int(data[pos]); pos+=1; res=[]
    for _ in range(T):
        na,nb=map(int,data[pos].split()); pos+=1
        a=list(map(int,data[pos].split())); pos+=1
        b=list(map(int,data[pos].split())); pos+=1
        res.append(' '.join(map(str,karat(a,b))))
    sys.stdout.write('\n'.join(res)+'\n')
main()
