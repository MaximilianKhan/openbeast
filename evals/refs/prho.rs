use std::io::{self, Read, Write, BufWriter};

fn gcdu(mut a: u64, mut b: u64) -> u64 {
    while b != 0 { let t = a % b; a = b; b = t; } a
}
fn mulmod(a: u64, b: u64, m: u64) -> u64 {
    ((a as u128 * b as u128) % m as u128) as u64
}
fn pollard(n: u64) -> u64 {
    if n % 2 == 0 { return 2; }
    let mut c: u64 = 1;
    loop {
        let (mut x, mut y, mut d) = (2u64, 2u64, 1u64);
        while d == 1 {
            x = (mulmod(x, x, n) + c) % n;
            y = (mulmod(y, y, n) + c) % n;
            y = (mulmod(y, y, n) + c) % n;
            let diff = if x > y { x - y } else { y - x };
            d = gcdu(diff, n);
        }
        if d != n { return d; }
        c += 1;
    }
}

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let n: u64 = it.next().unwrap().parse().unwrap();
        writeln!(out, "{}", pollard(n)).unwrap();
    }
}
