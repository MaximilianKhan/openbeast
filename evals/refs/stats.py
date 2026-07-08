def mean(values):
    if not values:
        raise ValueError("mean of empty data")
    return sum(values) / len(values)


def variance(values, sample=False):
    n = len(values)
    if n == 0:
        raise ValueError("variance of empty data")
    if sample and n == 1:
        raise ValueError("sample variance requires at least two data points")
    m = mean(values)
    ss = sum((v - m) ** 2 for v in values)
    denom = n - 1 if sample else n
    return ss / denom


def stdev(values, sample=False):
    return variance(values, sample=sample) ** 0.5
