import math
import cmath


def evolve_qubit(psi0, H, t):
    h00, h01 = H[0][0], H[0][1]
    h10, h11 = H[1][0], H[1][1]
    a = ((h00 + h11) / 2).real if isinstance(h00, complex) else (h00 + h11) / 2
    bb = (h01 + h10) / 2
    b = bb.real if isinstance(bb, complex) else bb
    cc = 1j * (h01 - h10) / 2
    c = cc.real if isinstance(cc, complex) else cc
    dd = (h00 - h11) / 2
    d = dd.real if isinstance(dd, complex) else dd
    n = math.sqrt(b * b + c * c + d * d)
    gp = cmath.exp(-1j * a * t)
    if n == 0:
        u = [[gp, 0], [0, gp]]
    else:
        co = math.cos(n * t)
        si = math.sin(n * t)
        u = [[gp * (co - 1j * si * d / n), gp * (-1j * si * (b - 1j * c) / n)],
             [gp * (-1j * si * (b + 1j * c) / n), gp * (co + 1j * si * d / n)]]
    a0, a1 = psi0
    return [u[0][0] * a0 + u[0][1] * a1, u[1][0] * a0 + u[1][1] * a1]


def measure_prob_zero(psi):
    return abs(psi[0]) ** 2
