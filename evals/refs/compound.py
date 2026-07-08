def compound(principal, annual_rate, years, n_per_year=1):
    return principal * (1 + annual_rate / n_per_year) ** (n_per_year * years)
