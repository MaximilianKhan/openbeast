import sys

def accel(p, mass, n, G=1.0):
    a = [[0.0, 0.0] for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j: continue
            dx = p[j][0] - p[i][0]
            dy = p[j][1] - p[i][1]
            r2 = dx*dx + dy*dy
            r = r2**0.5
            if r > 1e-12:
                f = G * mass[j] / (r2 * r)
                a[i][0] += f * dx
                a[i][1] += f * dy
    return a

data = sys.stdin.read().split()
idx = 0
T = int(data[idx]); idx += 1
out = []
for _ in range(T):
    N = int(data[idx]); idx += 1
    dt = float(data[idx]); idx += 1
    steps = int(data[idx]); idx += 1
    mass = []
    pos = []
    vel = []
    for _ in range(N):
        m = float(data[idx]); idx += 1
        x = float(data[idx]); idx += 1
        y = float(data[idx]); idx += 1
        vx = float(data[idx]); idx += 1
        vy = float(data[idx]); idx += 1
        mass.append(m); pos.append([x, y]); vel.append([vx, vy])
    a = accel(pos, mass, N)
    for _ in range(steps):
        for i in range(N):
            pos[i][0] += vel[i][0]*dt + 0.5*a[i][0]*dt*dt
            pos[i][1] += vel[i][1]*dt + 0.5*a[i][1]*dt*dt
        a_new = accel(pos, mass, N)
        for i in range(N):
            vel[i][0] += 0.5*(a[i][0] + a_new[i][0])*dt
            vel[i][1] += 0.5*(a[i][1] + a_new[i][1])*dt
        a = a_new
    for i in range(N):
        out.append(f'{pos[i][0]:.6f} {pos[i][1]:.6f} {vel[i][0]:.6f} {vel[i][1]:.6f}')
sys.stdout.write('\n'.join(out) + '\n')
