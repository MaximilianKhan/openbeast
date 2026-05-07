use std::io::{self, Read, Write, BufWriter};

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let n: usize = it.next().unwrap().parse().unwrap();
        let a: Vec<i64> = (0..n).map(|_| it.next().unwrap().parse().unwrap()).collect();
        let b: Vec<i64> = (0..n).map(|_| it.next().unwrap().parse().unwrap()).collect();
        let mut sum: i64 = 0;
        for i in 0..n {
            sum += a[i] * b[i];
        }
        writeln!(out, "{}", sum).unwrap();
    }
}
