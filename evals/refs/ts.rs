use std::io::{self, Read, Write};

fn mulmod(a: u64, b: u64, m: u64) -> u64 {
    ((a as u128 * b as u128) % m as u128) as u64
}

fn powmod(mut base: u64, mut exp: u64, m: u64) -> u64 {
    if m == 1 {
        return 0;
    }
    let mut result: u64 = 1;
    base %= m;
    while exp > 0 {
        if exp & 1 == 1 {
            result = mulmod(result, base, m);
        }
        base = mulmod(base, base, m);
        exp >>= 1;
    }
    result
}

fn tonelli(mut n: u64, p: u64) -> Option<u64> {
    n %= p;
    if n == 0 {
        return Some(0);
    }
    if p == 2 {
        return Some(n % 2);
    }
    if powmod(n, (p - 1) / 2, p) != 1 {
        return None;
    }
    if p % 4 == 3 {
        return Some(powmod(n, (p + 1) / 4, p));
    }

    let mut q = p - 1;
    let mut s: u64 = 0;
    while q % 2 == 0 {
        q /= 2;
        s += 1;
    }

    let mut z: u64 = 2;
    while powmod(z, (p - 1) / 2, p) != p - 1 {
        z += 1;
    }

    let mut m = s;
    let mut c = powmod(z, q, p);
    let mut t = powmod(n, q, p);
    let mut r = powmod(n, (q + 1) / 2, p);

    while t != 1 {
        let mut i: u64 = 1;
        let mut t2 = t;
        while i < m {
            t2 = mulmod(t2, t2, p);
            if t2 == 1 {
                break;
            }
            i += 1;
        }
        let mut b = c;
        let mut k: u64 = 0;
        while k < m - i - 1 {
            b = mulmod(b, b, p);
            k += 1;
        }
        m = i;
        c = mulmod(b, b, p);
        t = mulmod(t, c, p);
        r = mulmod(r, b, p);
    }
    Some(r)
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut it = input.split_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for _ in 0..t {
        let n: u64 = it.next().unwrap().parse().unwrap();
        let p: u64 = it.next().unwrap().parse().unwrap();
        match tonelli(n, p) {
            Some(r) => writeln!(out, "{}", r).unwrap(),
            None => writeln!(out, "NONE").unwrap(),
        }
    }
}
