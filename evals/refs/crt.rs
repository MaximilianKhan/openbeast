use std::io::{self, Read, Write, BufWriter};

fn egcd(a: i64, b: i64) -> (i64, i64, i64) {
    if b == 0 { (a, 1, 0) }
    else {
        let (g, x1, y1) = egcd(b, a % b);
        (g, y1, x1 - (a / b) * y1)
    }
}
fn modinv(a: i64, n: i64) -> Option<i64> {
    let aa = ((a % n) + n) % n;
    let (g, x, _) = egcd(aa, n);
    if g != 1 { None } else { Some(((x % n) + n) % n) }
}
fn igcd(a: i64, b: i64) -> i64 {
    let mut a = a.abs();
    let mut b = b.abs();
    while b != 0 { let t = a % b; a = b; b = t; }
    a
}
fn crt(rs: &[i64], ms: &[i64]) -> i64 {
    let mut m_acc: i64 = 1;
    let mut x: i64 = 0;
    for i in 0..rs.len() {
        let (r, m) = (rs[i], ms[i]);
        let g = igcd(m_acc, m);
        if (((r - x) % g) + g) % g != 0 { return -1; }
        let m2 = m / g;
        let big_m2 = m_acc / g;
        let inv = match modinv(big_m2 % m2, m2) { Some(v) => v, None => return -1 };
        let k = ((((r - x) / g) * inv) % m2 + m2) % m2;
        x = x + m_acc * k;
        m_acc = m_acc * m2;
        x = ((x % m_acc) + m_acc) % m_acc;
    }
    if m_acc == 0 { 0 } else { ((x % m_acc) + m_acc) % m_acc }
}

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let k: usize = it.next().unwrap().parse().unwrap();
        let mut rs = Vec::with_capacity(k);
        let mut ms = Vec::with_capacity(k);
        for _ in 0..k {
            rs.push(it.next().unwrap().parse::<i64>().unwrap());
            ms.push(it.next().unwrap().parse::<i64>().unwrap());
        }
        writeln!(out, "{}", crt(&rs, &ms)).unwrap();
    }
}
