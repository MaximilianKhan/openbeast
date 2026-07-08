import math
def _crr(S,K,T,r,sigma,N,is_call):
    dt=T/N; u=math.exp(sigma*math.sqrt(dt)); d=1.0/u
    p=(math.exp(r*dt)-d)/(u-d); disc=math.exp(-r*dt)
    val=[max((S*u**(N-i)*d**i - K) if is_call else (K - S*u**(N-i)*d**i), 0.0) for i in range(N+1)]
    for step in range(N-1,-1,-1):
        nv=[]
        for i in range(step+1):
            cont=disc*(p*val[i]+(1-p)*val[i+1])
            s=S*u**(step-i)*d**i
            ex=(s-K) if is_call else (K-s)
            nv.append(max(cont,ex))
        val=nv
    return val[0]
def american_call(S,K,T,r,sigma,N=200): return _crr(S,K,T,r,sigma,N,True)
def american_put(S,K,T,r,sigma,N=200): return _crr(S,K,T,r,sigma,N,False)
