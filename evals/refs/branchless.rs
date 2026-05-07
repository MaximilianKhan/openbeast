use std::io::{self, Read, Write, BufWriter};

fn min2(a: i64, b: i64) -> i64 {
    b ^ ((a ^ b) & ((a - b) >> 63))
}
fn min3(a: i64, b: i64, c: i64) -> i64 {
    min2(a, min2(b, c))
}

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let a: i64 = it.next().unwrap().parse().unwrap();
        let b: i64 = it.next().unwrap().parse().unwrap();
        let c: i64 = it.next().unwrap().parse().unwrap();
        writeln!(out, "{}", min3(a, b, c)).unwrap();
    }
}
