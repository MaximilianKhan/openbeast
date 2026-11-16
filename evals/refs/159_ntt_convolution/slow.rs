// Correct but too slow: Karatsuba convolution mod p = 998244353 instead of
// an NTT. rustc -O
use std::io::{self, Read, Write};

const MOD: u64 = 998244353;
const CUT: usize = 20000;

// r[0 .. 2n-2] += a * b for a, b of length n.
fn kara(a: &[u64], b: &[u64], r: &mut [u64]) {
    let n = a.len();
    if n <= CUT {
        for i in 0..n {
            let x = a[i];
            if x == 0 {
                continue;
            }
            for j in 0..n {
                r[i + j] = (r[i + j] + x * b[j]) % MOD;
            }
        }
        return;
    }
    let h = (n + 1) / 2;
    let mut a1 = vec![0u64; h];
    let mut b1 = vec![0u64; h];
    a1[..n - h].copy_from_slice(&a[h..]);
    b1[..n - h].copy_from_slice(&b[h..]);
    let sa: Vec<u64> = (0..h).map(|i| (a[i] + a1[i]) % MOD).collect();
    let sb: Vec<u64> = (0..h).map(|i| (b[i] + b1[i]) % MOD).collect();
    let mut z0 = vec![0u64; 2 * h - 1];
    let mut z2 = vec![0u64; 2 * h - 1];
    let mut zm = vec![0u64; 2 * h - 1];
    kara(&a[..h], &b[..h], &mut z0);
    kara(&a1, &b1, &mut z2);
    kara(&sa, &sb, &mut zm);
    let rl = 2 * n - 1;
    for i in 0..2 * h - 1 {
        r[i] = (r[i] + z0[i]) % MOD;
        let mid = (zm[i] + 2 * MOD - z0[i] - z2[i]) % MOD;
        r[i + h] = (r[i + h] + mid) % MOD;
        if i + 2 * h < rl {
            r[i + 2 * h] = (r[i + 2 * h] + z2[i]) % MOD;
        }
    }
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
        let n = na.max(nb);
        let mut a = vec![0u64; n];
        let mut b = vec![0u64; n];
        for i in 0..na {
            a[i] = it.next().unwrap().parse::<u64>().unwrap() % MOD;
        }
        for i in 0..nb {
            b[i] = it.next().unwrap().parse::<u64>().unwrap() % MOD;
        }
        if na == 0 || nb == 0 {
            out.push_str("0\n");
            continue;
        }
        let mut r = vec![0u64; 2 * n - 1];
        kara(&a, &b, &mut r);
        for i in 0..na + nb - 1 {
            if i > 0 {
                out.push(' ');
            }
            out.push_str(&r[i].to_string());
        }
        out.push('\n');
    }
    io::stdout().write_all(out.as_bytes()).unwrap();
}
