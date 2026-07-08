// Polynomial convolution via NTT mod p = 998244353, primitive root g = 3.
// Iterative radix-2 with bit-reversal permutation. u128 modular multiply.
// Matches the reference Python NTT byte-for-byte.
use std::io::{self, Read, Write};

const MOD: u64 = 998244353;

#[inline]
fn mulmod(a: u64, b: u64) -> u64 {
    ((a as u128 * b as u128) % MOD as u128) as u64
}

fn powmod(mut a: u64, mut e: u64) -> u64 {
    let mut r: u64 = 1;
    a %= MOD;
    while e > 0 {
        if e & 1 == 1 {
            r = mulmod(r, a);
        }
        a = mulmod(a, a);
        e >>= 1;
    }
    r
}

fn ntt(a: &mut [u64], inv: bool) {
    let n = a.len();
    let mut j = 0usize;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j ^= bit;
        if i < j {
            a.swap(i, j);
        }
    }
    let mut len = 2usize;
    while len <= n {
        let mut w = powmod(3, (MOD - 1) / len as u64);
        if inv {
            w = powmod(w, MOD - 2);
        }
        let half = len >> 1;
        let mut i = 0usize;
        while i < n {
            let mut wn: u64 = 1;
            for k in 0..half {
                let u = a[i + k];
                let v = mulmod(a[i + k + half], wn);
                a[i + k] = (u + v) % MOD;
                a[i + k + half] = (u + MOD - v) % MOD;
                wn = mulmod(wn, w);
            }
            i += len;
        }
        len <<= 1;
    }
    if inv {
        let ninv = powmod(n as u64, MOD - 2);
        for x in a.iter_mut() {
            *x = mulmod(*x, ninv);
        }
    }
}

fn conv(a: &[u64], b: &[u64]) -> Vec<u64> {
    if a.is_empty() || b.is_empty() {
        return vec![0];
    }
    let rl = a.len() + b.len() - 1;
    let mut n = 1usize;
    while n < rl {
        n <<= 1;
    }
    let mut fa = vec![0u64; n];
    let mut fb = vec![0u64; n];
    fa[..a.len()].copy_from_slice(a);
    fb[..b.len()].copy_from_slice(b);
    ntt(&mut fa, false);
    ntt(&mut fb, false);
    for i in 0..n {
        fa[i] = mulmod(fa[i], fb[i]);
    }
    ntt(&mut fa, true);
    fa.truncate(rl);
    fa
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut it = input.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    let mut out = String::new();
    for _ in 0..t {
        let na: usize = it.next().unwrap().parse().unwrap();
        let nb: usize = it.next().unwrap().parse().unwrap();
        let mut a = Vec::with_capacity(na);
        for _ in 0..na {
            a.push(it.next().unwrap().parse::<u64>().unwrap() % MOD);
        }
        let mut b = Vec::with_capacity(nb);
        for _ in 0..nb {
            b.push(it.next().unwrap().parse::<u64>().unwrap() % MOD);
        }
        let c = conv(&a, &b);
        let mut first = true;
        for v in c {
            if !first {
                out.push(' ');
            }
            first = false;
            out.push_str(&v.to_string());
        }
        out.push('\n');
    }
    io::stdout().write_all(out.as_bytes()).unwrap();
}
