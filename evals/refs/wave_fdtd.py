def wave_1d(initial, c, dx, dt, steps):
    if (c * dt / dx) ** 2 > 1:
        raise ValueError("CFL violation")
    n = len(initial)
    r2 = (c * dt / dx) ** 2
    u_prev = list(initial)
    u_curr = list(initial)
    for _ in range(steps):
        u_next = [0.0] * n
        for i in range(1, n - 1):
            u_next[i] = (2 * u_curr[i] - u_prev[i]
                         + r2 * (u_curr[i + 1] - 2 * u_curr[i] + u_curr[i - 1]))
        u_prev, u_curr = u_curr, u_next
    return u_curr
