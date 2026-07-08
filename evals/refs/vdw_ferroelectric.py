import math


def spontaneous_polarization(alpha, beta, gamma=0):
    if alpha >= 0:
        raise ValueError("no ferroelectric phase (alpha >= 0)")
    if gamma == 0:
        return math.sqrt(-alpha / beta)
    # gamma*u^2 + beta*u + alpha = 0, u = P^2, take the positive root
    disc = beta * beta - 4 * gamma * alpha
    u = (-beta + math.sqrt(disc)) / (2 * gamma)
    return math.sqrt(u)


def coercive_field(alpha, beta, gamma=0):
    if alpha >= 0:
        raise ValueError("no ferroelectric phase (alpha >= 0)")
    if gamma == 0:
        p_inf = math.sqrt(-alpha / (3 * beta))
        return abs(alpha * p_inf + beta * p_inf ** 3)
    # 5*gamma*v^2 + 3*beta*v + alpha = 0, v = P^2
    disc = 9 * beta * beta - 20 * gamma * alpha
    v = (-3 * beta + math.sqrt(disc)) / (10 * gamma)
    p_inf = math.sqrt(v)
    return abs(alpha * p_inf + beta * p_inf ** 3 + gamma * p_inf ** 5)
