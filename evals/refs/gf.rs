use std::io::{self, Read, Write, BufWriter};

fn parse_byte(s: &str) -> i32 {
    if s.starts_with("0x") || s.starts_with("0X") {
        i32::from_str_radix(&s[2..], 16).unwrap()
    } else {
        s.parse().unwrap()
    }
}

fn gfmul(mut a: i32, mut b: i32) -> i32 {
    let mut p = 0;
    for _ in 0..8 {
        if b & 1 != 0 { p ^= a; }
        b >>= 1;
        let hi = a & 0x80;
        a = (a << 1) & 0xFF;
        if hi != 0 { a ^= 0x1B; }
    }
    p
}

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let op = it.next().unwrap();
        let a = parse_byte(it.next().unwrap());
        let b = parse_byte(it.next().unwrap());
        let v = if op == "+" { a ^ b } else { gfmul(a, b) };
        writeln!(out, "{}", v).unwrap();
    }
}
