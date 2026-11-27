def rkf45(f, t0, y0, t_end, tol=1e-8, h_init=0.01):
    t, y, h = t0, y0, h_init
    out = [(t, y)]
    while t < t_end:
        # clamp the final step so we land exactly on t_end
        if t + h > t_end:
            h = t_end - t
        k1 = h * f(t, y)
        k2 = h * f(t + h / 4, y + k1 / 4)
        k3 = h * f(t + 3 * h / 8, y + 3 * k1 / 32 + 9 * k2 / 32)
        k4 = h * f(t + 12 * h / 13,
                   y + 1932 * k1 / 2197 - 7200 * k2 / 2197 + 7296 * k3 / 2197)
        k5 = h * f(t + h,
                   y + 439 * k1 / 216 - 8 * k2 + 3680 * k3 / 513 - 845 * k4 / 4104)
        k6 = h * f(t + h / 2,
                   y - 8 * k1 / 27 + 2 * k2 - 3544 * k3 / 2565
                   + 1859 * k4 / 4104 - 11 * k5 / 40)
        y4 = y + 25 * k1 / 216 + 1408 * k3 / 2565 + 2197 * k4 / 4104 - k5 / 5
        y5 = (y + 16 * k1 / 135 + 6656 * k3 / 12825 + 28561 * k4 / 56430
              - 9 * k5 / 50 + 2 * k6 / 55)
        err = abs(y5 - y4)
        if err == 0:
            # exact step: accept and grow h by the max factor
            t += h
            y = y5
            out.append((t, y))
            h = h * 5
            continue
        if err < tol:
            t += h
            y = y5
            out.append((t, y))
        # adapt h (clamped to [0.1*h, 5*h]); on reject we simply retry from
        # the same t with the shrunk h
        factor = 0.9 * (tol / err) ** 0.2
        factor = max(0.1, min(5.0, factor))
        h = h * factor
    return out
