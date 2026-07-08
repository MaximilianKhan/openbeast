import math


def bootstrap_zero_rates(par_swap_rates, frequency=2):
    out = {}
    d_prior_sum = 0.0
    for T, par in par_swap_rates:
        c = par / frequency
        D_T = (1 - c * d_prior_sum) / (1 + c)
        d_prior_sum += D_T
        out[T] = -math.log(D_T) / T
    return out
