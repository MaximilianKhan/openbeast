use std::io::{self, Read, Write};

fn mulmod(a: u64, b: u64, m: u64) -> u64 {
    ((a as u128 * b as u128) % m as u128) as u64
}

fn powmod(mut base: u64, mut exp: u64, m: u64) -> u64 {
    let mut res: u64 = 1;
    base %= m;
    while exp > 0 {
        if exp & 1 == 1 {
            res = mulmod(res, base, m);
        }
        base = mulmod(base, base, m);
        exp >>= 1;
    }
    res
}

fn is_prime(n: u64) -> bool {
    if n < 2 {
        return false;
    }
    let primes = [2u64, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37];
    for &p in &primes {
        if n == p {
            return true;
        }
        if n % p == 0 {
            return false;
        }
    }
    let mut d = n - 1;
    let mut s = 0;
    while d & 1 == 0 {
        d >>= 1;
        s += 1;
    }
    'witness: for &a in &primes {
        let mut x = powmod(a, d, n);
        if x == 1 || x == n - 1 {
            continue;
        }
        for _ in 0..s - 1 {
            x = mulmod(x, x, n);
            if x == n - 1 {
                continue 'witness;
            }
        }
        return false;
    }
    true
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut nums = input.split_whitespace();
    let t: usize = nums.next().unwrap().parse().unwrap();
    let mut out = String::new();
    for _ in 0..t {
        let n: u64 = nums.next().unwrap().parse().unwrap();
        out.push_str(if is_prime(n) { "true\n" } else { "false\n" });
    }
    io::stdout().write_all(out.as_bytes()).unwrap();
}
