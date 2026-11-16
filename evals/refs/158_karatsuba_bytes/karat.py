import sys
# Karatsuba on little-endian base-256 digit lists. The recursion multiplies
# the digit sequences as polynomials (coefficients are ordinary small ints,
# never a collapsed big number); one carry pass at the end turns the product
# coefficients back into bytes.
CUT=40
def _school(a,b):
    lb=len(b); out=[0]*(len(a)+lb-1)
    for i,x in enumerate(a):
        if x:
            out[i:i+lb]=[o+x*y for o,y in zip(out[i:i+lb],b)]
    return out
def _poly(a,b):
    # len(a)==len(b)
    n=len(a)
    if n<=CUT: return _school(a,b)
    h=(n+1)//2; pad=[0]*(2*h-n)
    a0=a[:h]; a1=a[h:]+pad; b0=b[:h]; b1=b[h:]+pad
    z0=_poly(a0,b0); z2=_poly(a1,b1)
    zm=_poly([x+y for x,y in zip(a0,a1)],[x+y for x,y in zip(b0,b1)])
    out=[0]*(4*h-1)
    out[:2*h-1]=z0
    out[2*h:4*h-1]=z2
    mid=[m-x-y for m,x,y in zip(zm,z0,z2)]
    out[h:3*h-1]=[o+m for o,m in zip(out[h:3*h-1],mid)]
    return out[:2*n-1]
def karat(a,b):
    n=max(len(a),len(b))
    c=_poly(a+[0]*(n-len(a)),b+[0]*(n-len(b)))
    out=[]; carry=0
    for v in c:
        s=v+carry; out.append(s&0xFF); carry=s>>8
    while carry: out.append(carry&0xFF); carry>>=8
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
