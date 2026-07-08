// Karatsuba multiplication on little-endian base-256 byte arrays.
// Vec<u8> digit ops; no u128/i128, no bignum crate.
// rustc -O
use std::io::{Read, Write};

const CUTOFF: usize = 32;

fn trim(mut n: Vec<u8>) -> Vec<u8> {
    while n.len() > 1 && *n.last().unwrap() == 0 {
        n.pop();
    }
    n
}

fn add(a: &[u8], b: &[u8]) -> Vec<u8> {
    let n = a.len().max(b.len());
    let mut out = vec![0u8; n + 1];
    let mut carry: u32 = 0;
    for i in 0..n {
        let mut s = carry;
        if i < a.len() {
            s += a[i] as u32;
        }
        if i < b.len() {
            s += b[i] as u32;
        }
        out[i] = (s & 0xFF) as u8;
        carry = s >> 8;
    }
    out[n] = carry as u8;
    trim(out)
}

// a - b, assuming a >= b.
fn sub(a: &[u8], b: &[u8]) -> Vec<u8> {
    let mut out = vec![0u8; a.len()];
    let mut borrow: i32 = 0;
    for i in 0..a.len() {
        let mut d = a[i] as i32 - borrow;
        if i < b.len() {
            d -= b[i] as i32;
        }
        if d < 0 {
            d += 256;
            borrow = 1;
        } else {
            borrow = 0;
        }
        out[i] = d as u8;
    }
    trim(out)
}

fn school(a: &[u8], b: &[u8]) -> Vec<u8> {
    let mut acc = vec![0u32; a.len() + b.len()];
    for i in 0..a.len() {
        let x = a[i] as u32;
        if x == 0 {
            continue;
        }
        let mut carry: u32 = 0;
        for j in 0..b.len() {
            let s = acc[i + j] + x * (b[j] as u32) + carry;
            acc[i + j] = s & 0xFF;
            carry = s >> 8;
        }
        let mut k = i + b.len();
        while carry != 0 {
            let s = acc[k] + carry;
            acc[k] = s & 0xFF;
            carry = s >> 8;
            k += 1;
        }
    }
    trim(acc.iter().map(|&v| v as u8).collect())
}

fn karat(a: &[u8], b: &[u8]) -> Vec<u8> {
    if a.len() <= CUTOFF || b.len() <= CUTOFF {
        return school(a, b);
    }
    let m = a.len().min(b.len()) / 2;
    let (a0, a1) = a.split_at(m);
    let (b0, b1) = b.split_at(m);

    let z0 = karat(a0, b0);
    let z2 = karat(a1, b1);
    let sa = add(a0, a1);
    let sb = add(b0, b1);
    let zm = karat(&sa, &sb);
    let z1 = sub(&sub(&zm, &z0), &z2);

    let total = a.len() + b.len() + 4;
    let mut acc = vec![0u32; total];
    for (i, &v) in z0.iter().enumerate() {
        acc[i] += v as u32;
    }
    for (i, &v) in z1.iter().enumerate() {
        acc[i + m] += v as u32;
    }
    for (i, &v) in z2.iter().enumerate() {
        acc[i + 2 * m] += v as u32;
    }
    let mut carry: u32 = 0;
    for slot in acc.iter_mut() {
        let s = *slot + carry;
        *slot = s & 0xFF;
        carry = s >> 8;
    }
    trim(acc.iter().map(|&v| v as u8).collect())
}

fn main() {
    let mut input = Vec::new();
    std::io::stdin().read_to_end(&mut input).unwrap();

    let mut pos = 0usize;
    let read_int = |pos: &mut usize| -> usize {
        while *pos < input.len() && !(input[*pos] >= b'0' && input[*pos] <= b'9') {
            *pos += 1;
        }
        let mut n = 0usize;
        while *pos < input.len() && input[*pos] >= b'0' && input[*pos] <= b'9' {
            n = n * 10 + (input[*pos] - b'0') as usize;
            *pos += 1;
        }
        n
    };

    let mut out: Vec<u8> = Vec::with_capacity(1 << 20);
    let t = read_int(&mut pos);
    for _ in 0..t {
        let na = read_int(&mut pos);
        let nb = read_int(&mut pos);
        let mut a = vec![0u8; na];
        let mut b = vec![0u8; nb];
        for i in 0..na {
            a[i] = read_int(&mut pos) as u8;
        }
        for i in 0..nb {
            b[i] = read_int(&mut pos) as u8;
        }
        let a = trim(a);
        let b = trim(b);

        let p = karat(&a, &b);
        for (i, &v) in p.iter().enumerate() {
            if i > 0 {
                out.push(b' ');
            }
            out.extend_from_slice(v.to_string().as_bytes());
        }
        out.push(b'\n');
    }
    std::io::stdout().write_all(&out).unwrap();
}
